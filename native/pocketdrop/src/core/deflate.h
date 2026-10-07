#pragma once
#include <cstddef>
#include <cstdint>
#include <vector>

// Standard CRC-32 (zip/gzip), slice-by-8. Pass 0 to start.
uint32_t crc32_update(uint32_t crc, const uint8_t* p, size_t n);

// Raw DEFLATE (RFC 1951): LZ77 hash chains + lazy match + dynamic Huffman,
// falling back to stored blocks when data doesn't compress.
std::vector<uint8_t> deflate_compress(const uint8_t* data, size_t n);
