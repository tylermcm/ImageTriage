// C bridge between PocketDrop's shared UI and a host written in another
// language. See pocketdrop_capi.h. PocketDrop's own sources are used unchanged.
#include "pocketdrop_capi.h"
#include "../src/ui/ui.h"
#include <deque>
#include <mutex>
#include <stdexcept>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <shobjidl.h>
#include <winrt/Windows.ApplicationModel.DataTransfer.h>
#include <winrt/Windows.Foundation.h>
#endif

struct pd_sink {
    std::vector<std::string> items;
    std::string text;
    bool hasText = false;
};

namespace {

void rectOut(const RectF& r, float out[4]) {
    out[0] = r.left;
    out[1] = r.top;
    out[2] = r.right;
    out[3] = r.bottom;
}

void colorOut(Color c, float out[4]) {
    out[0] = c.r;
    out[1] = c.g;
    out[2] = c.b;
    out[3] = c.a;
}

std::string jsonString(const std::string& s) {
    std::string out = "\"";
    for (unsigned char ch : s) {
        switch (ch) {
        case '"': out += "\\\""; break;
        case '\\': out += "\\\\"; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        default:
            if (ch < 0x20) {
                char buf[8];
                snprintf(buf, sizeof buf, "\\u%04x", ch);
                out += buf;
            } else {
                out += (char)ch;
            }
        }
    }
    return out + "\"";
}

std::string menuJson(const std::vector<MenuItem>& items) {
    std::string out = "[";
    for (size_t i = 0; i < items.size(); i++) {
        const MenuItem& m = items[i];
        if (i) out += ",";
        out += "{\"id\":" + std::to_string(m.id) + ",\"label\":" + jsonString(m.label) +
               ",\"checked\":" + (m.checked ? "true" : "false") + ",\"enabled\":" + (m.enabled ? "true" : "false") +
               ",\"separator\":" + (m.separator ? "true" : "false") + ",\"submenu\":" + menuJson(m.submenu) + "}";
    }
    return out + "]";
}

class CallbackGfx : public Gfx {
public:
    explicit CallbackGfx(const pd_gfx& g) : g_(g) {}
    float scale_ = 1.0f;

    void clear(Color c) override {
        float col[4];
        colorOut(c, col);
        g_.clear(g_.ctx, col);
    }
    void fillRect(const RectF& r, Color c) override {
        float rr[4], col[4];
        rectOut(r, rr);
        colorOut(c, col);
        g_.fill_rect(g_.ctx, rr, col);
    }
    void fillRound(const RectF& r, float radius, Color c) override {
        float rr[4], col[4];
        rectOut(r, rr);
        colorOut(c, col);
        g_.fill_round(g_.ctx, rr, radius, col);
    }
    void strokeRound(const RectF& r, float radius, Color c, float width, bool dashed) override {
        float rr[4], col[4];
        rectOut(r, rr);
        colorOut(c, col);
        g_.stroke_round(g_.ctx, rr, radius, col, width, dashed ? 1 : 0);
    }
    void fillCircle(PointF p, float radius, Color c) override {
        float col[4];
        colorOut(c, col);
        g_.fill_circle(g_.ctx, p.x, p.y, radius, col);
    }
    void gradientRound(const RectF& r, float radius, Color a, Color b) override {
        float rr[4], ca[4], cb[4];
        rectOut(r, rr);
        colorOut(a, ca);
        colorOut(b, cb);
        g_.gradient_round(g_.ctx, rr, radius, ca, cb);
    }
    void strokePolyline(const std::vector<PointF>& pts, bool closed, Color c, float width) override {
        if (pts.size() < 2) return;
        std::vector<float> xy;
        xy.reserve(pts.size() * 2);
        for (const auto& p : pts) {
            xy.push_back(p.x);
            xy.push_back(p.y);
        }
        float col[4];
        colorOut(c, col);
        g_.stroke_polyline(g_.ctx, xy.data(), (int)pts.size(), closed ? 1 : 0, col, width);
    }
    void text(const std::string& s, const RectF& r, Font f, Color c, Align align) override {
        float rr[4], col[4];
        rectOut(r, rr);
        colorOut(c, col);
        g_.text(g_.ctx, s.c_str(), rr, (int)f, col, (int)align);
    }
    float measure(const std::string& s, Font f) override { return g_.measure(g_.ctx, s.c_str(), (int)f); }
    void setAliased(bool aliased) override { g_.set_aliased(g_.ctx, aliased ? 1 : 0); }
    void pushClip(const RectF& r) override {
        float rr[4];
        rectOut(r, rr);
        g_.push_clip(g_.ctx, rr);
    }
    void popClip() override { g_.pop_clip(g_.ctx); }
    void fileIcon(const std::string& path, const RectF& r) override {
        float rr[4];
        rectOut(r, rr);
        g_.file_icon(g_.ctx, path.c_str(), rr);
    }
    float scale() const override { return scale_; }

private:
    pd_gfx g_;
};

class CallbackShell : public Shell {
public:
    explicit CallbackShell(const pd_shell& s) : s_(s) {}

    // -- posted work (any thread in, UI thread out)
    void post(std::function<void()> fn) override {
        std::lock_guard<std::mutex> lock(mu_);
        if (dead_) return;
        queue_.push_back(std::move(fn));
        s_.wake(s_.ctx);
    }
    void runPosted() {
        for (;;) {
            std::function<void()> fn;
            {
                std::lock_guard<std::mutex> lock(mu_);
                if (queue_.empty() || dead_) return;
                fn = std::move(queue_.front());
                queue_.pop_front();
            }
            fn();
        }
    }
    void kill() {
        std::lock_guard<std::mutex> lock(mu_);
        dead_ = true;
        queue_.clear();
    }

    void invalidate() override { s_.invalidate(s_.ctx); }
    void setAnimating(bool on) override { s_.set_animating(s_.ctx, on ? 1 : 0); }
    void clientSize(float& w, float& h) override { s_.client_size(s_.ctx, &w, &h); }

    void copyText(const std::string& text) override { s_.copy_text(s_.ctx, text.c_str()); }
    bool copyImage(const std::vector<uint8_t>& rgba, int w, int h) override {
        if (w <= 0 || h <= 0 || rgba.size() != (size_t)w * h * 4) return false;
        return s_.copy_image(s_.ctx, rgba.data(), w, h) != 0;
    }
    void readClipboard(std::vector<std::string>& paths, std::string& text) override {
        pd_sink sink;
        s_.read_clipboard(s_.ctx, &sink);
        paths = std::move(sink.items);
        if (sink.hasText) text = std::move(sink.text);
    }
    void browse(bool folders, std::function<void(const std::vector<std::string>&)> done) override {
        pd_sink sink;
        s_.browse(s_.ctx, folders ? 1 : 0, &sink);
        if (!sink.items.empty()) done(sink.items);
    }
    void chooseFolder(const std::string& title, std::function<void(const std::string&)> done) override {
        pd_sink sink;
        s_.choose_folder(s_.ctx, title.c_str(), &sink);
        if (sink.hasText && !sink.text.empty()) done(sink.text);
    }
    void openUrl(const std::string& url) override { s_.open_url(s_.ctx, url.c_str()); }
    void openBluetoothSetup() override { s_.open_bluetooth_setup(s_.ctx); }
    void shareText(const std::string& title, const std::string& text) override;
    bool confirmAnywhereRisk(bool& dontShowAgain) override {
        int dont = 0;
        bool ok = s_.confirm_anywhere_risk(s_.ctx, &dont) != 0;
        dontShowAgain = dont != 0;
        return ok;
    }
    void revealPath(const std::string& path) override { s_.reveal_path(s_.ctx, path.c_str()); }
    void attention() override { s_.attention(s_.ctx); }
    int popupMenu(const std::vector<MenuItem>& items, float x, float y) override {
        return s_.popup_menu(s_.ctx, menuJson(items).c_str(), x, y);
    }
    void alert(const std::string& title, const std::string& message) override {
        s_.alert(s_.ctx, title.c_str(), message.c_str());
    }

    int loadSetting(const char* key, int def) override { return s_.load_setting(s_.ctx, key, def); }
    void saveSetting(const char* key, int value) override { s_.save_setting(s_.ctx, key, value); }
    std::string loadString(const char* key, const std::string& def) override {
        pd_sink sink;
        s_.load_string(s_.ctx, key, &sink);
        return sink.hasText ? sink.text : def;
    }
    void saveString(const char* key, const std::string& value) override { s_.save_string(s_.ctx, key, value.c_str()); }
    void setTopmost(bool on) override { s_.set_topmost(s_.ctx, on ? 1 : 0); }
    std::string cleanPath(const std::string& path) override {
        pd_sink sink;
        s_.clean_path(s_.ctx, path.c_str(), &sink);
        return sink.hasText ? sink.text : std::string();
    }

private:
    pd_shell s_;
    std::mutex mu_;
    std::deque<std::function<void()>> queue_;
    bool dead_ = false;
#ifdef _WIN32
    winrt::Windows::ApplicationModel::DataTransfer::DataTransferManager shareManager_{nullptr};
    winrt::event_token shareToken_{};
    bool shareHooked_ = false;
#endif
};

#ifdef _WIN32
// The Windows share sheet, as PocketDrop's own Windows host opens it.
void CallbackShell::shareText(const std::string& title, const std::string& text) {
    using namespace winrt::Windows::ApplicationModel::DataTransfer;
    HWND hwnd = (HWND)s_.native_window(s_.ctx);
    try {
        if (!hwnd) throw std::runtime_error("no window");
        if (shareHooked_ && shareManager_) shareManager_.DataRequested(shareToken_);
        auto interop = winrt::get_activation_factory<DataTransferManager, IDataTransferManagerInterop>();
        DataTransferManager manager{nullptr};
        winrt::check_hresult(interop->GetForWindow(hwnd, winrt::guid_of<DataTransferManager>(), winrt::put_abi(manager)));
        shareManager_ = manager;
        shareToken_ = shareManager_.DataRequested([title, text](auto const&, DataRequestedEventArgs const& args) {
            auto data = args.Request().Data();
            data.Properties().Title(winrt::to_hstring(title));
            data.SetText(winrt::to_hstring(text));
            data.SetWebLink(winrt::Windows::Foundation::Uri(winrt::to_hstring(text)));
        });
        shareHooked_ = true;
        winrt::check_hresult(interop->ShowShareUIForWindow(hwnd));
    } catch (...) {
        copyText(text);
        alert("PocketDrop", "The Windows share sheet couldn't open, so the link was copied instead.");
    }
}
#else
void CallbackShell::shareText(const std::string&, const std::string& text) { copyText(text); }
#endif

} // namespace

struct pd_host {
    CallbackShell shell;
    CallbackGfx gfx;
    Ui ui;
    pd_host(const pd_shell& s, const pd_gfx& g) : shell(s), gfx(g), ui(shell) {}
};

extern "C" {

PD_API int pd_abi_version(void) { return PD_ABI_VERSION; }

PD_API pd_host* pd_create(const pd_shell* shell, const pd_gfx* gfx) {
    if (!shell || !gfx) return nullptr;
    try {
        return new pd_host(*shell, *gfx);
    } catch (...) {
        return nullptr;
    }
}

PD_API void pd_start(pd_host* h) {
    if (h) h->ui.start();
}

PD_API void pd_destroy(pd_host* h) {
    if (!h) return;
    h->ui.shutdown();
    h->shell.kill();
    delete h;
}

PD_API void pd_paint(pd_host* h, float scale) {
    if (!h) return;
    h->gfx.scale_ = scale > 0 ? scale : 1.0f;
    h->ui.paint(h->gfx);
}

PD_API void pd_tick(pd_host* h) {
    if (h) h->ui.tick();
}
PD_API void pd_run_posted(pd_host* h) {
    if (h) h->shell.runPosted();
}

PD_API void pd_mouse_move(pd_host* h, float x, float y) {
    if (h) h->ui.mouseMove(x, y);
}
PD_API void pd_mouse_leave(pd_host* h) {
    if (h) h->ui.mouseLeave();
}
PD_API void pd_mouse_down(pd_host* h, float x, float y) {
    if (h) h->ui.mouseDown(x, y);
}
PD_API void pd_mouse_up(pd_host* h, float x, float y) {
    if (h) h->ui.mouseUp(x, y);
}
PD_API void pd_wheel(pd_host* h, float lines) {
    if (h) h->ui.wheel(lines);
}
PD_API int pd_wants_pointer(pd_host* h) { return h && h->ui.wantsPointer() ? 1 : 0; }

PD_API void pd_set_drag_over(pd_host* h, int on) {
    if (h) h->ui.setDragOver(on != 0);
}
PD_API void pd_add_paths(pd_host* h, const char* const* paths, int count) {
    if (!h || !paths || count <= 0) return;
    std::vector<std::string> list;
    for (int i = 0; i < count; i++)
        if (paths[i]) list.emplace_back(paths[i]);
    h->ui.addPaths(list);
}
PD_API void pd_add_text(pd_host* h, const char* text) {
    if (h && text) h->ui.addText(text);
}
PD_API void pd_paste(pd_host* h) {
    if (h) h->ui.paste();
}
PD_API void pd_browse(pd_host* h, int folders) {
    if (h) h->ui.browse(folders != 0);
}
PD_API void pd_copy_link(pd_host* h) {
    if (h) h->ui.copyLink();
}
PD_API void pd_share_link(pd_host* h) {
    if (h) h->ui.shareLink();
}
PD_API void pd_refresh_network(pd_host* h) {
    if (h) h->ui.refreshNetwork();
}

PD_API void pd_sink_add(pd_sink* s, const char* text) {
    if (s && text) s->items.emplace_back(text);
}
PD_API void pd_sink_set(pd_sink* s, const char* text) {
    if (!s || !text) return;
    s->text = text;
    s->hasText = true;
}

} // extern "C"
