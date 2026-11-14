#pragma once
#include "platform.h"
#include <atomic>
#include <mutex>
#include <thread>

struct NearbyPeer {
    std::string id, name, ip, offer;
    int port = 0;
    uint64_t seen = 0;
};

// Only explicit Send sessions advertise a temporary PC offer. Phone tokens are never advertised.
class NearbyDiscovery {
public:
    explicit NearbyDiscovery(int discoveryPort = 47292) : discoveryPort_(discoveryPort) {}
    ~NearbyDiscovery() { stop(); }
    bool start(int httpPort);
    void setOffer(const std::string& offer);
    void stop();
    std::vector<NearbyPeer> peers() const;
    static std::string announce(const std::string& id, const std::string& name, int port, const std::string& offer = {});
    static bool parse(const std::string& packet, NearbyPeer& peer);
private:
#ifdef POCKETDROP_RENDER_TEST
    friend struct UiRenderTest;
#endif
    void run(int httpPort);
    std::atomic<bool> running_{false};
    std::atomic<bool> offerChanged_{false};
    std::thread worker_;
    mutable std::mutex mu_;
    std::vector<NearbyPeer> peers_;
    std::string id_;
    std::string offer_;
    int discoveryPort_;
    plat::Sock socket_ = plat::BAD_SOCK;
};
