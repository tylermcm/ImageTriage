#include "bundle.h"
#include "deflate.h"
#include <algorithm>
#include <ctime>
#include <iterator>
#include <map>
#include <set>

namespace {

// Formats that are already compressed; deflating them wastes CPU.
const char* const STORE_EXT[] = {
    "jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "avif", "jxl", "mp4", "m4v", "mov", "mkv", "webm",
    "avi", "wmv", "flv", "3gp", "mp3", "m4a", "aac", "ogg", "oga", "opus", "flac", "wma", "zip", "7z",
    "rar", "gz", "tgz", "bz2", "xz", "zst", "br", "lz4", "lzma", "cab", "apk", "aab", "ipa", "jar",
    "docx", "xlsx", "pptx", "odt", "ods", "odp", "epub", "msix", "appx", "nupkg", "whl", "dmg", "woff",
    "woff2", "cr2", "nef", "arw", "dng", "mpg", "mpeg", "ts", "m2ts", "vob", "pages", "numbers", "key",
};
// Formats that reliably compress well.
const char* const DEFLATE_EXT[] = {
    "txt", "csv", "tsv", "json", "xml", "html", "htm", "css", "js", "mjs", "md", "log", "svg", "bmp",
    "tif", "tiff", "wav", "aiff", "psd", "sql", "yaml", "yml", "ini", "cfg", "conf", "toml", "c", "cc",
    "cpp", "h", "hpp", "py", "java", "cs", "go", "rs", "rb", "php", "sh", "bat", "ps1", "rtf", "obj",
    "stl", "ply", "dxf", "srt", "vtt", "ics", "vcf", "eml", "ipynb", "tex", "xaml", "plist", "swift", "m", "mm",
};

const uint64_t MAX_DEFLATE_FILE = 64ull << 20;
const int64_t DEFLATE_BUDGET = 512ll << 20;

bool inList(const std::string& ext, const char* const* list, size_t n) {
    for (size_t i = 0; i < n; i++)
        if (ext == list[i]) return true;
    return false;
}

std::string extOf(const std::string& name) {
    size_t p = name.rfind('.');
    if (p == std::string::npos || p == 0) return {};
    return util::lower(name.substr(p + 1));
}

struct Put {
    std::vector<uint8_t>& v;
    void u16(uint32_t x) {
        v.push_back((uint8_t)x);
        v.push_back((uint8_t)(x >> 8));
    }
    void u32(uint32_t x) {
        u16(x & 0xFFFF);
        u16(x >> 16);
    }
    void u64(uint64_t x) {
        u32((uint32_t)x);
        u32((uint32_t)(x >> 32));
    }
    void str(const std::string& s) { v.insert(v.end(), s.begin(), s.end()); }
};

void addFile(Bundle& b, const std::string& path, const std::string& rel, uint64_t size, int64_t mtime) {
    BundleFile f;
    f.path = path;
    f.rel = rel;
    size_t slash = rel.rfind('/');
    f.name = slash == std::string::npos ? rel : rel.substr(slash + 1);
    f.size = size;
    plat::dos_datetime(mtime, f.dosTime, f.dosDate);
    b.files.push_back(std::move(f));
}

void walk(Bundle& b, const std::string& dir, const std::string& rel, int depth) {
    b.dirs.push_back(rel + "/");
    if (depth > 64) return;
    auto entries = plat::list_dir(dir);
    std::sort(entries.begin(), entries.end(), [](const plat::DirEntry& x, const plat::DirEntry& y) { return x.name < y.name; });
    for (const auto& e : entries) {
        if (e.st.isLink) continue; // avoid link loops
        std::string full = util::path_join(dir, e.name);
        std::string r = rel + "/" + e.name;
        if (e.st.isDir) {
            walk(b, full, r, depth + 1);
        } else {
            std::string lname = util::lower(e.name);
            if (lname == "thumbs.db" || lname == "desktop.ini" || lname == ".ds_store") continue;
            addFile(b, full, r, e.st.size, e.st.mtime);
        }
    }
}

std::string uniqueName(std::set<std::string>& used, const std::string& name, bool isDir) {
    std::string base = name, ext;
    size_t dot = name.rfind('.');
    if (!isDir && dot != std::string::npos && dot > 0) {
        base = name.substr(0, dot);
        ext = name.substr(dot);
    }
    std::string cand = name;
    for (int i = 2; used.count(util::lower(cand)); i++) cand = base + " (" + std::to_string(i) + ")" + ext;
    used.insert(util::lower(cand));
    return cand;
}

} // namespace

std::shared_ptr<Bundle> build_bundle(const std::vector<std::string>& paths, const std::vector<std::string>& texts,
                                     const Bundle* prev) {
    auto b = std::make_shared<Bundle>();
    b->texts = texts;
    std::set<std::string> used;
    for (const auto& p0 : paths) {
        std::string p = p0;
        while (p.size() > 1 && util::is_sep(p.back()) && !(p.size() == 3 && p[1] == ':')) p.pop_back();
        plat::FileStat st = plat::file_stat(p);
        if (!st.exists) continue;
        std::string leaf = util::path_leaf(p);
        if (leaf.empty() || (leaf.size() == 3 && leaf[1] == ':')) leaf = p.size() >= 2 && p[1] == ':' ? std::string("Drive-") + p[0] : "Root";
        BundleRoot root;
        root.path = p;
        root.isDir = st.isDir;
        root.name = uniqueName(used, leaf, st.isDir);
        size_t before = b->files.size();
        if (st.isDir) walk(*b, p, root.name, 0);
        else addFile(*b, p, root.name, st.size, st.mtime);
        for (size_t i = before; i < b->files.size(); i++) root.bytes += b->files[i].size;
        root.fileCount = (uint32_t)(b->files.size() - before);
        b->roots.push_back(std::move(root));
    }
    for (auto& f : b->files) b->totalBytes += f.size;

    bool anyDir = std::any_of(b->roots.begin(), b->roots.end(), [](const BundleRoot& r) { return r.isDir; });
    b->zipPreferred = b->files.size() > 1 || anyDir;
    if (b->roots.size() == 1 && anyDir) {
        b->zipName = b->roots[0].name + ".zip";
    } else {
        std::time_t t = std::time(nullptr);
        std::tm tm{};
#ifdef _WIN32
        localtime_s(&tm, &t);
#else
        localtime_r(&t, &tm);
#endif
        char buf[64];
        strftime(buf, sizeof buf, "PocketDrop-%Y%m%d-%H%M.zip", &tm);
        b->zipName = buf;
    }

    if (prev && prev->ready) {
        std::map<std::string, const BundleFile*> old;
        for (const auto& f : prev->files) old[f.path] = &f;
        for (auto& f : b->files) {
            auto it = old.find(f.path);
            if (it == old.end()) continue;
            const BundleFile& o = *it->second;
            if (o.prepared && !o.missing && o.size == f.size && o.dosTime == f.dosTime && o.dosDate == f.dosDate) {
                f.prepared = true;
                f.crc = o.crc;
                f.method = o.method;
                f.packed = o.packed;
            }
        }
    }
    return b;
}

Bundle::~Bundle() {
    cancel_ = true;
    if (worker_.joinable()) worker_.join();
}

void Bundle::startPrepare(std::function<void()> onDone) {
    worker_ = std::thread([this, onDone] {
        std::atomic<size_t> next{0};
        std::atomic<int64_t> budget{DEFLATE_BUDGET};
        auto work = [&] {
            std::vector<uint8_t> buf;
            for (;;) {
                size_t i = next++;
                if (i >= files.size() || cancel_) break;
                prepareFile(files[i], buf, budget);
            }
        };
        unsigned nt = std::clamp(std::thread::hardware_concurrency(), 1u, 8u);
        nt = (unsigned)std::min<size_t>(nt, std::max<size_t>(files.size(), 1));
        std::vector<std::thread> pool;
        for (unsigned k = 1; k < nt; k++) pool.emplace_back(work);
        work();
        for (auto& t : pool) t.join();
        if (cancel_) return;
        buildPlan();
        ready = true;
        if (onDone) onDone();
    });
}

void Bundle::prepareFile(BundleFile& f, std::vector<uint8_t>& buf, std::atomic<int64_t>& budget) {
    if (f.prepared) {
        prepDone += f.size;
        return;
    }
    f.prepared = true;
    plat::File h = plat::file_open_read(f.path);
    if (h == plat::BAD_FILE) {
        f.missing = true;
        prepDone += f.size;
        return;
    }
    uint64_t expected = f.size;
    int64_t actual = plat::file_size(h);
    if (actual >= 0) f.size = (uint64_t)actual;

    std::string ext = extOf(f.name);
    // 0 = store, 1 = deflate, 2 = sniff a sample first
    int mode = 2;
    if (f.size < 512 || f.size > MAX_DEFLATE_FILE || inList(ext, STORE_EXT, std::size(STORE_EXT))) mode = 0;
    else if (inList(ext, DEFLATE_EXT, std::size(DEFLATE_EXT))) mode = 1;
    if (mode) {
        int64_t prevBudget = budget.fetch_sub((int64_t)f.size);
        if (prevBudget < (int64_t)f.size) {
            budget += (int64_t)f.size;
            mode = 0;
        }
    }

    bool ok = true;
    if (mode) {
        std::vector<uint8_t> data((size_t)f.size);
        size_t got = 0;
        while (got < data.size()) {
            int64_t r = plat::file_read(h, data.data() + got, std::min<size_t>(data.size() - got, 1 << 24));
            if (r <= 0) break;
            got += (size_t)r;
        }
        if (got != data.size()) ok = false;
        else {
            f.crc = crc32_update(0, data.data(), data.size());
            if (mode == 2) {
                size_t s = std::min<size_t>(data.size(), 1 << 16);
                if (deflate_compress(data.data(), s).size() > s * 85 / 100) mode = 0;
            }
            if (mode && !cancel_) {
                auto packed = deflate_compress(data.data(), data.size());
                if (packed.size() < data.size() * 95 / 100) {
                    f.method = 8;
                    f.packed = std::make_shared<const std::vector<uint8_t>>(std::move(packed));
                }
            }
        }
        if (f.method != 8) budget += (int64_t)f.size;
        prepDone += expected;
    } else {
        buf.resize(1 << 20);
        uint32_t crc = 0;
        uint64_t total = 0;
        for (;;) {
            int64_t r = plat::file_read(h, buf.data(), buf.size());
            if (r < 0) {
                ok = false;
                break;
            }
            if (r == 0) break;
            crc = crc32_update(crc, buf.data(), (size_t)r);
            total += (uint64_t)r;
            prepDone += (uint64_t)r;
            if (cancel_) break;
        }
        if (total != f.size) ok = false;
        f.crc = crc;
    }
    plat::file_close(h);
    if (!ok) f.missing = true;
}

void Bundle::buildPlan() {
    struct Entry {
        std::string name;
        uint16_t method, time, date;
        uint32_t crc, attr;
        uint64_t csize, usize, offset;
    };
    std::vector<Entry> entries;
    ZipPlan& p = plan_;
    p.buffers.reserve(dirs.size() + files.size() + 1);
    uint64_t off = 0;

    auto local = [&](const Entry& e) {
        std::vector<uint8_t> v;
        Put w{v};
        bool z64 = e.usize >= 0xFFFFFFFFull || e.csize >= 0xFFFFFFFFull;
        w.u32(0x04034b50);
        w.u16(z64 ? 45 : 20);
        w.u16(0x0800); // UTF-8 names
        w.u16(e.method);
        w.u16(e.time);
        w.u16(e.date);
        w.u32(e.crc);
        w.u32(z64 ? 0xFFFFFFFF : (uint32_t)e.csize);
        w.u32(z64 ? 0xFFFFFFFF : (uint32_t)e.usize);
        w.u16((uint32_t)e.name.size());
        w.u16(z64 ? 20 : 0);
        w.str(e.name);
        if (z64) {
            w.u16(1);
            w.u16(16);
            w.u64(e.usize);
            w.u64(e.csize);
        }
        p.buffers.push_back(std::move(v));
        p.segs.push_back({p.buffers.back().data(), -1, p.buffers.back().size()});
        off += p.buffers.back().size();
    };

    uint16_t nowT, nowD;
    plat::dos_datetime(plat::unix_time(), nowT, nowD);

    for (const auto& d : dirs) {
        Entry e{d, 0, nowT, nowD, 0, 0x10, 0, 0, off};
        local(e);
        entries.push_back(e);
    }
    for (size_t i = 0; i < files.size(); i++) {
        const BundleFile& f = files[i];
        if (f.missing) continue;
        Entry e{f.rel, f.method, f.dosTime, f.dosDate, f.crc, 0x20, f.csize(), f.size, off};
        local(e);
        entries.push_back(e);
        if (f.method == 8) p.segs.push_back({f.packed->data(), -1, f.packed->size()});
        else if (f.size) p.segs.push_back({nullptr, (int)i, f.size});
        off += e.csize;
        if (f.method == 8) deflated++;
    }

    uint64_t cdStart = off;
    std::vector<uint8_t> cd;
    Put w{cd};
    bool needZ64 = entries.size() >= 0xFFFF;
    for (const auto& e : entries) {
        bool z64 = e.usize >= 0xFFFFFFFFull || e.csize >= 0xFFFFFFFFull || e.offset >= 0xFFFFFFFFull;
        needZ64 |= z64;
        w.u32(0x02014b50);
        w.u16(45);
        w.u16(z64 ? 45 : 20);
        w.u16(0x0800);
        w.u16(e.method);
        w.u16(e.time);
        w.u16(e.date);
        w.u32(e.crc);
        w.u32(z64 ? 0xFFFFFFFF : (uint32_t)e.csize);
        w.u32(z64 ? 0xFFFFFFFF : (uint32_t)e.usize);
        w.u16((uint32_t)e.name.size());
        w.u16(z64 ? 28 : 0);
        w.u16(0);
        w.u16(0);
        w.u16(0);
        w.u32(e.attr);
        w.u32(z64 ? 0xFFFFFFFF : (uint32_t)e.offset);
        w.str(e.name);
        if (z64) {
            w.u16(1);
            w.u16(24);
            w.u64(e.usize);
            w.u64(e.csize);
            w.u64(e.offset);
        }
    }
    uint64_t cdSize = cd.size();
    needZ64 |= cdStart >= 0xFFFFFFFFull || cdSize >= 0xFFFFFFFFull;
    if (needZ64) {
        uint64_t eocd64 = cdStart + cdSize;
        w.u32(0x06064b50);
        w.u64(44);
        w.u16(45);
        w.u16(45);
        w.u32(0);
        w.u32(0);
        w.u64(entries.size());
        w.u64(entries.size());
        w.u64(cdSize);
        w.u64(cdStart);
        w.u32(0x07064b50);
        w.u32(0);
        w.u64(eocd64);
        w.u32(1);
    }
    w.u32(0x06054b50);
    w.u16(0);
    w.u16(0);
    w.u16((uint32_t)std::min<size_t>(entries.size(), 0xFFFF));
    w.u16((uint32_t)std::min<size_t>(entries.size(), 0xFFFF));
    w.u32((uint32_t)std::min<uint64_t>(cdSize, 0xFFFFFFFF));
    w.u32((uint32_t)std::min<uint64_t>(cdStart, 0xFFFFFFFF));
    w.u16(0);

    p.buffers.push_back(std::move(cd));
    p.segs.push_back({p.buffers.back().data(), -1, p.buffers.back().size()});
    p.total = cdStart + p.buffers.back().size();
    zipBytes = p.total;
}
