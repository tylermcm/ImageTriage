#include "platform.h"

// Linux has its own (src/linux/hotspot_linux.cpp); Windows has WinRT.
#if !defined(_WIN32) && !defined(__linux__)
namespace plat {

struct Hotspot::Impl {
    explicit Impl(std::function<void()> fn) : changed(std::move(fn)) {}
    std::function<void()> changed;
};

Hotspot::Hotspot(std::function<void()> changed) : impl_(std::make_shared<Impl>(std::move(changed))) {}
Hotspot::~Hotspot() = default;
bool Hotspot::supported() const { return false; }
void Hotspot::start() {}
void Hotspot::stop() {}
void Hotspot::poll() {}
HotspotState Hotspot::state() const { return HotspotState::Unavailable; }
bool Hotspot::connected() const { return false; }
bool Hotspot::uses_bluetooth() const { return false; }
std::string Hotspot::ssid() const { return {}; }
std::string Hotspot::passphrase() const { return {}; }
std::string Hotspot::local_ip() const { return {}; }
std::string Hotspot::message() const { return "Offline hotspot isn't available on this computer."; }

} // namespace plat
#endif
