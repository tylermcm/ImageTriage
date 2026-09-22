#pragma once
// Drawing interface shared by the UI. Coordinates are DIPs (points on macOS).
#include <cstdint>
#include <string>
#include <vector>

struct Color {
    float r, g, b, a;
};
inline Color rgb(uint32_t hex, float a = 1.0f) {
    return {((hex >> 16) & 0xFF) / 255.0f, ((hex >> 8) & 0xFF) / 255.0f, (hex & 0xFF) / 255.0f, a};
}

struct PointF {
    float x, y;
};
struct RectF {
    float left, top, right, bottom;
};
inline RectF rc(float l, float t, float r, float b) { return {l, t, r, b}; }
inline bool inside(const RectF& r, float x, float y) { return x >= r.left && x < r.right && y >= r.top && y < r.bottom; }
inline RectF inset(const RectF& r, float d) { return {r.left + d, r.top + d, r.right - d, r.bottom - d}; }

enum class Font { Title, Body, BodyBold, Small, SmallBold, Mono, Big, Count };
enum class Icon { Dots, Plus, Close, Copy, Share, Check, Wifi, Globe, Folder, Drop, Text, Clipboard, Phone, Retry, Bolt };
enum class Align { Left, Center, Right };

class Gfx {
public:
    virtual ~Gfx() = default;
    virtual void clear(Color c) = 0;
    virtual void fillRect(const RectF& r, Color c) = 0;
    virtual void fillRound(const RectF& r, float radius, Color c) = 0;
    virtual void strokeRound(const RectF& r, float radius, Color c, float width = 1.0f, bool dashed = false) = 0;
    virtual void fillCircle(PointF p, float radius, Color c) = 0;
    virtual void gradientRound(const RectF& r, float radius, Color a, Color b) = 0; // top-left to bottom-right
    virtual void strokePolyline(const std::vector<PointF>& pts, bool closed, Color c, float width) = 0; // round caps/joins
    // Single line of UTF-8 text, vertically centred in r, ellipsized to fit.
    virtual void text(const std::string& s, const RectF& r, Font f, Color c, Align align = Align::Left) = 0;
    virtual float measure(const std::string& s, Font f) = 0;
    virtual void setAliased(bool aliased) = 0;
    virtual void pushClip(const RectF& r) = 0;
    virtual void popClip() = 0;
    virtual void fileIcon(const std::string& path, const RectF& r) = 0; // the system icon for a file or folder
    virtual float scale() const = 0;                                    // device pixels per DIP

    // Shared vector icons (src/ui/icons.cpp)
    void icon(Icon i, const RectF& r, Color c, float stroke = 1.7f);
    void spinner(PointF center, float radius, float angle, Color c, float width = 2.5f);
};
