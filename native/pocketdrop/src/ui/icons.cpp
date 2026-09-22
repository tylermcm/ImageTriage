// Vector icons on a 24x24 grid, flattened to polylines so every backend draws them identically.
#include "gfx.h"
#include <algorithm>
#include <cmath>

namespace {

const double PI = 3.14159265358979323846;

class Path {
public:
    explicit Path(const RectF& r) : r_(r), s_((r.right - r.left) / 24.0f) {}

    Path& move(float x, float y) {
        flush();
        cur_ = {x, y};
        pts_.push_back(map(x, y));
        return *this;
    }
    Path& to(float x, float y) {
        cur_ = {x, y};
        pts_.push_back(map(x, y));
        return *this;
    }
    // SVG-style endpoint arc (y-down: clockwise = increasing angle).
    Path& arc(float x2, float y2, float rx, float ry, bool clockwise, bool large = false) {
        double x1 = cur_.x, y1 = cur_.y;
        double dx = (x1 - x2) / 2, dy = (y1 - y2) / 2;
        double Rx = rx, Ry = ry;
        if (Rx <= 0 || Ry <= 0 || (dx == 0 && dy == 0)) return to(x2, y2);
        double lam = dx * dx / (Rx * Rx) + dy * dy / (Ry * Ry);
        if (lam > 1) {
            Rx *= std::sqrt(lam);
            Ry *= std::sqrt(lam);
        }
        double num = Rx * Rx * Ry * Ry - Rx * Rx * dy * dy - Ry * Ry * dx * dx;
        double den = Rx * Rx * dy * dy + Ry * Ry * dx * dx;
        double coef = (large != clockwise ? 1.0 : -1.0) * std::sqrt(std::max(0.0, num / den));
        double cxp = coef * (Rx * dy / Ry), cyp = coef * (-Ry * dx / Rx);
        double cx = cxp + (x1 + x2) / 2, cy = cyp + (y1 + y2) / 2;
        auto angle = [](double ux, double uy, double vx, double vy) { return std::atan2(ux * vy - uy * vx, ux * vx + uy * vy); };
        double ux = (dx - cxp) / Rx, uy = (dy - cyp) / Ry, vx = (-dx - cxp) / Rx, vy = (-dy - cyp) / Ry;
        double t1 = angle(1, 0, ux, uy), dt = angle(ux, uy, vx, vy);
        if (!clockwise && dt > 0) dt -= 2 * PI;
        else if (clockwise && dt < 0) dt += 2 * PI;
        int n = std::max(4, (int)std::ceil(std::fabs(dt) / (PI / 18)));
        for (int i = 1; i <= n; i++) {
            double t = t1 + dt * i / n;
            pts_.push_back(map((float)(cx + Rx * std::cos(t)), (float)(cy + Ry * std::sin(t))));
        }
        cur_ = {x2, y2};
        return *this;
    }
    Path& arc(float x, float y, float radius, bool clockwise, bool large = false) {
        return arc(x, y, radius, radius, clockwise, large);
    }
    Path& close() {
        flush(true);
        return *this;
    }
    void draw(Gfx& g, Color c, float width) {
        flush();
        for (auto& f : figures_) g.strokePolyline(f.pts, f.closed, c, width);
    }
    PointF map(float x, float y) const { return {r_.left + x * s_, r_.top + y * s_}; }
    float scale() const { return s_; }

private:
    struct Figure {
        std::vector<PointF> pts;
        bool closed;
    };
    void flush(bool closed = false) {
        if (pts_.size() > 1) figures_.push_back({pts_, closed});
        pts_.clear();
    }
    RectF r_;
    float s_;
    PointF cur_{0, 0};
    std::vector<PointF> pts_;
    std::vector<Figure> figures_;
};

} // namespace

void Gfx::icon(Icon i, const RectF& r, Color c, float stroke) {
    Path p(r);
    float sw = stroke * (r.right - r.left) / 20.0f;
    switch (i) {
    case Icon::Dots:
        for (float x : {5.0f, 12.0f, 19.0f}) fillCircle(p.map(x, 12), 1.8f * p.scale(), c);
        return;
    case Icon::Plus: p.move(12, 5).to(12, 19).move(5, 12).to(19, 12); break;
    case Icon::Close: p.move(6.5f, 6.5f).to(17.5f, 17.5f).move(17.5f, 6.5f).to(6.5f, 17.5f); break;
    case Icon::Check: p.move(5, 12.5f).to(9.5f, 17).to(19, 7.5f); break;
    case Icon::Copy:
        p.move(11, 9).to(18, 9).arc(20, 11, 2, true).to(20, 18).arc(18, 20, 2, true).to(11, 20).arc(9, 18, 2, true)
            .to(9, 11).arc(11, 9, 2, true).close();
        p.move(5, 15).to(5, 6).arc(7, 4, 2, true).to(15, 4);
        break;
    case Icon::Share:
        p.move(12, 15).to(12, 3).move(7.5f, 7.5f).to(12, 3).to(16.5f, 7.5f);
        p.move(5, 11).to(5, 19).arc(7, 21, 2, false).to(17, 21).arc(19, 19, 2, false).to(19, 11);
        break;
    case Icon::Wifi:
        for (float rad : {4.0f, 8.0f, 12.0f}) {
            float dx = rad * 0.72f, dy = rad * 0.69f;
            p.move(12 - dx, 19.5f - dy).arc(12 + dx, 19.5f - dy, rad, true);
        }
        p.draw(*this, c, sw);
        fillCircle(p.map(12, 19.5f), 1.5f * p.scale(), c);
        return;
    case Icon::Globe:
        p.move(12, 3).arc(12, 21, 9, true).arc(12, 3, 9, true).close();
        p.move(12, 3).arc(12, 21, 4.2f, 9.0f, true).arc(12, 3, 4.2f, 9.0f, true).close();
        p.move(3.5f, 12).to(20.5f, 12);
        break;
    case Icon::Folder:
        p.move(3, 7).arc(5, 5, 2, true).to(9, 5).to(11, 7.5f).to(19, 7.5f).arc(21, 9.5f, 2, true).to(21, 17)
            .arc(19, 19, 2, true).to(5, 19).arc(3, 17, 2, true).close();
        break;
    case Icon::Drop: p.move(12, 3.5f).to(12, 14.5f).move(7, 10).to(12, 15).to(17, 10).move(5, 20).to(19, 20); break;
    case Icon::Text:
        p.move(5, 6).to(19, 6).move(5, 10.5f).to(19, 10.5f).move(5, 15).to(15, 15).move(5, 19.5f).to(11, 19.5f);
        break;
    case Icon::Clipboard:
        p.move(8, 5).to(7, 5).arc(5, 7, 2, false).to(5, 19).arc(7, 21, 2, false).to(17, 21).arc(19, 19, 2, false)
            .to(19, 7).arc(17, 5, 2, false).to(16, 5);
        p.move(9, 3).to(15, 3).to(15, 7).to(9, 7).close();
        break;
    case Icon::Phone:
        p.move(9, 2.5f).to(15, 2.5f).arc(17.5f, 5, 2.5f, true).to(17.5f, 19).arc(15, 21.5f, 2.5f, true).to(9, 21.5f)
            .arc(6.5f, 19, 2.5f, true).to(6.5f, 5).arc(9, 2.5f, 2.5f, true).close();
        p.move(11, 18).to(13, 18);
        break;
    case Icon::Retry:
        p.move(19.5f, 12).arc(15.8f, 5.6f, 7.5f, false, true);
        p.move(16, 2.5f).to(16, 6).to(19.5f, 6);
        break;
    case Icon::Bolt: p.move(13, 3).to(5.5f, 13.5f).to(12, 13.5f).to(11, 21).to(18.5f, 10.5f).to(12, 10.5f).close(); break;
    }
    p.draw(*this, c, sw);
}

void Gfx::spinner(PointF center, float radius, float angle, Color c, float width) {
    std::vector<PointF> pts;
    const int n = 40;
    for (int i = 0; i <= n; i++) {
        double t = angle + 4.4 * i / n;
        pts.push_back({center.x + radius * (float)std::cos(t), center.y + radius * (float)std::sin(t)});
    }
    strokePolyline(pts, false, c, width);
}
