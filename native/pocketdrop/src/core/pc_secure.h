#pragma once
// PC Share v2: the two PCs agree a key with X25519, both show the same six-digit code so a person can
// confirm nobody sits between them, and everything after that travels as ChaCha20-Poly1305 frames.
//
//   key   = HMAC(prk, "key"   || pkSender || pkReceiver)   (encrypts the data)
//   claim = HMAC(prk, "claim" || ...)                      (names the receiver after pairing; only the two PCs know it)
//   code  = HMAC(prk, "code"  || ...) -> six digits         (shown on both screens)
//
// A frame is a 4-byte little-endian plaintext length (0 ends the stream), the ciphertext, and a 16-byte
// tag over both; the nonce counts frames, so reordering, dropping, repeating or cutting off frames fails.
#include "crypto.h"
#include "platform.h"
#include <functional>
#include <vector>

namespace pcsecure {

constexpr size_t FRAME = 64 * 1024;
constexpr size_t HEADER = 4;

struct Keys {
    uint8_t key[32] = {};
    std::string claim; // 32 characters, safe for the receiver header
    std::string code;  // "123 456"
};

inline void make_keypair(uint8_t secret[32], uint8_t pub[32]) {
    plat::random_bytes(secret, 32);
    crypto::x25519_public(pub, secret);
}

// False when the peer's public key is a low-order point (no real secret can come out of it).
inline bool derive(const uint8_t secret[32], const uint8_t peerPub[32], const uint8_t pkSender[32], const uint8_t pkReceiver[32],
                   Keys& out) {
    uint8_t shared[32];
    if (!crypto::x25519(shared, secret, peerPub)) return false;
    static const std::string salt = "PocketDrop PC Share v2";
    crypto::Digest prk = crypto::hmac_sha256(salt.data(), salt.size(), shared, 32);
    std::string context((const char*)pkSender, 32);
    context.append((const char*)pkReceiver, 32);
    auto part = [&](const char* label) {
        std::string msg = std::string(label) + context;
        return crypto::hmac_sha256(prk.data(), prk.size(), msg.data(), msg.size());
    };
    crypto::Digest key = part("key");
    std::memcpy(out.key, key.data(), 32);
    out.claim = crypto::to_hex(part("claim")).substr(0, 32);
    crypto::Digest c = part("code");
    uint32_t v = (uint32_t)c[0] | (uint32_t)c[1] << 8 | (uint32_t)c[2] << 16 | (uint32_t)c[3] << 24;
    std::string digits = std::to_string(v % 1000000);
    digits.insert(0, 6 - digits.size(), '0');
    out.code = digits.substr(0, 3) + " " + digits.substr(3);
    return true;
}

inline void nonce_for(uint8_t nonce[12], uint64_t counter) {
    std::memset(nonce, 0, 12);
    for (int i = 0; i < 8; i++) nonce[4 + i] = (uint8_t)(counter >> (8 * i));
}

// The sealed length of `size` bytes of data sent as frames, including the closing frame.
inline uint64_t sealed_size(uint64_t size) {
    uint64_t frames = (size + FRAME - 1) / FRAME + 1;
    return size + frames * (HEADER + crypto::AEAD_TAG);
}

// Small one-off messages (the file's name and size) travel as a single sealed block with counter 0.
inline std::string seal_message(const Keys& k, const std::string& plain) {
    uint8_t nonce[12];
    nonce_for(nonce, 0);
    std::string out(plain.size() + crypto::AEAD_TAG, '\0');
    crypto::aead_seal((uint8_t*)out.data(), k.key, nonce, (const uint8_t*)"pdpc2 info", 10, (const uint8_t*)plain.data(), plain.size());
    return out;
}

inline bool open_message(const Keys& k, const std::string& sealed, std::string& plain) {
    if (sealed.size() < crypto::AEAD_TAG || sealed.size() > 8192) return false;
    uint8_t nonce[12];
    nonce_for(nonce, 0);
    plain.assign(sealed.size() - crypto::AEAD_TAG, '\0');
    return crypto::aead_open((uint8_t*)plain.data(), k.key, nonce, (const uint8_t*)"pdpc2 info", 10, (const uint8_t*)sealed.data(),
                             plain.size());
}

// Turns a stream of plaintext into frames. emit gets each finished frame; it returns false to stop.
class Encoder {
public:
    using Emit = std::function<bool(const uint8_t*, size_t)>;
    explicit Encoder(const Keys& k) : k_(k) { pending_.reserve(FRAME); }
    bool push(const uint8_t* data, size_t n, const Emit& emit) {
        while (n) {
            size_t take = std::min(n, FRAME - pending_.size());
            pending_.insert(pending_.end(), data, data + take);
            data += take;
            n -= take;
            if (pending_.size() == FRAME && !flush(emit)) return false;
        }
        return true;
    }
    // Sends what is left and the closing frame.
    bool finish(const Emit& emit) { return (pending_.empty() || flush(emit)) && flush(emit); }

private:
    bool flush(const Emit& emit) {
        std::vector<uint8_t> frame(HEADER + pending_.size() + crypto::AEAD_TAG);
        uint32_t n = (uint32_t)pending_.size();
        for (int i = 0; i < 4; i++) frame[(size_t)i] = (uint8_t)(n >> (8 * i));
        uint8_t nonce[12];
        nonce_for(nonce, ++counter_);
        crypto::aead_seal(frame.data() + HEADER, k_.key, nonce, frame.data(), HEADER, pending_.data(), pending_.size());
        pending_.clear();
        return emit(frame.data(), frame.size());
    }
    Keys k_;
    std::vector<uint8_t> pending_;
    uint64_t counter_ = 0;
};

// Rebuilds the plaintext from whatever slices of the stream arrive. Any damage, reordering, cut-off or
// trailing data makes it fail for good.
class Decoder {
public:
    using Emit = std::function<bool(const uint8_t*, size_t)>;
    explicit Decoder(const Keys& k) : k_(k) {}
    bool push(const uint8_t* data, size_t n, const Emit& emit) {
        if (failed_) return false;
        while (n) {
            if (finished_) return fail(); // nothing may follow the closing frame
            size_t want = need();
            size_t take = std::min(n, want - buf_.size());
            buf_.insert(buf_.end(), data, data + take);
            data += take;
            n -= take;
            if (buf_.size() < want) continue;
            if (!haveHeader_) {
                len_ = (uint32_t)buf_[0] | (uint32_t)buf_[1] << 8 | (uint32_t)buf_[2] << 16 | (uint32_t)buf_[3] << 24;
                if (len_ > FRAME) return fail();
                haveHeader_ = true;
                continue; // body (ciphertext + tag) comes next, even when len_ is 0
            }
            std::vector<uint8_t> plain(len_);
            uint8_t nonce[12];
            nonce_for(nonce, ++counter_);
            if (!crypto::aead_open(plain.data(), k_.key, nonce, buf_.data(), HEADER, buf_.data() + HEADER, len_)) return fail();
            haveHeader_ = false;
            buf_.clear();
            if (len_ == 0) finished_ = true;
            else if (!emit(plain.data(), plain.size())) return fail();
        }
        return true;
    }
    bool complete() const { return finished_ && !failed_ && buf_.empty(); }

private:
    // Bytes needed before the next step: the header, then body and tag.
    size_t need() const { return haveHeader_ ? HEADER + len_ + crypto::AEAD_TAG : HEADER; }
    bool fail() {
        failed_ = true;
        return false;
    }
    Keys k_;
    std::vector<uint8_t> buf_;
    bool haveHeader_ = false, finished_ = false, failed_ = false;
    uint32_t len_ = 0;
    uint64_t counter_ = 0;
};

} // namespace pcsecure
