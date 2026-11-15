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

// Someone asked this PC to take files: where to fetch them, until the person here accepts or declines.
struct NearbyInvite {
    std::string senderId, name, ip, offer;
    int port = 0;
    uint64_t seen = 0;
};

// Every instance can be seen by the others (presence), which is what lets a PC be a drop target. A temporary PC offer is
// advertised only by an explicit Send, and phone tokens never are. An invitation goes to one PC, by address.
class NearbyDiscovery {
public:
    explicit NearbyDiscovery(int discoveryPort = 47292) : discoveryPort_(discoveryPort) {}
    ~NearbyDiscovery() { stop(); }
    bool start(int httpPort);
    void setOffer(const std::string& offer);
    // Announce this PC to the others on the network even without an offer. Off, it still listens and answers a query.
    void setVisible(bool on) { visible_ = on; offerChanged_ = true; }
    bool running() const { return running_; }
    int httpPort() const { return httpPort_; }
    // Ask one PC to fetch `offer` from this one. Returns false when the packet could not be sent.
    bool invite(const NearbyPeer& target, const std::string& offer, int targetDiscoveryPort = 0);
    std::vector<NearbyInvite> invites() const;
    void dismissInvite(const std::string& senderId);
    void stop();
    std::vector<NearbyPeer> peers() const;
    static std::string announce(const std::string& id, const std::string& name, int port, const std::string& offer = {});
    static bool parse(const std::string& packet, NearbyPeer& peer);
    static std::string inviteMessage(const std::string& id, const std::string& name, int port, const std::string& offer);
    static bool parseInvite(const std::string& packet, NearbyInvite& invite);
private:
#ifdef POCKETDROP_RENDER_TEST
    friend struct UiRenderTest;
#endif
    void run(int httpPort);
    std::atomic<bool> running_{false};
    std::atomic<bool> offerChanged_{false};
    std::atomic<bool> visible_{false};
    int httpPort_ = 0;
    std::vector<NearbyInvite> invites_;
    std::thread worker_;
    mutable std::mutex mu_;
    std::vector<NearbyPeer> peers_;
    std::string id_;
    std::string offer_;
    int discoveryPort_;
    plat::Sock socket_ = plat::BAD_SOCK;
};
