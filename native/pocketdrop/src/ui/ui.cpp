#include "ui.h"
#include <algorithm>
#include <cmath>
#include <cstring>

namespace {

const uint32_t BG = 0x0F0F13, SURF = 0x19191F, SURF2 = 0x23232B, SURF3 = 0x2E2E38, TEXT = 0xF3F3F6, MUTE = 0x9B9BA8,
               FAINT = 0x6B6B78, ACC = 0x8B7CFF, ACCHI = 0x9D90FF, ACCTXT = 0xC4BBFF, ACC2 = 0x4FC3F7,
               GREEN = 0x3ECF8E, RED = 0xFF6B6B, AMBER = 0xF5B84B, WHITE = 0xFFFFFF;
const float M = 16.0f, ROW = 44.0f;

enum MenuId {
    ID_ADD_FILES = 100,
    ID_ADD_FOLDER,
    ID_PASTE,
    ID_COPY,
    ID_OPEN_BROWSER,
    ID_NEW_LINK,
    ID_BURN,
    ID_AUTO_ANY,
    ID_WARN_ANY,
    ID_TOPMOST,
    ID_STOP_TUNNEL,
    ID_ABOUT,
    ID_OPEN_INBOX,
    ID_CHANGE_INBOX,
    ID_ADDR_BASE = 500,
};

#ifdef __APPLE__
const char* KEY_OPEN = "", *KEY_PASTE = "", *KEY_COPY = "";
#else
const char* KEY_OPEN = "\tCtrl+O", *KEY_PASTE = "\tCtrl+V", *KEY_COPY = "\tCtrl+C";
#endif

std::string plural(uint64_t n, const char* word) { return std::to_string(n) + " " + word + (n == 1 ? "" : "s"); }
std::string sizeStr(uint64_t b) { return util::format_size(b); }
const char* DOT = "  \xC2\xB7  "; // " · "

// How an icon + label is drawn in a box that may be too narrow for it, largest first.
enum class Fit { Full, Small, Label, Icon };

// The largest fit every label reaches within `room`. Full is icon + BodyBold label,
// Small a slightly smaller icon + SmallBold label.
Fit fitContent(Gfx& g, float room, const std::vector<const char*>& labels, float icon, float gap) {
    float body = 0, small = 0;
    for (const char* label : labels) {
        body = std::max(body, g.measure(label, Font::BodyBold));
        small = std::max(small, g.measure(label, Font::SmallBold));
    }
    if (icon + gap + body <= room) return Fit::Full;
    if (icon - 2 + gap - 1 + small <= room) return Fit::Small;
    if (small <= room) return Fit::Label;
    return Fit::Icon;
}

void drawFitted(Gfx& g, Fit fit, const RectF& r, Icon ic, const char* label, float icon, float gap, float stroke,
                Color col) {
    float cy = (r.top + r.bottom) / 2;
    if (fit == Fit::Icon) {
        float x = std::floor((r.left + r.right - icon) / 2);
        g.icon(ic, rc(x, cy - icon / 2, x + icon, cy + icon / 2), col, stroke);
        return;
    }
    Font font = fit == Fit::Full ? Font::BodyBold : Font::SmallBold;
    float lw = g.measure(label, font);
    if (fit == Fit::Label) {
        g.text(label, rc(r.left, r.top, r.right, r.bottom), font, col, Align::Center);
        return;
    }
    if (fit == Fit::Small) {
        icon -= 2;
        gap -= 1;
    }
    float total = icon + gap + lw, x = std::floor((r.left + r.right - total) / 2);
    g.icon(ic, rc(x, cy - icon / 2, x + icon, cy + icon / 2), col, stroke);
    g.text(label, rc(x + icon + gap, r.top, x + total + 4, r.bottom), font, col);
}

bool copyQrImage(Shell& shell, const qr::Code& code) {
    if (!code.size) return false;
    constexpr int quiet = 4;
    int scale = std::max(4, 512 / (code.size + quiet * 2));
    int side = (code.size + quiet * 2) * scale;
    std::vector<uint8_t> rgba((size_t)side * side * 4, 255);
    for (int y = 0; y < code.size; y++) {
        for (int x = 0; x < code.size; x++) {
            if (!code.dark(x, y)) continue;
            int left = (x + quiet) * scale, top = (y + quiet) * scale;
            for (int py = top; py < top + scale; py++) {
                uint8_t* row = rgba.data() + ((size_t)py * side + left) * 4;
                for (int px = 0; px < scale; px++, row += 4) row[0] = row[1] = row[2] = 0;
            }
        }
    }
    return shell.copyImage(rgba, side, side);
}

size_t utf8Length(const std::string& s) {
    size_t n = 0;
    for (unsigned char c : s) n += (c & 0xC0) != 0x80;
    return n;
}

std::string firstLine(const std::string& t) {
    size_t a = t.find_first_not_of(" \t\r\n");
    if (a == std::string::npos) return {};
    size_t e = t.find_first_of("\r\n", a);
    std::string s = t.substr(a, e == std::string::npos ? std::string::npos : e - a);
    if (s.size() > 240) {
        size_t cut = 240;
        while (cut > 0 && ((unsigned char)s[cut] & 0xC0) == 0x80) cut--;
        s.resize(cut);
    }
    return s;
}

// file:// URL for a local folder, understood by every platform's openUrl.
std::string fileUrl(const std::string& path) {
    static const char* hex = "0123456789ABCDEF";
    std::string p = path;
    std::replace(p.begin(), p.end(), '\\', '/');
    std::string out = p.rfind('/', 0) == 0 ? "file://" : "file:///";
    for (unsigned char c : p) {
        if (isalnum(c) || strchr("/-_.~:", c)) out.push_back((char)c);
        else {
            out.push_back('%');
            out.push_back(hex[c >> 4]);
            out.push_back(hex[c & 15]);
        }
    }
    return out;
}

void cleanOldPastes() {
    std::string dir = util::path_join(plat::app_data_dir(), "Pasted");
    int64_t now = plat::unix_time();
    for (const auto& e : plat::list_dir(dir))
        if (!e.st.isDir && now - e.st.mtime > 7 * 24 * 3600) plat::file_remove(util::path_join(dir, e.name));
}

} // namespace

// ---------------------------------------------------------------- lifecycle

void Ui::post(std::function<void()> fn) {
    std::weak_ptr<bool> alive = alive_;
    shell_.post([alive, fn = std::move(fn)] {
        if (alive.lock()) fn();
    });
}

void Ui::start() {
    started_ = true;
    topmost_ = shell_.loadSetting("Topmost", 0) != 0;
    burnAfter_ = shell_.loadSetting("BurnAfter", 0) != 0;
    autoAnywhere_ = shell_.loadSetting("AutoAnywhere", 0) != 0;
    inbox_ = shell_.loadString("InboxDir", util::path_join(plat::downloads_dir(), "PocketDrop"));

    tunnel_ = std::make_unique<Tunnel>([this] { post([this] { refreshQr(); updateAnimation(); }); });
    hotspot_ = std::make_unique<plat::Hotspot>([this] {
        post([this] {
            refreshNetwork();
            updateAnimation();
        });
    });
    int savedMode = shell_.loadSetting("Mode", 0);
    if (savedMode == 1 && !shell_.loadSetting("AnywhereRiskDismissed", 0)) {
        bool dismiss = false;
        bool proceed = shell_.confirmAnywhereRisk(dismiss);
        if (!proceed) savedMode = 0;
        if (proceed && dismiss) shell_.saveSetting("AnywhereRiskDismissed", 1);
    }
    mode_ = savedMode == 1 ? 1 : (savedMode == 2 && hotspot_->supported() ? 2 : 0);
    server_.onEvent = [this](HttpServer::Event e) { post([this, e] { onServer(e); }); };
    server_.setInbox(inbox_);
    if (!server_.start(DEFAULT_PORT)) shell_.alert("PocketDrop", "PocketDrop couldn't start its local server.");
    addrs_ = plat::lan_addresses();
    server_.publish(nullptr);
    if (topmost_) shell_.setTopmost(true);
    if (mode_ == 1) rotateReceiveCode();
    if (mode_ == 1 || (autoAnywhere_ && !Tunnel::findBinary().empty())) tunnel_->start(server_.port(), false);
    if (mode_ == 2) hotspot_->start();
    std::thread(cleanOldPastes).detach();
    refreshQr();
    updateAnimation();
}

void Ui::shutdown() {
    if (!started_) return;
    started_ = false;
    *alive_ = false;
    alive_ = std::make_shared<bool>(false);
    saveSettings();
    shell_.setAnimating(false);
    if (hotspot_) hotspot_->stop();
    if (tunnel_) tunnel_->stop();
    server_.stop();
    bundle_.reset();
}

void Ui::saveSettings() {
    shell_.saveSetting("Mode", mode_);
    shell_.saveSetting("Topmost", topmost_);
    shell_.saveSetting("BurnAfter", burnAfter_);
    shell_.saveSetting("AutoAnywhere", autoAnywhere_);
}

// ---------------------------------------------------------------- content

void Ui::addPaths(const std::vector<std::string>& list) {
    bool changed = false;
    for (const auto& raw : list) {
        std::string p = shell_.cleanPath(raw);
        if (p.empty()) continue;
        std::string lp = util::lower(p);
        if (std::any_of(paths_.begin(), paths_.end(), [&](const std::string& q) { return util::lower(q) == lp; }))
            continue;
        paths_.push_back(p);
        changed = true;
    }
    if (changed) rebuild();
}

void Ui::addText(const std::string& text) {
    if (text.find_first_not_of(" \t\r\n") == std::string::npos) return;
    if (std::find(texts_.begin(), texts_.end(), text) != texts_.end()) return;
    texts_.push_back(text);
    rebuild();
}

void Ui::removeRow(int index) {
    if (index < 0 || index >= rowCount()) return;
    int nr = (int)received_.size(), np = (int)paths_.size();
    hover_ = {};
    if (index < nr) {
        received_.erase(received_.end() - 1 - index);
    } else if (index < nr + np) {
        paths_.erase(paths_.begin() + (index - nr));
        rebuild();
    } else {
        texts_.erase(texts_.begin() + (index - nr - np));
        rebuild();
    }
    scroll_ = std::min(scroll_, maxScroll());
    invalidate();
}

void Ui::clearAll() {
    paths_.clear();
    texts_.clear();
    received_.clear();
    scroll_ = 0;
    rebuild();
}

void Ui::rebuild() {
    uint64_t seq = ++buildSeq_;
    building_ = true;
    std::weak_ptr<bool> alive = alive_;
    Shell* shell = &shell_;
    std::thread([this, seq, alive, shell, p = paths_, t = texts_, prev = bundle_] {
        std::shared_ptr<Bundle> b = build_bundle(p, t, prev.get());
        shell->post([this, seq, alive, b] {
            if (alive.lock()) onBuilt(seq, b);
        });
    }).detach();
    updateAnimation();
    invalidate();
}

void Ui::onBuilt(uint64_t seq, std::shared_ptr<Bundle> b) {
    if (seq != buildSeq_) return;
    building_ = false;
    bundle_ = std::move(b);
    bundle_->startPrepare([this] { post([this] { updateAnimation(); invalidate(); }); });
    server_.publish(bundle_);
    refreshQr();
    updateAnimation();
    invalidate();
}

void Ui::paste() {
    std::vector<std::string> list;
    std::string text;
    shell_.readClipboard(list, text);
    if (!list.empty()) addPaths(list);
    else if (!text.empty()) addText(text);
}

void Ui::browse(bool folders) {
    shell_.browse(folders, [this, alive = std::weak_ptr<bool>(alive_)](const std::vector<std::string>& list) {
        if (alive.lock()) addPaths(list);
    });
}

void Ui::setDragOver(bool on) {
    dragOver_ = on;
    invalidate();
}

// ---------------------------------------------------------------- sharing

void Ui::setMode(int m) {
    if (m == 2 && (!hotspot_ || !hotspot_->supported())) return;
    if (m == 1 && mode_ != 1 && !shell_.loadSetting("AnywhereRiskDismissed", 0)) {
        bool dismiss = false;
        if (!shell_.confirmAnywhereRisk(dismiss)) return;
        if (dismiss) shell_.saveSetting("AnywhereRiskDismissed", 1);
    }
    if (mode_ == 2 && m != 2) hotspot_->stop();
    if (mode_ == 1 && m != 1) {
        server_.newLink();
        if (!autoAnywhere_) tunnel_->stop();
        receiveCode_.clear();
        server_.setReceiveCode({});
    }
    bool enteringAnywhere = m == 1 && mode_ != 1;
    mode_ = m;
    if (m == 1) {
        if (enteringAnywhere) {
            server_.newLink();
            rotateReceiveCode();
        }
        auto st = tunnel_->state();
        if (st == Tunnel::State::Off || st == Tunnel::State::Failed || st == Tunnel::State::Missing)
            tunnel_->start(server_.port(), false);
    } else if (m == 2) {
        auto st = hotspot_->state();
        if (st == plat::HotspotState::Off || st == plat::HotspotState::Failed) hotspot_->start();
    }
    saveSettings();
    refreshQr();
    updateAnimation();
}

void Ui::newLink() {
    server_.newLink();
    if (mode_ == 1) rotateReceiveCode();
    refreshQr();
}

void Ui::rotateReceiveCode() {
    static constexpr char alphabet[] = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    uint8_t bytes[8];
    plat::random_bytes(bytes, sizeof bytes);
    receiveCode_.clear();
    for (uint8_t byte : bytes) receiveCode_.push_back(alphabet[byte % (sizeof alphabet - 1)]);
    server_.setReceiveCode(receiveCode_);
}

std::string Ui::shareUrl() const {
    std::string token = server_.token();
    if (token.empty() || !server_.port()) return {};
    if (mode_ == 1) {
        if (tunnel_->state() != Tunnel::State::Online) return {};
        std::string u = tunnel_->url();
        return u.empty() ? std::string() : u + "/" + token + "/";
    }
    if (mode_ == 2) {
        if (hotspot_->state() != plat::HotspotState::On || !hotspot_->connected()) return {};
        std::string ip = hotspot_->local_ip();
        if (ip.empty()) return {};
        return "http://" + ip + ":" + std::to_string(server_.port()) + "/" + token + "/";
    }
    if (addrs_.empty()) return {};
    return "http://" + addrs_[(size_t)addrIndex_].ip + ":" + std::to_string(server_.port()) + "/" + token + "/";
}

void Ui::refreshQr() {
    std::string s;
    if (mode_ == 2 && hotspot_->state() == plat::HotspotState::On && !hotspot_->connected())
        s = qr::wifi(hotspot_->ssid(), hotspot_->passphrase());
    else
        s = shareUrl();
    if (s != qrText_) {
        qrText_ = s;
        qr_ = s.empty() ? qr::Code{} : qr::encode(s, qr::Ecc::M);
    }
    invalidate();
}

void Ui::refreshNetwork() {
    addrs_ = plat::lan_addresses();
    if (addrIndex_ >= (int)addrs_.size()) addrIndex_ = 0;
    refreshQr();
}

void Ui::copyLink() {
    if (mode_ == 1 && copyQrImage(shell_, qr_)) {
        copiedAt_ = plat::tick_ms();
        invalidate();
        return;
    }
    std::string value = mode_ == 2 && hotspot_->state() == plat::HotspotState::On && !hotspot_->connected()
                            ? hotspot_->passphrase()
                            : shareUrl();
    if (value.empty()) return;
    shell_.copyText(value);
    copiedAt_ = plat::tick_ms();
    invalidate();
}

void Ui::shareLink() {
    std::string url = shareUrl();
    if (!url.empty()) shell_.shareText("PocketDrop link", url);
}

void Ui::onServer(HttpServer::Event ev) {
    uint64_t now = plat::tick_ms();
    switch (ev) {
    case HttpServer::Event::Visit:
        visitAt_ = now;
        visitDevice_ = server_.lastDevice();
        break;
    case HttpServer::Event::TransferStart: transfers_ = server_.transfers(); break;
    case HttpServer::Event::TransferEnd: {
        transfers_ = server_.transfers();
        bool burn = false;
        for (const auto& t : transfers_) {
            if (!t.done || ended_.count(t.id)) continue;
            ended_.insert(t.id);
            if (!t.ok || t.incoming) continue; // arrivals are announced by Event::Received
            doneText_ = "Sent " + t.name + " to " + t.device;
            doneAt_ = now;
            burn = burnAfter_;
        }
        if (burn) newLink();
        break;
    }
    case HttpServer::Event::Received: onReceived(); break;
    }
    updateAnimation();
    invalidate();
}

void Ui::onReceived() {
    auto items = server_.takeReceived();
    if (items.empty()) return;
    for (auto& it : items) {
        doneText_ = it.isText ? "Received a note from " + it.device : "Received " + it.name + " from " + it.device;
        received_.push_back(std::move(it));
    }
    doneAt_ = plat::tick_ms();
    scroll_ = 0;
    shell_.attention();
}

void Ui::tick() {
    uint64_t now = plat::tick_ms();
    if (mode_ == 2 && hotspot_) hotspot_->poll();
    if (server_.maintain(sharing())) refreshQr();
    spin_ += 0.2f;
    if (spin_ > 6.2832f) spin_ -= 6.2832f;
    bool anyActive = std::any_of(transfers_.begin(), transfers_.end(), [](const TransferInfo& t) { return !t.done; });
    if (anyActive) {
        transfers_ = server_.transfers();
        for (const auto& t : transfers_) {
            if (t.done) continue;
            Speed& sp = speeds_[t.id];
            if (!sp.tick) {
                sp.sent = t.sent;
                sp.tick = now;
            } else if (now - sp.tick >= 400) {
                double inst = (double)(t.sent - sp.sent) * 1000.0 / (double)(now - sp.tick);
                sp.bps = sp.bps > 0 ? sp.bps * 0.6 + inst * 0.4 : inst;
                sp.sent = t.sent;
                sp.tick = now;
            }
        }
    }
    bool recent = (copiedAt_ && now - copiedAt_ < 2500) || (doneAt_ && now - doneAt_ < 7000) ||
                  (visitAt_ && now - visitAt_ < 125000) || (copiedRowAt_ && now - copiedRowAt_ < 2500);
    updateAnimation();
    if (animating_ || recent) invalidate();
}

void Ui::updateAnimation() {
    auto st = tunnel_ ? tunnel_->state() : Tunnel::State::Off;
    auto hotspotState = hotspot_ ? hotspot_->state() : plat::HotspotState::Off;
    bool busy = building_ || (bundle_ && !bundle_->ready) || st == Tunnel::State::Starting ||
                st == Tunnel::State::Downloading || hotspotState == plat::HotspotState::Starting ||
                std::any_of(transfers_.begin(), transfers_.end(), [](const TransferInfo& t) { return !t.done; });
    if (busy != animating_) {
        animating_ = busy;
        shell_.setAnimating(busy);
        invalidate();
    }
}

// ---------------------------------------------------------------- input

void Ui::mouseMove(float x, float y) {
    HitTarget t = hitTest(x, y);
    if (!(t == hover_)) {
        hover_ = t;
        invalidate();
    }
}

void Ui::mouseLeave() {
    hover_ = {};
    invalidate();
}

void Ui::mouseDown(float x, float y) { pressed_ = hitTest(x, y); }

void Ui::mouseUp(float x, float y) {
    HitTarget t = hitTest(x, y), p = pressed_;
    pressed_ = {};
    if (t == p && t.hit != Hit::None) click(t);
    invalidate();
}

void Ui::wheel(float lines) {
    scroll_ = std::clamp(scroll_ - lines * ROW, 0.0f, maxScroll());
    invalidate();
}

void Ui::click(const HitTarget& t) {
    switch (t.hit) {
    case Hit::Menu: showMenu(); break;
    case Hit::ModeLocal: setMode(0); break;
    case Hit::ModeOffline: setMode(2); break;
    case Hit::ModeAnywhere: setMode(1); break;
    case Hit::CardButton:
        if (cardState() == CardState::NoNetwork) setMode(2);
        else if (cardState() == CardState::BluetoothSetup) shell_.openBluetoothSetup();
        else if (mode_ == 2) hotspot_->start();
        else tunnel_->start(server_.port(), cardState() == CardState::TunnelMissing);
        updateAnimation();
        break;
    case Hit::Url:
        if (std::string url = shareUrl(); !url.empty()) shell_.openUrl(url);
        break;
    case Hit::Copy: copyLink(); break;
    case Hit::Share: shareLink(); break;
    case Hit::AddFiles: browse(false); break;
    case Hit::AddFolder: browse(true); break;
    case Hit::Third:
        if (rowCount()) clearAll();
        else paste();
        break;
    case Hit::Row:
        if (t.index < (int)received_.size()) {
            const ReceivedItem& it = receivedRow(t.index);
            if (it.isText) {
                shell_.copyText(it.text);
                copiedRowId_ = it.id;
                copiedRowAt_ = plat::tick_ms();
            } else if (plat::file_stat(it.path).exists) {
                shell_.revealPath(it.path);
            }
        }
        break;
    case Hit::RowRemove: removeRow(t.index); break;
    default: break;
    }
    invalidate();
}

void Ui::showMenu() {
    auto item = [](int id, std::string label, bool checked = false, bool enabled = true) {
        MenuItem m;
        m.id = id;
        m.label = std::move(label);
        m.checked = checked;
        m.enabled = enabled;
        return m;
    };
    MenuItem sep;
    sep.separator = true;
    bool hasLink = !shareUrl().empty();
    bool joiningHotspot = mode_ == 2 && hotspot_->state() == plat::HotspotState::On && !hotspot_->connected();

    std::vector<MenuItem> items = {
        item(ID_ADD_FILES, std::string("Add files\xE2\x80\xA6") + KEY_OPEN),
        item(ID_ADD_FOLDER, "Add folder\xE2\x80\xA6"),
        item(ID_PASTE, std::string("Paste") + KEY_PASTE),
        sep,
        item(ID_COPY, std::string(joiningHotspot ? "Copy hotspot password" : mode_ == 1 ? "Copy QR code" : "Copy link") + KEY_COPY, false,
             hasLink || joiningHotspot),
        item(ID_OPEN_BROWSER, "Open link in browser", false, hasLink),
        item(ID_NEW_LINK, "New link (old one stops working)"),
    };
    MenuItem net;
    net.label = "Network address";
    if (addrs_.empty()) net.submenu.push_back(item(0, "No networks found", false, false));
    for (size_t i = 0; i < addrs_.size() && i < 32; i++)
        net.submenu.push_back(item(ID_ADDR_BASE + (int)i, addrs_[i].ip + "   " + addrs_[i].adapter, (int)i == addrIndex_));
    items.push_back(net);
    items.push_back(sep);
    items.push_back(item(ID_OPEN_INBOX, "Open received files folder"));
    items.push_back(item(ID_CHANGE_INBOX, "Change received files folder\xE2\x80\xA6"));
    items.push_back(sep);
    items.push_back(item(ID_BURN, "Stop sharing after a download", burnAfter_));
    items.push_back(item(ID_AUTO_ANY, "Keep Anywhere tunnel ready", autoAnywhere_));
    items.push_back(item(ID_WARN_ANY, "Warn before using Anywhere", !shell_.loadSetting("AnywhereRiskDismissed", 0)));
    items.push_back(item(ID_TOPMOST, "Always on top", topmost_));
    for (auto& extra : shell_.extraMenuItems()) items.push_back(extra);
    if (tunnel_->state() != Tunnel::State::Off && tunnel_->state() != Tunnel::State::Missing)
        items.push_back(item(ID_STOP_TUNNEL, "Close Anywhere tunnel"));
    items.push_back(sep);
    items.push_back(item(ID_ABOUT, "About PocketDrop"));

    Layout L = layout();
    int cmd = shell_.popupMenu(items, L.menu.right, L.menu.bottom + 4);
    switch (cmd) {
    case 0: break;
    case ID_ADD_FILES: browse(false); break;
    case ID_ADD_FOLDER: browse(true); break;
    case ID_PASTE: paste(); break;
    case ID_COPY: copyLink(); break;
    case ID_OPEN_BROWSER:
        if (hasLink) shell_.openUrl(shareUrl());
        break;
    case ID_NEW_LINK: newLink(); break;
    case ID_OPEN_INBOX:
        plat::make_dir(inbox_);
        shell_.openUrl(fileUrl(inbox_));
        break;
    case ID_CHANGE_INBOX:
        shell_.chooseFolder("Choose where received files are saved",
                            [this, alive = std::weak_ptr<bool>(alive_)](const std::string& dir) {
                                if (!alive.lock() || dir.empty()) return;
                                inbox_ = dir;
                                shell_.saveString("InboxDir", dir);
                                server_.setInbox(dir);
                            });
        break;
    case ID_BURN: burnAfter_ = !burnAfter_; break;
    case ID_AUTO_ANY:
        autoAnywhere_ = !autoAnywhere_;
        if (autoAnywhere_ && tunnel_->state() == Tunnel::State::Off && !Tunnel::findBinary().empty())
            tunnel_->start(server_.port(), false);
        break;
    case ID_WARN_ANY:
        shell_.saveSetting("AnywhereRiskDismissed", shell_.loadSetting("AnywhereRiskDismissed", 0) ? 0 : 1);
        break;
    case ID_TOPMOST:
        topmost_ = !topmost_;
        shell_.setTopmost(topmost_);
        break;
    case ID_STOP_TUNNEL:
        tunnel_->stop();
        if (mode_ == 1) setMode(0);
        break;
    case ID_ABOUT:
        shell_.alert("About PocketDrop",
                     "PocketDrop 2.0\n\nScan the QR code to move files and text between this computer and your phone, "
                     "in either direction. No app or account needed on the phone.\n\nSame Wi-Fi: direct transfer over "
                     "your local network (port " +
                         std::to_string(server_.port()) +
                      ").\nOffline (Windows): PocketDrop creates a temporary Wi-Fi network, or uses Bluetooth "
                      "tethering when the PC has no Wi-Fi adapter.\nAnywhere: a Cloudflare quick tunnel (cloudflared) so "
                      "phones on mobile data can connect too. Phone uploads require the receive code shown on this "
                      "computer.\n\nReceived files are saved to:\n" +
                         inbox_ +
                         "\n\nLinks use a random 128-bit token, refresh automatically while unused, and stop working "
                         "when PocketDrop closes.");
        break;
    default:
        if (cmd >= ID_ADDR_BASE && cmd < ID_ADDR_BASE + (int)addrs_.size()) {
            addrIndex_ = cmd - ID_ADDR_BASE;
            refreshQr();
        } else if (cmd >= 1000) {
            shell_.handleExtraMenu(cmd);
        }
        break;
    }
    saveSettings();
    updateAnimation();
    invalidate();
}

// ---------------------------------------------------------------- layout

Layout Ui::layout() const {
    Layout L;
    shell_.clientSize(L.w, L.h);
    float w = L.w;
    L.logo = rc(M, 16, M + 26, 42);
    L.title = rc(M + 36, 12, w - M - 40, 46);
    L.menu = rc(w - M - 32, 13, w - M, 45);
    L.seg = rc(M, 56, w - M, 92);
    if (hotspot_ && hotspot_->supported()) {
        float third = std::floor((L.seg.right - L.seg.left) / 3);
        L.segLocal = rc(L.seg.left, L.seg.top, L.seg.left + third, L.seg.bottom);
        L.segAny = rc(L.segLocal.right, L.seg.top, L.segLocal.right + third, L.seg.bottom);
        L.segOffline = rc(L.segAny.right, L.seg.top, L.seg.right, L.seg.bottom);
    } else {
        float mid = std::floor((L.seg.left + L.seg.right) / 2);
        L.segLocal = rc(L.seg.left, L.seg.top, mid, L.seg.bottom);
        L.segAny = rc(mid, L.seg.top, L.seg.right, L.seg.bottom);
    }

    const float cardTop = 104;
    float size = w - 2 * M;
    float reserved = 12 + 36 + 8 + 22 + 8 + ROW * 2 + 10 + 36 + 12;
    size = std::min(size, std::max(180.0f, L.h - cardTop - reserved));
    float cardLeft = std::floor((w - size) / 2);
    L.card = rc(cardLeft, cardTop, cardLeft + size, cardTop + size);
    float cx = (L.card.left + L.card.right) / 2, by = L.card.top + size * 0.72f;
    float half = std::min(108.0f, std::floor((size - 28) / 2));
    L.cardButton = rc(cx - half, by, cx + half, by + 38);

    float y = L.card.bottom + 12;
    L.link = rc(M, y, w - M, y + 36);
    // Labelled Copy/Share while the address keeps LINK_ROOM to itself, icons after that.
    const float LINK_ROOM = 140;
    float actions = mode_ == 1 ? 156.0f : 76.0f;
    L.compactLink = (L.link.right - L.link.left) - actions - 20 < LINK_ROOM;
    float aw = L.compactLink ? 30.0f : 72.0f, agap = L.compactLink ? 6.0f : 8.0f;
    L.copy = rc(L.link.right - 4 - aw, y + 4, L.link.right - 4, y + 32);
    if (mode_ == 1) L.share = rc(L.copy.left - agap - aw, y + 4, L.copy.left - agap, y + 32);
    L.url = rc(L.link.left + 12, y, (mode_ == 1 ? L.share.left : L.copy.left) - 8, y + 36);
    y += 44;
    L.info = rc(M + 2, y, w - M - 2, y + 22);
    y += 30;
    float bottomTop = L.h - 48;
    L.list = rc(M, y, w - M, bottomTop - 10);
    float bw = w - 2 * M, gap = 8, addW = std::floor(bw * 0.40f), other = std::floor((bw - addW - 2 * gap) / 2);
    L.add = rc(M, bottomTop, M + addW, bottomTop + 36);
    L.folder = rc(L.add.right + gap, bottomTop, L.add.right + gap + other, bottomTop + 36);
    L.third = rc(L.folder.right + gap, bottomTop, w - M, bottomTop + 36);
    return L;
}

CardState Ui::cardState() const {
    if (dragOver_) return CardState::DragOver;
    if (mode_ == 1) {
        switch (tunnel_->state()) {
        case Tunnel::State::Online: break;
        case Tunnel::State::Missing: return CardState::TunnelMissing;
        case Tunnel::State::Downloading: return CardState::TunnelDownloading;
        case Tunnel::State::Failed: return CardState::TunnelFailed;
        default: return CardState::TunnelBusy;
        }
    } else if (mode_ == 2) {
        switch (hotspot_->state()) {
        case plat::HotspotState::On: break;
        case plat::HotspotState::Waiting: return CardState::BluetoothSetup;
        case plat::HotspotState::Failed:
        case plat::HotspotState::Unavailable: return CardState::HotspotFailed;
        default: return CardState::HotspotBusy;
        }
    } else if (addrs_.empty()) {
        return CardState::NoNetwork;
    }
    if (qr_.size) return CardState::Qr;
    return mode_ == 2 ? CardState::HotspotBusy : CardState::TunnelBusy;
}

float Ui::maxScroll() const {
    Layout L = layout();
    return std::max(0.0f, rowCount() * ROW - (L.list.bottom - L.list.top));
}

HitTarget Ui::hitTest(float x, float y) const {
    Layout L = layout();
    if (inside(L.menu, x, y)) return {Hit::Menu};
    if (inside(L.segLocal, x, y)) return {Hit::ModeLocal};
    if (hotspot_ && hotspot_->supported() && inside(L.segOffline, x, y)) return {Hit::ModeOffline};
    if (inside(L.segAny, x, y)) return {Hit::ModeAnywhere};
    if (inside(L.card, x, y)) {
        CardState cs = cardState();
        if ((cs == CardState::TunnelMissing || cs == CardState::TunnelFailed || cs == CardState::BluetoothSetup ||
             cs == CardState::HotspotFailed ||
             (cs == CardState::NoNetwork && hotspot_ && hotspot_->supported())) &&
            inside(L.cardButton, x, y))
            return {Hit::CardButton};
        return {};
    }
    if (!qrText_.empty() && inside(L.copy, x, y)) return {Hit::Copy};
    if (mode_ == 1 && !qrText_.empty() && inside(L.share, x, y)) return {Hit::Share};
    if (!shareUrl().empty() && inside(L.url, x, y)) return {Hit::Url};
    if (inside(L.list, x, y)) {
        int i = (int)std::floor((y - L.list.top + scroll_) / ROW);
        if (i < 0 || i >= rowCount()) return {};
        float top = L.list.top + i * ROW - scroll_;
        RectF rm = rc(L.list.right - 38, top + 8, L.list.right - 6, top + 36);
        return {inside(rm, x, y) ? Hit::RowRemove : Hit::Row, i};
    }
    if (inside(L.add, x, y)) return {Hit::AddFiles};
    if (inside(L.folder, x, y)) return {Hit::AddFolder};
    if (inside(L.third, x, y)) return {Hit::Third};
    return {};
}

// ---------------------------------------------------------------- painting

void Ui::paint(Gfx& g) {
    Layout L = layout();
    uint64_t now = plat::tick_ms();
    bool hotspotJoin = mode_ == 2 && hotspot_->state() == plat::HotspotState::On && !hotspot_->connected();
    auto hov = [&](Hit h) { return hover_.hit == h; };
    g.clear(rgb(BG));

    // ---- Header
    g.gradientRound(L.logo, 8, rgb(ACC), rgb(ACC2));
    g.icon(Icon::Drop, inset(L.logo, 5), rgb(WHITE), 2.6f);

    const TransferInfo* active = nullptr;
    for (const auto& t : transfers_)
        if (!t.done) active = &t;
    std::string status;
    uint32_t dot;
    if ((mode_ == 1 && tunnel_->state() == Tunnel::State::Failed) ||
        (mode_ == 2 && hotspot_->state() == plat::HotspotState::Failed)) {
        status = "Offline";
        dot = RED;
    } else if (mode_ == 2 && hotspot_->state() == plat::HotspotState::Waiting) {
        status = "Bluetooth setup";
        dot = AMBER;
    } else if (active) {
        status = active->incoming ? "Receiving" : "Sending";
        dot = active->incoming ? ACC2 : ACC;
    } else if (visitAt_ && now - visitAt_ < 120000) {
        status = visitDevice_ + " connected";
        dot = GREEN;
    } else if (!qrText_.empty()) {
        status = mode_ == 2 && !hotspot_->connected() ? "Hotspot ready" : "Ready";
        dot = GREEN;
    } else {
        status = "Idle";
        dot = FAINT;
    }
    // Narrow windows give up the title first, then the pill's words (keeping its dot),
    // so the pill never covers the logo.
    float tw = g.measure(status, Font::SmallBold), pillRight = L.menu.left - 8;
    bool pillWords = pillRight - tw - 30 >= L.logo.right + 8;
    RectF pill = pillWords ? rc(pillRight - tw - 30, 17, pillRight, 41) : rc(pillRight - 26, 17, pillRight, 41);
    bool pillShown = pill.left >= L.logo.right + 4;
    float titleRight = pillShown ? pill.left - 6 : L.menu.left - 6;
    if (L.title.left + g.measure("PocketDrop", Font::Title) <= titleRight)
        g.text("PocketDrop", rc(L.title.left, L.title.top, titleRight, L.title.bottom), Font::Title, rgb(TEXT));
    if (pillShown) {
        g.fillRound(pill, 12, rgb(SURF));
        g.fillCircle({pill.left + 13, 29}, 3.5f, rgb(dot));
        if (pillWords)
            g.text(status, rc(pill.left + 22, pill.top, pill.right - 8, pill.bottom), Font::SmallBold, rgb(MUTE));
    }
    if (hov(Hit::Menu)) g.fillRound(L.menu, 8, rgb(SURF2));
    g.icon(Icon::Dots, inset(L.menu, 7), rgb(hov(Hit::Menu) ? TEXT : MUTE));

    // ---- Mode switch
    g.fillRound(L.seg, 11, rgb(SURF));
    const RectF& selectedSegment = mode_ == 0 ? L.segLocal : mode_ == 2 ? L.segOffline : L.segAny;
    g.fillRound(inset(selectedSegment, 3), 8, rgb(SURF3));
    bool offlineShown = hotspot_ && hotspot_->supported();
    const Fit segFit = fitContent(g, L.segLocal.right - L.segLocal.left - 8,
                                  offlineShown ? std::vector<const char*>{"Same Wi-Fi", "Anywhere", "Offline"}
                                               : std::vector<const char*>{"Same Wi-Fi", "Anywhere"},
                                  16, 7);
    auto segment = [&](const RectF& r, Icon ic, const char* label, bool selected, bool hovered, uint32_t badge) {
        uint32_t col = selected ? TEXT : hovered ? 0xCFCFD8 : MUTE;
        drawFitted(g, segFit, r, ic, label, 16, 7, 2.0f, rgb(col));
        if (badge) g.fillCircle({r.right - 10, r.top + 9}, 3.2f, rgb(badge));
    };
    uint32_t anyBadge = 0;
    switch (tunnel_->state()) {
    case Tunnel::State::Online: anyBadge = GREEN; break;
    case Tunnel::State::Starting:
    case Tunnel::State::Downloading: anyBadge = AMBER; break;
    case Tunnel::State::Failed: anyBadge = RED; break;
    default: break;
    }
    segment(L.segLocal, Icon::Wifi, "Same Wi-Fi", mode_ == 0, hov(Hit::ModeLocal), 0);
    segment(L.segAny, Icon::Globe, "Anywhere", mode_ == 1, hov(Hit::ModeAnywhere), anyBadge);
    if (offlineShown)
        segment(L.segOffline, Icon::Phone, "Offline", mode_ == 2, hov(Hit::ModeOffline),
                mode_ == 2 && hotspot_->state() == plat::HotspotState::Failed
                    ? RED
                    : mode_ == 2 && hotspot_->state() == plat::HotspotState::Waiting ? AMBER : 0);

    // ---- Card
    const RectF& c = L.card;
    float cx = (c.left + c.right) / 2, cy = (c.top + c.bottom) / 2, cw = c.right - c.left;
    auto centered = [&](const std::string& s, float y, Font f, uint32_t col, float h = 22) {
        g.text(s, rc(c.left + 14, y, c.right - 14, y + h), f, rgb(col), Align::Center);
    };
    auto cardButton = [&](const std::string& label, bool primary) {
        bool h = hov(Hit::CardButton);
        g.fillRound(L.cardButton, 10, rgb(primary ? (h ? ACCHI : ACC) : (h ? SURF3 : SURF2)));
        g.text(label, L.cardButton, Font::BodyBold, rgb(primary ? WHITE : TEXT), Align::Center);
    };
    switch (cardState()) {
    case CardState::DragOver:
        g.fillRound(c, 20, rgb(ACC, 0.10f));
        g.strokeRound(c, 20, rgb(ACC), 2.0f, true);
        g.fillCircle({cx, cy - 30}, 36, rgb(ACC, 0.16f));
        g.icon(Icon::Drop, rc(cx - 18, cy - 48, cx + 18, cy - 12), rgb(ACCTXT), 2.0f);
        centered("Drop to share", cy + 20, Font::Big, TEXT, 28);
        break;
    case CardState::TunnelBusy:
        g.fillRound(c, 20, rgb(SURF));
        g.spinner({cx, cy - 26}, 18, spin_, rgb(ACC), 3.0f);
        centered("Opening secure tunnel\xE2\x80\xA6", cy + 14, Font::BodyBold, TEXT);
        centered("Usually takes a few seconds", cy + 38, Font::Small, MUTE);
        break;
    case CardState::TunnelMissing:
        g.fillRound(c, 20, rgb(SURF));
        g.fillCircle({cx, cy - 76}, 30, rgb(SURF2));
        g.icon(Icon::Globe, rc(cx - 15, cy - 91, cx + 15, cy - 61), rgb(ACC2), 1.9f);
        centered("Share beyond your Wi-Fi", cy - 30, Font::Big, TEXT, 28);
        centered("A free Cloudflare tunnel lets your phone", cy + 4, Font::Small, MUTE, 18);
        centered("connect over mobile data. No account needed.", cy + 22, Font::Small, MUTE, 18);
        cardButton(std::string("Enable") + DOT + "one-time 40 MB", true);
        break;
    case CardState::TunnelDownloading: {
        g.fillRound(c, 20, rgb(SURF));
        int p = std::clamp(tunnel_->progress(), 0, 100);
        centered("Downloading cloudflared\xE2\x80\xA6", cy - 34, Font::BodyBold, TEXT);
        RectF bar = rc(c.left + 52, cy - 3, c.right - 52, cy + 3);
        g.fillRound(bar, 3, rgb(SURF3));
        g.fillRound(rc(bar.left, bar.top, bar.left + (bar.right - bar.left) * p / 100.0f, bar.bottom), 3, rgb(ACC));
        centered(std::to_string(p) + "%", cy + 14, Font::Small, MUTE);
        break;
    }
    case CardState::TunnelFailed:
        g.fillRound(c, 20, rgb(SURF));
        g.fillCircle({cx, cy - 62}, 28, rgb(RED, 0.14f));
        g.icon(Icon::Close, rc(cx - 12, cy - 74, cx + 12, cy - 50), rgb(RED), 2.2f);
        centered("Couldn't open the tunnel", cy - 16, Font::BodyBold, TEXT);
        centered(tunnel_->message(), cy + 8, Font::Small, MUTE, 18);
        cardButton("Try again", false);
        break;
    case CardState::HotspotBusy:
        g.fillRound(c, 20, rgb(SURF));
        g.spinner({cx, cy - 26}, 18, spin_, rgb(ACC2), 3.0f);
        centered("Starting offline hotspot\xE2\x80\xA6", cy + 14, Font::BodyBold, TEXT);
        centered("No router or internet needed", cy + 38, Font::Small, MUTE);
        break;
    case CardState::BluetoothSetup:
        g.fillRound(c, 20, rgb(SURF));
        g.fillCircle({cx, cy - 76}, 30, rgb(ACC2, 0.14f));
        g.icon(Icon::Phone, rc(cx - 15, cy - 91, cx + 15, cy - 61), rgb(ACC2), 1.9f);
        centered("Connect your phone over Bluetooth", cy - 30, Font::Big, TEXT, 28);
        centered("Enable Bluetooth tethering or Personal Hotspot,", cy + 4, Font::Small, MUTE, 18);
        centered("then connect using Access point in Windows.", cy + 22, Font::Small, MUTE, 18);
        cardButton("Open Bluetooth setup", true);
        break;
    case CardState::HotspotFailed:
        g.fillRound(c, 20, rgb(SURF));
        g.fillCircle({cx, cy - 62}, 28, rgb(RED, 0.14f));
        g.icon(Icon::Close, rc(cx - 12, cy - 74, cx + 12, cy - 50), rgb(RED), 2.2f);
        centered("Couldn't start the hotspot", cy - 16, Font::BodyBold, TEXT);
        centered(hotspot_->message(), cy + 8, Font::Small, MUTE, 18);
        cardButton("Try again", false);
        break;
    case CardState::NoNetwork:
        g.fillRound(c, 20, rgb(SURF));
        g.icon(Icon::Wifi, rc(cx - 22, cy - 82, cx + 22, cy - 38), rgb(FAINT), 1.8f);
        centered("Not connected to a network", cy - 22, Font::BodyBold, TEXT);
        if (hotspot_ && hotspot_->supported()) {
            centered("Create one directly from this computer.", cy + 2, Font::Small, MUTE);
            cardButton("Start offline hotspot", true);
        } else {
            centered("Join Wi-Fi, or switch to Anywhere", cy + 2, Font::Small, MUTE);
        }
        break;
    case CardState::Qr: {
        g.fillRound(c, 20, rgb(WHITE));
        float scale = g.scale();
        float modPx = std::max(1.0f, std::floor(cw * scale / (float)(qr_.size + 7)));
        float mod = modPx / scale, total = mod * (float)qr_.size;
        float ox = std::round((cx - total / 2) * scale) / scale, oy = std::round((cy - total / 2) * scale) / scale;
        g.setAliased(true);
        for (int y = 0; y < qr_.size; y++) {
            for (int x = 0; x < qr_.size;) {
                if (!qr_.dark(x, y)) {
                    x++;
                    continue;
                }
                int x0 = x;
                while (x < qr_.size && qr_.dark(x, y)) x++;
                g.fillRect(rc(ox + x0 * mod, oy + y * mod, ox + x * mod, oy + (y + 1) * mod), rgb(0x111116));
            }
        }
        g.setAliased(false);
        break;
    }
    }

    // ---- Link row
    g.fillRound(L.link, 10, rgb(SURF));
    if (!qrText_.empty()) {
        std::string u = hotspotJoin ? hotspot_->ssid() + DOT + "Password " + hotspot_->passphrase() : shareUrl();
        for (const char* scheme : {"https://", "http://"})
            if (u.rfind(scheme, 0) == 0) u.erase(0, strlen(scheme));
        g.text(u, L.url, Font::Mono, rgb(hov(Hit::Url) ? TEXT : MUTE));
        auto linkAction = [&](const RectF& r, Hit hit, Icon icon, const char* label, bool done) {
            g.fillRound(r, 7, rgb(done ? GREEN : ACC, hover_.hit == hit ? 0.30f : 0.18f));
            float lw = L.compactLink ? 0 : g.measure(label, Font::SmallBold);
            float total = L.compactLink ? 14 : 14 + 5 + lw;
            float x = std::floor((r.left + r.right - total) / 2), ly = (r.top + r.bottom) / 2;
            uint32_t col = done ? GREEN : ACCTXT;
            g.icon(done ? Icon::Check : icon, rc(x, ly - 7, x + 14, ly + 7), rgb(col), 2.0f);
            if (!L.compactLink) g.text(label, rc(x + 19, r.top, x + total + 3, r.bottom), Font::SmallBold, rgb(col));
        };
        if (mode_ == 1) linkAction(L.share, Hit::Share, Icon::Share, "Share", false);
        bool copied = copiedAt_ && now - copiedAt_ < 1600;
        linkAction(L.copy, Hit::Copy, Icon::Copy, copied ? "Done" : mode_ == 1 ? "Copy QR" : "Copy", copied);
    } else {
        const char* msg = mode_ == 1   ? "Link appears once the tunnel is up"
                          : mode_ == 2 && hotspot_->uses_bluetooth() ? "Link appears once Bluetooth is connected"
                          : mode_ == 2 ? "Hotspot details appear once it is ready"
                                       : "No network address available";
        g.text(msg, rc(L.link.left + 12, L.link.top, L.link.right - 12, L.link.bottom), Font::Small, rgb(FAINT));
    }

    // ---- Info line
    const RectF& in = L.info;
    if (active) {
        double frac = active->total ? (double)active->sent / (double)active->total : 0.0;
        auto sp = speeds_.find(active->id);
        std::string right = sp != speeds_.end() && sp->second.bps > 0 ? sizeStr((uint64_t)sp->second.bps) + "/s" : "";
        float rw = g.measure(right, Font::Small);
        std::string left = std::to_string((int)(frac * 100)) + "%" + DOT + active->name +
                           (active->incoming ? "  \xE2\x86\x90  " : "  \xE2\x86\x92  ") + active->device;
        g.text(left, rc(in.left, in.top, in.right - rw - 10, in.bottom), Font::SmallBold, rgb(TEXT));
        g.text(right, in, Font::Small, rgb(MUTE), Align::Right);
        RectF bar = rc(in.left, in.bottom + 2, in.right, in.bottom + 4);
        g.fillRound(bar, 1, rgb(SURF3));
        g.fillRound(rc(bar.left, bar.top, bar.left + (float)((bar.right - bar.left) * frac), bar.bottom), 1,
                    rgb(active->incoming ? ACC2 : ACC));
    } else if (mode_ == 1 && !receiveCode_.empty()) {
        std::string code = receiveCode_.substr(0, 4) + "-" + receiveCode_.substr(4);
        g.text("Receive code  " + code + "  \xC2\xB7  required for phone uploads", in, Font::SmallBold, rgb(ACCTXT),
               Align::Center);
    } else if (doneAt_ && now - doneAt_ < 6000) {
        g.icon(Icon::Check, rc(in.left, in.top + 3, in.left + 16, in.top + 19), rgb(GREEN), 2.2f);
        g.text(doneText_, rc(in.left + 22, in.top, in.right, in.bottom), Font::SmallBold, rgb(GREEN));
    } else if (hotspotJoin) {
        g.text("Scan to join  \xC2\xB7  scan again when connected", in, Font::Small, rgb(FAINT), Align::Center);
    } else if (mode_ == 2 && hotspot_->uses_bluetooth() && hotspot_->state() == plat::HotspotState::Waiting) {
        g.text("Waiting for your phone  \xC2\xB7  Bluetooth is slower for large files", in, Font::Small, rgb(FAINT),
               Align::Center);
    } else if (sharing()) {
        std::string left, right;
        if (bundle_ && !building_) {
            if (!bundle_->files.empty()) left = plural(bundle_->files.size(), "file") + DOT + sizeStr(bundle_->totalBytes);
            if (!texts_.empty()) left += (left.empty() ? "" : DOT) + plural(texts_.size(), "note");
            if (!bundle_->files.empty()) {
                if (!bundle_->ready) {
                    double f = bundle_->totalBytes ? (double)bundle_->prepDone / (double)bundle_->totalBytes : 0.0;
                    right = "Packing " + std::to_string((int)(std::min(f, 0.99) * 100)) + "%";
                } else if (bundle_->zipPreferred) {
                    right = "zip " + sizeStr(bundle_->zipBytes) + (bundle_->deflated ? " \xC2\xB7 compressed" : " \xC2\xB7 stored");
                } else {
                    right = "direct download";
                }
            }
        } else {
            left = "Scanning\xE2\x80\xA6";
        }
        float rw = g.measure(right, Font::Small);
        g.text(left, rc(in.left, in.top, in.right - rw - 10, in.bottom), Font::Small, rgb(MUTE));
        g.text(right, in, Font::Small, rgb(FAINT), Align::Right);
    } else {
        const char* message = mode_ == 0   ? "Scan to send or receive  \xC2\xB7  same Wi-Fi"
                              : mode_ == 2 && hotspot_->uses_bluetooth()
                                  ? "Scan to send or receive  \xC2\xB7  Bluetooth (slower)"
                              : mode_ == 2 ? "Scan to send or receive  \xC2\xB7  offline hotspot"
                                           : "Scan to send or receive  \xC2\xB7  works anywhere";
        g.text(message, in, Font::Small, rgb(FAINT), Align::Center);
    }

    // ---- Item list: received (newest first), then shared files, then shared notes
    g.pushClip(L.list);
    int n = rowCount(), nr = (int)received_.size(), np = (int)paths_.size();
    if (n == 0) {
        float ty = L.list.top + 18;
        g.text("Drop files here to share them with your phone.", rc(L.list.left, ty, L.list.right, ty + 20),
               Font::Small, rgb(FAINT), Align::Center);
        g.text("Anything your phone sends shows up here too.", rc(L.list.left, ty + 20, L.list.right, ty + 40),
               Font::Small, rgb(FAINT), Align::Center);
    }
    for (int i = 0; i < n; i++) {
        float top = L.list.top + i * ROW - scroll_;
        if (top + ROW < L.list.top || top > L.list.bottom) continue;
        RectF r = rc(L.list.left, top + 2, L.list.right, top + ROW - 2);
        bool h = hover_.index == i && (hover_.hit == Hit::Row || hover_.hit == Hit::RowRemove);
        if (h) g.fillRound(r, 10, rgb(SURF));
        RectF ic = rc(r.left + 8, top + 8, r.left + 36, top + 36);
        std::string title, sub;
        if (i < nr) {
            const ReceivedItem& it = receivedRow(i);
            if (it.isText) {
                g.fillRound(ic, 7, rgb(GREEN, 0.14f));
                g.icon(Icon::Text, inset(ic, 6), rgb(GREEN), 2.0f);
                title = firstLine(it.text);
                if (title.empty()) title = "(blank text)";
                bool copied = copiedRowId_ == it.id && now - copiedRowAt_ < 1600;
                sub = copied ? "Copied to clipboard" : "Note from " + it.device + DOT + "click to copy";
            } else {
                g.fileIcon(it.path, ic);
                PointF b{ic.right - 1, ic.bottom - 1};
                g.fillCircle(b, 7.5f, rgb(BG));
                g.fillCircle(b, 6.0f, rgb(GREEN));
                g.icon(Icon::Drop, rc(b.x - 4.5f, b.y - 4.5f, b.x + 4.5f, b.y + 4.5f), rgb(WHITE), 3.0f);
                title = it.name;
                sub = "From " + it.device + DOT + sizeStr(it.size) + (h ? std::string(DOT) + "show in folder" : "");
            }
        } else if (i < nr + np) {
            const std::string& p = paths_[(size_t)(i - nr)];
            g.fileIcon(p, ic);
            title = util::path_leaf(p);
            if (title.empty()) title = p;
            const BundleRoot* root = nullptr;
            if (bundle_)
                for (const auto& br : bundle_->roots)
                    if (util::lower(br.path) == util::lower(p)) root = &br;
            if (root) sub = root->isDir ? std::string("Folder") + DOT + plural(root->fileCount, "file") + DOT + sizeStr(root->bytes)
                                        : sizeStr(root->bytes);
            else sub = building_ ? "Scanning\xE2\x80\xA6" : "Not found";
        } else {
            const std::string& t = texts_[(size_t)(i - nr - np)];
            g.fillRound(ic, 7, rgb(SURF2));
            g.icon(Icon::Text, inset(ic, 6), rgb(ACCTXT), 2.0f);
            title = firstLine(t);
            if (title.empty()) title = "(blank text)";
            sub = std::string("Text") + DOT + plural(utf8Length(t), "character");
        }
        float textRight = r.right - (h ? 44 : 10);
        g.text(title, rc(ic.right + 12, top + 5, textRight, top + 24), Font::Body, rgb(TEXT));
        g.text(sub, rc(ic.right + 12, top + 23, textRight, top + 40), Font::Small, rgb(i < nr ? 0x8FD9B6 : MUTE));
        if (h) {
            RectF rm = rc(L.list.right - 38, top + 8, L.list.right - 6, top + 36);
            bool hr = hover_.hit == Hit::RowRemove;
            if (hr) g.fillRound(rm, 8, rgb(SURF3));
            g.icon(Icon::Close, inset(rm, 9), rgb(hr ? TEXT : MUTE), 2.2f);
        }
    }
    float listH = L.list.bottom - L.list.top;
    if (n * ROW > listH) {
        float content = n * ROW, th = std::max(24.0f, listH * listH / content);
        float ty = L.list.top + (listH - th) * (scroll_ / (content - listH));
        g.fillRound(rc(L.list.right - 3, ty, L.list.right, ty + th), 1.5f, rgb(SURF3));
    }
    g.popClip();

    // ---- Bottom buttons
    // One fit for the row, the tightest any button needs, so the three match.
    const char* thirdLabel = rowCount() ? "Clear" : "Paste";
    Fit buttonFit = std::max({fitContent(g, L.add.right - L.add.left - 16, {"Add files"}, 15, 7),
                              fitContent(g, L.folder.right - L.folder.left - 16, {"Folder"}, 15, 7),
                              fitContent(g, L.third.right - L.third.left - 16, {thirdLabel}, 15, 7)});
    auto button = [&](const RectF& r, Hit hit, Icon ic, const char* label, bool primary) {
        bool hv = hov(hit);
        g.fillRound(r, 10, rgb(primary ? (hv ? ACCHI : ACC) : (hv ? SURF3 : SURF2)));
        drawFitted(g, buttonFit, r, ic, label, 15, 7, 2.2f, rgb(primary ? WHITE : TEXT));
    };
    button(L.add, Hit::AddFiles, Icon::Plus, "Add files", true);
    button(L.folder, Hit::AddFolder, Icon::Folder, "Folder", false);
    button(L.third, Hit::Third, rowCount() ? Icon::Close : Icon::Clipboard, thirdLabel, false);
}
