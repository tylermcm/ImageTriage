#include "deflate.h"
#include <algorithm>
#include <cstring>

namespace {

uint32_t crcTab[8][256];
uint8_t lenCodeTab[259];

const uint16_t LBASE[29] = {3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258};
const uint8_t LEXTRA[29] = {0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0};
const uint16_t DBASE[30] = {1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577};
const uint8_t DEXTRA[30] = {0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13};
const uint8_t CL_ORDER[19] = {16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15};

struct Init {
    Init() {
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t c = i;
            for (int k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >> 1)) : (c >> 1);
            crcTab[0][i] = c;
        }
        for (int i = 0; i < 256; i++)
            for (int t = 1; t < 8; t++)
                crcTab[t][i] = (crcTab[t - 1][i] >> 8) ^ crcTab[0][crcTab[t - 1][i] & 0xFF];
        for (int c = 0; c < 29; c++)
            for (int l = LBASE[c]; l < LBASE[c] + (1 << LEXTRA[c]) && l <= 258; l++) lenCodeTab[l] = (uint8_t)c;
    }
} init;

inline int distCode(int dist) {
    return (int)(std::upper_bound(DBASE, DBASE + 30, (uint16_t)dist) - DBASE) - 1;
}

struct BitWriter {
    std::vector<uint8_t>& out;
    uint64_t buf = 0;
    int cnt = 0;
    void put(uint32_t bits, int n) {
        buf |= (uint64_t)bits << cnt;
        cnt += n;
        while (cnt >= 8) {
            out.push_back((uint8_t)buf);
            buf >>= 8;
            cnt -= 8;
        }
    }
    void align() {
        if (cnt > 0) out.push_back((uint8_t)buf);
        buf = 0;
        cnt = 0;
    }
};

// Length-limited Huffman code lengths (tree build + Kraft fix-up, as in miniz).
void buildLengths(const uint32_t* freq, int n, int maxLen, uint8_t* lens) {
    std::fill(lens, lens + n, (uint8_t)0);
    std::vector<int> syms;
    for (int i = 0; i < n; i++)
        if (freq[i]) syms.push_back(i);
    if (syms.empty()) return;
    if (syms.size() == 1) {
        lens[syms[0]] = 1;
        lens[syms[0] == 0 ? 1 : 0] = 1;
        return;
    }
    std::stable_sort(syms.begin(), syms.end(), [&](int a, int b) { return freq[a] < freq[b]; });
    int m = (int)syms.size();
    std::vector<uint64_t> w(2 * m);
    std::vector<int> parent(2 * m, -1);
    for (int i = 0; i < m; i++) w[i] = freq[syms[i]];
    int leaf = 0, nodeRead = m;
    for (int k = m; k < 2 * m - 1; k++) {
        int pick[2];
        for (int t = 0; t < 2; t++) {
            if (leaf < m && (nodeRead >= k || w[leaf] <= w[nodeRead])) pick[t] = leaf++;
            else pick[t] = nodeRead++;
        }
        w[k] = w[pick[0]] + w[pick[1]];
        parent[pick[0]] = parent[pick[1]] = k;
    }
    std::vector<int> depth(2 * m - 1, 0);
    for (int k = 2 * m - 3; k >= 0; k--) depth[k] = depth[parent[k]] + 1;
    uint32_t count[33] = {0};
    for (int i = 0; i < m; i++) count[std::min(depth[i], maxLen)]++;
    uint32_t total = 0;
    for (int i = maxLen; i > 0; i--) total += count[i] << (maxLen - i);
    while (total != (1u << maxLen)) {
        count[maxLen]--;
        for (int i = maxLen - 1; i > 0; i--)
            if (count[i]) {
                count[i]--;
                count[i + 1] += 2;
                break;
            }
        total--;
    }
    int idx = 0;
    for (int len = maxLen; len >= 1; len--)
        for (uint32_t c = 0; c < count[len]; c++) lens[syms[idx++]] = (uint8_t)len;
}

void makeCodes(const uint8_t* lens, int n, uint16_t* codes) {
    int bl[16] = {0};
    for (int i = 0; i < n; i++) bl[lens[i]]++;
    bl[0] = 0;
    int next[16] = {0}, code = 0;
    for (int b = 1; b < 16; b++) {
        code = (code + bl[b - 1]) << 1;
        next[b] = code;
    }
    for (int i = 0; i < n; i++) {
        codes[i] = 0;
        if (!lens[i]) continue;
        int c = next[lens[i]]++, r = 0;
        for (int k = 0; k < lens[i]; k++) {
            r = (r << 1) | (c & 1);
            c >>= 1;
        }
        codes[i] = (uint16_t)r;
    }
}

struct Sym {
    uint16_t litlen; // literal byte when dist == 0, else match length
    uint16_t dist;
};

void writeStored(BitWriter& bw, const uint8_t* d, size_t start, size_t end, bool final) {
    size_t p = start;
    do {
        size_t len = std::min<size_t>(65535, end - p);
        bool fin = final && p + len == end;
        bw.put(fin ? 1 : 0, 1);
        bw.put(0, 2);
        bw.align();
        bw.out.push_back((uint8_t)len);
        bw.out.push_back((uint8_t)(len >> 8));
        bw.out.push_back((uint8_t)~len);
        bw.out.push_back((uint8_t)(~len >> 8));
        bw.out.insert(bw.out.end(), d + p, d + p + len);
        p += len;
    } while (p < end);
}

void flushBlock(BitWriter& bw, const uint8_t* d, size_t start, size_t end, const std::vector<Sym>& syms, bool final) {
    uint32_t lf[286] = {0}, df[30] = {0};
    for (const Sym& s : syms) {
        if (s.dist == 0) lf[s.litlen]++;
        else {
            lf[257 + lenCodeTab[s.litlen]]++;
            df[distCode(s.dist)]++;
        }
    }
    lf[256] = 1;
    uint8_t ll[286], dl[30];
    buildLengths(lf, 286, 15, ll);
    buildLengths(df, 30, 15, dl);
    if (!dl[0] && !dl[1] && std::all_of(dl, dl + 30, [](uint8_t v) { return v == 0; })) dl[0] = dl[1] = 1;

    int hlit = 286;
    while (hlit > 257 && ll[hlit - 1] == 0) hlit--;
    int hdist = 30;
    while (hdist > 1 && dl[hdist - 1] == 0) hdist--;
    std::vector<uint8_t> all(ll, ll + hlit);
    all.insert(all.end(), dl, dl + hdist);

    std::vector<std::pair<int, int>> rle;
    for (size_t i = 0; i < all.size();) {
        uint8_t v = all[i];
        size_t run = 1;
        while (i + run < all.size() && all[i + run] == v) run++;
        size_t r = run;
        if (v == 0) {
            while (r >= 11) {
                size_t t = std::min<size_t>(r, 138);
                rle.push_back({18, (uint8_t)(t - 11)});
                r -= t;
            }
            if (r >= 3) {
                rle.push_back({17, (uint8_t)(r - 3)});
                r = 0;
            }
        } else {
            rle.push_back({v, 0});
            r--;
            while (r >= 3) {
                size_t t = std::min<size_t>(r, 6);
                rle.push_back({16, (uint8_t)(t - 3)});
                r -= t;
            }
        }
        while (r--) rle.push_back({v, 0});
        i += run;
    }

    uint32_t cf[19] = {0};
    for (auto& p : rle) cf[p.first]++;
    uint8_t cl[19];
    buildLengths(cf, 19, 7, cl);
    int hclen = 19;
    while (hclen > 4 && cl[CL_ORDER[hclen - 1]] == 0) hclen--;

    // Cost comparison against stored blocks.
    uint64_t bits = 3 + 14 + 3ull * hclen;
    for (auto& p : rle) bits += cl[p.first] + (p.first == 16 ? 2 : p.first == 17 ? 3 : p.first == 18 ? 7 : 0);
    for (const Sym& s : syms) {
        if (s.dist == 0) bits += ll[s.litlen];
        else {
            int lc = lenCodeTab[s.litlen], dc = distCode(s.dist);
            bits += ll[257 + lc] + LEXTRA[lc] + dl[dc] + DEXTRA[dc];
        }
    }
    bits += ll[256];
    uint64_t storedBits = (end - start) * 8ull + ((end - start) / 65535 + 1) * 40;
    if (bits >= storedBits) {
        writeStored(bw, d, start, end, final);
        return;
    }

    uint16_t cc[19], lc[286], dc[30];
    makeCodes(cl, 19, cc);
    makeCodes(ll, 286, lc);
    makeCodes(dl, 30, dc);
    bw.put(final ? 1 : 0, 1);
    bw.put(2, 2);
    bw.put(hlit - 257, 5);
    bw.put(hdist - 1, 5);
    bw.put(hclen - 4, 4);
    for (int i = 0; i < hclen; i++) bw.put(cl[CL_ORDER[i]], 3);
    for (auto& p : rle) {
        bw.put(cc[p.first], cl[p.first]);
        if (p.first == 16) bw.put(p.second, 2);
        else if (p.first == 17) bw.put(p.second, 3);
        else if (p.first == 18) bw.put(p.second, 7);
    }
    for (const Sym& s : syms) {
        if (s.dist == 0) {
            bw.put(lc[s.litlen], ll[s.litlen]);
        } else {
            int c = lenCodeTab[s.litlen];
            bw.put(lc[257 + c], ll[257 + c]);
            if (LEXTRA[c]) bw.put(s.litlen - LBASE[c], LEXTRA[c]);
            int k = distCode(s.dist);
            bw.put(dc[k], dl[k]);
            if (DEXTRA[k]) bw.put(s.dist - DBASE[k], DEXTRA[k]);
        }
    }
    bw.put(lc[256], ll[256]);
}

} // namespace

uint32_t crc32_update(uint32_t crc, const uint8_t* p, size_t n) {
    crc = ~crc;
    while (n >= 8) {
        uint32_t a = crc ^ ((uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24);
        crc = crcTab[7][a & 0xFF] ^ crcTab[6][(a >> 8) & 0xFF] ^ crcTab[5][(a >> 16) & 0xFF] ^ crcTab[4][a >> 24] ^
              crcTab[3][p[4]] ^ crcTab[2][p[5]] ^ crcTab[1][p[6]] ^ crcTab[0][p[7]];
        p += 8;
        n -= 8;
    }
    while (n--) crc = (crc >> 8) ^ crcTab[0][(crc ^ *p++) & 0xFF];
    return ~crc;
}

std::vector<uint8_t> deflate_compress(const uint8_t* d, size_t n) {
    std::vector<uint8_t> out;
    out.reserve(n / 2 + 64);
    BitWriter bw{out};
    if (n == 0) {
        bw.put(1, 1);
        bw.put(1, 2);
        bw.put(0, 7);
        bw.align();
        return out;
    }

    const int WSIZE = 32768, WMASK = WSIZE - 1, HBITS = 15;
    std::vector<int32_t> head(1 << HBITS, -1), prev(WSIZE, -1);
    auto hash = [&](size_t i) {
        return ((uint32_t)d[i] | (uint32_t)d[i + 1] << 8 | (uint32_t)d[i + 2] << 16) * 2654435761u >> (32 - HBITS);
    };
    auto insert = [&](size_t i) {
        if (i + 2 < n) {
            uint32_t h = hash(i);
            prev[i & WMASK] = head[h];
            head[h] = (int32_t)i;
        }
    };
    auto find = [&](size_t i, int& bestDist) -> int {
        if (i + 2 >= n) return 0;
        int best = 2, maxLen = (int)std::min<size_t>(258, n - i), chain = 48;
        bestDist = 0;
        int32_t cand = head[hash(i)];
        const uint8_t* a = d + i;
        while (cand >= 0 && (int64_t)i - cand < WSIZE && chain-- > 0) {
            const uint8_t* b = d + cand;
            if (b[best] == a[best] && b[0] == a[0] && b[1] == a[1]) {
                int l = 2;
                while (l < maxLen && a[l] == b[l]) l++;
                if (l > best) {
                    best = l;
                    bestDist = (int)(i - cand);
                    if (l >= maxLen || l >= 128) break;
                }
            }
            cand = prev[cand & WMASK];
        }
        return best >= 3 ? best : 0;
    };

    std::vector<Sym> syms;
    syms.reserve(33000);
    size_t pos = 0, blockStart = 0;
    while (pos < n) {
        int dist = 0, len = find(pos, dist);
        if (len && len < 32) {
            insert(pos);
            int d2 = 0, l2 = find(pos + 1, d2);
            if (l2 > len) {
                syms.push_back({d[pos], 0});
                pos++;
                len = l2;
                dist = d2;
                syms.push_back({(uint16_t)len, (uint16_t)dist});
                for (int k = 0; k < len; k++) insert(pos + k);
            } else {
                syms.push_back({(uint16_t)len, (uint16_t)dist});
                for (int k = 1; k < len; k++) insert(pos + k);
            }
            pos += len;
        } else if (len) {
            syms.push_back({(uint16_t)len, (uint16_t)dist});
            for (int k = 0; k < len; k++) insert(pos + k);
            pos += len;
        } else {
            insert(pos);
            syms.push_back({d[pos], 0});
            pos++;
        }
        if (syms.size() >= 32000 || pos >= n) {
            flushBlock(bw, d, blockStart, pos, syms, pos >= n);
            syms.clear();
            blockStart = pos;
        }
    }
    bw.align();
    return out;
}
