// Compact QR Code Model 2 encoder (byte mode, versions 1-40).
#include "qr.h"
#include <algorithm>
#include <cstdlib>

namespace qr {
namespace {

std::string wifiEscape(const std::string& s) {
    std::string out;
    out.reserve(s.size());
    for (char c : s) {
        if (c == '\\' || c == ';' || c == ',' || c == ':' || c == '"') out.push_back('\\');
        out.push_back(c);
    }
    return out;
}

const int8_t ECC_PER_BLOCK[4][41] = {
    {-1,  7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30},
    {-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28},
    {-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30},
    {-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30},
};
const int8_t NUM_BLOCKS[4][41] = {
    {-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4,  4,  4,  4,  4,  6,  6,  6,  6,  7,  8,  8,  9,  9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25},
    {-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5,  5,  8,  9,  9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49},
    {-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8,  8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68},
    {-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81},
};
const int FORMAT_BITS[4] = {1, 0, 3, 2}; // L, M, Q, H

int rawModules(int ver) {
    int r = (16 * ver + 128) * ver + 64;
    if (ver >= 2) {
        int na = ver / 7 + 2;
        r -= (25 * na - 10) * na - 55;
        if (ver >= 7) r -= 36;
    }
    return r;
}

int dataCodewords(int ver, int e) {
    return rawModules(ver) / 8 - ECC_PER_BLOCK[e][ver] * NUM_BLOCKS[e][ver];
}

uint8_t gfMul(uint8_t x, uint8_t y) {
    int z = 0;
    for (int i = 7; i >= 0; i--) {
        z = (z << 1) ^ ((z >> 7) * 0x11D);
        z ^= ((y >> i) & 1) * x;
    }
    return (uint8_t)z;
}

std::vector<uint8_t> rsDivisor(int degree) {
    std::vector<uint8_t> r(degree);
    r[degree - 1] = 1;
    uint8_t root = 1;
    for (int i = 0; i < degree; i++) {
        for (int j = 0; j < degree; j++) {
            r[j] = gfMul(r[j], root);
            if (j + 1 < degree) r[j] ^= r[j + 1];
        }
        root = gfMul(root, 2);
    }
    return r;
}

std::vector<uint8_t> rsRemainder(const uint8_t* data, size_t len, const std::vector<uint8_t>& div) {
    std::vector<uint8_t> r(div.size());
    for (size_t k = 0; k < len; k++) {
        uint8_t factor = data[k] ^ r[0];
        r.erase(r.begin());
        r.push_back(0);
        for (size_t i = 0; i < r.size(); i++) r[i] ^= gfMul(div[i], factor);
    }
    return r;
}

struct Builder {
    int ver, size, ecc;
    std::vector<uint8_t> mod, fn;

    Builder(int v, int e) : ver(v), size(v * 4 + 17), ecc(e), mod(size * size), fn(size * size) {}

    void setFn(int x, int y, bool d) { mod[y * size + x] = d; fn[y * size + x] = 1; }

    std::vector<int> alignPos() const {
        if (ver == 1) return {};
        int na = ver / 7 + 2;
        int step = (ver * 8 + na * 3 + 5) / (na * 4 - 4) * 2;
        std::vector<int> r(na);
        r[0] = 6;
        for (int i = na - 1, pos = size - 7; i >= 1; i--, pos -= step) r[i] = pos;
        return r;
    }

    void finder(int x, int y) {
        for (int dy = -4; dy <= 4; dy++)
            for (int dx = -4; dx <= 4; dx++) {
                int dist = std::max(std::abs(dx), std::abs(dy));
                int xx = x + dx, yy = y + dy;
                if (xx >= 0 && xx < size && yy >= 0 && yy < size) setFn(xx, yy, dist != 2 && dist != 4);
            }
    }

    void align(int x, int y) {
        for (int dy = -2; dy <= 2; dy++)
            for (int dx = -2; dx <= 2; dx++)
                setFn(x + dx, y + dy, std::max(std::abs(dx), std::abs(dy)) != 1);
    }

    void drawFormat(int mask) {
        int data = FORMAT_BITS[ecc] << 3 | mask;
        int rem = data;
        for (int i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >> 9) * 0x537);
        int bits = (data << 10 | rem) ^ 0x5412;
        auto bit = [&](int i) { return ((bits >> i) & 1) != 0; };
        for (int i = 0; i <= 5; i++) setFn(8, i, bit(i));
        setFn(8, 7, bit(6));
        setFn(8, 8, bit(7));
        setFn(7, 8, bit(8));
        for (int i = 9; i < 15; i++) setFn(14 - i, 8, bit(i));
        for (int i = 0; i < 8; i++) setFn(size - 1 - i, 8, bit(i));
        for (int i = 8; i < 15; i++) setFn(8, size - 15 + i, bit(i));
        setFn(8, size - 8, true);
    }

    void drawVersion() {
        if (ver < 7) return;
        int rem = ver;
        for (int i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >> 11) * 0x1F25);
        long bits = (long)ver << 12 | rem;
        for (int i = 0; i < 18; i++) {
            bool b = ((bits >> i) & 1) != 0;
            int a = size - 11 + i % 3, c = i / 3;
            setFn(a, c, b);
            setFn(c, a, b);
        }
    }

    void drawFunctionPatterns() {
        for (int i = 0; i < size; i++) {
            setFn(6, i, i % 2 == 0);
            setFn(i, 6, i % 2 == 0);
        }
        finder(3, 3);
        finder(size - 4, 3);
        finder(3, size - 4);
        auto ap = alignPos();
        int na = (int)ap.size();
        for (int i = 0; i < na; i++)
            for (int j = 0; j < na; j++)
                if (!((i == 0 && j == 0) || (i == 0 && j == na - 1) || (i == na - 1 && j == 0)))
                    align(ap[i], ap[j]);
        drawFormat(0);
        drawVersion();
    }

    std::vector<uint8_t> addEccAndInterleave(const std::vector<uint8_t>& data) const {
        int nb = NUM_BLOCKS[ecc][ver], eccLen = ECC_PER_BLOCK[ecc][ver];
        int raw = rawModules(ver) / 8;
        int numShort = nb - raw % nb, shortLen = raw / nb;
        auto div = rsDivisor(eccLen);
        std::vector<std::vector<uint8_t>> blocks;
        size_t k = 0;
        for (int i = 0; i < nb; i++) {
            size_t len = shortLen - eccLen + (i < numShort ? 0 : 1);
            std::vector<uint8_t> blk(data.begin() + k, data.begin() + k + len);
            k += len;
            auto e = rsRemainder(blk.data(), blk.size(), div);
            blk.insert(blk.end(), e.begin(), e.end());
            blocks.push_back(std::move(blk));
        }
        std::vector<uint8_t> out;
        size_t maxData = blocks.back().size() - eccLen; // long blocks come last
        for (size_t i = 0; i < maxData; i++)
            for (int j = 0; j < nb; j++)
                if (i < blocks[j].size() - eccLen) out.push_back(blocks[j][i]);
        for (int i = 0; i < eccLen; i++)
            for (int j = 0; j < nb; j++) out.push_back(blocks[j][blocks[j].size() - eccLen + i]);
        return out;
    }

    void drawCodewords(const std::vector<uint8_t>& data) {
        size_t i = 0, total = data.size() * 8;
        for (int right = size - 1; right >= 1; right -= 2) {
            if (right == 6) right = 5;
            for (int vert = 0; vert < size; vert++)
                for (int j = 0; j < 2; j++) {
                    int x = right - j;
                    bool upward = ((right + 1) & 2) == 0;
                    int y = upward ? size - 1 - vert : vert;
                    if (!fn[y * size + x] && i < total) {
                        mod[y * size + x] = (data[i >> 3] >> (7 - (i & 7))) & 1;
                        i++;
                    }
                }
        }
    }

    void applyMask(int m) {
        for (int y = 0; y < size; y++)
            for (int x = 0; x < size; x++) {
                bool inv;
                switch (m) {
                case 0: inv = (x + y) % 2 == 0; break;
                case 1: inv = y % 2 == 0; break;
                case 2: inv = x % 3 == 0; break;
                case 3: inv = (x + y) % 3 == 0; break;
                case 4: inv = (x / 3 + y / 2) % 2 == 0; break;
                case 5: inv = x * y % 2 + x * y % 3 == 0; break;
                case 6: inv = (x * y % 2 + x * y % 3) % 2 == 0; break;
                default: inv = ((x + y) % 2 + x * y % 3) % 2 == 0; break;
                }
                if (inv && !fn[y * size + x]) mod[y * size + x] ^= 1;
            }
    }

    long penaltyLine(const std::vector<uint8_t>& line) const {
        long p = 0;
        int n = (int)line.size();
        for (int i = 0; i < n;) {
            int j = i;
            while (j < n && line[j] == line[i]) j++;
            if (j - i >= 5) p += 3 + (j - i - 5);
            i = j;
        }
        static const uint8_t pat[7] = {1, 0, 1, 1, 1, 0, 1};
        auto light = [&](int a, int b) {
            for (int k = a; k < b; k++)
                if (k >= 0 && k < n && line[k]) return false;
            return true;
        };
        for (int i = 0; i + 7 <= n; i++) {
            if (!std::equal(pat, pat + 7, line.begin() + i)) continue;
            if (light(i - 4, i) || light(i + 7, i + 11)) p += 40;
        }
        return p;
    }

    long penalty() const {
        long p = 0;
        std::vector<uint8_t> line(size);
        for (int y = 0; y < size; y++) {
            for (int x = 0; x < size; x++) line[x] = mod[y * size + x];
            p += penaltyLine(line);
        }
        for (int x = 0; x < size; x++) {
            for (int y = 0; y < size; y++) line[y] = mod[y * size + x];
            p += penaltyLine(line);
        }
        long dark = 0;
        for (int y = 0; y < size; y++)
            for (int x = 0; x < size; x++) {
                uint8_t c = mod[y * size + x];
                dark += c;
                if (x + 1 < size && y + 1 < size && c == mod[y * size + x + 1] &&
                    c == mod[(y + 1) * size + x] && c == mod[(y + 1) * size + x + 1])
                    p += 3;
            }
        long total = (long)size * size;
        long k = (std::labs(dark * 20 - total * 10) + total - 1) / total - 1;
        return p + k * 10;
    }
};

} // namespace

std::string wifi(const std::string& ssid, const std::string& passphrase) {
    return "WIFI:T:WPA;S:" + wifiEscape(ssid) + ";P:" + wifiEscape(passphrase) + ";;";
}

Code encode(const std::string& s, Ecc minEcc) {
    int e = (int)minEcc;
    size_t n = s.size();
    int ver = 1;
    for (; ver <= 40; ver++) {
        long bits = 4 + (ver <= 9 ? 8 : 16) + 8L * (long)n;
        if (bits <= dataCodewords(ver, e) * 8L) break;
    }
    if (ver > 40) return {};
    int cc = ver <= 9 ? 8 : 16;
    long used = 4 + cc + 8L * (long)n;
    for (int ne = e + 1; ne <= 3; ne++)
        if (used <= dataCodewords(ver, ne) * 8L) e = ne;

    std::vector<uint8_t> bits;
    auto app = [&](uint32_t val, int len) {
        for (int i = len - 1; i >= 0; i--) bits.push_back((val >> i) & 1);
    };
    app(4, 4);
    app((uint32_t)n, cc);
    for (unsigned char c : s) app(c, 8);
    size_t cap = (size_t)dataCodewords(ver, e) * 8;
    app(0, (int)std::min<size_t>(4, cap - bits.size()));
    app(0, (int)((8 - bits.size() % 8) % 8));
    for (uint8_t pad = 0xEC; bits.size() < cap; pad ^= 0xEC ^ 0x11) app(pad, 8);

    std::vector<uint8_t> data(bits.size() / 8);
    for (size_t i = 0; i < bits.size(); i++) data[i >> 3] |= bits[i] << (7 - (i & 7));

    Builder b(ver, e);
    b.drawFunctionPatterns();
    b.drawCodewords(b.addEccAndInterleave(data));

    int best = 0;
    long bestP = -1;
    for (int m = 0; m < 8; m++) {
        b.applyMask(m);
        b.drawFormat(m);
        long p = b.penalty();
        if (bestP < 0 || p < bestP) { bestP = p; best = m; }
        b.applyMask(m);
    }
    b.applyMask(best);
    b.drawFormat(best);

    Code c;
    c.size = b.size;
    c.modules = std::move(b.mod);
    return c;
}

} // namespace qr
