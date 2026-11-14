#pragma once
#define PD_VERSION "2.2.1"
#define PD_VERSION_NUM 2,2,1,0
#ifndef RC_INVOKED
#include <array>
#include <string>

inline bool parseVersion(std::string value, std::array<int, 3>& out) {
    if (!value.empty() && value[0] == 'v') value.erase(0, 1);
    for (int i = 0; i < 3; ++i) {
        size_t dot = value.find('.');
        auto part = value.substr(0, dot);
        if (part.empty() || part.size() > 6 || part.find_first_not_of("0123456789") != std::string::npos) return false;
        out[i] = std::stoi(part);
        if (i == 2) return dot == std::string::npos;
        if (dot == std::string::npos) return false;
        value.erase(0, dot + 1);
    }
    return false;
}
inline bool newerVersion(const std::string& candidate, const std::string& current = PD_VERSION) {
    std::array<int, 3> a{}, b{};
    return parseVersion(candidate, a) && parseVersion(current, b) && a > b;
}
#endif
