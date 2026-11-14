#pragma once
// Text formats for what PocketDrop keeps between runs: the transfer history and remembered phones.
// One record per line, fields separated by tabs, with tab, newline and backslash escaped inside a
// field, so a hostile file or device name can never add a record or change another field.
#include "http.h"
#include <cstdint>
#include <string>
#include <vector>

namespace persist {

struct HistoryEntry {
    enum Kind : char { FileIn = 'f', NoteIn = 'n', SentToPhone = 's', PcIn = 'p' };
    char kind = FileIn;
    int64_t time = 0; // unix seconds
    uint64_t size = 0;
    std::string name, device, path, text;
};

constexpr size_t MAX_HISTORY = 100;
constexpr size_t MAX_NOTE_KEPT = 500;

inline std::string esc(const std::string& s) {
    std::string out;
    for (char c : s) {
        if (c == '\\') out += "\\\\";
        else if (c == '\t') out += "\\t";
        else if (c == '\n') out += "\\n";
        else if (c == '\r') out += "\\r";
        else out += c;
    }
    return out;
}

inline std::string unesc(const std::string& s) {
    std::string out;
    for (size_t i = 0; i < s.size(); i++) {
        if (s[i] == '\\' && i + 1 < s.size()) {
            char n = s[++i];
            out += n == 't' ? '\t' : n == 'n' ? '\n' : n == 'r' ? '\r' : n;
        } else {
            out += s[i];
        }
    }
    return out;
}

inline std::vector<std::string> split(const std::string& s, char sep) {
    std::vector<std::string> parts;
    size_t from = 0;
    for (;;) {
        size_t at = s.find(sep, from);
        if (at == std::string::npos) {
            parts.push_back(s.substr(from));
            return parts;
        }
        parts.push_back(s.substr(from, at - from));
        from = at + 1;
    }
}

inline std::string serialize(const std::vector<HistoryEntry>& entries) {
    std::string out;
    size_t first = entries.size() > MAX_HISTORY ? entries.size() - MAX_HISTORY : 0;
    for (size_t i = first; i < entries.size(); i++) {
        const HistoryEntry& e = entries[i];
        std::string text = e.text.size() > MAX_NOTE_KEPT ? e.text.substr(0, MAX_NOTE_KEPT) : e.text;
        out += std::string(1, e.kind) + "\t" + std::to_string(e.time) + "\t" + std::to_string(e.size) + "\t" + esc(e.name) +
               "\t" + esc(e.device) + "\t" + esc(e.path) + "\t" + esc(text) + "\n";
    }
    return out;
}

inline std::vector<HistoryEntry> parse_history(const std::string& data) {
    std::vector<HistoryEntry> entries;
    for (const std::string& line : split(data, '\n')) {
        auto f = split(line, '\t');
        if (f.size() != 7 || f[0].size() != 1 || std::string("fnsp").find(f[0][0]) == std::string::npos) continue;
        HistoryEntry e;
        e.kind = f[0][0];
        char* end = nullptr;
        e.time = std::strtoll(f[1].c_str(), &end, 10);
        if (!end || *end) continue;
        e.size = std::strtoull(f[2].c_str(), &end, 10);
        if (!end || *end) continue;
        e.name = unesc(f[3]);
        e.device = unesc(f[4]);
        e.path = unesc(f[5]);
        e.text = unesc(f[6]);
        entries.push_back(std::move(e));
    }
    if (entries.size() > MAX_HISTORY) entries.erase(entries.begin(), entries.end() - (long)MAX_HISTORY);
    return entries;
}

inline std::string serialize(const std::vector<TrustedDevice>& devices) {
    std::string out;
    for (const TrustedDevice& d : devices)
        out += esc(d.id) + "\t" + esc(d.secret) + "\t" + std::to_string(d.created) + "\t" + std::to_string(d.lastUsed) + "\t" +
               esc(d.name) + "\n";
    return out;
}

inline std::vector<TrustedDevice> parse_devices(const std::string& data) {
    std::vector<TrustedDevice> devices;
    for (const std::string& line : split(data, '\n')) {
        auto f = split(line, '\t');
        if (f.size() != 5) continue;
        TrustedDevice d;
        d.id = unesc(f[0]);
        d.secret = unesc(f[1]);
        char* end = nullptr;
        d.created = std::strtoll(f[2].c_str(), &end, 10);
        if (!end || *end) continue;
        d.lastUsed = std::strtoll(f[3].c_str(), &end, 10);
        if (!end || *end) continue;
        d.name = unesc(f[4]);
        devices.push_back(std::move(d));
    }
    return devices;
}

// "just now", "5 min ago", "3 h ago", "2 d ago".
inline std::string ago(int64_t seconds) {
    if (seconds < 45) return "just now";
    if (seconds < 90 * 60) return std::to_string(std::max<int64_t>(1, (seconds + 30) / 60)) + " min ago";
    if (seconds < 36 * 3600) return std::to_string((seconds + 1800) / 3600) + " h ago";
    return std::to_string((seconds + 43200) / 86400) + " d ago";
}

} // namespace persist
