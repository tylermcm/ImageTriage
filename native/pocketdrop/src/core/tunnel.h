#pragma once
#include "platform.h"
#include "util.h"
#include <atomic>
#include <functional>
#include <mutex>
#include <string>
#include <thread>

// "Anywhere" mode: a Cloudflare quick tunnel (https://*.trycloudflare.com) that
// forwards to the local server, so phones on mobile data can reach this computer
// without port forwarding, accounts or a VPN client on the phone.
class Tunnel {
public:
    enum class State { Off, Missing, Downloading, Starting, Online, Failed };

    explicit Tunnel(std::function<void()> onChange) : onChange_(std::move(onChange)) {}
    ~Tunnel() { stop(); }

    // Call from the UI thread. allowDownload fetches cloudflared if it isn't found.
    void start(int port, bool allowDownload);
    void stop();

    State state() const { return state_; }
    int progress() const { return progress_; }
    std::string url() const;
    std::string message() const;

    static std::string findBinary();

private:
    void run(int port, bool allowDownload);
    // One cloudflared process lifetime. Returns a user-facing failure message.
    std::string runOnce(const std::string& exe, const std::string& home, int port, plat::File log, bool& registered);
    void waitUntilPublic(const std::string& url, plat::File log);
    void set(State s, const std::string& msg);
    bool download(std::string& err);

    std::function<void()> onChange_;
    std::atomic<State> state_{State::Off};
    std::atomic<int> progress_{0};
    std::atomic<bool> stop_{false};
    std::atomic<bool> alive_{false}; // cloudflared process currently running
    mutable std::mutex mu_;
    std::string url_, msg_;
    std::thread thread_, probeThread_;
    plat::Process* proc_ = nullptr; // guarded by mu_
};
