// Windows offline hotspot using the Wi-Fi Direct legacy access-point API.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include "../core/platform.h"
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <iphlpapi.h>
#include <winrt/Windows.Devices.Enumeration.h>
#include <winrt/Windows.Devices.WiFiDirect.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Foundation.Collections.h>
#include <winrt/Windows.Networking.h>
#include <winrt/Windows.Security.Credentials.h>
#include <algorithm>
#include <atomic>
#include <cwctype>
#include <cstring>
#include <mutex>
#include <vector>

namespace plat {
namespace {

std::string randomText(size_t length, const char* alphabet) {
    std::vector<uint8_t> bytes(length);
    random_bytes(bytes.data(), bytes.size());
    size_t count = strlen(alphabet);
    std::string out;
    out.reserve(length);
    for (uint8_t b : bytes) out.push_back(alphabet[b % count]);
    return out;
}

std::string utf8(const winrt::hstring& value) { return winrt::to_string(value); }

bool hasWifiAdapter() {
    ULONG size = 0;
    const ULONG flags = GAA_FLAG_INCLUDE_ALL_INTERFACES | GAA_FLAG_SKIP_ANYCAST | GAA_FLAG_SKIP_MULTICAST |
                        GAA_FLAG_SKIP_DNS_SERVER;
    if (GetAdaptersAddresses(AF_UNSPEC, flags, nullptr, nullptr, &size) != ERROR_BUFFER_OVERFLOW) return false;
    std::vector<uint8_t> buffer(size);
    if (GetAdaptersAddresses(AF_UNSPEC, flags, nullptr, (PIP_ADAPTER_ADDRESSES)buffer.data(), &size) != NO_ERROR)
        return false;
    for (auto* adapter = (PIP_ADAPTER_ADDRESSES)buffer.data(); adapter; adapter = adapter->Next)
        if (adapter->IfType == IF_TYPE_IEEE80211) return true;
    return false;
}

struct BluetoothPan {
    bool present = false;
    std::string ip;
};

std::wstring lowerWide(const wchar_t* value) {
    std::wstring out = value ? value : L"";
    std::transform(out.begin(), out.end(), out.begin(), [](wchar_t c) { return (wchar_t)std::towlower(c); });
    return out;
}

BluetoothPan bluetoothPan() {
    BluetoothPan result;
    ULONG size = 0;
    const ULONG flags = GAA_FLAG_INCLUDE_ALL_INTERFACES | GAA_FLAG_SKIP_ANYCAST | GAA_FLAG_SKIP_MULTICAST |
                        GAA_FLAG_SKIP_DNS_SERVER;
    if (GetAdaptersAddresses(AF_INET, flags, nullptr, nullptr, &size) != ERROR_BUFFER_OVERFLOW) return result;
    std::vector<uint8_t> buffer(size);
    if (GetAdaptersAddresses(AF_INET, flags, nullptr, (PIP_ADAPTER_ADDRESSES)buffer.data(), &size) != NO_ERROR)
        return result;
    for (auto* adapter = (PIP_ADAPTER_ADDRESSES)buffer.data(); adapter; adapter = adapter->Next) {
        std::wstring description = lowerWide(adapter->Description);
        if (description.find(L"bluetooth") == std::wstring::npos ||
            description.find(L"personal area network") == std::wstring::npos)
            continue;
        result.present = true;
        if (adapter->OperStatus != IfOperStatusUp) continue;
        for (auto* address = adapter->FirstUnicastAddress; address; address = address->Next) {
            if (!address->Address.lpSockaddr || address->Address.lpSockaddr->sa_family != AF_INET) continue;
            char ip[INET_ADDRSTRLEN]{};
            auto* v4 = reinterpret_cast<sockaddr_in*>(address->Address.lpSockaddr);
            if (!inet_ntop(AF_INET, &v4->sin_addr, ip, sizeof(ip))) continue;
            std::string candidate = ip;
            if (candidate == "0.0.0.0" || candidate.rfind("127.", 0) == 0 || candidate.rfind("169.254.", 0) == 0)
                continue;
            result.ip = std::move(candidate);
            return result;
        }
    }
    return result;
}

} // namespace

struct Hotspot::Impl : std::enable_shared_from_this<Impl> {
    explicit Impl(std::function<void()> fn) : changed(std::move(fn)) {}

    mutable std::mutex mutex;
    std::function<void()> changed;
    HotspotState current = HotspotState::Off;
    bool hasClient = false, bluetoothMode = false;
    std::string networkName, password, localIp, error;
    std::atomic<uint64_t> generation{0};

    winrt::Windows::Devices::WiFiDirect::WiFiDirectAdvertisementPublisher publisher{nullptr};
    winrt::Windows::Devices::WiFiDirect::WiFiDirectConnectionListener listener{nullptr};
    std::vector<winrt::Windows::Devices::WiFiDirect::WiFiDirectDevice> clients;
    winrt::event_token statusToken{}, requestToken{};
    bool statusHooked = false, requestHooked = false;

    void notify() {
        auto fn = changed;
        if (fn) fn();
    }

    void setFailed(std::string why, uint64_t run) {
        {
            std::lock_guard lock(mutex);
            if (generation.load() != run) return;
            current = HotspotState::Failed;
            error = std::move(why);
            hasClient = false;
            localIp.clear();
        }
        notify();
    }

    void accept(const winrt::Windows::Devices::WiFiDirect::WiFiDirectConnectionRequestedEventArgs& args,
                uint64_t run) {
        try {
            auto request = args.GetConnectionRequest();
            auto operation = winrt::Windows::Devices::WiFiDirect::WiFiDirectDevice::FromIdAsync(
                request.DeviceInformation().Id());
            std::weak_ptr<Impl> weak = shared_from_this();
            operation.Completed([weak, request, run](auto const& completed,
                                                     winrt::Windows::Foundation::AsyncStatus status) {
                auto self = weak.lock();
                if (!self || self->generation.load() != run ||
                    status != winrt::Windows::Foundation::AsyncStatus::Completed)
                    return;
                try {
                    auto device = completed.GetResults();
                    std::string local;
                    for (const auto& endpoints : device.GetConnectionEndpointPairs()) {
                        auto host = endpoints.LocalHostName();
                        if (host && host.Type() == winrt::Windows::Networking::HostNameType::Ipv4) {
                            local = utf8(host.CanonicalName());
                            break;
                        }
                    }
                    {
                        std::lock_guard lock(self->mutex);
                        if (self->current != HotspotState::On) return;
                        self->clients.push_back(device); // Keeps the Wi-Fi Direct connection alive.
                        self->hasClient = true;
                        self->localIp = std::move(local);
                    }
                    self->notify();
                } catch (const winrt::hresult_error& e) {
                    self->setFailed("Couldn't accept the phone: " + utf8(e.message()), run);
                }
            });
        } catch (const winrt::hresult_error& e) {
            setFailed("Couldn't accept the phone: " + utf8(e.message()), run);
        }
    }

    void onStarted(uint64_t run) {
        if (generation.load() != run) return;
        try {
            auto newListener = winrt::Windows::Devices::WiFiDirect::WiFiDirectConnectionListener();
            std::weak_ptr<Impl> weak = shared_from_this();
            auto newToken = newListener.ConnectionRequested([weak, run](auto const&, auto const& args) {
                if (auto self = weak.lock(); self && self->generation.load() == run) self->accept(args, run);
            });
            if (generation.load() != run) {
                newListener.ConnectionRequested(newToken);
                return;
            }
            listener = std::move(newListener);
            requestToken = newToken;
            requestHooked = true;
            {
                std::lock_guard lock(mutex);
                if (generation.load() != run) return;
                current = HotspotState::On;
                error.clear();
            }
            notify();
        } catch (const winrt::hresult_error& e) {
            setFailed("Couldn't listen for phones: " + utf8(e.message()), run);
        }
    }

    void start() {
        stop(false);
        uint64_t run = ++generation;
        if (!hasWifiAdapter()) {
            BluetoothPan pan = bluetoothPan();
            if (!pan.present) {
                setFailed("No Wi-Fi or Bluetooth PAN adapter found.", run);
                return;
            }
            {
                std::lock_guard lock(mutex);
                bluetoothMode = true;
                hasClient = !pan.ip.empty();
                localIp = std::move(pan.ip);
                current = hasClient ? HotspotState::On : HotspotState::Waiting;
                error.clear();
            }
            notify();
            return;
        }
        {
            std::lock_guard lock(mutex);
            networkName = "PocketDrop-" + randomText(4, "ABCDEFGHJKLMNPQRSTUVWXYZ23456789");
            password = randomText(12, "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789");
            current = HotspotState::Starting;
            error.clear();
        }
        notify();

        try {
            using namespace winrt::Windows::Devices::WiFiDirect;
            publisher = WiFiDirectAdvertisementPublisher();
            auto advertisement = publisher.Advertisement();
            advertisement.IsAutonomousGroupOwnerEnabled(true);
            auto legacy = advertisement.LegacySettings();
            legacy.IsEnabled(true);
            legacy.Ssid(winrt::to_hstring(networkName));
            legacy.Passphrase().Password(winrt::to_hstring(password));

            std::weak_ptr<Impl> weak = shared_from_this();
            statusToken = publisher.StatusChanged([weak, run](auto const&, auto const& args) {
                auto self = weak.lock();
                if (!self || self->generation.load() != run) return;
                switch (args.Status()) {
                case WiFiDirectAdvertisementPublisherStatus::Started: self->onStarted(run); break;
                case WiFiDirectAdvertisementPublisherStatus::Aborted:
                    if (args.Error() == WiFiDirectError::RadioNotAvailable)
                        self->setFailed("Turn on Wi-Fi, then try again.", run);
                    else if (args.Error() == WiFiDirectError::ResourceInUse)
                        self->setFailed("Wi-Fi is already in use by another hotspot.", run);
                    else
                        self->setFailed("The Wi-Fi adapter can't start an offline hotspot.", run);
                    break;
                case WiFiDirectAdvertisementPublisherStatus::Stopped: break;
                default: break;
                }
            });
            statusHooked = true;
            publisher.Start();
        } catch (const winrt::hresult_error& e) {
            setFailed("Couldn't start the hotspot: " + utf8(e.message()), run);
        }
    }

    void poll() {
        uint64_t run = generation.load();
        {
            std::lock_guard lock(mutex);
            if (!bluetoothMode || (current != HotspotState::Waiting && current != HotspotState::On)) return;
        }
        BluetoothPan pan = bluetoothPan();
        bool connected = !pan.ip.empty();
        bool changedState = false;
        {
            std::lock_guard lock(mutex);
            if (generation.load() != run || !bluetoothMode) return;
            HotspotState next = connected ? HotspotState::On : HotspotState::Waiting;
            changedState = current != next || hasClient != connected || localIp != pan.ip;
            current = next;
            hasClient = connected;
            localIp = std::move(pan.ip);
        }
        if (changedState) notify();
    }

    void stop(bool tell = true) {
        ++generation;
        try {
            if (requestHooked && listener) listener.ConnectionRequested(requestToken);
        } catch (...) {
        }
        requestHooked = false;
        try {
            if (statusHooked && publisher) publisher.StatusChanged(statusToken);
        } catch (...) {
        }
        statusHooked = false;
        try {
            if (publisher) publisher.Stop();
        } catch (...) {
        }
        clients.clear();
        listener = nullptr;
        publisher = nullptr;
        {
            std::lock_guard lock(mutex);
            current = HotspotState::Off;
            hasClient = false;
            bluetoothMode = false;
            networkName.clear();
            password.clear();
            localIp.clear();
            error.clear();
        }
        if (tell) notify();
    }
};

Hotspot::Hotspot(std::function<void()> changed) : impl_(std::make_shared<Impl>(std::move(changed))) {}
Hotspot::~Hotspot() {
    if (impl_) impl_->stop(false);
}
bool Hotspot::supported() const { return true; }
void Hotspot::start() { impl_->start(); }
void Hotspot::stop() { impl_->stop(); }
void Hotspot::poll() { impl_->poll(); }
HotspotState Hotspot::state() const {
    std::lock_guard lock(impl_->mutex);
    return impl_->current;
}
bool Hotspot::connected() const {
    std::lock_guard lock(impl_->mutex);
    return impl_->hasClient;
}
bool Hotspot::uses_bluetooth() const {
    std::lock_guard lock(impl_->mutex);
    return impl_->bluetoothMode;
}
std::string Hotspot::ssid() const {
    std::lock_guard lock(impl_->mutex);
    return impl_->networkName;
}
std::string Hotspot::passphrase() const {
    std::lock_guard lock(impl_->mutex);
    return impl_->password;
}
std::string Hotspot::local_ip() const {
    std::lock_guard lock(impl_->mutex);
    return impl_->localIp;
}
std::string Hotspot::message() const {
    std::lock_guard lock(impl_->mutex);
    return impl_->error;
}

} // namespace plat
