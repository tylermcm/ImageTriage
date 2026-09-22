#pragma once
#include "bundle.h"
#include "platform.h"
#include <atomic>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <vector>

struct TransferInfo {
    uint64_t id = 0;
    std::string name, device;
    uint64_t total = 0, sent = 0;
    bool done = false, ok = false, viaTunnel = false;
    bool incoming = false;       // phone -> computer
    uint64_t start = 0, end = 0; // plat::tick_ms()
};

// Something a phone sent to this computer.
struct ReceivedItem {
    uint64_t id = 0;
    bool isText = false;
    std::string name, path; // files: saved name and full path
    std::string text;       // notes
    uint64_t size = 0;
    std::string device;
    int64_t time = 0; // unix seconds
};

// When links and phone sessions expire.
struct LinkPolicy {
    uint64_t rotateMs = 60 * 1000;           // idle link rotation while nothing is shared
    uint64_t idleExpireMs = 15 * 60 * 1000;  // link and session expiry with no phone activity
    uint64_t activeWindowMs = 60 * 1000;     // a session counts as connected this long after its last request
};

struct Req;

class HttpServer {
public:
    enum class Event { Visit, TransferStart, TransferEnd, Received };
    std::function<void(Event)> onEvent; // invoked on worker threads

    ~HttpServer() { stop(); }
    bool start(int preferredPort, bool loopbackOnly = false);
    void stop();
    int port() const { return port_; }

    // What phones can download.
    void publish(std::shared_ptr<Bundle> bundle);

    // The QR link is /<token>/. Opening it starts a phone session at /s/<id>/, which
    // keeps working when the token later rotates.
    std::string token() const;
    void newLink(); // fresh token; ends every session and upload
    void setPolicy(const LinkPolicy& p);
    // Applies the expiry rules. Call about twice a second; returns true when the token changed.
    bool maintain(bool sharing);
    bool phoneConnected() const;

    void setInbox(const std::string& dir); // where received files are saved
    void setReceiveCode(const std::string& code); // empty disables phone -> computer authorization
    std::vector<ReceivedItem> takeReceived(); // items that arrived since the last call

    std::vector<TransferInfo> transfers() const;
    std::string lastDevice() const;

    // Test hooks.
    void debugSetToken(const std::string& token);
    void debugDiskFull(bool full) { debugDiskFull_ = full; }

    struct Transfer;
    struct Session;
    struct Upload;

private:
    void acceptLoop();
    void serve(plat::Sock s);
    bool handle(plat::Sock s, const Req& r, std::string& buf, bool viaTunnel);
    bool handleSession(plat::Sock s, const Req& r, std::string& buf, bool viaTunnel, Session& session,
                       const std::string& rest, uint64_t bodyLen);
    std::shared_ptr<Transfer> begin(const std::string& name, const std::string& device, uint64_t total, bool tunnel,
                                    bool incoming);
    void end(const std::shared_ptr<Transfer>& t, bool ok);
    std::shared_ptr<Upload> findUpload(const std::string& id, const std::string& sid);
    void abortUpload(const std::shared_ptr<Upload>& u);        // caller holds u->io
    std::string finishUpload(const std::shared_ptr<Upload>& u); // caller holds u->io; returns saved name

    plat::Sock listen_ = plat::BAD_SOCK;
    int port_ = 0;
    std::atomic<bool> running_{false};
    std::atomic<int> threads_{0};
    std::thread acceptThread_;
    std::atomic<bool> debugDiskFull_{false};

    mutable std::mutex mu_;
    std::shared_ptr<Bundle> bundle_;
    int rev_ = 0;
    std::string token_;
    uint64_t tokenIssuedAt_ = 0, tokenUsedAt_ = 0;
    LinkPolicy policy_;
    std::map<std::string, std::shared_ptr<Session>> sessions_;
    std::map<std::string, std::shared_ptr<Upload>> uploads_;
    std::string inbox_;
    std::string receiveCode_;
    std::vector<ReceivedItem> received_;
    uint64_t nextReceivedId_ = 1;
    std::set<plat::Sock> clients_;
    std::vector<std::shared_ptr<Transfer>> transfers_;
    std::string lastDevice_;
    uint64_t nextId_ = 1;
};
