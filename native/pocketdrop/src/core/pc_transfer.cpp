#include "pc_transfer.h"
#include "deflate.h"
#include "pc_secure.h"
#include "util.h"
#include <algorithm>
#include <charconv>

namespace {
bool token(const std::string& s) {
    return s.size() == 32 && s.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_") == std::string::npos;
}

bool safeLeaf(const std::string& name) {
    if (name.empty() || name.size() > 1024 || name == "." || name == ".." || name.back() == '.' || name.back() == ' ') return false;
    for (unsigned char c : name) if (c < 32 || c == 127 || std::string("/\\:*?\"<>|").find(c) != std::string::npos) return false;
    auto stem = util::lower(name.substr(0, name.find('.')));
    if (stem == "con" || stem == "prn" || stem == "aux" || stem == "nul") return false;
    if (stem.size() == 4 && (stem.rfind("com", 0) == 0 || stem.rfind("lpt", 0) == 0) && stem[3] >= '1' && stem[3] <= '9') return false;
    return true;
}

std::string numbered(const std::string& name, int n) {
    if (n == 1) return name;
    auto dot = name.rfind('.');
    if (dot == std::string::npos || dot == 0) dot = name.size();
    return name.substr(0, dot) + " (" + std::to_string(n) + ")" + name.substr(dot);
}
}

bool PcReceiver::parseInfo(const std::string& body, std::string& name, uint64_t& size) {
    if (body.size() > 4096 || body.rfind("PDPC1\n", 0) != 0) return false;
    size_t end = body.find('\n', 6);
    if (end == std::string::npos || end == 6) return false;
    uint64_t value = 0;
    auto result = std::from_chars(body.data() + 6, body.data() + end, value);
    auto leaf = body.substr(end + 1);
    if (result.ec != std::errc{} || result.ptr != body.data() + end || value > INT64_MAX || !safeLeaf(leaf)) return false;
    name = leaf; size = value;
    return true;
}

void PcReceiver::start(const NearbyPeer& peer, const std::string& inbox) {
    stop();
    cancel_ = false;
    {
        std::lock_guard<std::mutex> lock(mu_);
        state_ = {};
        state_.phase = PcDownload::Phase::Connecting;
        state_.device = peer.name;
    }
    worker_ = std::thread([this, peer, inbox] { run(peer, inbox); });
}

void PcReceiver::stop() {
    cancel_ = true;
    if (worker_.joinable()) worker_.join();
}

PcDownload PcReceiver::snapshot() const {
    std::lock_guard<std::mutex> lock(mu_);
    return state_;
}

void PcReceiver::fail(const std::string& message) {
    std::lock_guard<std::mutex> lock(mu_);
    state_.phase = cancel_ ? PcDownload::Phase::Cancelled : PcDownload::Phase::Failed;
    state_.error = cancel_ ? "Transfer cancelled" : message;
}

void PcReceiver::run(NearbyPeer peer, std::string inbox) {
    if (!token(peer.offer) || peer.port < 1 || peer.port > 65535 || peer.ip.empty() || peer.ip.size() > 15 ||
        peer.ip.find_first_not_of("0123456789.") != std::string::npos) { fail("Invalid sender address"); return; }
    const auto base = "http://" + peer.ip + ":" + std::to_string(peer.port) + "/pc/" + peer.offer + "/";
    const auto receiver = util::random_token(24); // names the partial file; the pairing has its own claim
    auto host = plat::host_name();
    host.erase(std::remove_if(host.begin(), host.end(), [](unsigned char c) { return c < 32 || c == 127; }), host.end());

    // Key exchange: send our public key, get theirs, and show the code both PCs derive.
    uint8_t secret[32], pub[32], peerPub[32];
    pcsecure::make_keypair(secret, pub);
    auto hello = plat::http_get(base + "hello",
                                {"X-PocketDrop-Receiver: " + receiver, "X-PocketDrop-Name: " + host,
                                 "X-PocketDrop-Key: " + crypto::to_hex(pub, 32), "Connection: close"},
                                plat::BAD_FILE, {}, 3000);
    if (cancel_) { fail({}); return; }
    if (hello.status == 409) { fail("Another PC is already receiving this transfer."); return; }
    pcsecure::Keys keys;
    bool keyOk = hello.status == 200 && hello.body.size() == 64;
    if (keyOk) {
        for (size_t i = 0; i < 32 && keyOk; i++) {
            auto digit = [](char c) { return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1; };
            int hi = digit(hello.body[2 * i]), lo = digit(hello.body[2 * i + 1]);
            keyOk = hi >= 0 && lo >= 0;
            if (keyOk) peerPub[i] = (uint8_t)(hi << 4 | lo);
        }
    }
    if (!keyOk || !pcsecure::derive(secret, peerPub, peerPub, pub, keys)) {
        fail("The sender stopped sharing, couldn't be reached, or needs the updated PocketDrop. Try Receive again.");
        return;
    }
    std::vector<std::string> headers = {"X-PocketDrop-Receiver: " + keys.claim, "X-PocketDrop-Name: " + host, "Connection: close"};
    auto control = [&](const std::string& action) {
        return plat::http_get(base + action, headers, plat::BAD_FILE, {}, 3000);
    };
    {
        std::lock_guard<std::mutex> lock(mu_);
        state_.phase = PcDownload::Phase::Pairing;
        state_.code = keys.code;
    }
    // The person at the sender compares the codes and accepts.
    bool accepted = false;
    for (uint64_t waited = 0; !cancel_ && waited < 120000; waited += 400) {
        auto status = control("status");
        if (status.status != 200) { fail("The sender stopped sharing or couldn't be reached. Try Receive again."); return; }
        if (status.body == "declined") { control("cancel"); fail("The sender declined the transfer."); return; }
        if (status.body == "accepted") { accepted = true; break; }
        plat::sleep_ms(400);
    }
    if (cancel_) { control("cancel"); fail({}); return; }
    if (!accepted) { control("cancel"); fail("The sender didn't accept in time. Choose Receive again."); return; }

    auto info = control("info");
    std::string name, infoText;
    uint64_t size = 0;
    if (cancel_) { fail({}); return; }
    if (info.status != 200 || !pcsecure::open_message(keys, info.body, infoText) || !parseInfo(infoText, name, size)) {
        control("cancel");
        fail("The sender stopped sharing or couldn't be reached. Try Receive again.");
        return;
    }
    plat::make_dir(inbox);
    uint64_t space = plat::free_disk_space(inbox);
    if (space != UINT64_MAX && (space < size || space - size < (16ull << 20))) { fail("Not enough disk space in the received-files folder."); return; }
    auto partial = util::path_join(inbox, "PocketDrop-" + receiver + ".pcpart");
    auto output = plat::file_create(partial);
    if (output == plat::BAD_FILE) { fail("Couldn't create a file in the received-files folder."); return; }
    {
        std::lock_guard<std::mutex> lock(mu_);
        state_.name = name; state_.total = size; state_.phase = PcDownload::Phase::Receiving;
    }
    plat::HttpResult response;
    const uint64_t sealed = pcsecure::sealed_size(size);
    bool streamIntact = false;
    for (int attempt = 0; attempt < 5; ++attempt) {
        // The sender's frames are opened as they arrive, so the file on disk is already the plain data.
        pcsecure::Decoder decoder(keys);
        uint64_t plain = 0;
        auto sink = [&](const uint8_t* data, size_t n) {
            return decoder.push(data, n, [&](const uint8_t* p, size_t m) {
                plain += m;
                return plain <= size && plat::file_write(output, p, m);
            });
        };
        response = plat::http_get(base + "file", headers, output, [&](uint64_t, uint64_t total) {
            std::lock_guard<std::mutex> lock(mu_);
            state_.received = std::min(plain, size);
            // Small HTTP error bodies must retain their status (e.g. 409) so a
            // released receiver claim can be retried. Successful files still
            // require the exact advertised size and checksum before saving.
            return !cancel_ && (!total || total == sealed || total <= 4096);
        }, 3000, sink);
        plat::file_close(output);
        streamIntact = decoder.complete() && plain == size;
        if (response.status != 409 || cancel_ || attempt == 4) break;
        // A cancelled receiver may still be unwinding its socket on the sender.
        plat::sleep_ms(100);
        output = plat::file_create(partial);
        if (output == plat::BAD_FILE) { response.status = 0; break; }
    }
    auto discard = [&](const std::string& message) {
        plat::file_remove(partial);
        control("cancel");
        fail(message);
    };
    if (cancel_ || response.status != 200 || !streamIntact || plat::file_stat(partial).size != size) {
        discard(response.status == 409 ? "Another PC is already receiving this transfer." :
                "Transfer interrupted. Choose Receive again to retry.");
        return;
    }
    { std::lock_guard<std::mutex> lock(mu_); state_.phase = PcDownload::Phase::Verifying; }
    auto input = plat::file_open_read(partial);
    bool ok = input != plat::BAD_FILE;
    uint32_t crc = 0;
    uint64_t keepaliveAt = plat::tick_ms();
    std::vector<uint8_t> buffer(1 << 20);
    while (ok && !cancel_) {
        auto n = plat::file_read(input, buffer.data(), buffer.size());
        if (n <= 0) { ok = n == 0; break; }
        crc = crc32_update(crc, buffer.data(), (size_t)n);
        if (plat::tick_ms() - keepaliveAt >= 5000) {
            ok = control("keepalive").status == 204;
            keepaliveAt = plat::tick_ms();
        }
    }
    if (input != plat::BAD_FILE) plat::file_close(input);
    if (!ok || cancel_) { discard("Couldn't verify the received file."); return; }
    auto query = "?crc=" + std::to_string(crc);
    auto verification = control("verify" + query);
    // The receiver can finish reading just before the sender records stream completion.
    for (int i = 0; i < 5 && verification.status == 409 && !cancel_; ++i) {
        plat::sleep_ms(100); verification = control("verify" + query);
    }
    if (verification.status != 204 || cancel_) { discard("The transfer couldn't be verified. Choose Receive again to retry."); return; }
    std::string saved;
    for (int n = 1; n <= 10000 && !cancel_; ++n) {
        auto path = util::path_join(inbox, numbered(name, n));
        if (plat::file_rename_new(partial, path)) { saved = path; break; }
        if (!plat::file_stat(path).exists) break;
    }
    if (saved.empty()) { discard("Couldn't save the received file. Check the destination folder."); return; }
    auto receipt = control("complete" + query);
    for (int i = 0; i < 2 && receipt.status != 204; ++i) receipt = control("complete" + query);
    {
        std::lock_guard<std::mutex> lock(mu_);
        state_.path = saved; state_.name = util::path_leaf(saved); state_.received = size;
        state_.phase = PcDownload::Phase::Done;
        if (receipt.status != 204) state_.error = "File saved; the sender's confirmation was not received.";
    }
}
