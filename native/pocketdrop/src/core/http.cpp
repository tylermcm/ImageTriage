#include "http.h"
#include "webpage.h"
#include <algorithm>
#include <cctype>
#include <condition_variable>
#include <cstdlib>
#include <cstring>
#include <unordered_map>

struct HttpServer::Transfer {
    uint64_t id = 0;
    std::string name, device;
    uint64_t total = 0;
    std::atomic<uint64_t> sent{0};
    std::atomic<bool> done{false};
    bool ok = false, viaTunnel = false, incoming = false;
    uint64_t start = 0, end = 0;
};

struct HttpServer::Session {
    std::string id, device;
    uint64_t lastSeen = 0; // guarded by the server mutex
    int inflight = 0;      // requests in progress
    bool receiveAuthorized = false;
    int authFailures = 0;
};

struct HttpServer::Upload {
    std::string id, sid, device, name, dir, partPath;
    uint64_t size = 0;
    uint64_t received = 0; // guarded by io
    std::atomic<uint64_t> lastSeen{0};
    plat::File file = plat::BAD_FILE;
    std::shared_ptr<Transfer> transfer;
    std::mutex io; // one writer at a time
    bool closed = false;
};

struct Req {
    std::string method, path, query;
    std::map<std::string, std::string> hdr;
    std::string h(const char* k) const {
        auto it = hdr.find(k);
        return it == hdr.end() ? std::string() : it->second;
    }
};

namespace {

const size_t IO_CHUNK = 1 << 20;
const uint64_t UPLOAD_CHUNK = 8ull << 20; // Cloudflare caps request bodies at 100 MB
const uint64_t MAX_CHUNK = 32ull << 20;
const uint64_t MAX_TEXT = 1ull << 20;
const uint64_t DISK_MARGIN = 16ull << 20;
std::string g_pcName;

// Limits concurrent thumbnail renders (std::counting_semaphore isn't available on older macOS).
class Slots {
public:
    explicit Slots(int n) : n_(n) {}
    void acquire() {
        std::unique_lock<std::mutex> l(m_);
        cv_.wait(l, [&] { return n_ > 0; });
        n_--;
    }
    void release() {
        {
            std::lock_guard<std::mutex> l(m_);
            n_++;
        }
        cv_.notify_one();
    }

private:
    std::mutex m_;
    std::condition_variable cv_;
    int n_;
};
Slots g_thumbSlots(3);
std::mutex g_thumbMu;
std::unordered_map<std::string, std::shared_ptr<const std::vector<uint8_t>>> g_thumbs;

std::string trim(const std::string& s) {
    size_t a = s.find_first_not_of(" \t"), b = s.find_last_not_of(" \t");
    return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
}

bool readRequest(plat::Sock s, std::string& buf, Req& r) {
    for (;;) {
        size_t end = buf.find("\r\n\r\n");
        if (end != std::string::npos) {
            std::string head = buf.substr(0, end);
            buf.erase(0, end + 4);
            size_t le = head.find("\r\n");
            std::string first = head.substr(0, le);
            size_t a = first.find(' '), b = first.rfind(' ');
            if (a == std::string::npos || b <= a) return false;
            r = Req{};
            r.method = first.substr(0, a);
            std::string target = first.substr(a + 1, b - a - 1);
            size_t q = target.find('?');
            r.path = target.substr(0, q);
            if (q != std::string::npos) r.query = target.substr(q + 1);
            size_t pos = le == std::string::npos ? head.size() : le + 2;
            while (pos < head.size()) {
                size_t e = head.find("\r\n", pos);
                if (e == std::string::npos) e = head.size();
                std::string line = head.substr(pos, e - pos);
                size_t c = line.find(':');
                if (c != std::string::npos) r.hdr[util::lower(trim(line.substr(0, c)))] = trim(line.substr(c + 1));
                pos = e + 2;
            }
            return true;
        }
        if (buf.size() > 32768) return false;
        char tmp[8192];
        int n = plat::tcp_recv(s, tmp, sizeof tmp);
        if (n <= 0) return false;
        buf.append(tmp, (size_t)n);
    }
}

// Reads exactly `len` body bytes (starting with any already buffered after the headers).
// Returns false only if the connection fails.
bool readBody(plat::Sock s, std::string& buf, uint64_t len, const std::function<void(const char*, size_t)>& sink) {
    if (!buf.empty() && len) {
        size_t n = (size_t)std::min<uint64_t>(len, buf.size());
        sink(buf.data(), n);
        buf.erase(0, n);
        len -= n;
    }
    std::vector<char> tmp(1 << 16);
    while (len) {
        int n = plat::tcp_recv(s, tmp.data(), (int)std::min<uint64_t>(len, tmp.size()));
        if (n <= 0) return false;
        sink(tmp.data(), (size_t)n);
        len -= (uint64_t)n;
    }
    return true;
}

bool drain(plat::Sock s, std::string& buf, uint64_t len) {
    return len <= MAX_CHUNK && readBody(s, buf, len, [](const char*, size_t) {});
}

bool sendAll(plat::Sock s, const char* p, size_t n, std::atomic<uint64_t>* counter = nullptr) {
    while (n) {
        int k = plat::tcp_send(s, p, (int)std::min(n, IO_CHUNK));
        if (k <= 0) return false;
        p += k;
        n -= (size_t)k;
        if (counter) *counter += (uint64_t)k;
    }
    return true;
}

bool sendAll(plat::Sock s, const std::string& str) { return sendAll(s, str.data(), str.size()); }

const char* reason(int code) {
    switch (code) {
    case 200: return "OK";
    case 204: return "No Content";
    case 206: return "Partial Content";
    case 301: return "Moved Permanently";
    case 302: return "Found";
    case 400: return "Bad Request";
    case 404: return "Not Found";
    case 405: return "Method Not Allowed";
    case 409: return "Conflict";
    case 411: return "Length Required";
    case 413: return "Payload Too Large";
    case 416: return "Range Not Satisfiable";
    case 503: return "Service Unavailable";
    case 507: return "Insufficient Storage";
    default: return "Error";
    }
}

std::string header(int code, const std::string& type, uint64_t len, bool keep, const std::string& extra = {}) {
    std::string h = "HTTP/1.1 " + std::to_string(code) + " " + reason(code) + "\r\n";
    if (!type.empty()) h += "Content-Type: " + type + "\r\n";
    h += "Content-Length: " + std::to_string(len) + "\r\n";
    h += keep ? "Connection: keep-alive\r\nKeep-Alive: timeout=20\r\n" : "Connection: close\r\n";
    h += "X-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\nServer: PocketDrop\r\n";
    h += extra;
    h += "\r\n";
    return h;
}

bool simple(plat::Sock s, int code, bool head, bool keep, const std::string& extra = {}) {
    std::string body = code >= 400 ? std::string(reason(code)) + "\n" : std::string();
    std::string h = header(code, body.empty() ? "" : "text/plain; charset=utf-8", body.size(), keep,
                           extra + "Cache-Control: no-store\r\n");
    return sendAll(s, head ? h : h + body) && keep;
}

bool json(plat::Sock s, int code, const std::string& body, bool keep) {
    std::string h = header(code, "application/json; charset=utf-8", body.size(), keep, "Cache-Control: no-store\r\n");
    return sendAll(s, h + body) && keep;
}

// Single "bytes=" range. Returns false when unsatisfiable (-> 416).
bool parseRange(const std::string& v, uint64_t total, uint64_t& start, uint64_t& len, bool& partial) {
    partial = false;
    start = 0;
    len = total;
    if (v.rfind("bytes=", 0) != 0 || v.find(',') != std::string::npos) return true;
    std::string spec = v.substr(6);
    size_t dash = spec.find('-');
    if (dash == std::string::npos) return true;
    std::string a = trim(spec.substr(0, dash)), b = trim(spec.substr(dash + 1));
    if (total == 0) return false;
    uint64_t s, e;
    if (a.empty()) {
        if (b.empty()) return true;
        uint64_t n = std::min<uint64_t>(std::strtoull(b.c_str(), nullptr, 10), total);
        if (n == 0) return false;
        s = total - n;
        e = total - 1;
    } else {
        s = std::strtoull(a.c_str(), nullptr, 10);
        e = b.empty() ? total - 1 : std::min<uint64_t>(std::strtoull(b.c_str(), nullptr, 10), total - 1);
        if (s >= total || e < s) return false;
    }
    start = s;
    len = e - s + 1;
    partial = true;
    return true;
}

std::string urlDecode(const std::string& s) {
    std::string o;
    for (size_t i = 0; i < s.size(); i++) {
        if (s[i] == '+') o.push_back(' ');
        else if (s[i] == '%' && i + 2 < s.size() && isxdigit((unsigned char)s[i + 1]) && isxdigit((unsigned char)s[i + 2])) {
            o.push_back((char)std::strtol(s.substr(i + 1, 2).c_str(), nullptr, 16));
            i += 2;
        } else o.push_back(s[i]);
    }
    return o;
}

std::string queryParam(const std::string& q, const char* key) {
    size_t klen = strlen(key), pos = 0;
    while (pos <= q.size()) {
        size_t amp = q.find('&', pos);
        std::string part = q.substr(pos, amp == std::string::npos ? std::string::npos : amp - pos);
        if (part.size() > klen && part.compare(0, klen, key) == 0 && part[klen] == '=') return urlDecode(part.substr(klen + 1));
        if (amp == std::string::npos) break;
        pos = amp + 1;
    }
    return {};
}

bool isDigits(const std::string& s) {
    return !s.empty() && s.size() <= 19 && s.find_first_not_of("0123456789") == std::string::npos;
}

// A name that is safe to save on any of our platforms.
std::string safeName(std::string n) {
    size_t slash = n.find_last_of("/\\");
    if (slash != std::string::npos) n = n.substr(slash + 1);
    std::string out;
    for (unsigned char c : n) out.push_back(c < 0x20 || c == 0x7F || strchr("<>:\"/\\|?*", c) ? '_' : (char)c);
    while (!out.empty() && (out.back() == '.' || out.back() == ' ')) out.pop_back();
    size_t a = out.find_first_not_of(". ");
    out = a == std::string::npos ? std::string() : out.substr(a);
    if (out.empty()) out = "file";
    std::string stem = util::lower(out.substr(0, out.find('.')));
    static const char* reserved[] = {"con",  "prn",  "aux",  "nul",  "com1", "com2", "com3", "com4",
                                     "com5", "com6", "com7", "com8", "com9", "lpt1", "lpt2", "lpt3",
                                     "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9"};
    for (const char* r : reserved)
        if (stem == r) out = "_" + out;
    if (out.size() > 180) {
        std::string ext;
        size_t dot = out.rfind('.');
        if (dot != std::string::npos && out.size() - dot <= 16) ext = out.substr(dot);
        size_t keep = 180 - ext.size();
        while (keep > 0 && ((unsigned char)out[keep] & 0xC0) == 0x80) keep--;
        out = out.substr(0, keep) + ext;
    }
    return out;
}

std::string uniquePath(const std::string& dir, const std::string& name) {
    std::string path = util::path_join(dir, name);
    if (!plat::file_stat(path).exists) return path;
    std::string base = name, ext;
    size_t dot = name.rfind('.');
    if (dot != std::string::npos && dot > 0) {
        base = name.substr(0, dot);
        ext = name.substr(dot);
    }
    for (int i = 2; i < 100000; i++) {
        path = util::path_join(dir, base + " (" + std::to_string(i) + ")" + ext);
        if (!plat::file_stat(path).exists) break;
    }
    return path;
}

std::string extOf(const std::string& name) {
    size_t p = name.rfind('.');
    return p == std::string::npos ? std::string() : util::lower(name.substr(p + 1));
}

std::string mimeOf(const std::string& name) {
    static const std::unordered_map<std::string, const char*> m = {
        {"jpg", "image/jpeg"}, {"jpeg", "image/jpeg"}, {"png", "image/png"}, {"gif", "image/gif"},
        {"webp", "image/webp"}, {"heic", "image/heic"}, {"heif", "image/heif"}, {"avif", "image/avif"},
        {"bmp", "image/bmp"}, {"svg", "image/svg+xml"}, {"tif", "image/tiff"}, {"tiff", "image/tiff"},
        {"mp4", "video/mp4"}, {"m4v", "video/x-m4v"}, {"mov", "video/quicktime"}, {"webm", "video/webm"},
        {"mkv", "video/x-matroska"}, {"3gp", "video/3gpp"}, {"mp3", "audio/mpeg"}, {"m4a", "audio/mp4"},
        {"aac", "audio/aac"}, {"wav", "audio/wav"}, {"ogg", "audio/ogg"}, {"opus", "audio/ogg"},
        {"flac", "audio/flac"}, {"pdf", "application/pdf"}, {"txt", "text/plain; charset=utf-8"},
        {"md", "text/plain; charset=utf-8"}, {"log", "text/plain; charset=utf-8"},
        {"csv", "text/csv; charset=utf-8"}, {"json", "application/json"}, {"xml", "application/xml"},
        {"html", "text/html; charset=utf-8"}, {"htm", "text/html; charset=utf-8"}, {"zip", "application/zip"},
        {"apk", "application/vnd.android.package-archive"}, {"ics", "text/calendar"}, {"vcf", "text/vcard"},
        {"epub", "application/epub+zip"},
        {"docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
        {"xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
        {"pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"},
    };
    auto it = m.find(extOf(name));
    return it == m.end() ? "application/octet-stream" : it->second;
}

const char* kindOf(const std::string& name) {
    std::string e = extOf(name);
    auto in = [&](std::initializer_list<const char*> l) {
        for (auto x : l)
            if (e == x) return true;
        return false;
    };
    if (in({"jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "avif", "bmp", "tif", "tiff", "svg", "dng", "cr2",
            "nef", "arw"}))
        return "image";
    if (in({"mp4", "m4v", "mov", "webm", "mkv", "avi", "3gp", "wmv", "mpg", "mpeg"})) return "video";
    if (in({"mp3", "m4a", "aac", "wav", "ogg", "opus", "flac", "wma", "aiff"})) return "audio";
    if (e == "pdf") return "pdf";
    if (in({"zip", "7z", "rar", "gz", "tgz", "bz2", "xz", "zst", "tar"})) return "archive";
    if (in({"txt", "md", "csv", "json", "xml", "log", "html", "htm", "css", "js", "ini", "yaml", "yml"}))
        return "text";
    return "file";
}

std::string disposition(const std::string& name, bool attach) {
    std::string ascii;
    for (unsigned char c : name) ascii.push_back(c >= 0x20 && c < 0x7F && c != '"' && c != '\\' ? (char)c : '_');
    return std::string("Content-Disposition: ") + (attach ? "attachment" : "inline") + "; filename=\"" + ascii +
           "\"; filename*=UTF-8''" + util::url_encode(name) + "\r\n";
}

std::string deviceFromUA(const std::string& ua) {
    if (ua.find("iPhone") != std::string::npos) return "iPhone";
    if (ua.find("iPad") != std::string::npos) return "iPad";
    if (ua.find("Android") != std::string::npos) return "Android";
    if (ua.find("Macintosh") != std::string::npos) return "Mac";
    if (ua.find("Windows") != std::string::npos) return "Windows PC";
    if (ua.find("Linux") != std::string::npos) return "Linux";
    return "Browser";
}

bool tokenMatch(const std::string& a, const char* b, size_t blen) {
    if (a.size() != blen) return false;
    unsigned char diff = 0;
    for (size_t i = 0; i < blen; i++) diff |= (unsigned char)(a[i] ^ b[i]);
    return diff == 0;
}

std::string manifest(const Bundle* b, int rev, bool receiveAuth, bool receiveOk) {
    std::string j = "{\"v\":2,\"rev\":" + std::to_string(rev) + ",\"pc\":\"" + util::json_escape(g_pcName) +
                    "\",\"chunk\":" + std::to_string(UPLOAD_CHUNK) +
                    ",\"receiveAuth\":" + (receiveAuth ? "true" : "false") +
                    ",\"receiveOk\":" + (receiveOk ? "true" : "false");
    if (!b) return j + ",\"ready\":true,\"prep\":1,\"total\":0,\"files\":[],\"texts\":[],\"zip\":{\"name\":\"\",\"size\":0,\"preferred\":false}}";
    bool ready = b->ready;
    double prep = ready ? 1.0 : b->totalBytes ? std::min(0.99, (double)b->prepDone / (double)b->totalBytes) : 0.0;
    char num[32];
    snprintf(num, sizeof num, "%.3f", prep);
    j += ",\"ready\":" + std::string(ready ? "true" : "false") + ",\"prep\":" + num +
         ",\"total\":" + std::to_string(b->totalBytes);
    j += ",\"zip\":{\"name\":\"" + util::json_escape(b->zipName) + "\",\"size\":" +
         std::to_string(ready ? b->zipBytes : 0) + ",\"preferred\":" + (b->zipPreferred ? "true" : "false") + "}";
    j += ",\"files\":[";
    for (size_t i = 0; i < b->files.size(); i++) {
        const auto& f = b->files[i];
        if (i) j += ",";
        j += "{\"i\":" + std::to_string(i) + ",\"name\":\"" + util::json_escape(f.name) + "\",\"path\":\"" +
             util::json_escape(f.rel) + "\",\"size\":" + std::to_string(f.size) + ",\"kind\":\"" + kindOf(f.name) +
             "\"" + (ready && f.missing ? ",\"missing\":true" : "") + "}";
    }
    j += "],\"texts\":[";
    for (size_t i = 0; i < b->texts.size(); i++) j += (i ? ",\"" : "\"") + util::json_escape(b->texts[i]) + "\"";
    return j + "]}";
}

bool streamFile(plat::Sock s, plat::File h, uint64_t offset, uint64_t len, std::atomic<uint64_t>* counter,
                std::vector<char>& buf, const std::atomic<bool>& running) {
    if (!plat::file_seek(h, offset)) return false;
    while (len) {
        size_t want = (size_t)std::min<uint64_t>(len, buf.size());
        int64_t got = plat::file_read(h, buf.data(), want);
        if (got <= 0) return false;
        if (!sendAll(s, buf.data(), (size_t)got, counter)) return false;
        len -= (uint64_t)got;
        if (!running) return false;
    }
    return true;
}

} // namespace

// ---------------------------------------------------------------- lifecycle

bool HttpServer::start(int preferredPort, bool loopbackOnly) {
    if (!plat::net_init()) return false;
    g_pcName = plat::host_name();
    {
        std::lock_guard<std::mutex> l(mu_);
        token_ = util::random_token(16);
        tokenIssuedAt_ = plat::tick_ms();
    }
    for (int p : {preferredPort, preferredPort + 1, preferredPort + 2, 0}) {
        listen_ = plat::tcp_listen(p, loopbackOnly, &port_);
        if (listen_ != plat::BAD_SOCK) break;
    }
    if (listen_ == plat::BAD_SOCK) return false;
    running_ = true;
    acceptThread_ = std::thread([this] { acceptLoop(); });
    return true;
}

void HttpServer::stop() {
    if (!running_.exchange(false)) return;
    if (acceptThread_.joinable()) acceptThread_.join();
    plat::tcp_close(listen_);
    listen_ = plat::BAD_SOCK;
    {
        std::lock_guard<std::mutex> l(mu_);
        for (plat::Sock c : clients_) plat::tcp_shutdown(c);
    }
    for (int i = 0; i < 300 && threads_ > 0; i++) plat::sleep_ms(10);
    std::vector<std::shared_ptr<Upload>> ups;
    {
        std::lock_guard<std::mutex> l(mu_);
        for (auto& [id, u] : uploads_) ups.push_back(u);
    }
    for (auto& u : ups) {
        std::lock_guard<std::mutex> io(u->io);
        abortUpload(u);
    }
}

void HttpServer::publish(std::shared_ptr<Bundle> bundle) {
    std::lock_guard<std::mutex> l(mu_);
    bundle_ = std::move(bundle);
    rev_++;
}

void HttpServer::setReceiveCode(const std::string& code) {
    std::lock_guard<std::mutex> l(mu_);
    receiveCode_ = code;
    for (auto& [id, session] : sessions_) {
        session->receiveAuthorized = code.empty();
        session->authFailures = 0;
    }
}

std::string HttpServer::token() const {
    std::lock_guard<std::mutex> l(mu_);
    return token_;
}

void HttpServer::debugSetToken(const std::string& token) {
    std::lock_guard<std::mutex> l(mu_);
    token_ = token;
    tokenIssuedAt_ = plat::tick_ms();
}

void HttpServer::newLink() {
    std::vector<std::shared_ptr<Upload>> ups;
    {
        std::lock_guard<std::mutex> l(mu_);
        token_ = util::random_token(16);
        tokenIssuedAt_ = plat::tick_ms();
        tokenUsedAt_ = 0;
        sessions_.clear();
        for (auto& [id, u] : uploads_) ups.push_back(u);
        rev_++;
    }
    for (auto& u : ups) {
        std::lock_guard<std::mutex> io(u->io);
        abortUpload(u);
    }
}

void HttpServer::setPolicy(const LinkPolicy& p) {
    std::lock_guard<std::mutex> l(mu_);
    policy_ = p;
}

// Timestamps are written by request threads, so one can be newer than a clock read taken
// elsewhere; never let that wrap around into a huge "elapsed" time.
static uint64_t since(uint64_t now, uint64_t t) { return now > t ? now - t : 0; }

bool HttpServer::maintain(bool sharing) {
    std::vector<std::shared_ptr<Upload>> stale;
    bool changed = false;
    {
        std::lock_guard<std::mutex> l(mu_);
        uint64_t now = plat::tick_ms();
        uint64_t lastActivity = tokenUsedAt_;
        bool active = false;
        for (auto it = sessions_.begin(); it != sessions_.end();) {
            const Session& s = *it->second;
            if (s.inflight == 0 && since(now, s.lastSeen) >= policy_.idleExpireMs) {
                it = sessions_.erase(it);
                continue;
            }
            if (s.inflight > 0 || since(now, s.lastSeen) < policy_.activeWindowMs) active = true;
            lastActivity = std::max(lastActivity, s.inflight > 0 ? now : s.lastSeen);
            ++it;
        }
        for (auto& [id, u] : uploads_)
            if (since(now, u->lastSeen) >= policy_.idleExpireMs) stale.push_back(u);
        uint64_t idleSince = std::max(tokenIssuedAt_, lastActivity);
        bool rotate = !active && (sharing ? since(now, idleSince) >= policy_.idleExpireMs
                                          : since(now, tokenIssuedAt_) >= policy_.rotateMs);
        if (rotate) {
            token_ = util::random_token(16);
            tokenIssuedAt_ = now;
            tokenUsedAt_ = 0;
            changed = true;
        }
    }
    for (auto& u : stale) {
        std::unique_lock<std::mutex> io(u->io, std::try_to_lock);
        if (io.owns_lock()) abortUpload(u);
    }
    return changed;
}

bool HttpServer::phoneConnected() const {
    std::lock_guard<std::mutex> l(mu_);
    uint64_t now = plat::tick_ms();
    for (const auto& [id, s] : sessions_)
        if (s->inflight > 0 || since(now, s->lastSeen) < policy_.activeWindowMs) return true;
    return false;
}

void HttpServer::setInbox(const std::string& dir) {
    bool idle;
    {
        std::lock_guard<std::mutex> l(mu_);
        inbox_ = dir;
        idle = uploads_.empty();
    }
    if (!idle) return;
    // Leftovers from an upload that was interrupted when PocketDrop closed.
    for (const auto& e : plat::list_dir(dir)) {
        const std::string suffix = ".pdpart";
        if (!e.st.isDir && e.name.size() > suffix.size() &&
            e.name.compare(e.name.size() - suffix.size(), suffix.size(), suffix) == 0)
            plat::file_remove(util::path_join(dir, e.name));
    }
}

std::vector<ReceivedItem> HttpServer::takeReceived() {
    std::lock_guard<std::mutex> l(mu_);
    std::vector<ReceivedItem> out;
    out.swap(received_);
    return out;
}

std::vector<TransferInfo> HttpServer::transfers() const {
    std::lock_guard<std::mutex> l(mu_);
    std::vector<TransferInfo> v;
    for (const auto& t : transfers_) {
        TransferInfo i;
        i.id = t->id;
        i.name = t->name;
        i.device = t->device;
        i.total = t->total;
        i.sent = t->sent;
        i.done = t->done;
        i.ok = t->ok;
        i.viaTunnel = t->viaTunnel;
        i.incoming = t->incoming;
        i.start = t->start;
        i.end = t->end;
        v.push_back(std::move(i));
    }
    return v;
}

std::string HttpServer::lastDevice() const {
    std::lock_guard<std::mutex> l(mu_);
    return lastDevice_;
}

std::shared_ptr<HttpServer::Transfer> HttpServer::begin(const std::string& name, const std::string& device,
                                                        uint64_t total, bool tunnel, bool incoming) {
    auto t = std::make_shared<Transfer>();
    t->name = name;
    t->device = device;
    t->total = total;
    t->viaTunnel = tunnel;
    t->incoming = incoming;
    t->start = plat::tick_ms();
    {
        std::lock_guard<std::mutex> l(mu_);
        t->id = nextId_++;
        transfers_.push_back(t);
        if (transfers_.size() > 24) {
            auto it = std::find_if(transfers_.begin(), transfers_.end(), [](auto& x) { return x->done.load(); });
            if (it != transfers_.end()) transfers_.erase(it);
        }
    }
    if (onEvent) onEvent(Event::TransferStart);
    return t;
}

void HttpServer::end(const std::shared_ptr<Transfer>& t, bool ok) {
    {
        std::lock_guard<std::mutex> l(mu_);
        if (t->done) return;
        t->ok = ok;
        t->end = plat::tick_ms();
        t->done = true;
    }
    if (onEvent) onEvent(Event::TransferEnd);
}

std::shared_ptr<HttpServer::Upload> HttpServer::findUpload(const std::string& id, const std::string& sid) {
    std::lock_guard<std::mutex> l(mu_);
    auto it = uploads_.find(id);
    return it != uploads_.end() && it->second->sid == sid ? it->second : nullptr;
}

void HttpServer::abortUpload(const std::shared_ptr<Upload>& u) {
    if (u->closed) return;
    u->closed = true;
    plat::file_close(u->file);
    u->file = plat::BAD_FILE;
    plat::file_remove(u->partPath);
    {
        std::lock_guard<std::mutex> l(mu_);
        uploads_.erase(u->id);
    }
    end(u->transfer, false);
}

std::string HttpServer::finishUpload(const std::shared_ptr<Upload>& u) {
    u->closed = true;
    plat::file_close(u->file);
    u->file = plat::BAD_FILE;
    std::string saved;
    {
        std::lock_guard<std::mutex> l(mu_);
        uploads_.erase(u->id);
        std::string finalPath = uniquePath(u->dir, u->name);
        if (plat::file_rename(u->partPath, finalPath)) {
            saved = util::path_leaf(finalPath);
            ReceivedItem item;
            item.id = nextReceivedId_++;
            item.name = saved;
            item.path = finalPath;
            item.size = u->size;
            item.device = u->device;
            item.time = plat::unix_time();
            received_.push_back(std::move(item));
        }
    }
    if (saved.empty()) {
        plat::file_remove(u->partPath);
        end(u->transfer, false);
        return {};
    }
    end(u->transfer, true);
    if (onEvent) onEvent(Event::Received);
    return saved;
}

void HttpServer::acceptLoop() {
    while (running_) {
        plat::Sock c = plat::tcp_accept(listen_, 250);
        if (c == plat::BAD_SOCK) continue;
        if (!running_) {
            plat::tcp_close(c);
            break;
        }
        {
            std::lock_guard<std::mutex> l(mu_);
            clients_.insert(c);
        }
        threads_++;
        std::thread([this, c] {
            serve(c);
            threads_--;
        }).detach();
    }
}

void HttpServer::serve(plat::Sock s) {
    plat::tcp_configure(s, 25000);
    bool viaTunnel = plat::tcp_peer_is_loopback(s);
    std::string buf;
    Req r;
    while (running_ && readRequest(s, buf, r)) {
        if (!handle(s, r, buf, viaTunnel)) break;
    }
    {
        std::lock_guard<std::mutex> l(mu_);
        clients_.erase(s);
    }
    plat::tcp_close(s);
}

// ---------------------------------------------------------------- routing

bool HttpServer::handle(plat::Sock s, const Req& r, std::string& buf, bool viaTunnel) {
    bool head = r.method == "HEAD";
    bool keep = util::lower(r.h("connection")) != "close";
    if (!r.h("transfer-encoding").empty()) return simple(s, 411, false, false);
    uint64_t bodyLen = std::strtoull(r.h("content-length").c_str(), nullptr, 10);
    if (bodyLen && util::lower(r.h("expect")) == "100-continue" && !sendAll(s, "HTTP/1.1 100 Continue\r\n\r\n"))
        return false;

    if (r.path == "/ping") {
        if (!drain(s, buf, bodyLen)) return false;
        return simple(s, 204, head, keep, "Access-Control-Allow-Origin: *\r\n");
    }

    // Phone session: /s/<id>/...
    if (r.path.rfind("/s/", 0) == 0) {
        size_t slash = r.path.find('/', 3);
        std::string sid = r.path.substr(3, slash == std::string::npos ? std::string::npos : slash - 3);
        std::shared_ptr<Session> session;
        {
            std::lock_guard<std::mutex> l(mu_);
            auto it = sessions_.find(sid);
            if (it != sessions_.end()) {
                session = it->second;
                session->inflight++;
                session->lastSeen = plat::tick_ms();
            }
        }
        if (!session) {
            if (!drain(s, buf, bodyLen)) return false;
            return simple(s, 404, head, keep);
        }
        bool result;
        if (slash == std::string::npos) {
            result = drain(s, buf, bodyLen) && simple(s, 301, head, keep, "Location: /s/" + sid + "/\r\n");
        } else {
            result = handleSession(s, r, buf, viaTunnel, *session, r.path.substr(slash), bodyLen);
        }
        std::lock_guard<std::mutex> l(mu_);
        session->inflight--;
        session->lastSeen = plat::tick_ms();
        return result;
    }

    if (r.method != "GET" && !head) return simple(s, 405, false, false);
    if (!drain(s, buf, bodyLen)) return false;

    // QR link: /<token> or /<token>/ opens a new session.
    std::string p = r.path;
    if (p.size() > 1 && p.back() == '/') p.pop_back();
    std::string token = this->token();
    if (p.size() == token.size() + 1 && tokenMatch(token, p.data() + 1, token.size())) {
        auto session = std::make_shared<Session>();
        session->id = util::random_token(16);
        session->device = deviceFromUA(r.h("user-agent"));
        session->lastSeen = plat::tick_ms();
        {
            std::lock_guard<std::mutex> l(mu_);
            sessions_[session->id] = session;
            tokenUsedAt_ = session->lastSeen;
            lastDevice_ = session->device;
        }
        if (onEvent) onEvent(Event::Visit);
        return simple(s, 302, head, keep, "Location: /s/" + session->id + "/\r\n");
    }
    return simple(s, 404, head, keep);
}

bool HttpServer::handleSession(plat::Sock s, const Req& r, std::string& buf, bool viaTunnel, Session& session,
                               const std::string& rest, uint64_t bodyLen) {
    bool head = r.method == "HEAD";
    bool keep = util::lower(r.h("connection")) != "close";
    const std::string& device = session.device;
    std::shared_ptr<Bundle> b;
    int rev;
    {
        std::lock_guard<std::mutex> l(mu_);
        b = bundle_;
        rev = rev_;
    }

    if (r.method == "POST" && rest == "/auth") {
        if (bodyLen > 32) return json(s, 413, "{\"error\":\"code\"}", false);
        std::string code;
        if (!readBody(s, buf, bodyLen, [&](const char* p, size_t n) { code.append(p, n); })) return false;
        code.erase(std::remove_if(code.begin(), code.end(), [](unsigned char c) { return c == '-' || std::isspace(c); }),
                   code.end());
        std::transform(code.begin(), code.end(), code.begin(), [](unsigned char c) { return (char)std::toupper(c); });
        int status = 401;
        {
            std::lock_guard<std::mutex> l(mu_);
            if (receiveCode_.empty()) {
                session.receiveAuthorized = true;
                session.authFailures = 0;
                status = 204;
            } else if (session.authFailures >= 8) {
                status = 429;
            } else if (tokenMatch(receiveCode_, code.data(), code.size())) {
                session.receiveAuthorized = true;
                session.authFailures = 0;
                status = 204;
            } else if (++session.authFailures >= 8) {
                status = 429;
            }
        }
        return status == 204 ? simple(s, 204, false, keep)
                             : json(s, status, status == 429 ? "{\"error\":\"locked\"}" : "{\"error\":\"code\"}", keep);
    }

    bool receiveRoute = (r.method == "POST" && (rest == "/up" || rest == "/text")) ||
                        (rest.rfind("/up/", 0) == 0 && (r.method == "PUT" || r.method == "GET" || r.method == "DELETE"));
    if (receiveRoute) {
        bool allowed;
        {
            std::lock_guard<std::mutex> l(mu_);
            allowed = receiveCode_.empty() || session.receiveAuthorized;
        }
        if (!allowed) {
            if (!drain(s, buf, bodyLen)) return false;
            return json(s, 401, "{\"error\":\"auth\"}", keep);
        }
    }

    // ---- phone -> computer
    if (r.method == "POST" && rest == "/up") {
        if (!drain(s, buf, bodyLen)) return false;
        std::string sizeText = queryParam(r.query, "size");
        if (!isDigits(sizeText)) return json(s, 400, "{\"error\":\"size\"}", keep);
        uint64_t size = std::strtoull(sizeText.c_str(), nullptr, 10);
        std::string dir;
        {
            std::lock_guard<std::mutex> l(mu_);
            dir = inbox_;
        }
        if (dir.empty()) return json(s, 500, "{\"error\":\"save\"}", keep);
        plat::make_dir(dir);
        if (debugDiskFull_ || plat::free_disk_space(dir) < size + DISK_MARGIN) return json(s, 507, "{\"error\":\"disk\"}", keep);

        auto u = std::make_shared<Upload>();
        u->id = util::random_token(12);
        u->sid = session.id;
        u->device = device;
        u->name = safeName(queryParam(r.query, "name"));
        u->dir = dir;
        u->size = size;
        u->lastSeen = plat::tick_ms();
        u->partPath = util::path_join(dir, u->name + "." + u->id + ".pdpart");
        u->file = plat::file_create(u->partPath);
        if (u->file == plat::BAD_FILE) return json(s, 500, "{\"error\":\"save\"}", keep);
        u->transfer = begin(u->name, device, size, viaTunnel, true);
        {
            std::lock_guard<std::mutex> l(mu_);
            uploads_[u->id] = u;
        }
        if (size == 0) {
            std::lock_guard<std::mutex> io(u->io);
            std::string saved = finishUpload(u);
            if (saved.empty()) return json(s, 500, "{\"error\":\"save\"}", keep);
            return json(s, 200, "{\"id\":\"" + u->id + "\",\"received\":0,\"done\":true,\"name\":\"" + util::json_escape(saved) + "\"}", keep);
        }
        return json(s, 200, "{\"id\":\"" + u->id + "\",\"received\":0,\"chunk\":" + std::to_string(UPLOAD_CHUNK) + "}", keep);
    }

    if (rest.rfind("/up/", 0) == 0 && (r.method == "PUT" || r.method == "GET" || r.method == "DELETE")) {
        std::shared_ptr<Upload> u = findUpload(rest.substr(4), session.id);
        if (r.method == "PUT" && bodyLen > MAX_CHUNK) return json(s, 413, "{\"error\":\"chunk\"}", false);
        if (!u) {
            if (!drain(s, buf, bodyLen)) return false;
            return json(s, 404, "{\"error\":\"gone\"}", keep);
        }
        std::unique_lock<std::mutex> io(u->io);
        if (u->closed) {
            io.unlock();
            if (!drain(s, buf, bodyLen)) return false;
            return json(s, 404, "{\"error\":\"gone\"}", keep);
        }
        u->lastSeen = plat::tick_ms();
        if (r.method == "GET") return json(s, 200, "{\"received\":" + std::to_string(u->received) + "}", keep);
        if (r.method == "DELETE") {
            abortUpload(u);
            io.unlock();
            if (!drain(s, buf, bodyLen)) return false;
            return simple(s, 204, false, keep);
        }

        std::string offText = queryParam(r.query, "offset");
        uint64_t offset = std::strtoull(offText.c_str(), nullptr, 10);
        if (!isDigits(offText) || offset > u->received || offset + bodyLen > u->size) {
            int code = isDigits(offText) && offset > u->received ? 409 : 400;
            std::string reply = "{\"received\":" + std::to_string(u->received) + "}";
            io.unlock();
            if (!drain(s, buf, bodyLen)) return false;
            return json(s, code, reply, keep);
        }
        bool writeErr = debugDiskFull_ || !plat::file_seek(u->file, offset);
        uint64_t pos = offset;
        bool alive = readBody(s, buf, bodyLen, [&](const char* p, size_t n) {
            if (writeErr) return;
            if (!plat::file_write(u->file, p, n)) {
                writeErr = true;
                return;
            }
            pos += n;
            if (pos > u->transfer->sent) u->transfer->sent = pos;
            u->lastSeen = plat::tick_ms();
        });
        u->lastSeen = plat::tick_ms();
        if (!alive) return false; // phone will retry from the last confirmed offset
        if (writeErr) {
            bool full = debugDiskFull_ || plat::free_disk_space(u->dir) < bodyLen + DISK_MARGIN;
            abortUpload(u);
            return json(s, full ? 507 : 500, full ? "{\"error\":\"disk\"}" : "{\"error\":\"save\"}", keep);
        }
        u->received = std::max(u->received, offset + bodyLen);
        if (u->received < u->size) return json(s, 200, "{\"received\":" + std::to_string(u->received) + "}", keep);
        std::string saved = finishUpload(u);
        if (saved.empty()) return json(s, 500, "{\"error\":\"save\"}", keep);
        return json(s, 200, "{\"received\":" + std::to_string(u->size) + ",\"done\":true,\"name\":\"" + util::json_escape(saved) + "\"}", keep);
    }

    if (r.method == "POST" && rest == "/text") {
        if (bodyLen > MAX_TEXT) return json(s, 413, "{\"error\":\"too_long\"}", false);
        std::string text;
        if (!readBody(s, buf, bodyLen, [&](const char* p, size_t n) { text.append(p, n); })) return false;
        if (text.find_first_not_of(" \t\r\n") == std::string::npos) return json(s, 400, "{\"error\":\"empty\"}", keep);
        {
            std::lock_guard<std::mutex> l(mu_);
            ReceivedItem item;
            item.id = nextReceivedId_++;
            item.isText = true;
            item.text = std::move(text);
            item.size = item.text.size();
            item.device = device;
            item.time = plat::unix_time();
            received_.push_back(std::move(item));
        }
        if (onEvent) onEvent(Event::Received);
        return simple(s, 204, false, keep);
    }

    if (r.method != "GET" && !head) {
        if (!drain(s, buf, bodyLen)) return false;
        return simple(s, 405, false, keep);
    }
    if (!drain(s, buf, bodyLen)) return false;

    // ---- computer -> phone
    if (rest == "/") {
        const std::string& page = web_page();
        std::string h = header(200, "text/html; charset=utf-8", page.size(), keep,
                               "Cache-Control: no-store\r\nContent-Security-Policy: default-src 'none'; img-src 'self' "
                               "data: blob:; media-src 'self' blob:; style-src 'unsafe-inline'; script-src "
                               "'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'\r\n");
        return sendAll(s, head ? h : h + page) && keep;
    }

    if (rest == "/api/manifest") {
        bool receiveAuth, receiveOk;
        {
            std::lock_guard<std::mutex> l(mu_);
            receiveAuth = !receiveCode_.empty();
            receiveOk = !receiveAuth || session.receiveAuthorized;
        }
        std::string j = manifest(b.get(), rev, receiveAuth, receiveOk);
        std::string h = header(200, "application/json; charset=utf-8", j.size(), keep, "Cache-Control: no-store\r\n");
        return sendAll(s, head ? h : h + j) && keep;
    }

    auto indexAfter = [&](size_t prefix) -> long long {
        size_t e = rest.find('/', prefix);
        std::string n = rest.substr(prefix, e == std::string::npos ? std::string::npos : e - prefix);
        if (n.empty() || n.size() > 9 || n.find_first_not_of("0123456789") != std::string::npos) return -1;
        long long i = atoll(n.c_str());
        return b && i < (long long)b->files.size() ? i : -1;
    };

    if (rest.rfind("/f/", 0) == 0) {
        long long i = indexAfter(3);
        if (i < 0) return simple(s, 404, head, keep);
        const BundleFile& f = b->files[(size_t)i];
        plat::File h = plat::file_open_read(f.path);
        if (h == plat::BAD_FILE) return simple(s, 404, head, keep);
        int64_t sz = plat::file_size(h);
        uint64_t total = sz > 0 ? (uint64_t)sz : 0, start, len;
        bool partial, download = r.query.find("dl=1") != std::string::npos;
        if (!parseRange(r.h("range"), total, start, len, partial)) {
            plat::file_close(h);
            return simple(s, 416, head, keep, "Content-Range: bytes */" + std::to_string(total) + "\r\n");
        }
        std::string mime = mimeOf(f.name);
        std::string extra = "Accept-Ranges: bytes\r\nCache-Control: private, no-cache\r\n" + disposition(f.name, download);
        if (partial)
            extra += "Content-Range: bytes " + std::to_string(start) + "-" + std::to_string(start + len - 1) + "/" +
                     std::to_string(total) + "\r\n";
        if (!download && (mime.find("html") != std::string::npos || mime.find("svg") != std::string::npos ||
                          mime.find("xml") != std::string::npos))
            extra += "Content-Security-Policy: sandbox\r\n";
        bool ok = sendAll(s, header(partial ? 206 : 200, mime, len, keep, extra));
        if (ok && !head && len) {
            std::shared_ptr<Transfer> t;
            if (download && len >= (256 << 10)) t = begin(f.name, device, len, viaTunnel, false);
            std::vector<char> io(IO_CHUNK);
            ok = streamFile(s, h, start, len, t ? &t->sent : nullptr, io, running_);
            if (t) end(t, ok);
        }
        plat::file_close(h);
        return ok && keep;
    }

    if (rest.rfind("/zip/", 0) == 0) {
        if (!b || b->files.empty()) return simple(s, 404, head, keep);
        if (!b->ready) return simple(s, 503, head, keep, "Retry-After: 2\r\n");
        const ZipPlan& p = b->plan();
        uint64_t start, len;
        bool partial;
        if (!parseRange(r.h("range"), p.total, start, len, partial))
            return simple(s, 416, head, keep, "Content-Range: bytes */" + std::to_string(p.total) + "\r\n");
        std::string extra = "Accept-Ranges: bytes\r\nCache-Control: private, no-cache\r\n" + disposition(b->zipName, true);
        if (partial)
            extra += "Content-Range: bytes " + std::to_string(start) + "-" + std::to_string(start + len - 1) + "/" +
                     std::to_string(p.total) + "\r\n";
        if (!sendAll(s, header(partial ? 206 : 200, "application/zip", len, keep, extra))) return false;
        if (head || !len) return keep;

        auto t = begin(b->zipName, device, len, viaTunnel, false);
        std::vector<char> io(IO_CHUNK);
        bool ok = true;
        uint64_t pos = 0, remaining = len;
        for (const ZipSegment& seg : p.segs) {
            if (!remaining) break;
            uint64_t segEnd = pos + seg.len;
            if (segEnd <= start) {
                pos = segEnd;
                continue;
            }
            uint64_t off = start > pos ? start - pos : 0;
            uint64_t n = std::min(seg.len - off, remaining);
            if (seg.mem) {
                ok = sendAll(s, (const char*)seg.mem + off, (size_t)n, &t->sent);
            } else {
                const BundleFile& f = b->files[(size_t)seg.file];
                plat::File h = plat::file_open_read(f.path);
                ok = h != plat::BAD_FILE && plat::file_size(h) == (int64_t)f.size &&
                     streamFile(s, h, off, n, &t->sent, io, running_);
                if (h != plat::BAD_FILE) plat::file_close(h);
            }
            if (!ok) break;
            remaining -= n;
            pos = segEnd;
        }
        end(t, ok);
        return ok && keep;
    }

    if (rest.rfind("/t/", 0) == 0) {
        long long i = indexAfter(3);
        if (i < 0) return simple(s, 404, head, keep);
        const BundleFile& f = b->files[(size_t)i];
        std::string kind = kindOf(f.name);
        if (kind != "image" && kind != "video" && kind != "pdf") return simple(s, 404, head, keep);
        std::string key = f.path + "|" + std::to_string(f.size) + "|" + std::to_string(f.dosDate) + "|" +
                          std::to_string(f.dosTime);
        std::shared_ptr<const std::vector<uint8_t>> jpg;
        {
            std::lock_guard<std::mutex> l(g_thumbMu);
            auto it = g_thumbs.find(key);
            if (it != g_thumbs.end()) jpg = it->second;
        }
        if (!jpg) {
            g_thumbSlots.acquire();
            auto data = plat::thumbnail_jpeg(f.path, 256);
            g_thumbSlots.release();
            jpg = std::make_shared<const std::vector<uint8_t>>(std::move(data));
            std::lock_guard<std::mutex> l(g_thumbMu);
            if (g_thumbs.size() > 2000) g_thumbs.clear();
            g_thumbs[key] = jpg;
        }
        if (jpg->empty()) return simple(s, 404, head, keep);
        std::string h = header(200, "image/jpeg", jpg->size(), keep, "Cache-Control: private, max-age=3600\r\n");
        if (!sendAll(s, h)) return false;
        return (head || sendAll(s, (const char*)jpg->data(), jpg->size())) && keep;
    }

    return simple(s, 404, head, keep);
}
