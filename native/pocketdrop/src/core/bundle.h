#pragma once
#include "util.h"
#include <atomic>
#include <functional>
#include <memory>
#include <string>
#include <thread>
#include <vector>

struct BundleFile {
    std::string path; // UTF-8 filesystem path
    std::string rel;  // path inside the zip, '/' separated
    std::string name; // leaf name
    uint64_t size = 0;
    uint16_t dosTime = 0, dosDate = 0;
    // Filled in by preparation:
    bool prepared = false;
    bool missing = false;
    uint32_t crc = 0;
    uint16_t method = 0; // 0 = stored, 8 = deflated
    std::shared_ptr<const std::vector<uint8_t>> packed;
    uint64_t csize() const { return method == 8 ? packed->size() : size; }
};

// What the user dropped at top level (shown in the desktop list).
struct BundleRoot {
    std::string path;
    std::string name;
    bool isDir = false;
    uint32_t fileCount = 0;
    uint64_t bytes = 0;
};

struct ZipSegment {
    const uint8_t* mem = nullptr; // memory segment when set
    int file = -1;                // otherwise index into files (stored data)
    uint64_t len = 0;
};

struct ZipPlan {
    std::vector<std::vector<uint8_t>> buffers;
    std::vector<ZipSegment> segs;
    uint64_t total = 0;
};

class Bundle {
public:
    std::vector<BundleRoot> roots;
    std::vector<BundleFile> files;
    std::vector<std::string> dirs;  // folder entries for the zip ("a/b/")
    std::vector<std::string> texts; // shared text snippets
    uint64_t totalBytes = 0;
    std::string zipName;
    bool zipPreferred = false;

    std::atomic<bool> ready{false};
    std::atomic<uint64_t> prepDone{0};
    uint64_t zipBytes = 0; // final zip size (valid when ready)
    int deflated = 0;      // entries that got compressed

    ~Bundle();
    void startPrepare(std::function<void()> onDone);
    const ZipPlan& plan() const { return plan_; } // only when ready

private:
    ZipPlan plan_;
    std::thread worker_;
    std::atomic<bool> cancel_{false};
    void prepareFile(BundleFile& f, std::vector<uint8_t>& buf, std::atomic<int64_t>& budget);
    void buildPlan();
};

// Expands top-level paths (files and folders) into a bundle. Preparation work
// already done in `prev` is reused for unchanged files.
std::shared_ptr<Bundle> build_bundle(const std::vector<std::string>& paths, const std::vector<std::string>& texts,
                                     const Bundle* prev);
