#pragma once
// Small, dependency-free primitives for device pairing and PC Share: SHA-256, HMAC-SHA-256, a
// constant-time compare, and hex. Header-only so every platform build picks it up unchanged.
// Verified against the FIPS 180-4 and RFC 4231 test vectors in tests/crypto_test.cpp.
#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

namespace crypto {

using Digest = std::array<uint8_t, 32>;

class Sha256 {
public:
    Sha256() { reset(); }
    void reset() {
        static const uint32_t init[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                                         0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
        std::memcpy(h_, init, sizeof h_);
        len_ = 0;
        fill_ = 0;
    }
    void update(const void* data, size_t n) {
        const uint8_t* p = (const uint8_t*)data;
        len_ += n;
        while (n) {
            size_t take = std::min(n, sizeof block_ - fill_);
            std::memcpy(block_ + fill_, p, take);
            fill_ += take;
            p += take;
            n -= take;
            if (fill_ == sizeof block_) {
                compress(block_);
                fill_ = 0;
            }
        }
    }
    void update(const std::string& s) { update(s.data(), s.size()); }
    Digest finish() {
        uint64_t bits = len_ * 8;
        uint8_t pad = 0x80;
        update(&pad, 1);
        uint8_t zero = 0;
        while (fill_ != 56) update(&zero, 1);
        uint8_t length[8];
        for (int i = 0; i < 8; i++) length[i] = (uint8_t)(bits >> (56 - 8 * i));
        update(length, 8);
        Digest out;
        for (int i = 0; i < 8; i++) {
            out[(size_t)i * 4] = (uint8_t)(h_[i] >> 24);
            out[(size_t)i * 4 + 1] = (uint8_t)(h_[i] >> 16);
            out[(size_t)i * 4 + 2] = (uint8_t)(h_[i] >> 8);
            out[(size_t)i * 4 + 3] = (uint8_t)h_[i];
        }
        reset();
        return out;
    }

private:
    static uint32_t rotr(uint32_t x, int n) { return x >> n | x << (32 - n); }
    void compress(const uint8_t* b) {
        static const uint32_t k[64] = {
            0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
            0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
            0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
            0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
            0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
            0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
            0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
            0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};
        uint32_t w[64];
        for (int i = 0; i < 16; i++)
            w[i] = (uint32_t)b[i * 4] << 24 | (uint32_t)b[i * 4 + 1] << 16 | (uint32_t)b[i * 4 + 2] << 8 | b[i * 4 + 3];
        for (int i = 16; i < 64; i++) {
            uint32_t s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
            uint32_t s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16] + s0 + w[i - 7] + s1;
        }
        uint32_t a = h_[0], bb = h_[1], c = h_[2], d = h_[3], e = h_[4], f = h_[5], g = h_[6], h = h_[7];
        for (int i = 0; i < 64; i++) {
            uint32_t t1 = h + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + k[i] + w[i];
            uint32_t t2 = (rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & bb) ^ (a & c) ^ (bb & c));
            h = g; g = f; f = e; e = d + t1; d = c; c = bb; bb = a; a = t1 + t2;
        }
        h_[0] += a; h_[1] += bb; h_[2] += c; h_[3] += d; h_[4] += e; h_[5] += f; h_[6] += g; h_[7] += h;
    }
    uint32_t h_[8];
    uint8_t block_[64];
    size_t fill_ = 0;
    uint64_t len_ = 0;
};

inline Digest sha256(const void* data, size_t n) {
    Sha256 h;
    h.update(data, n);
    return h.finish();
}
inline Digest sha256(const std::string& s) { return sha256(s.data(), s.size()); }

inline Digest hmac_sha256(const void* key, size_t keyLen, const void* msg, size_t msgLen) {
    uint8_t k[64] = {};
    if (keyLen > 64) {
        Digest d = sha256(key, keyLen);
        std::memcpy(k, d.data(), d.size());
    } else if (keyLen) {
        std::memcpy(k, key, keyLen);
    }
    uint8_t ipad[64], opad[64];
    for (int i = 0; i < 64; i++) {
        ipad[i] = k[i] ^ 0x36;
        opad[i] = k[i] ^ 0x5c;
    }
    Sha256 inner;
    inner.update(ipad, 64);
    inner.update(msg, msgLen);
    Digest ih = inner.finish();
    Sha256 outer;
    outer.update(opad, 64);
    outer.update(ih.data(), ih.size());
    return outer.finish();
}
inline Digest hmac_sha256(const std::string& key, const std::string& msg) {
    return hmac_sha256(key.data(), key.size(), msg.data(), msg.size());
}

inline std::string to_hex(const uint8_t* p, size_t n) {
    static const char digits[] = "0123456789abcdef";
    std::string out;
    out.reserve(n * 2);
    for (size_t i = 0; i < n; i++) {
        out.push_back(digits[p[i] >> 4]);
        out.push_back(digits[p[i] & 15]);
    }
    return out;
}
inline std::string to_hex(const Digest& d) { return to_hex(d.data(), d.size()); }

// Equal strings compare equal without leaking where they first differ.
inline bool equal_constant_time(const std::string& a, const std::string& b) {
    uint8_t diff = a.size() != b.size();
    size_t n = std::min(a.size(), b.size());
    for (size_t i = 0; i < n; i++) diff |= (uint8_t)(a[i] ^ b[i]);
    return diff == 0;
}


// ---------------------------------------------------------------------------------------------
// X25519 (RFC 7748). Field arithmetic follows TweetNaCl: sixteen 16-bit limbs, branch-free swaps.
namespace detail {
using gf = int64_t[16];
inline void car25519(gf o) {
    for (int i = 0; i < 16; i++) {
        o[i] += (int64_t)1 << 16;
        int64_t c = o[i] >> 16;
        o[(i + 1) * (i < 15)] += c - 1 + 37 * (c - 1) * (i == 15);
        o[i] -= c << 16;
    }
}
inline void sel25519(gf p, gf q, int64_t b) {
    int64_t c = ~(b - 1);
    for (int i = 0; i < 16; i++) {
        int64_t t = c & (p[i] ^ q[i]);
        p[i] ^= t;
        q[i] ^= t;
    }
}
inline void pack25519(uint8_t* o, const gf n) {
    gf m, t;
    for (int i = 0; i < 16; i++) t[i] = n[i];
    car25519(t);
    car25519(t);
    car25519(t);
    for (int j = 0; j < 2; j++) {
        m[0] = t[0] - 0xffed;
        for (int i = 1; i < 15; i++) {
            m[i] = t[i] - 0xffff - ((m[i - 1] >> 16) & 1);
            m[i - 1] &= 0xffff;
        }
        m[15] = t[15] - 0x7fff - ((m[14] >> 16) & 1);
        int64_t b = (m[15] >> 16) & 1;
        m[14] &= 0xffff;
        sel25519(t, m, 1 - b);
    }
    for (int i = 0; i < 16; i++) {
        o[2 * i] = (uint8_t)(t[i] & 0xff);
        o[2 * i + 1] = (uint8_t)(t[i] >> 8);
    }
}
inline void unpack25519(gf o, const uint8_t* n) {
    for (int i = 0; i < 16; i++) o[i] = n[2 * i] + ((int64_t)n[2 * i + 1] << 8);
    o[15] &= 0x7fff;
}
inline void fadd(gf o, const gf a, const gf b) { for (int i = 0; i < 16; i++) o[i] = a[i] + b[i]; }
inline void fsub(gf o, const gf a, const gf b) { for (int i = 0; i < 16; i++) o[i] = a[i] - b[i]; }
inline void fmul(gf o, const gf a, const gf b) {
    int64_t t[31] = {};
    for (int i = 0; i < 16; i++)
        for (int j = 0; j < 16; j++) t[i + j] += a[i] * b[j];
    for (int i = 0; i < 15; i++) t[i] += 38 * t[i + 16];
    for (int i = 0; i < 16; i++) o[i] = t[i];
    car25519(o);
    car25519(o);
}
inline void fsq(gf o, const gf a) { fmul(o, a, a); }
inline void finv(gf o, const gf in) {
    gf c;
    for (int a = 0; a < 16; a++) c[a] = in[a];
    for (int a = 253; a >= 0; a--) {
        fsq(c, c);
        if (a != 2 && a != 4) fmul(c, c, in);
    }
    for (int a = 0; a < 16; a++) o[a] = c[a];
}
} // namespace detail

// out = scalar * point; both 32 bytes. Returns false when the result is all zero (a low-order point).
inline bool x25519(uint8_t out[32], const uint8_t scalar[32], const uint8_t point[32]) {
    using namespace detail;
    uint8_t z[32];
    int64_t x[80];
    gf a, b, c, d, e, f;
    static const gf k121665 = {0xDB41, 1};
    for (int i = 0; i < 32; i++) z[i] = scalar[i];
    z[31] = (uint8_t)((scalar[31] & 127) | 64);
    z[0] &= 248;
    unpack25519(x, point);
    for (int i = 0; i < 16; i++) {
        b[i] = x[i];
        d[i] = a[i] = c[i] = 0;
    }
    a[0] = d[0] = 1;
    for (int i = 254; i >= 0; --i) {
        int64_t r = (z[i >> 3] >> (i & 7)) & 1;
        sel25519(a, b, r);
        sel25519(c, d, r);
        fadd(e, a, c);
        fsub(a, a, c);
        fadd(c, b, d);
        fsub(b, b, d);
        fsq(d, e);
        fsq(f, a);
        fmul(a, c, a);
        fmul(c, b, e);
        fadd(e, a, c);
        fsub(a, a, c);
        fsq(b, a);
        fsub(c, d, f);
        fmul(a, c, k121665);
        fadd(a, a, d);
        fmul(c, c, a);
        fmul(a, d, f);
        fmul(d, b, x);
        fsq(b, e);
        sel25519(a, b, r);
        sel25519(c, d, r);
    }
    for (int i = 0; i < 16; i++) {
        x[i + 16] = a[i];
        x[i + 32] = c[i];
        x[i + 48] = b[i];
        x[i + 64] = d[i];
    }
    finv(x + 32, x + 32);
    fmul(x + 16, x + 16, x + 32);
    pack25519(out, x + 16);
    uint8_t any = 0;
    for (int i = 0; i < 32; i++) any |= out[i];
    return any != 0;
}

inline void x25519_public(uint8_t pub[32], const uint8_t secret[32]) {
    static const uint8_t base[32] = {9};
    x25519(pub, secret, base);
}

// ---------------------------------------------------------------------------------------------
// ChaCha20 and Poly1305 (RFC 8439) and the combined AEAD.
namespace detail {
inline uint32_t rotl(uint32_t x, int n) { return x << n | x >> (32 - n); }
inline uint32_t le32(const uint8_t* p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
inline void put_le32(uint8_t* p, uint32_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24); }

inline void chacha_block(uint8_t out[64], const uint8_t key[32], uint32_t counter, const uint8_t nonce[12]) {
    uint32_t s[16] = {0x61707865, 0x3320646e, 0x79622d32, 0x6b206574};
    for (int i = 0; i < 8; i++) s[4 + i] = le32(key + 4 * i);
    s[12] = counter;
    for (int i = 0; i < 3; i++) s[13 + i] = le32(nonce + 4 * i);
    uint32_t w[16];
    std::memcpy(w, s, sizeof w);
    auto qr = [&](int a, int b, int c, int d) {
        w[a] += w[b]; w[d] = rotl(w[d] ^ w[a], 16);
        w[c] += w[d]; w[b] = rotl(w[b] ^ w[c], 12);
        w[a] += w[b]; w[d] = rotl(w[d] ^ w[a], 8);
        w[c] += w[d]; w[b] = rotl(w[b] ^ w[c], 7);
    };
    for (int i = 0; i < 10; i++) {
        qr(0, 4, 8, 12); qr(1, 5, 9, 13); qr(2, 6, 10, 14); qr(3, 7, 11, 15);
        qr(0, 5, 10, 15); qr(1, 6, 11, 12); qr(2, 7, 8, 13); qr(3, 4, 9, 14);
    }
    for (int i = 0; i < 16; i++) put_le32(out + 4 * i, w[i] + s[i]);
}

inline void chacha_xor(uint8_t* data, size_t n, const uint8_t key[32], uint32_t counter, const uint8_t nonce[12]) {
    uint8_t block[64];
    for (size_t off = 0; off < n; off += 64, counter++) {
        chacha_block(block, key, counter, nonce);
        size_t take = std::min<size_t>(64, n - off);
        for (size_t i = 0; i < take; i++) data[off + i] ^= block[i];
    }
}

class Poly1305 {
public:
    explicit Poly1305(const uint8_t key[32]) {
        r_[0] = le32(key) & 0x3ffffff;
        r_[1] = (le32(key + 3) >> 2) & 0x3ffff03;
        r_[2] = (le32(key + 6) >> 4) & 0x3ffc0ff;
        r_[3] = (le32(key + 9) >> 6) & 0x3f03fff;
        r_[4] = (le32(key + 12) >> 8) & 0x00fffff;
        for (int i = 0; i < 4; i++) pad_[i] = le32(key + 16 + 4 * i);
    }
    void update(const uint8_t* m, size_t n) {
        while (n) {
            size_t take = std::min(n, sizeof buf_ - fill_);
            std::memcpy(buf_ + fill_, m, take);
            fill_ += take;
            m += take;
            n -= take;
            if (fill_ == 16) {
                block(buf_, 1u << 24);
                fill_ = 0;
            }
        }
    }
    void finish(uint8_t tag[16]) {
        if (fill_) {
            buf_[fill_] = 1;
            for (size_t i = fill_ + 1; i < 16; i++) buf_[i] = 0;
            block(buf_, 0);
        }
        uint32_t h0 = h_[0], h1 = h_[1], h2 = h_[2], h3 = h_[3], h4 = h_[4], c;
        c = h1 >> 26; h1 &= 0x3ffffff; h2 += c;
        c = h2 >> 26; h2 &= 0x3ffffff; h3 += c;
        c = h3 >> 26; h3 &= 0x3ffffff; h4 += c;
        c = h4 >> 26; h4 &= 0x3ffffff; h0 += c * 5;
        c = h0 >> 26; h0 &= 0x3ffffff; h1 += c;
        uint32_t g0 = h0 + 5; c = g0 >> 26; g0 &= 0x3ffffff;
        uint32_t g1 = h1 + c; c = g1 >> 26; g1 &= 0x3ffffff;
        uint32_t g2 = h2 + c; c = g2 >> 26; g2 &= 0x3ffffff;
        uint32_t g3 = h3 + c; c = g3 >> 26; g3 &= 0x3ffffff;
        uint32_t g4 = h4 + c - (1u << 26);
        uint32_t mask = (g4 >> 31) - 1;
        g0 &= mask; g1 &= mask; g2 &= mask; g3 &= mask; g4 &= mask;
        mask = ~mask;
        h0 = (h0 & mask) | g0; h1 = (h1 & mask) | g1; h2 = (h2 & mask) | g2; h3 = (h3 & mask) | g3; h4 = (h4 & mask) | g4;
        uint32_t w0 = h0 | h1 << 26, w1 = h1 >> 6 | h2 << 20, w2 = h2 >> 12 | h3 << 14, w3 = h3 >> 18 | h4 << 8;
        uint64_t f = (uint64_t)w0 + pad_[0];
        put_le32(tag, (uint32_t)f);
        f = (uint64_t)w1 + pad_[1] + (f >> 32);
        put_le32(tag + 4, (uint32_t)f);
        f = (uint64_t)w2 + pad_[2] + (f >> 32);
        put_le32(tag + 8, (uint32_t)f);
        f = (uint64_t)w3 + pad_[3] + (f >> 32);
        put_le32(tag + 12, (uint32_t)f);
    }

private:
    void block(const uint8_t* m, uint32_t hibit) {
        const uint32_t r0 = r_[0], r1 = r_[1], r2 = r_[2], r3 = r_[3], r4 = r_[4];
        const uint32_t s1 = r1 * 5, s2 = r2 * 5, s3 = r3 * 5, s4 = r4 * 5;
        uint32_t h0 = h_[0] + (le32(m) & 0x3ffffff);
        uint32_t h1 = h_[1] + ((le32(m + 3) >> 2) & 0x3ffffff);
        uint32_t h2 = h_[2] + ((le32(m + 6) >> 4) & 0x3ffffff);
        uint32_t h3 = h_[3] + ((le32(m + 9) >> 6) & 0x3ffffff);
        uint32_t h4 = h_[4] + ((le32(m + 12) >> 8) | hibit);
        uint64_t d0 = (uint64_t)h0 * r0 + (uint64_t)h1 * s4 + (uint64_t)h2 * s3 + (uint64_t)h3 * s2 + (uint64_t)h4 * s1;
        uint64_t d1 = (uint64_t)h0 * r1 + (uint64_t)h1 * r0 + (uint64_t)h2 * s4 + (uint64_t)h3 * s3 + (uint64_t)h4 * s2;
        uint64_t d2 = (uint64_t)h0 * r2 + (uint64_t)h1 * r1 + (uint64_t)h2 * r0 + (uint64_t)h3 * s4 + (uint64_t)h4 * s3;
        uint64_t d3 = (uint64_t)h0 * r3 + (uint64_t)h1 * r2 + (uint64_t)h2 * r1 + (uint64_t)h3 * r0 + (uint64_t)h4 * s4;
        uint64_t d4 = (uint64_t)h0 * r4 + (uint64_t)h1 * r3 + (uint64_t)h2 * r2 + (uint64_t)h3 * r1 + (uint64_t)h4 * r0;
        uint32_t c = (uint32_t)(d0 >> 26); h0 = (uint32_t)d0 & 0x3ffffff;
        d1 += c; c = (uint32_t)(d1 >> 26); h1 = (uint32_t)d1 & 0x3ffffff;
        d2 += c; c = (uint32_t)(d2 >> 26); h2 = (uint32_t)d2 & 0x3ffffff;
        d3 += c; c = (uint32_t)(d3 >> 26); h3 = (uint32_t)d3 & 0x3ffffff;
        d4 += c; c = (uint32_t)(d4 >> 26); h4 = (uint32_t)d4 & 0x3ffffff;
        h0 += c * 5; c = h0 >> 26; h0 &= 0x3ffffff; h1 += c;
        h_[0] = h0; h_[1] = h1; h_[2] = h2; h_[3] = h3; h_[4] = h4;
    }
    uint32_t r_[5], h_[5] = {}, pad_[4];
    uint8_t buf_[16];
    size_t fill_ = 0;
};

inline void poly_aead_tag(uint8_t tag[16], const uint8_t key[32], const uint8_t nonce[12], const uint8_t* aad, size_t aadLen,
                          const uint8_t* ct, size_t ctLen) {
    uint8_t polyKey[64];
    chacha_block(polyKey, key, 0, nonce);
    Poly1305 mac(polyKey);
    static const uint8_t zeros[16] = {};
    mac.update(aad, aadLen);
    mac.update(zeros, (16 - aadLen % 16) % 16);
    mac.update(ct, ctLen);
    mac.update(zeros, (16 - ctLen % 16) % 16);
    uint8_t lengths[16];
    for (int i = 0; i < 8; i++) {
        lengths[i] = (uint8_t)((uint64_t)aadLen >> (8 * i));
        lengths[8 + i] = (uint8_t)((uint64_t)ctLen >> (8 * i));
    }
    mac.update(lengths, 16);
    mac.finish(tag);
}
} // namespace detail

constexpr size_t AEAD_TAG = 16;

// ChaCha20-Poly1305: out receives n bytes of ciphertext followed by the 16-byte tag.
inline void aead_seal(uint8_t* out, const uint8_t key[32], const uint8_t nonce[12], const uint8_t* aad, size_t aadLen,
                      const uint8_t* plain, size_t n) {
    if (n) std::memcpy(out, plain, n);
    detail::chacha_xor(out, n, key, 1, nonce);
    detail::poly_aead_tag(out + n, key, nonce, aad, aadLen, out, n);
}

// in holds n + 16 bytes. Verifies the tag before decrypting; false leaves out untouched.
inline bool aead_open(uint8_t* out, const uint8_t key[32], const uint8_t nonce[12], const uint8_t* aad, size_t aadLen,
                      const uint8_t* in, size_t n) {
    uint8_t tag[16];
    detail::poly_aead_tag(tag, key, nonce, aad, aadLen, in, n);
    uint8_t diff = 0;
    for (size_t i = 0; i < 16; i++) diff |= (uint8_t)(tag[i] ^ in[n + i]);
    if (diff) return false;
    if (n) std::memcpy(out, in, n);
    detail::chacha_xor(out, n, key, 1, nonce);
    return true;
}

} // namespace crypto
