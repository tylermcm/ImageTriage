#pragma once
// Platform-neutral window contents: state, behaviour, layout, painting and hit testing.
#include "../core/bundle.h"
#include "../core/http.h"
#include "../core/qr.h"
#include "../core/tunnel.h"
#include "gfx.h"
#include "shell.h"
#include <map>
#include <memory>
#include <set>

inline constexpr int DEFAULT_PORT = 47291;

enum class Hit {
    None,
    Menu,
    ModeLocal,
    ModeOffline,
    ModeAnywhere,
    CardButton,
    Url,
    Copy,
    Share,
    AddFiles,
    AddFolder,
    Third,
    Row,
    RowRemove
};

struct HitTarget {
    Hit hit = Hit::None;
    int index = -1;
    bool operator==(const HitTarget& o) const { return hit == o.hit && index == o.index; }
};

enum class CardState {
    DragOver,
    TunnelBusy,
    TunnelMissing,
    TunnelDownloading,
    TunnelFailed,
    HotspotBusy,
    BluetoothSetup,
    HotspotFailed,
    NoNetwork,
    Qr
};

struct Layout {
    float w = 0, h = 0;
    RectF logo, title, menu, seg, segLocal, segOffline, segAny, card, cardButton, link, url, copy, share, info, list, add,
        folder, third;
    bool compactLink = false; // Copy/Share shown as icons, leaving the address room on a narrow window
};

class Ui {
public:
    explicit Ui(Shell& shell) : shell_(shell) {}
    ~Ui() { shutdown(); }

    void start();    // once the window exists
    void shutdown(); // before the window goes away

    void paint(Gfx& g);
    void tick();
    void mouseMove(float x, float y);
    void mouseLeave();
    void mouseDown(float x, float y);
    void mouseUp(float x, float y);
    void wheel(float lines); // positive scrolls toward the top
    bool wantsPointer() const {
        return (hover_.hit != Hit::None && hover_.hit != Hit::Row) ||
               (hover_.hit == Hit::Row && hover_.index < (int)received_.size());
    }

    void setDragOver(bool on);
    void addPaths(const std::vector<std::string>& list);
    void addText(const std::string& text);
    void paste();
    void browse(bool folders);
    void copyLink();
    void shareLink();
    void refreshNetwork();

private:
    struct Speed {
        uint64_t sent = 0, tick = 0;
        double bps = 0;
    };

    void post(std::function<void()> fn);
    void invalidate() { shell_.invalidate(); }
    void rebuild();
    void onBuilt(uint64_t seq, std::shared_ptr<Bundle> b);
    void removeRow(int index);
    void clearAll();
    void setMode(int m);
    void newLink();
    void rotateReceiveCode();
    std::string shareUrl() const;
    void refreshQr();
    void showMenu();
    void onServer(HttpServer::Event ev);
    void onReceived();
    void updateAnimation();
    void saveSettings();
    void click(const HitTarget& t);
    bool sharing() const { return !paths_.empty() || !texts_.empty(); }
    const ReceivedItem& receivedRow(int index) const { return received_[received_.size() - 1 - (size_t)index]; }

    Layout layout() const;
    CardState cardState() const;
    HitTarget hitTest(float x, float y) const;
    int rowCount() const { return (int)(received_.size() + paths_.size() + texts_.size()); }
    float maxScroll() const;

    Shell& shell_;
    std::shared_ptr<bool> alive_ = std::make_shared<bool>(true);
    bool started_ = false;

    std::vector<std::string> paths_, texts_;
    std::vector<ReceivedItem> received_; // oldest first; shown newest first
    std::shared_ptr<Bundle> bundle_;
    uint64_t buildSeq_ = 0;
    bool building_ = false;
    std::string inbox_;

    HttpServer server_;
    std::unique_ptr<Tunnel> tunnel_;
    std::unique_ptr<plat::Hotspot> hotspot_;
    std::vector<plat::LanAddr> addrs_;
    int addrIndex_ = 0;
    int mode_ = 0; // 0 = same Wi-Fi, 1 = anywhere, 2 = offline hotspot
    std::string qrText_;
    std::string receiveCode_;
    qr::Code qr_;

    bool topmost_ = false, burnAfter_ = false, autoAnywhere_ = false;

    HitTarget hover_, pressed_;
    float scroll_ = 0, spin_ = 0;
    bool dragOver_ = false, animating_ = false;
    uint64_t copiedAt_ = 0, visitAt_ = 0, doneAt_ = 0;
    uint64_t copiedRowId_ = 0, copiedRowAt_ = 0;
    std::string visitDevice_, doneText_;
    std::vector<TransferInfo> transfers_;
    std::map<uint64_t, Speed> speeds_;
    std::set<uint64_t> ended_;
};
