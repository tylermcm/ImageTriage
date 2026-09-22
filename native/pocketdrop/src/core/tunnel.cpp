#include "tunnel.h"
#include <vector>

namespace {

const int MAX_ATTEMPTS = 3;

// 1 = a public resolver has the name, 0 = not yet, -1 = resolver unreachable.
// Uses DNS-over-HTTPS by IP so the local resolver is never asked early: routers
// cache the "does not exist" answer for a brand-new name, breaking the link for
// every device on that network for up to half an hour.
int publicDnsHas(const std::string& host, const char* endpoint) {
    plat::HttpResult r = plat::http_get(std::string(endpoint) + host + "&type=A", {"Accept: application/dns-json"},
                                        plat::BAD_FILE, nullptr, 2500);
    if (r.status != 200) return -1;
    return r.body.find("\"Status\":0") != std::string::npos && r.body.find("\"Answer\"") != std::string::npos ? 1 : 0;
}

void logWrite(plat::File log, const std::string& s) {
    if (log != plat::BAD_FILE) plat::file_write(log, s.data(), s.size());
}

std::string friendlyError(const std::string& raw) {
    if (raw.find("429") != std::string::npos || raw.find("Too Many Requests") != std::string::npos)
        return "Cloudflare is rate-limiting new tunnels. Try again in a minute.";
    if (raw.find("failed to request quick Tunnel") != std::string::npos &&
        (raw.find("deadline exceeded") != std::string::npos || raw.find("Timeout") != std::string::npos))
        return "Cloudflare's tunnel service didn't respond. Try again.";
    if (raw.find("no such host") != std::string::npos || raw.find("dial tcp") != std::string::npos)
        return "Can't reach Cloudflare. Check your internet connection.";
    return raw;
}

} // namespace

std::string Tunnel::findBinary() {
    for (const auto& p : plat::cloudflared_candidates()) {
        plat::FileStat st = plat::file_stat(p);
        if (st.exists && !st.isDir) return p;
    }
    return {};
}

std::string Tunnel::url() const {
    std::lock_guard<std::mutex> l(mu_);
    return url_;
}

std::string Tunnel::message() const {
    std::lock_guard<std::mutex> l(mu_);
    return msg_;
}

void Tunnel::set(State s, const std::string& msg) {
    {
        std::lock_guard<std::mutex> l(mu_);
        msg_ = msg;
    }
    state_ = s;
    if (onChange_) onChange_();
}

void Tunnel::start(int port, bool allowDownload) {
    stop();
    stop_ = false;
    {
        std::lock_guard<std::mutex> l(mu_);
        url_.clear();
    }
    thread_ = std::thread([this, port, allowDownload] { run(port, allowDownload); });
}

void Tunnel::stop() {
    stop_ = true;
    {
        std::lock_guard<std::mutex> l(mu_);
        if (proc_) plat::process_kill(proc_);
    }
    if (thread_.joinable()) thread_.join();
    if (probeThread_.joinable()) probeThread_.join();
    if (state_ != State::Off) set(State::Off, "");
}

bool Tunnel::download(std::string& err) {
    std::string tmp = util::path_join(plat::app_data_dir(), "cloudflared.download");
    plat::File f = plat::file_create(tmp);
    if (f == plat::BAD_FILE) {
        err = "Cannot write " + tmp;
        return false;
    }
    uint64_t lastTick = 0;
    plat::HttpResult r = plat::http_get(plat::cloudflared_download_url(), {}, f,
                                        [&](uint64_t got, uint64_t total) {
                                            if (total) progress_ = (int)(got * 100 / total);
                                            uint64_t now = plat::tick_ms();
                                            if (now - lastTick > 150) {
                                                lastTick = now;
                                                if (onChange_) onChange_();
                                            }
                                            return !stop_;
                                        },
                                        30000);
    plat::file_close(f);
    bool ok = r.status == 200;
    if (!ok) err = !r.err.empty() ? r.err : "Download failed (HTTP " + std::to_string(r.status) + ")";
    else ok = plat::cloudflared_install(tmp, err);
    plat::file_remove(tmp);
    return ok;
}

void Tunnel::waitUntilPublic(const std::string& url, plat::File log) {
    std::string host = url.substr(8); // strip "https://"
    uint64_t start = plat::tick_ms();
    // Any resolver asked before the record exists caches "does not exist", so give
    // Cloudflare a head start and only ask its own resolver, which learns the name first.
    for (int i = 0; i < 30 && !stop_; i++) plat::sleep_ms(100);
    bool live = false;
    int unreachable = 0;
    while (!stop_ && alive_ && plat::tick_ms() - start < 30000) {
        int r = publicDnsHas(host, "https://1.1.1.1/dns-query?name=");
        char line[96];
        snprintf(line, sizeof line, "[PocketDrop] 1.1.1.1 lookup at %.1fs: %s\n", (plat::tick_ms() - start) / 1000.0,
                 r == 1 ? "found" : r == 0 ? "not yet" : "unreachable");
        logWrite(log, line);
        if (r == 1) {
            live = true;
            break;
        }
        if (r < 0 && ++unreachable >= 3) break; // DNS-over-HTTPS blocked here; fall back to a fixed delay
        for (int i = 0; i < 10 && !stop_; i++) plat::sleep_ms(100);
    }
    // Let other resolvers (e.g. mobile carriers) catch up before anyone scans.
    for (int i = 0; i < (live ? 20 : 50) && !stop_; i++) plat::sleep_ms(100);
    if (stop_ || !alive_) return;
    {
        std::lock_guard<std::mutex> l(mu_);
        url_ = url;
    }
    set(State::Online, "Reachable from anywhere");
}

void Tunnel::run(int port, bool allowDownload) {
    progress_ = 0;
    std::string exe = findBinary();
    if (exe.empty()) {
        if (!allowDownload) {
            set(State::Missing, "Needs cloudflared (free, ~40 MB)");
            return;
        }
        set(State::Downloading, "Downloading cloudflared…");
        std::string err;
        if (!download(err)) {
            set(stop_ ? State::Off : State::Failed, err);
            return;
        }
        exe = plat::cloudflared_install_path();
    }
    if (stop_) return;

    std::string home = util::path_join(plat::app_data_dir(), "cf-home");
    plat::make_dir(home);
    // Full output of the latest session, kept for diagnosis.
    plat::File log = plat::file_create(util::path_join(plat::app_data_dir(), "cloudflared.log"));
    std::string failure;
    for (int attempt = 1; attempt <= MAX_ATTEMPTS && !stop_; attempt++) {
        if (attempt > 1) {
            logWrite(log, "\n[PocketDrop] retrying, attempt " + std::to_string(attempt) + "\n");
            for (int i = 0; i < attempt * 15 && !stop_; i++) plat::sleep_ms(100);
            if (stop_) break;
        }
        set(State::Starting, attempt == 1 ? "Opening secure tunnel…" : "Retrying tunnel…");
        bool registered = false;
        failure = runOnce(exe, home, port, log, registered);
        // Only retry startup failures; a tunnel that was up and then dropped is reported.
        if (registered) break;
    }
    if (probeThread_.joinable()) probeThread_.join(); // it writes to the log
    if (log != plat::BAD_FILE) plat::file_close(log);
    if (!stop_) {
        {
            std::lock_guard<std::mutex> l(mu_);
            url_.clear();
        }
        set(State::Failed, failure);
    }
}

std::string Tunnel::runOnce(const std::string& exe, const std::string& home, int port, plat::File log,
                            bool& registered) {
    std::string err;
    // A private home dir so a user's ~/.cloudflared/config.yml can't interfere with quick tunnels.
    plat::Process* proc = plat::process_start(
        exe, {"tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:" + std::to_string(port)},
        {{"USERPROFILE", home}, {"HOME", home}}, home, err);
    if (!proc) {
        logWrite(log, "[PocketDrop] could not start cloudflared: " + err + "\n");
        return "Could not start cloudflared: " + err;
    }
    {
        std::lock_guard<std::mutex> l(mu_);
        proc_ = proc;
    }
    alive_ = true;
    if (stop_) plat::process_kill(proc); // stop() raced us before proc_ was visible

    std::string acc, lastErr, lastLine, found;
    char buf[4096];
    int n;
    while ((n = plat::process_read(proc, buf, sizeof buf)) > 0) {
        logWrite(log, std::string(buf, (size_t)n));
        acc.append(buf, (size_t)n);
        size_t nl;
        while ((nl = acc.find('\n')) != std::string::npos) {
            std::string line = acc.substr(0, nl);
            acc.erase(0, nl + 1);
            while (!line.empty() && (line.back() == '\r' || line.back() == ' ')) line.pop_back();
            if (!line.empty()) {
                size_t z = line.find("Z ");
                lastLine = line.substr(z != std::string::npos && z < 30 ? z + 2 : 0, 200);
            }
            size_t e = line.find(" ERR ");
            if (e != std::string::npos) lastErr = line.substr(e + 5, 160);
            if (found.empty()) {
                size_t h = line.find("https://");
                size_t t = line.find(".trycloudflare.com");
                if (h != std::string::npos && t != std::string::npos && t > h &&
                    line.find("api.trycloudflare.com") == std::string::npos)
                    found = line.substr(h, t + 18 - h);
            }
            if (!found.empty() && !registered && line.find("Registered tunnel connection") != std::string::npos) {
                registered = true;
                if (probeThread_.joinable()) probeThread_.join();
                probeThread_ = std::thread([this, found, log] { waitUntilPublic(found, log); });
            }
        }
    }
    alive_ = false;
    {
        std::lock_guard<std::mutex> l(mu_);
        proc_ = nullptr;
        url_.clear();
    }
    int code = plat::process_finish(proc, 2000);
    logWrite(log, "\n[PocketDrop] cloudflared ended, exit code " + std::to_string(code) +
                      (stop_ ? " (stopped by app)" : "") + "\n");
    if (!lastErr.empty()) return friendlyError(lastErr);
    if (!lastLine.empty()) return friendlyError(lastLine);
    return "cloudflared exited (code " + std::to_string(code) + ")";
}
