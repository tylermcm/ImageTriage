#pragma once
// Parsing for the Linux offline hotspot, which is NetworkManager's `nmcli`. Kept apart from the
// process handling so the text handling is tested without a Wi-Fi adapter.
#include <sstream>
#include <string>

namespace hotspot_nm {

// `nmcli -t -f DEVICE,TYPE,STATE device`: the first Wi-Fi adapter that is not unavailable or unmanaged.
inline std::string wifi_device(const std::string& output) {
    std::istringstream lines(output);
    std::string line;
    while (std::getline(lines, line)) {
        size_t a = line.find(':'), b = a == std::string::npos ? a : line.find(':', a + 1);
        if (b == std::string::npos) continue;
        std::string dev = line.substr(0, a), type = line.substr(a + 1, b - a - 1), state = line.substr(b + 1);
        if (type == "wifi" && state != "unavailable" && state != "unmanaged" && !dev.empty()) return dev;
    }
    return {};
}

// `nmcli -g IP4.ADDRESS device show <dev>` prints "10.42.0.1/24" (several addresses are separated by '|').
inline std::string ipv4_address(const std::string& output) {
    std::string text = output.substr(0, output.find_first_of("\r\n"));
    text = text.substr(0, text.find_first_of("|/ "));
    int dots = 0;
    for (char c : text) {
        if (c == '.') dots++;
        else if (c < '0' || c > '9') return {};
    }
    return dots == 3 ? text : std::string();
}

// /proc/net/arp: is any neighbour on `iface` fully resolved (flags 0x2)? That is a phone that joined.
inline bool has_client(const std::string& arpTable, const std::string& iface) {
    std::istringstream lines(arpTable);
    std::string line;
    std::getline(lines, line); // header
    while (std::getline(lines, line)) {
        std::istringstream f(line);
        std::string ip, hw, flags, mac, mask, dev;
        if (!(f >> ip >> hw >> flags >> mac >> mask >> dev)) continue;
        if (dev == iface && flags == "0x2" && mac != "00:00:00:00:00:00") return true;
    }
    return false;
}

} // namespace hotspot_nm
