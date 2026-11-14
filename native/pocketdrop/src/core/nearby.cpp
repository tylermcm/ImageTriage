#include "nearby.h"
#include "util.h"
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/select.h>
#include <unistd.h>
#endif
#include <algorithm>
#include <sstream>

namespace {
constexpr const char* MULTICAST_GROUP = "239.255.47.29";
#ifdef _WIN32
using NativeSocket = SOCKET;
#else
using NativeSocket = int;
#endif
NativeSocket native(plat::Sock s) { return (NativeSocket)s; }
}

std::string NearbyDiscovery::announce(const std::string& id, const std::string& name, int port, const std::string& offer) {
    std::string clean;
    for (unsigned char ch : name) if (ch >= 32 && ch != 127 && clean.size() < 96) clean += (char)ch;
    return "POCKETDROP/2\n" + id + "\n" + std::to_string(port) + "\n" + clean + "\n" + offer;
}

bool NearbyDiscovery::parse(const std::string& packet, NearbyPeer& peer) {
    if (packet.size() > 256) return false;
    std::istringstream in(packet);
    std::string magic, port;
    NearbyPeer p;
    if (!std::getline(in, magic) || magic != "POCKETDROP/2" || !std::getline(in, p.id) ||
        !std::getline(in, port) || !std::getline(in, p.name)) return false;
    std::getline(in, p.offer);
    if (!p.offer.empty() && (p.offer.size() != 32 || p.offer.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_") != std::string::npos)) return false;
    if (p.id.size() != 32 || p.id.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_") != std::string::npos ||
        port.empty() || port.size() > 5 || port.find_first_not_of("0123456789") != std::string::npos ||
        p.name.empty() || p.name.size() > 96 || in.peek() != EOF) return false;
    for (unsigned char c : p.name) if (c < 32 || c == 127) return false;
    p.port = std::stoi(port);
    if (p.port < 1 || p.port > 65535) return false;
    peer = std::move(p);
    return true;
}

bool NearbyDiscovery::start(int httpPort) {
    stop();
    if (httpPort < 1 || httpPort > 65535 || discoveryPort_ < 1 || discoveryPort_ > 65535 || !plat::net_init()) return false;
    socket_ = (plat::Sock)::socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (socket_ == plat::BAD_SOCK) return false;
    int yes = 1;
    setsockopt(native(socket_), SOL_SOCKET, SO_REUSEADDR, (const char*)&yes, sizeof yes);
    sockaddr_in local{};
    local.sin_family = AF_INET;
    local.sin_port = htons((uint16_t)discoveryPort_);
    local.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(native(socket_), (sockaddr*)&local, sizeof local) != 0) {
        plat::tcp_close(socket_); socket_ = plat::BAD_SOCK; return false;
    }
#ifdef _WIN32
    DWORD ttl = 1, loop = 0;
#else
    unsigned char ttl = 1, loop = 0;
#endif
    setsockopt(native(socket_), IPPROTO_IP, IP_MULTICAST_TTL, (const char*)&ttl, sizeof ttl);
    setsockopt(native(socket_), IPPROTO_IP, IP_MULTICAST_LOOP, (const char*)&loop, sizeof loop);
    auto addresses = plat::lan_addresses();
    for (const auto& address : addresses) {
        ip_mreq membership{};
        inet_pton(AF_INET, MULTICAST_GROUP, &membership.imr_multiaddr);
        inet_pton(AF_INET, address.ip.c_str(), &membership.imr_interface);
        setsockopt(native(socket_), IPPROTO_IP, IP_ADD_MEMBERSHIP, (const char*)&membership, sizeof membership);
    }
    id_ = util::random_token(24);
    offerChanged_ = false;
    running_ = true;
    worker_ = std::thread([this, httpPort] { run(httpPort); });
    return true;
}

void NearbyDiscovery::stop() {
    running_ = false;
    if (worker_.joinable()) worker_.join();
    if (socket_ != plat::BAD_SOCK) plat::tcp_close(socket_);
    socket_ = plat::BAD_SOCK;
    std::lock_guard<std::mutex> lock(mu_);
    peers_.clear();
    offer_.clear();
}

void NearbyDiscovery::setOffer(const std::string& offer) {
    std::lock_guard<std::mutex> lock(mu_);
    offer_ = offer;
    offerChanged_ = true;
}

std::vector<NearbyPeer> NearbyDiscovery::peers() const {
    std::lock_guard<std::mutex> lock(mu_);
    std::vector<NearbyPeer> result;
    auto now = plat::tick_ms();
    for (const auto& peer : peers_) if (now - peer.seen < 12000) result.push_back(peer);
    std::sort(result.begin(), result.end(), [](const auto& a, const auto& b) { return a.name < b.name; });
    return result;
}

void NearbyDiscovery::run(int httpPort) {
    sockaddr_in group{};
    group.sin_family = AF_INET;
    group.sin_port = htons((uint16_t)discoveryPort_);
    inet_pton(AF_INET, MULTICAST_GROUP, &group.sin_addr);
    uint64_t last = 0;
    while (running_) {
        auto now = plat::tick_ms();
        bool changed = offerChanged_.exchange(false);
        if (changed || !last || now - last >= 3000) {
            last = now;
            std::string offer;
            { std::lock_guard<std::mutex> lock(mu_); offer = offer_; }
            const auto packet = announce(id_, plat::host_name(), httpPort, offer);
            for (const auto& address : plat::lan_addresses()) {
                if (offer.empty() && !changed) break;
                in_addr iface{};
                inet_pton(AF_INET, address.ip.c_str(), &iface);
                setsockopt(native(socket_), IPPROTO_IP, IP_MULTICAST_IF, (const char*)&iface, sizeof iface);
                sendto(native(socket_), packet.data(), (int)packet.size(), 0, (sockaddr*)&group, sizeof group);
            }
        }
        fd_set reads;
        FD_ZERO(&reads); FD_SET(native(socket_), &reads);
        timeval timeout{0, 250000};
        if (select((int)native(socket_) + 1, &reads, nullptr, nullptr, &timeout) <= 0) continue;
        char buffer[257];
        sockaddr_in from{};
#ifdef _WIN32
        int len = sizeof from;
#else
        socklen_t len = sizeof from;
#endif
        int n = (int)recvfrom(native(socket_), buffer, sizeof buffer, 0, (sockaddr*)&from, &len);
        NearbyPeer peer;
        if (n <= 0 || !parse(std::string(buffer, n), peer) || peer.id == id_) continue;
        char ip[INET_ADDRSTRLEN]{};
        inet_ntop(AF_INET, &from.sin_addr, ip, sizeof ip);
        peer.ip = ip; peer.seen = plat::tick_ms();
        std::lock_guard<std::mutex> lock(mu_);
        if (peer.offer.empty() && !offer_.empty()) offerChanged_ = true; // reply to Receive's discovery query
        std::erase_if(peers_, [&](const auto& p) { return peer.seen - p.seen >= 12000; });
        auto it = std::find_if(peers_.begin(), peers_.end(), [&](const auto& p) { return p.id == peer.id; });
        if (it != peers_.end()) *it = peer;
        else if (peers_.size() < 64) peers_.push_back(peer);
    }
    // Withdraw this instance immediately, rather than waiting for peer expiry.
    const auto withdrawn = announce(id_, plat::host_name(), httpPort);
    for (const auto& address : plat::lan_addresses()) {
        in_addr iface{};
        inet_pton(AF_INET, address.ip.c_str(), &iface);
        setsockopt(native(socket_), IPPROTO_IP, IP_MULTICAST_IF, (const char*)&iface, sizeof iface);
        sendto(native(socket_), withdrawn.data(), (int)withdrawn.size(), 0, (sockaddr*)&group, sizeof group);
    }
}
