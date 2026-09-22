// Linux implementation of core/platform.h.
#include "../core/platform.h"
#include <arpa/inet.h>
#include <curl/curl.h>
#include <dirent.h>
#include <fcntl.h>
#include <gdk-pixbuf/gdk-pixbuf.h>
#include <ifaddrs.h>
#include <linux/limits.h>
#include <net/if.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <signal.h>
#include <sys/prctl.h>
#include <sys/random.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/wait.h>
#include <unistd.h>
#include <algorithm>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <mutex>

namespace {

struct CurlCtx {
    plat::File out;
    std::string* body;
    const std::function<bool(uint64_t, uint64_t)>* progress;
    bool writeErr = false;
};

size_t curlWrite(char* p, size_t sz, size_t n, void* ud) {
    auto* c = (CurlCtx*)ud;
    size_t len = sz * n;
    if (c->out != plat::BAD_FILE) {
        if (!plat::file_write(c->out, p, len)) {
            c->writeErr = true;
            return 0;
        }
    } else if (c->body->size() < 65536) {
        c->body->append(p, std::min<size_t>(len, 65536 - c->body->size()));
    }
    return len;
}

int curlProgress(void* ud, curl_off_t total, curl_off_t now, curl_off_t, curl_off_t) {
    auto* c = (CurlCtx*)ud;
    return c->progress && *c->progress && !(*c->progress)((uint64_t)now, (uint64_t)total) ? 1 : 0;
}

bool executableLooksValid(const std::string& path) {
    int fd = open(path.c_str(), O_RDONLY | O_CLOEXEC);
    if (fd < 0) return false;
    unsigned char magic[4]{};
    ssize_t n = read(fd, magic, sizeof magic);
    struct stat st {};
    bool ok = n == 4 && fstat(fd, &st) == 0 && st.st_size > (1 << 20) && magic[0] == 0x7f && magic[1] == 'E' &&
              magic[2] == 'L' && magic[3] == 'F';
    close(fd);
    return ok;
}

} // namespace

namespace plat {

const char PATH_SEP = '/';

// ---- sockets
bool net_init() {
    static std::once_flag once;
    std::call_once(once, [] { signal(SIGPIPE, SIG_IGN); });
    return true;
}

Sock tcp_listen(int port, bool loopbackOnly, int* boundPort) {
    int s = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (s < 0) return BAD_SOCK;
    int one = 1;
    setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    sockaddr_in a{};
    a.sin_family = AF_INET;
    a.sin_addr.s_addr = htonl(loopbackOnly ? INADDR_LOOPBACK : INADDR_ANY);
    a.sin_port = htons((uint16_t)port);
    if (bind(s, (sockaddr*)&a, sizeof a) != 0 || listen(s, SOMAXCONN) != 0) {
        close(s);
        return BAD_SOCK;
    }
    socklen_t len = sizeof a;
    getsockname(s, (sockaddr*)&a, &len);
    if (boundPort) *boundPort = ntohs(a.sin_port);
    return s;
}

Sock tcp_accept(Sock s, int timeoutMs) {
    pollfd p{(int)s, POLLIN, 0};
    if (poll(&p, 1, timeoutMs) <= 0) return BAD_SOCK;
    int c = accept4((int)s, nullptr, nullptr, SOCK_CLOEXEC);
    return c < 0 ? BAD_SOCK : c;
}

void tcp_configure(Sock s, int recvTimeoutMs) {
    timeval tv{recvTimeoutMs / 1000, (recvTimeoutMs % 1000) * 1000};
    setsockopt((int)s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    int one = 1;
    setsockopt((int)s, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
}

int tcp_recv(Sock s, char* buf, int len) {
    ssize_t n;
    do n = recv((int)s, buf, (size_t)len, 0);
    while (n < 0 && errno == EINTR);
    return (int)n;
}

int tcp_send(Sock s, const char* buf, int len) {
    ssize_t n;
    do n = send((int)s, buf, (size_t)len, MSG_NOSIGNAL);
    while (n < 0 && errno == EINTR);
    return (int)n;
}

bool tcp_peer_is_loopback(Sock s) {
    sockaddr_in peer{};
    socklen_t len = sizeof peer;
    return getpeername((int)s, (sockaddr*)&peer, &len) == 0 && ntohl(peer.sin_addr.s_addr) == INADDR_LOOPBACK;
}

void tcp_shutdown(Sock s) { shutdown((int)s, SHUT_RDWR); }
void tcp_close(Sock s) {
    if (s != BAD_SOCK) close((int)s);
}

// ---- files
FileStat file_stat(const std::string& path) {
    FileStat st;
    struct stat sb {};
    if (lstat(path.c_str(), &sb) != 0) return st;
    st.exists = true;
    st.isLink = S_ISLNK(sb.st_mode);
    if (st.isLink && stat(path.c_str(), &sb) != 0) return st;
    st.isDir = S_ISDIR(sb.st_mode);
    st.size = (uint64_t)sb.st_size;
    st.mtime = (int64_t)sb.st_mtim.tv_sec;
    return st;
}

std::vector<DirEntry> list_dir(const std::string& dir) {
    std::vector<DirEntry> out;
    DIR* d = opendir(dir.c_str());
    if (!d) return out;
    while (dirent* e = readdir(d)) {
        std::string name = e->d_name;
        if (name == "." || name == "..") continue;
        DirEntry item{name, file_stat(dir + "/" + name)};
        if (item.st.exists) out.push_back(std::move(item));
    }
    closedir(d);
    return out;
}

File file_open_read(const std::string& path) {
    int fd = open(path.c_str(), O_RDONLY | O_CLOEXEC);
    if (fd < 0) return BAD_FILE;
    struct stat sb {};
    if (fstat(fd, &sb) != 0 || S_ISDIR(sb.st_mode)) {
        close(fd);
        return BAD_FILE;
    }
    return fd;
}

File file_create(const std::string& path) {
    int fd = open(path.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    return fd < 0 ? BAD_FILE : fd;
}

int64_t file_read(File f, void* buf, size_t len) {
    ssize_t n;
    do n = read((int)f, buf, std::min<size_t>(len, 1u << 30));
    while (n < 0 && errno == EINTR);
    return n;
}

bool file_write(File f, const void* buf, size_t len) {
    auto* p = (const char*)buf;
    while (len) {
        ssize_t n = write((int)f, p, std::min<size_t>(len, 1u << 30));
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return false;
        p += n;
        len -= (size_t)n;
    }
    return true;
}

bool file_seek(File f, uint64_t offset) { return lseek((int)f, (off_t)offset, SEEK_SET) >= 0; }
int64_t file_size(File f) {
    struct stat sb {};
    return fstat((int)f, &sb) == 0 ? (int64_t)sb.st_size : -1;
}
void file_close(File f) {
    if (f != BAD_FILE) close((int)f);
}
bool file_rename(const std::string& from, const std::string& to) { return rename(from.c_str(), to.c_str()) == 0; }
void file_remove(const std::string& path) { unlink(path.c_str()); }
void make_dir(const std::string& path) {
    std::error_code ec;
    std::filesystem::create_directories(path, ec);
}

std::string app_data_dir() {
    const char* xdg = getenv("XDG_DATA_HOME");
    const char* home = getenv("HOME");
    std::string base = xdg && *xdg ? xdg : home && *home ? std::string(home) + "/.local/share" : ".";
    std::string dir = base + "/PocketDrop";
    make_dir(dir);
    return dir;
}

std::string downloads_dir() {
    const char* homeEnv = getenv("HOME");
    std::string home = homeEnv && *homeEnv ? homeEnv : ".";
    const char* cfgEnv = getenv("XDG_CONFIG_HOME");
    std::string cfg = cfgEnv && *cfgEnv ? cfgEnv : home + "/.config";
    if (FILE* f = fopen((cfg + "/user-dirs.dirs").c_str(), "r")) {
        char line[1024];
        while (fgets(line, sizeof line, f)) {
            std::string s = line;
            if (s.rfind("XDG_DOWNLOAD_DIR=", 0) != 0) continue;
            size_t a = s.find('"'), b = s.rfind('"');
            if (a == std::string::npos || b <= a) break;
            std::string v = s.substr(a + 1, b - a - 1);
            if (v.rfind("$HOME", 0) == 0) v = home + v.substr(5);
            fclose(f);
            return v;
        }
        fclose(f);
    }
    return home + "/Downloads";
}

uint64_t free_disk_space(const std::string& path) {
    struct statvfs v {};
    return statvfs(path.c_str(), &v) == 0 ? (uint64_t)v.f_bavail * (uint64_t)v.f_frsize : UINT64_MAX;
}

std::string exe_dir() {
    char path[PATH_MAX + 1];
    ssize_t n = readlink("/proc/self/exe", path, PATH_MAX);
    if (n <= 0) return ".";
    path[n] = 0;
    std::string s = path;
    size_t slash = s.find_last_of('/');
    return slash == std::string::npos ? "." : s.substr(0, slash);
}

// ---- time and misc
int64_t unix_time() { return (int64_t)std::time(nullptr); }
uint64_t tick_ms() {
    timespec ts {};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000 + (uint64_t)ts.tv_nsec / 1000000;
}
void sleep_ms(int ms) { usleep((useconds_t)ms * 1000); }

void dos_datetime(int64_t unixTime, uint16_t& time, uint16_t& date) {
    time_t t = (time_t)unixTime;
    std::tm tm {};
    if (!localtime_r(&t, &tm) || tm.tm_year < 80) {
        time = 0;
        date = 0x21;
        return;
    }
    date = (uint16_t)(((tm.tm_year - 80) << 9) | ((tm.tm_mon + 1) << 5) | tm.tm_mday);
    time = (uint16_t)((tm.tm_hour << 11) | (tm.tm_min << 5) | (tm.tm_sec / 2));
}

void random_bytes(uint8_t* out, size_t len) {
    size_t done = 0;
    while (done < len) {
        ssize_t n = getrandom(out + done, len - done, 0);
        if (n > 0) done += (size_t)n;
        else if (errno != EINTR) break;
    }
    if (done == len) return;
    int fd = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
    while (fd >= 0 && done < len) {
        ssize_t n = read(fd, out + done, len - done);
        if (n > 0) done += (size_t)n;
        else if (errno != EINTR) break;
    }
    if (fd >= 0) close(fd);
}

std::string host_name() {
    char name[256]{};
    return gethostname(name, sizeof name) == 0 ? std::string(name) : "Linux PC";
}

// ---- network interfaces
std::vector<LanAddr> lan_addresses() {
    struct Candidate {
        LanAddr addr;
        int score;
    };
    std::vector<Candidate> candidates;
    ifaddrs* list = nullptr;
    if (getifaddrs(&list) != 0) return {};
    for (ifaddrs* i = list; i; i = i->ifa_next) {
        if (!i->ifa_addr || i->ifa_addr->sa_family != AF_INET) continue;
        if ((i->ifa_flags & IFF_LOOPBACK) || !(i->ifa_flags & IFF_UP) || !(i->ifa_flags & IFF_RUNNING)) continue;
        std::string name = i->ifa_name;
        int score = 0;
        for (const char* skip : {"docker", "veth", "virbr", "br-", "tun", "tap", "wg", "tailscale", "zt"})
            if (name.rfind(skip, 0) == 0) score -= 80;
        if (name.rfind("wl", 0) == 0) score += 70;
        else if (name.rfind("en", 0) == 0 || name.rfind("eth", 0) == 0) score += 50;
        auto* sin = (sockaddr_in*)i->ifa_addr;
        uint32_t ip = ntohl(sin->sin_addr.s_addr);
        if ((ip >> 16) == 0xA9FE) continue;
        if ((ip >> 24) == 10 || (ip >> 20) == 0xAC1 || (ip >> 16) == 0xC0A8) score += 10;
        char str[INET_ADDRSTRLEN]{};
        inet_ntop(AF_INET, &sin->sin_addr, str, sizeof str);
        candidates.push_back({{str, name, name.rfind("wl", 0) == 0}, score});
    }
    freeifaddrs(list);
    std::stable_sort(candidates.begin(), candidates.end(), [](const Candidate& a, const Candidate& b) {
        return a.score > b.score;
    });
    std::vector<LanAddr> out;
    for (auto& c : candidates) out.push_back(std::move(c.addr));
    return out;
}

// ---- thumbnails (GdkPixbuf covers common image formats; videos/PDFs fall back to icons)
std::vector<uint8_t> thumbnail_jpeg(const std::string& path, int maxSide) {
    std::vector<uint8_t> out;
    GError* error = nullptr;
    GdkPixbuf* image = gdk_pixbuf_new_from_file_at_scale(path.c_str(), maxSide, maxSide, TRUE, &error);
    if (error) g_error_free(error);
    if (!image) return out;
    gchar* data = nullptr;
    gsize size = 0;
    error = nullptr;
    if (gdk_pixbuf_save_to_buffer(image, &data, &size, "jpeg", &error, "quality", "82", nullptr))
        out.assign((uint8_t*)data, (uint8_t*)data + size);
    if (error) g_error_free(error);
    g_free(data);
    g_object_unref(image);
    return out;
}

// ---- HTTP client
HttpResult http_get(const std::string& url, const std::vector<std::string>& headers, File out,
                    const std::function<bool(uint64_t, uint64_t)>& progress, int timeoutMs) {
    static std::once_flag once;
    std::call_once(once, [] { curl_global_init(CURL_GLOBAL_DEFAULT); });
    HttpResult res;
    CURL* h = curl_easy_init();
    if (!h) {
        res.err = "Network unavailable";
        return res;
    }
    CurlCtx ctx{out, &res.body, &progress};
    curl_slist* hl = nullptr;
    for (const auto& x : headers) hl = curl_slist_append(hl, x.c_str());
    curl_easy_setopt(h, CURLOPT_URL, url.c_str());
    curl_easy_setopt(h, CURLOPT_FOLLOWLOCATION, 1L);
    curl_easy_setopt(h, CURLOPT_MAXREDIRS, 10L);
    curl_easy_setopt(h, CURLOPT_USERAGENT, "PocketDrop/1.0");
    curl_easy_setopt(h, CURLOPT_ACCEPT_ENCODING, "");
    curl_easy_setopt(h, CURLOPT_NOSIGNAL, 1L);
    curl_easy_setopt(h, CURLOPT_CONNECTTIMEOUT_MS, (long)timeoutMs);
    if (out == BAD_FILE) curl_easy_setopt(h, CURLOPT_TIMEOUT_MS, (long)timeoutMs);
    else {
        curl_easy_setopt(h, CURLOPT_LOW_SPEED_LIMIT, 1L);
        curl_easy_setopt(h, CURLOPT_LOW_SPEED_TIME, (long)std::max(1, timeoutMs / 1000));
    }
    curl_easy_setopt(h, CURLOPT_WRITEFUNCTION, curlWrite);
    curl_easy_setopt(h, CURLOPT_WRITEDATA, &ctx);
    curl_easy_setopt(h, CURLOPT_XFERINFOFUNCTION, curlProgress);
    curl_easy_setopt(h, CURLOPT_XFERINFODATA, &ctx);
    curl_easy_setopt(h, CURLOPT_NOPROGRESS, 0L);
    if (hl) curl_easy_setopt(h, CURLOPT_HTTPHEADER, hl);
    CURLcode code = curl_easy_perform(h);
    if (code == CURLE_OK) {
        long status = 0;
        curl_easy_getinfo(h, CURLINFO_RESPONSE_CODE, &status);
        res.status = (int)status;
    } else {
        res.err = ctx.writeErr ? "Disk write failed" : code == CURLE_ABORTED_BY_CALLBACK ? "Cancelled" : curl_easy_strerror(code);
    }
    if (hl) curl_slist_free_all(hl);
    curl_easy_cleanup(h);
    return res;
}

// ---- child processes
struct Process {
    pid_t pid;
    int rd;
};

Process* process_start(const std::string& exe, const std::vector<std::string>& args,
                       const std::vector<std::pair<std::string, std::string>>& envOverrides, const std::string& cwd,
                       std::string& err) {
    int fds[2];
    if (pipe2(fds, O_CLOEXEC) != 0) {
        err = strerror(errno);
        return nullptr;
    }
    pid_t pid = fork();
    if (pid < 0) {
        err = strerror(errno);
        close(fds[0]);
        close(fds[1]);
        return nullptr;
    }
    if (pid == 0) {
        setpgid(0, 0);
        prctl(PR_SET_PDEATHSIG, SIGTERM);
        int nul = open("/dev/null", O_RDONLY);
        if (nul >= 0) {
            dup2(nul, STDIN_FILENO);
            close(nul);
        }
        dup2(fds[1], STDOUT_FILENO);
        dup2(fds[1], STDERR_FILENO);
        close(fds[0]);
        close(fds[1]);
        if (!cwd.empty() && chdir(cwd.c_str()) != 0) _exit(126);
        for (const auto& e : envOverrides) setenv(e.first.c_str(), e.second.c_str(), 1);
        std::vector<std::string> store{exe};
        store.insert(store.end(), args.begin(), args.end());
        std::vector<char*> argv;
        for (auto& s : store) argv.push_back(s.data());
        argv.push_back(nullptr);
        execv(exe.c_str(), argv.data());
        _exit(127);
    }
    close(fds[1]);
    setpgid(pid, pid);
    return new Process{pid, fds[0]};
}

int process_read(Process* p, char* buf, int len) {
    ssize_t n;
    do n = read(p->rd, buf, (size_t)len);
    while (n < 0 && errno == EINTR);
    return (int)n;
}

void process_kill(Process* p) { kill(-p->pid, SIGTERM); }

int process_finish(Process* p, int timeoutMs) {
    int status = 0;
    pid_t result = 0;
    for (int waited = 0; (result = waitpid(p->pid, &status, WNOHANG)) == 0 && waited < timeoutMs; waited += 20)
        usleep(20000);
    if (result == 0) {
        kill(-p->pid, SIGKILL);
        result = waitpid(p->pid, &status, 0);
    }
    close(p->rd);
    delete p;
    return result > 0 && WIFEXITED(status) ? WEXITSTATUS(status) : -1;
}

// ---- cloudflared
std::string cloudflared_download_url() {
#if defined(__aarch64__)
    return "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64";
#else
    return "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64";
#endif
}

std::string cloudflared_install_path() { return app_data_dir() + "/cloudflared"; }

std::vector<std::string> cloudflared_candidates() {
    return {cloudflared_install_path(), "/usr/local/bin/cloudflared", "/usr/bin/cloudflared", exe_dir() + "/cloudflared"};
}

bool cloudflared_install(const std::string& downloadedFile, std::string& err) {
    if (!executableLooksValid(downloadedFile)) {
        err = "The downloaded cloudflared file is not a valid Linux executable";
        return false;
    }
    if (chmod(downloadedFile.c_str(), 0755) != 0 || !file_rename(downloadedFile, cloudflared_install_path())) {
        err = "Cannot install cloudflared";
        return false;
    }
    return true;
}

} // namespace plat
