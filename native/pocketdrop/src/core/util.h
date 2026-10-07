#pragma once
#include "platform.h"
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <string>

namespace util {

inline std::string format_size(uint64_t b) {
    static const char* units[] = {"B", "KB", "MB", "GB", "TB"};
    double v = (double)b;
    int u = 0;
    while (v >= 1024.0 && u < 4) {
        v /= 1024.0;
        u++;
    }
    char buf[32];
    if (u == 0) snprintf(buf, sizeof buf, "%llu B", (unsigned long long)b);
    else snprintf(buf, sizeof buf, v < 10 ? "%.2f %s" : v < 100 ? "%.1f %s" : "%.0f %s", v, units[u]);
    return buf;
}

// URL-safe random token (base64url, no padding).
inline std::string random_token(size_t bytes) {
    static const char* abc = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    std::string raw(bytes, '\0');
    plat::random_bytes((uint8_t*)raw.data(), bytes);
    std::string out;
    uint32_t acc = 0;
    int bits = 0;
    for (unsigned char c : raw) {
        acc = (acc << 8) | c;
        bits += 8;
        while (bits >= 6) {
            out.push_back(abc[(acc >> (bits - 6)) & 63]);
            bits -= 6;
        }
    }
    if (bits) out.push_back(abc[(acc << (6 - bits)) & 63]);
    return out;
}

inline std::string url_encode(const std::string& s) {
    static const char* hex = "0123456789ABCDEF";
    std::string o;
    for (unsigned char c : s) {
        if (isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') o.push_back((char)c);
        else {
            o.push_back('%');
            o.push_back(hex[c >> 4]);
            o.push_back(hex[c & 15]);
        }
    }
    return o;
}

inline std::string json_escape(const std::string& s) {
    std::string o;
    o.reserve(s.size() + 8);
    for (unsigned char c : s) {
        switch (c) {
        case '"': o += "\\\""; break;
        case '\\': o += "\\\\"; break;
        case '\n': o += "\\n"; break;
        case '\r': o += "\\r"; break;
        case '\t': o += "\\t"; break;
        case '<': o += "\\u003c"; break; // safe inside <script>
        default:
            if (c < 0x20) {
                char b[8];
                snprintf(b, sizeof b, "\\u%04x", c);
                o += b;
            } else o.push_back((char)c);
        }
    }
    return o;
}

inline std::string lower(std::string s) {
    for (auto& c : s) c = (char)tolower((unsigned char)c);
    return s;
}

inline bool is_sep(char c) {
#ifdef _WIN32
    return c == '\\' || c == '/';
#else
    return c == '/';
#endif
}

inline std::string path_join(const std::string& a, const std::string& b) {
    if (a.empty()) return b;
    return is_sep(a.back()) ? a + b : a + plat::PATH_SEP + b;
}

inline std::string path_leaf(const std::string& p) {
    size_t e = p.size();
    while (e > 1 && is_sep(p[e - 1])) e--;
    size_t s = e;
    while (s > 0 && !is_sep(p[s - 1])) s--;
    return p.substr(s, e - s);
}

} // namespace util
