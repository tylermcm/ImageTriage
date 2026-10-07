#pragma once
#include <cstdint>
#include <string>
#include <vector>

namespace qr {

enum class Ecc { L = 0, M = 1, Q = 2, H = 3 };

struct Code {
    int size = 0;
    std::vector<uint8_t> modules; // row-major, 1 = dark
    bool dark(int x, int y) const { return modules[(size_t)y * size + x] != 0; }
};

// Byte-mode encode using the smallest version that fits; ECC is boosted when it
// costs no extra modules. Returns size == 0 if the data is too long.
Code encode(const std::string& data, Ecc minEcc = Ecc::M);

// Payload understood by phone cameras as a request to join a WPA/WPA2 network.
std::string wifi(const std::string& ssid, const std::string& passphrase);

}
