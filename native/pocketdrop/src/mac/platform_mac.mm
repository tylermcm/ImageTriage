// macOS implementation of core/platform.h.
#include "../core/platform.h"
#import <Foundation/Foundation.h>
#import <ImageIO/ImageIO.h>
#import <QuickLookThumbnailing/QuickLookThumbnailing.h>
#import <Security/Security.h>
#import <SystemConfiguration/SystemConfiguration.h>
#include <arpa/inet.h>
#include <curl/curl.h>
#include <dirent.h>
#include <fcntl.h>
#include <ifaddrs.h>
#include <libproc.h>
#include <net/if.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <signal.h>
#include <spawn.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/wait.h>
#include <unistd.h>
#include <algorithm>
#include <cerrno>
#include <cstring>
#include <ctime>
#include <mutex>

extern char** environ;

namespace {

std::string ns2s(NSString* s) { return s ? std::string(s.UTF8String) : std::string(); }

void setCloexec(int fd) { fcntl(fd, F_SETFD, fcntl(fd, F_GETFD) | FD_CLOEXEC); }

std::string leafOf(const std::string& p) {
    size_t s = p.find_last_of('/');
    return s == std::string::npos ? p : p.substr(s + 1);
}

// Accepts binaries signed by Cloudflare; rejects other signers and broken signatures.
bool verifySigner(const std::string& path, std::string& err) {
    CFURLRef url = CFURLCreateFromFileSystemRepresentation(nullptr, (const UInt8*)path.c_str(), (CFIndex)path.size(), false);
    SecStaticCodeRef code = nullptr;
    OSStatus st = SecStaticCodeCreateWithPath(url, kSecCSDefaultFlags, &code);
    CFRelease(url);
    if (st != errSecSuccess || !code) {
        err = "Cannot read cloudflared's signature";
        return false;
    }
    st = SecStaticCodeCheckValidity(code, kSecCSDefaultFlags, nullptr);
    if (st == errSecCSUnsigned) {
        CFRelease(code);
        return true; // unsigned release asset, fetched over HTTPS from Cloudflare's GitHub
    }
    if (st != errSecSuccess) {
        CFRelease(code);
        err = "cloudflared's code signature is invalid";
        return false;
    }
    bool ok = false;
    CFDictionaryRef info = nullptr;
    if (SecCodeCopySigningInformation(code, kSecCSSigningInformation, &info) == errSecSuccess && info) {
        CFArrayRef certs = (CFArrayRef)CFDictionaryGetValue(info, kSecCodeInfoCertificates);
        if (certs && CFArrayGetCount(certs) > 0) {
            SecCertificateRef leaf = (SecCertificateRef)CFArrayGetValueAtIndex(certs, 0);
            if (CFStringRef summary = SecCertificateCopySubjectSummary(leaf)) {
                NSString* s = (__bridge NSString*)summary;
                ok = [s.lowercaseString containsString:@"cloudflare"];
                if (!ok) err = "Unexpected signer: " + ns2s(s);
                CFRelease(summary);
            }
        }
        CFRelease(info);
    }
    if (!ok && err.empty()) err = "cloudflared isn't signed by Cloudflare";
    CFRelease(code);
    return ok;
}

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
        c->body->append(p, len);
    }
    return len;
}

int curlProgress(void* ud, curl_off_t total, curl_off_t now, curl_off_t, curl_off_t) {
    auto* c = (CurlCtx*)ud;
    if (c->progress && *c->progress && !(*c->progress)((uint64_t)now, (uint64_t)total)) return 1;
    return 0;
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
    int s = socket(AF_INET, SOCK_STREAM, 0);
    if (s < 0) return BAD_SOCK;
    setCloexec(s);
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
    int c = accept((int)s, nullptr, nullptr);
    if (c < 0) return BAD_SOCK;
    setCloexec(c);
    return c;
}

void tcp_configure(Sock s, int recvTimeoutMs) {
    timeval tv{recvTimeoutMs / 1000, (recvTimeoutMs % 1000) * 1000};
    setsockopt((int)s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    int one = 1;
    setsockopt((int)s, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
    setsockopt((int)s, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof one);
}

int tcp_recv(Sock s, char* buf, int len) {
    ssize_t n;
    do n = recv((int)s, buf, (size_t)len, 0);
    while (n < 0 && errno == EINTR);
    return (int)n;
}

int tcp_send(Sock s, const char* buf, int len) {
    ssize_t n;
    do n = send((int)s, buf, (size_t)len, 0);
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
    struct stat sb;
    if (lstat(path.c_str(), &sb) != 0) return st;
    st.exists = true;
    st.isLink = S_ISLNK(sb.st_mode);
    if (st.isLink) stat(path.c_str(), &sb); // describe the target
    st.isDir = S_ISDIR(sb.st_mode);
    st.size = (uint64_t)sb.st_size;
    st.mtime = (int64_t)sb.st_mtimespec.tv_sec;
    return st;
}

std::vector<DirEntry> list_dir(const std::string& dir) {
    std::vector<DirEntry> out;
    DIR* d = opendir(dir.c_str());
    if (!d) return out;
    while (dirent* e = readdir(d)) {
        std::string name = e->d_name;
        if (name == "." || name == "..") continue;
        DirEntry de;
        de.name = name;
        de.st = file_stat(dir + "/" + name);
        if (de.st.exists) out.push_back(std::move(de));
    }
    closedir(d);
    return out;
}

File file_open_read(const std::string& path) {
    int fd = open(path.c_str(), O_RDONLY | O_CLOEXEC);
    if (fd < 0) return BAD_FILE;
    struct stat sb;
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
    struct stat sb;
    return fstat((int)f, &sb) == 0 ? (int64_t)sb.st_size : -1;
}

void file_close(File f) {
    if (f != BAD_FILE) close((int)f);
}

bool file_rename(const std::string& from, const std::string& to) { return rename(from.c_str(), to.c_str()) == 0; }
void file_remove(const std::string& path) { unlink(path.c_str()); }
void make_dir(const std::string& path) { mkdir(path.c_str(), 0755); }

std::string app_data_dir() {
    @autoreleasepool {
        NSArray* dirs = NSSearchPathForDirectoriesInDomains(NSApplicationSupportDirectory, NSUserDomainMask, YES);
        NSString* base = dirs.count ? dirs[0] : [NSHomeDirectory() stringByAppendingPathComponent:@"Library/Application Support"];
        NSString* dir = [base stringByAppendingPathComponent:@"PocketDrop"];
        [[NSFileManager defaultManager] createDirectoryAtPath:dir withIntermediateDirectories:YES attributes:nil error:nil];
        return ns2s(dir);
    }
}

std::string downloads_dir() {
    @autoreleasepool {
        NSArray* dirs = NSSearchPathForDirectoriesInDomains(NSDownloadsDirectory, NSUserDomainMask, YES);
        return ns2s(dirs.count ? dirs[0] : [NSHomeDirectory() stringByAppendingPathComponent:@"Downloads"]);
    }
}

uint64_t free_disk_space(const std::string& path) {
    struct statvfs v;
    return statvfs(path.c_str(), &v) == 0 ? (uint64_t)v.f_bavail * (uint64_t)v.f_frsize : UINT64_MAX;
}

std::string exe_dir() {
    @autoreleasepool {
        return ns2s([NSBundle mainBundle].executablePath.stringByDeletingLastPathComponent);
    }
}

// ---- time and misc
int64_t unix_time() { return (int64_t)std::time(nullptr); }

uint64_t tick_ms() {
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000 + (uint64_t)ts.tv_nsec / 1000000;
}

void sleep_ms(int ms) { usleep((useconds_t)ms * 1000); }

void dos_datetime(int64_t unixTime, uint16_t& time, uint16_t& date) {
    time_t t = (time_t)unixTime;
    std::tm tm{};
    if (!localtime_r(&t, &tm) || tm.tm_year < 80) {
        time = 0;
        date = 0x21;
        return;
    }
    date = (uint16_t)(((tm.tm_year - 80) << 9) | ((tm.tm_mon + 1) << 5) | tm.tm_mday);
    time = (uint16_t)((tm.tm_hour << 11) | (tm.tm_min << 5) | (tm.tm_sec / 2));
}

void random_bytes(uint8_t* out, size_t len) { arc4random_buf(out, len); }

std::string host_name() {
    if (CFStringRef name = SCDynamicStoreCopyComputerName(nullptr, nullptr)) {
        std::string s = ns2s((__bridge NSString*)name);
        CFRelease(name);
        if (!s.empty()) return s;
    }
    char buf[256];
    return gethostname(buf, sizeof buf) == 0 ? std::string(buf) : "Mac";
}

// ---- network interfaces
std::vector<LanAddr> lan_addresses() {
    struct Cand {
        LanAddr a;
        int score;
    };
    std::vector<Cand> cands;
    ifaddrs* list = nullptr;
    if (getifaddrs(&list) != 0) return {};
    for (ifaddrs* i = list; i; i = i->ifa_next) {
        if (!i->ifa_addr || i->ifa_addr->sa_family != AF_INET) continue;
        if ((i->ifa_flags & IFF_LOOPBACK) || !(i->ifa_flags & IFF_UP) || !(i->ifa_flags & IFF_RUNNING)) continue;
        std::string name = i->ifa_name;
        int score = 0;
        for (const char* skip : {"utun", "ipsec", "bridge", "awdl", "llw", "gif", "stf", "anpi", "vmenet", "vnic", "ppp"})
            if (name.rfind(skip, 0) == 0) score -= 80;
        if (name.rfind("en", 0) == 0) score += 50;
        if (name == "en0") score += 10;
        auto* sin = (sockaddr_in*)i->ifa_addr;
        uint32_t ip = ntohl(sin->sin_addr.s_addr);
        if ((ip >> 16) == 0xA9FE) continue; // link-local
        if ((ip >> 24) == 10 || (ip >> 20) == 0xAC1 || (ip >> 16) == 0xC0A8) score += 10;
        char str[INET_ADDRSTRLEN];
        inet_ntop(AF_INET, &sin->sin_addr, str, sizeof str);
        cands.push_back({{str, name, false}, score});
    }
    freeifaddrs(list);
    std::stable_sort(cands.begin(), cands.end(), [](const Cand& a, const Cand& b) { return a.score > b.score; });
    std::vector<LanAddr> out;
    for (auto& c : cands) out.push_back(c.a);
    return out;
}

// ---- thumbnails
std::vector<uint8_t> thumbnail_jpeg(const std::string& path, int maxSide) {
    std::vector<uint8_t> out;
    @autoreleasepool {
        NSURL* url = [NSURL fileURLWithPath:[NSString stringWithUTF8String:path.c_str()]];
        QLThumbnailGenerationRequest* req = [[QLThumbnailGenerationRequest alloc]
              initWithFileAtURL:url
                           size:CGSizeMake(maxSide, maxSide)
                          scale:1.0
            representationTypes:QLThumbnailGenerationRequestRepresentationTypeThumbnail];
        dispatch_semaphore_t sem = dispatch_semaphore_create(0);
        __block CGImageRef image = nullptr;
        [[QLThumbnailGenerator sharedGenerator]
            generateBestRepresentationForRequest:req
                               completionHandler:^(QLThumbnailRepresentation* rep, NSError*) {
                                 if (rep && rep.CGImage) image = CGImageRetain(rep.CGImage);
                                 dispatch_semaphore_signal(sem);
                               }];
        if (dispatch_semaphore_wait(sem, dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC)) != 0) {
            [[QLThumbnailGenerator sharedGenerator] cancelRequest:req];
            return out;
        }
        if (!image) return out;
        size_t w = CGImageGetWidth(image), h = CGImageGetHeight(image);
        CGColorSpaceRef cs = CGColorSpaceCreateDeviceRGB();
        CGContextRef ctx = CGBitmapContextCreate(nullptr, w, h, 8, 0, cs, (CGBitmapInfo)kCGImageAlphaNoneSkipLast);
        CGImageRef flat = nullptr;
        if (ctx) {
            CGContextSetRGBFillColor(ctx, 0x1E / 255.0, 0x20 / 255.0, 0x26 / 255.0, 1.0); // page card colour
            CGContextFillRect(ctx, CGRectMake(0, 0, w, h));
            CGContextDrawImage(ctx, CGRectMake(0, 0, w, h), image);
            flat = CGBitmapContextCreateImage(ctx);
        }
        if (flat) {
            NSMutableData* data = [NSMutableData data];
            CGImageDestinationRef dst = CGImageDestinationCreateWithData((__bridge CFMutableDataRef)data, CFSTR("public.jpeg"), 1, nullptr);
            if (dst) {
                NSDictionary* props = @{(__bridge NSString*)kCGImageDestinationLossyCompressionQuality : @0.82};
                CGImageDestinationAddImage(dst, flat, (__bridge CFDictionaryRef)props);
                if (CGImageDestinationFinalize(dst))
                    out.assign((const uint8_t*)data.bytes, (const uint8_t*)data.bytes + data.length);
                CFRelease(dst);
            }
            CGImageRelease(flat);
        }
        if (ctx) CGContextRelease(ctx);
        CGColorSpaceRelease(cs);
        CGImageRelease(image);
    }
    return out;
}

// ---- HTTP client (libcurl ships with macOS)
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
    curl_easy_setopt(h, CURLOPT_NOSIGNAL, 1L);
    curl_easy_setopt(h, CURLOPT_CONNECTTIMEOUT_MS, (long)timeoutMs);
    if (out == BAD_FILE) {
        curl_easy_setopt(h, CURLOPT_TIMEOUT_MS, (long)timeoutMs);
    } else { // long downloads: fail only when the transfer stalls
        curl_easy_setopt(h, CURLOPT_LOW_SPEED_LIMIT, 1L);
        curl_easy_setopt(h, CURLOPT_LOW_SPEED_TIME, (long)std::max(1, timeoutMs / 1000));
    }
    curl_easy_setopt(h, CURLOPT_WRITEFUNCTION, curlWrite);
    curl_easy_setopt(h, CURLOPT_WRITEDATA, &ctx);
    curl_easy_setopt(h, CURLOPT_XFERINFOFUNCTION, curlProgress);
    curl_easy_setopt(h, CURLOPT_XFERINFODATA, &ctx);
    curl_easy_setopt(h, CURLOPT_NOPROGRESS, 0L);
    if (hl) curl_easy_setopt(h, CURLOPT_HTTPHEADER, hl);
    CURLcode rc = curl_easy_perform(h);
    if (rc == CURLE_OK) {
        long code = 0;
        curl_easy_getinfo(h, CURLINFO_RESPONSE_CODE, &code);
        res.status = (int)code;
    } else {
        res.err = ctx.writeErr ? "Disk write failed" : rc == CURLE_ABORTED_BY_CALLBACK ? "Cancelled" : curl_easy_strerror(rc);
    }
    if (hl) curl_slist_free_all(hl);
    curl_easy_cleanup(h);
    return res;
}

// ---- child processes
struct Process {
    pid_t pid;
    int rd;
    std::string pidFile;
};

Process* process_start(const std::string& exe, const std::vector<std::string>& args,
                       const std::vector<std::pair<std::string, std::string>>& envOverrides, const std::string& cwd,
                       std::string& err) {
    // macOS can't tie a child's lifetime to ours, so remember its pid and reap any
    // copy a crashed PocketDrop left behind.
    std::string pidFile = app_data_dir() + "/" + leafOf(exe) + ".pid";
    if (FILE* f = fopen(pidFile.c_str(), "r")) {
        int old = 0;
        if (fscanf(f, "%d", &old) == 1 && old > 1) {
            char buf[PROC_PIDPATHINFO_MAXSIZE];
            if (proc_pidpath(old, buf, sizeof buf) > 0 && exe == buf) kill(old, SIGKILL);
        }
        fclose(f);
    }

    int fds[2];
    if (pipe(fds) != 0) {
        err = strerror(errno);
        return nullptr;
    }
    setCloexec(fds[0]);

    posix_spawn_file_actions_t fa;
    posix_spawn_file_actions_init(&fa);
    posix_spawn_file_actions_addopen(&fa, 0, "/dev/null", O_RDONLY, 0);
    posix_spawn_file_actions_adddup2(&fa, fds[1], 1);
    posix_spawn_file_actions_adddup2(&fa, fds[1], 2);
    if (!cwd.empty()) posix_spawn_file_actions_addchdir_np(&fa, cwd.c_str());
    posix_spawnattr_t attr;
    posix_spawnattr_init(&attr);
    // Close every other descriptor (our sockets, files) in the child.
    posix_spawnattr_setflags(&attr, POSIX_SPAWN_CLOEXEC_DEFAULT | POSIX_SPAWN_SETPGROUP);
    posix_spawnattr_setpgroup(&attr, 0);

    std::vector<std::string> envStore;
    for (char** e = environ; *e; e++) {
        std::string kv = *e;
        std::string key = kv.substr(0, kv.find('='));
        bool overridden = std::any_of(envOverrides.begin(), envOverrides.end(), [&](const auto& o) { return o.first == key; });
        if (!overridden) envStore.push_back(kv);
    }
    for (const auto& o : envOverrides) envStore.push_back(o.first + "=" + o.second);
    std::vector<char*> envp;
    for (auto& s : envStore) envp.push_back(s.data());
    envp.push_back(nullptr);

    std::vector<std::string> argStore = {exe};
    argStore.insert(argStore.end(), args.begin(), args.end());
    std::vector<char*> argv;
    for (auto& s : argStore) argv.push_back(s.data());
    argv.push_back(nullptr);

    pid_t pid = 0;
    int rc = posix_spawn(&pid, exe.c_str(), &fa, &attr, argv.data(), envp.data());
    posix_spawn_file_actions_destroy(&fa);
    posix_spawnattr_destroy(&attr);
    close(fds[1]);
    if (rc != 0) {
        close(fds[0]);
        err = strerror(rc);
        return nullptr;
    }
    if (FILE* f = fopen(pidFile.c_str(), "w")) {
        fprintf(f, "%d\n", (int)pid);
        fclose(f);
    }
    return new Process{pid, fds[0], pidFile};
}

int process_read(Process* p, char* buf, int len) {
    ssize_t n;
    do n = read(p->rd, buf, (size_t)len);
    while (n < 0 && errno == EINTR);
    return (int)n;
}

void process_kill(Process* p) { kill(p->pid, SIGTERM); }

int process_finish(Process* p, int timeoutMs) {
    int status = 0;
    pid_t r = 0;
    for (int waited = 0; (r = waitpid(p->pid, &status, WNOHANG)) == 0 && waited < timeoutMs; waited += 20) usleep(20000);
    if (r == 0) {
        kill(p->pid, SIGKILL);
        r = waitpid(p->pid, &status, 0);
    }
    close(p->rd);
    unlink(p->pidFile.c_str());
    delete p;
    return r > 0 && WIFEXITED(status) ? WEXITSTATUS(status) : -1;
}

// ---- cloudflared
std::string cloudflared_download_url() {
#if defined(__arm64__) || defined(__aarch64__)
    return "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64.tgz";
#else
    return "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64.tgz";
#endif
}

std::string cloudflared_install_path() { return app_data_dir() + "/cloudflared"; }

std::vector<std::string> cloudflared_candidates() {
    return {cloudflared_install_path(), "/opt/homebrew/bin/cloudflared", "/usr/local/bin/cloudflared",
            exe_dir() + "/cloudflared"};
}

bool cloudflared_install(const std::string& downloadedFile, std::string& err) {
    std::string dir = app_data_dir() + "/cloudflared-extract";
    mkdir(dir.c_str(), 0755);
    std::string bin = dir + "/cloudflared";
    unlink(bin.c_str());
    Process* tar = process_start("/usr/bin/tar", {"-xzf", downloadedFile, "-C", dir}, {}, "", err);
    if (!tar) {
        err = "Cannot run tar: " + err;
        return false;
    }
    char buf[512];
    while (process_read(tar, buf, sizeof buf) > 0) {}
    int code = process_finish(tar, 30000);
    FileStat st = file_stat(bin);
    if (code != 0 || !st.exists || st.isDir) {
        err = "Couldn't unpack cloudflared";
        return false;
    }
    chmod(bin.c_str(), 0755);
    bool ok = verifySigner(bin, err) && file_rename(bin, cloudflared_install_path());
    if (!ok && err.empty()) err = "Cannot install cloudflared";
    unlink(bin.c_str());
    rmdir(dir.c_str());
    return ok;
}

} // namespace plat
