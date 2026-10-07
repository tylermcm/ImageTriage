#pragma once
// What the shared UI needs from the host window/OS. Implemented per platform.
#include <functional>
#include <string>
#include <vector>

struct MenuItem {
    int id = 0; // 0 for separators and submenu parents
    std::string label;
    bool checked = false, enabled = true, separator = false;
    std::vector<MenuItem> submenu;
};

class Shell {
public:
    virtual ~Shell() = default;

    // The host calls Ui::tick() every 500 ms, and about every 33 ms while animating.
    virtual void invalidate() = 0;
    virtual void setAnimating(bool on) = 0;
    virtual void post(std::function<void()> fn) = 0; // thread-safe; runs fn on the UI thread
    virtual void clientSize(float& w, float& h) = 0; // DIPs

    virtual void copyText(const std::string& text) = 0;
    // Copies tightly packed RGBA pixels to the system clipboard.
    virtual bool copyImage(const std::vector<uint8_t>&, int, int) { return false; }
    // Clipboard contents: file paths, else an image saved as a PNG file path, else text.
    virtual void readClipboard(std::vector<std::string>& paths, std::string& text) = 0;
    virtual void browse(bool folders, std::function<void(const std::vector<std::string>&)> done) = 0;
    virtual void chooseFolder(const std::string& title, std::function<void(const std::string&)> done) = 0;
    virtual void openUrl(const std::string& url) = 0;
    virtual void openBluetoothSetup() {}
    virtual void shareText(const std::string&, const std::string& text) { copyText(text); }
    virtual bool confirmAnywhereRisk(bool& dontShowAgain) {
        dontShowAgain = false;
        alert("Anywhere link security",
              "Anyone with the active link can view and download the files you share until the link expires or is revoked.\n\n"
              "Sending files back to this computer requires the receive code shown in PocketDrop.");
        return true;
    }
    virtual void revealPath(const std::string& path) = 0; // show a file selected in the file manager
    virtual void attention() {}                           // taskbar flash / Dock bounce when not focused
    // Shows a menu with its top-right corner at (x, y) in DIPs. Returns the chosen id or 0.
    virtual int popupMenu(const std::vector<MenuItem>& items, float x, float y) = 0;
    virtual void alert(const std::string& title, const std::string& message) = 0;

    virtual int loadSetting(const char* key, int def) = 0;
    virtual void saveSetting(const char* key, int value) = 0;
    virtual std::string loadString(const char* key, const std::string& def) = 0;
    virtual void saveString(const char* key, const std::string& value) = 0;
    virtual void setTopmost(bool on) = 0;

    // Platform-only menu entries (ids >= 1000), e.g. Windows' "Send to" shortcut.
    virtual std::vector<MenuItem> extraMenuItems() { return {}; }
    virtual void handleExtraMenu(int) {}

    // Absolute path with trailing separators removed, or empty if it doesn't exist.
    virtual std::string cleanPath(const std::string& path) = 0;
};
