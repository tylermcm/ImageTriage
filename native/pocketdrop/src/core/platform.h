#pragma once
// Everything OS-specific the shared core needs. Paths and strings are UTF-8.
// Implemented by src/win/platform_win.cpp and src/mac/platform_mac.mm.
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace plat {

// ---- TCP sockets (IPv4)
using Sock = intptr_t;
constexpr Sock BAD_SOCK = -1;
bool net_init();
Sock tcp_listen(int port, bool loopbackOnly, int* boundPort);
Sock tcp_accept(Sock s, int timeoutMs); // BAD_SOCK on timeout or error
void tcp_configure(Sock s, int recvTimeoutMs); // no Nagle, receive timeout, no SIGPIPE
int tcp_recv(Sock s, char* buf, int len);      // <= 0: closed or error
int tcp_send(Sock s, const char* buf, int len); // <= 0: error
bool tcp_peer_is_loopback(Sock s);
void tcp_shutdown(Sock s); // both directions; wakes a thread blocked on s
void tcp_close(Sock s);

// ---- Files
struct FileStat {
    bool exists = false, isDir = false, isLink = false;
    uint64_t size = 0;
    int64_t mtime = 0; // unix seconds
};
FileStat file_stat(const std::string& path); // does not follow symlinks/reparse points
struct DirEntry {
    std::string name;
    FileStat st;
};
std::vector<DirEntry> list_dir(const std::string& dir); // excludes "." and ".."

using File = intptr_t;
constexpr File BAD_FILE = -1;
File file_open_read(const std::string& path);
File file_create(const std::string& path); // truncates
int64_t file_read(File f, void* buf, size_t len); // bytes read, 0 at EOF, -1 on error
bool file_write(File f, const void* buf, size_t len);
bool file_seek(File f, uint64_t offset);
int64_t file_size(File f);
void file_close(File f);
bool file_rename(const std::string& from, const std::string& to); // replaces the target
void file_remove(const std::string& path);
void make_dir(const std::string& path);
extern const char PATH_SEP;
std::string app_data_dir(); // created if missing
std::string exe_dir();
std::string downloads_dir();                       // the user's Downloads folder
uint64_t free_disk_space(const std::string& path); // bytes available to this user; UINT64_MAX if unknown

// ---- Time and misc
int64_t unix_time();
uint64_t tick_ms();
void sleep_ms(int ms);
void dos_datetime(int64_t unixTime, uint16_t& time, uint16_t& date); // local time, zip encoding
void random_bytes(uint8_t* out, size_t len);
std::string host_name();

// ---- Network interfaces
struct LanAddr {
    std::string ip, adapter;
    bool wifi = false;
};
std::vector<LanAddr> lan_addresses(); // best candidate first

// ---- Offline hotspot
enum class HotspotState { Unavailable, Off, Starting, Waiting, On, Failed };

class Hotspot {
public:
    explicit Hotspot(std::function<void()> changed);
    ~Hotspot();
    Hotspot(const Hotspot&) = delete;
    Hotspot& operator=(const Hotspot&) = delete;

    bool supported() const;
    void start();
    void stop();
    void poll();
    HotspotState state() const;
    bool connected() const;
    bool uses_bluetooth() const;
    std::string ssid() const;
    std::string passphrase() const;
    std::string local_ip() const;
    std::string message() const;

private:
    struct Impl;
    std::shared_ptr<Impl> impl_;
};

// ---- Thumbnails: JPEG bytes, empty when the OS can't render one
std::vector<uint8_t> thumbnail_jpeg(const std::string& path, int maxSide);

// ---- HTTP client
struct HttpResult {
    int status = 0; // 0 = no response
    std::string body, err;
};
// GET. The body streams to `out` when given; otherwise up to 64 KB is kept in `body`.
// `progress` returning false cancels.
HttpResult http_get(const std::string& url, const std::vector<std::string>& headers, File out,
                    const std::function<bool(uint64_t got, uint64_t total)>& progress, int timeoutMs);

// ---- Child processes: stdout and stderr merged into one pipe
struct Process;
Process* process_start(const std::string& exe, const std::vector<std::string>& args,
                       const std::vector<std::pair<std::string, std::string>>& envOverrides, const std::string& cwd,
                       std::string& err);
int process_read(Process* p, char* buf, int len); // blocks; <= 0 once output is closed
void process_kill(Process* p);                    // safe while another thread is reading
int process_finish(Process* p, int timeoutMs);    // waits, frees p, returns the exit code (-1 if unknown)

// ---- cloudflared
std::string cloudflared_download_url();
std::vector<std::string> cloudflared_candidates(); // existing install locations, in priority order
std::string cloudflared_install_path();
// Verifies a downloaded release asset and installs it at cloudflared_install_path().
bool cloudflared_install(const std::string& downloadedFile, std::string& err);

} // namespace plat
