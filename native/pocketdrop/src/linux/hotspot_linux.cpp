// Offline hotspot on Linux through NetworkManager (`nmcli device wifi hotspot`). The feature is offered
// only when nmcli exists and a Wi-Fi adapter is present, so everywhere else Offline stays hidden.
#include "../core/hotspot_nm.h"
#include "../core/platform.h"
#include <atomic>
#include <fstream>
#include <mutex>
#include <sstream>
#include <thread>
#include <unistd.h>

namespace plat {

namespace {
const char* const CONNECTION = "PocketDrop hotspot";

std::string nmcliPath() {
    for (const char* p : {"/usr/bin/nmcli", "/bin/nmcli", "/usr/local/bin/nmcli"})
        if (access(p, X_OK) == 0) return p;
    return {};
}

// Runs nmcli and returns its output and exit status (-1 when it could not be started or timed out).
int nmcli(const std::vector<std::string>& args, std::string& out, int timeoutMs = 20000) {
    out.clear();
    std::string exe = nmcliPath(), err;
    if (exe.empty()) return -1;
    Process* p = process_start(exe, args, {}, "", err);
    if (!p) return -1;
    char buf[512];
    int n;
    while ((n = process_read(p, buf, sizeof buf)) > 0 && out.size() < 65536) out.append(buf, (size_t)n);
    return process_finish(p, timeoutMs);
}

std::string readFile(const char* path) {
    std::ifstream f(path);
    return std::string(std::istreambuf_iterator<char>(f), {});
}
} // namespace

struct Hotspot::Impl {
    explicit Impl(std::function<void()> fn) : changed(std::move(fn)) {
        std::string out;
        if (nmcli({"-t", "-f", "DEVICE,TYPE,STATE", "device"}, out, 5000) == 0) device = hotspot_nm::wifi_device(out);
        available = !device.empty();
    }
    ~Impl() {
        if (worker.joinable()) worker.join();
    }
    std::function<void()> changed;
    std::string device;
    bool available = false;
    mutable std::mutex mu;
    HotspotState state = HotspotState::Off;
    bool clientJoined = false;
    std::string ssid, pass, ip, message;
    std::thread worker;
    std::atomic<bool> cancel{false};
};

Hotspot::Hotspot(std::function<void()> changed) : impl_(std::make_shared<Impl>(std::move(changed))) {}

Hotspot::~Hotspot() { stop(); }

bool Hotspot::supported() const { return impl_->available; }

void Hotspot::start() {
    auto& m = *impl_;
    if (!m.available) return;
    {
        std::lock_guard<std::mutex> l(m.mu);
        if (m.state == HotspotState::Starting || m.state == HotspotState::On) return;
        m.state = HotspotState::Starting;
        m.message.clear();
        m.clientJoined = false;
    }
    if (m.worker.joinable()) m.worker.join();
    m.cancel = false;
    m.worker = std::thread([impl = impl_] {
        auto& m = *impl;
        static const char alphabet[] = "abcdefghjkmnpqrstuvwxyz23456789";
        uint8_t raw[16];
        random_bytes(raw, sizeof raw);
        std::string ssid = "PocketDrop-", pass;
        for (int i = 0; i < 4; i++) ssid += alphabet[raw[i] % (sizeof alphabet - 1)];
        for (int i = 4; i < 14; i++) pass += alphabet[raw[i] % (sizeof alphabet - 1)];
        std::string out;
        nmcli({"connection", "delete", CONNECTION}, out, 5000); // a leftover from a crash
        int rc = nmcli({"device", "wifi", "hotspot", "ifname", m.device, "con-name", CONNECTION, "ssid", ssid, "password", pass}, out);
        std::string ip;
        if (rc == 0) {
            std::string addr;
            if (nmcli({"-g", "IP4.ADDRESS", "device", "show", m.device}, addr, 5000) == 0) ip = hotspot_nm::ipv4_address(addr);
        }
        {
            std::lock_guard<std::mutex> l(m.mu);
            if (m.cancel) {
                m.state = HotspotState::Off;
            } else if (rc == 0 && !ip.empty()) {
                m.state = HotspotState::On;
                m.ssid = ssid;
                m.pass = pass;
                m.ip = ip;
            } else {
                m.state = HotspotState::Failed;
                std::string detail = out.substr(0, out.find_first_of("\r\n"));
                m.message = "Couldn't start the hotspot" + (detail.empty() ? std::string(".") : ": " + detail);
            }
        }
        if (rc == 0 && ip.empty()) nmcli({"connection", "down", CONNECTION}, out, 5000);
        if (m.changed) m.changed();
    });
}

void Hotspot::stop() {
    auto& m = *impl_;
    m.cancel = true;
    if (m.worker.joinable()) m.worker.join();
    bool wasOn;
    {
        std::lock_guard<std::mutex> l(m.mu);
        wasOn = m.state == HotspotState::On || m.state == HotspotState::Failed;
        m.state = m.available ? HotspotState::Off : HotspotState::Unavailable;
        m.clientJoined = false;
        m.ip.clear();
        m.pass.clear();
        m.ssid.clear();
    }
    if (wasOn && m.available) {
        std::string out;
        nmcli({"connection", "down", CONNECTION}, out, 8000);
        nmcli({"connection", "delete", CONNECTION}, out, 5000);
    }
}

// Cheap enough for every UI tick: a phone has joined once its address appears in the ARP table.
void Hotspot::poll() {
    auto& m = *impl_;
    bool joined, was;
    {
        std::lock_guard<std::mutex> l(m.mu);
        if (m.state != HotspotState::On) return;
        was = m.clientJoined;
    }
    joined = hotspot_nm::has_client(readFile("/proc/net/arp"), m.device);
    if (joined != was) {
        {
            std::lock_guard<std::mutex> l(m.mu);
            m.clientJoined = joined;
        }
        if (m.changed) m.changed();
    }
}

HotspotState Hotspot::state() const {
    std::lock_guard<std::mutex> l(impl_->mu);
    return impl_->available ? impl_->state : HotspotState::Unavailable;
}
bool Hotspot::connected() const {
    std::lock_guard<std::mutex> l(impl_->mu);
    return impl_->clientJoined;
}
bool Hotspot::uses_bluetooth() const { return false; }
std::string Hotspot::ssid() const { std::lock_guard<std::mutex> l(impl_->mu); return impl_->ssid; }
std::string Hotspot::passphrase() const { std::lock_guard<std::mutex> l(impl_->mu); return impl_->pass; }
std::string Hotspot::local_ip() const { std::lock_guard<std::mutex> l(impl_->mu); return impl_->ip; }
std::string Hotspot::message() const {
    std::lock_guard<std::mutex> l(impl_->mu);
    if (!impl_->available) return "Offline hotspot needs NetworkManager and a Wi-Fi adapter.";
    return impl_->message;
}

} // namespace plat
