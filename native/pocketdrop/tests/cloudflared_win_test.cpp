// Build on Windows with the same system libraries as platform_win.cpp.
// Arguments: pinned release asset, disposable test directory. No tunnel is opened.
#include "../src/win/platform_win.cpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>

namespace fs = std::filesystem;
void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
bool verified(const fs::path& path) {
    std::string error;
    HANDLE file = openVerifiedCloudflared(path.wstring(), error);
    if (file == INVALID_HANDLE_VALUE) { require(!error.empty(), "failure needs a message"); return false; }
    CloseHandle(file);
    return true;
}
void corrupt(const fs::path& path) {
    std::fstream file(path, std::ios::in | std::ios::out | std::ios::binary);
    file.seekg(1024);
    char byte = 0;
    file.read(&byte, 1);
    byte ^= 1;
    file.seekp(1024);
    file.write(&byte, 1);
    require(bool(file), "could not mutate test fixture");
}
int wmain(int argc, wchar_t** argv) {
    try {
        require(argc == 3, "provide pinned asset and disposable directory");
        fs::path asset = fs::absolute(argv[1]), root = fs::absolute(argv[2]);
        fs::create_directories(root);
        SetEnvironmentVariableW(L"LOCALAPPDATA", root.c_str());
        require(verified(asset), "official asset rejected");
        require(plat::cloudflared_download_url().find("/2026.11.0/") != std::string::npos, "download is not pinned");
        auto copy = root / "verified.exe";
        fs::copy_file(asset, copy, fs::copy_options::overwrite_existing);
        std::string error;
        HANDLE lock = openVerifiedCloudflared(copy.wstring(), error);
        require(lock != INVALID_HANDLE_VALUE, "valid copy rejected");
        HANDLE writer = CreateFileW(copy.c_str(), GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                    nullptr, OPEN_EXISTING, 0, nullptr);
        require(writer == INVALID_HANDLE_VALUE, "verification allowed concurrent writing");
        require(!DeleteFileW(copy.c_str()), "verification allowed deletion");
        CloseHandle(lock);
        corrupt(copy);
        require(!verified(copy), "one-byte modification accepted");
        require(!plat::cloudflared_start(utf8(copy.wstring()), {"--version"}, {}, utf8(root.wstring()), error),
                "modified cached executable launched");
        auto fake = root / "unsigned.exe";
        { std::ofstream f(fake, std::ios::binary); f << "MZ unsigned not-cloudflare publisher"; }
        require(!verified(fake), "unsigned unrelated executable accepted");
        require(!verified(root / "missing.exe"), "missing executable accepted");
        auto truncated = root / "truncated.exe";
        fs::copy_file(asset, truncated, fs::copy_options::overwrite_existing);
        fs::resize_file(truncated, cloudflared_pin::SIZE - 1);
        require(!verified(truncated), "truncated executable accepted");
        auto installed = fs::path(wide(plat::cloudflared_install_path()));
        fs::copy_file(asset, installed, fs::copy_options::overwrite_existing);
        require(!plat::cloudflared_install(utf8(fake.wstring()), error), "bad download installed");
        require(verified(installed), "bad download replaced valid install");
        auto download = root / "cloudflared.download";
        fs::copy_file(asset, download, fs::copy_options::overwrite_existing);
        require(plat::cloudflared_install(utf8(download.wstring()), error), "valid download not installed");
        require(!fs::exists(download) && verified(installed), "installation did not publish verified bytes");
        auto candidates = plat::cloudflared_candidates();
        require(std::find(candidates.begin(), candidates.end(), utf8(installed.wstring())) != candidates.end(), "valid cache not discovered");
        corrupt(installed);
        candidates = plat::cloudflared_candidates();
        require(std::find(candidates.begin(), candidates.end(), utf8(installed.wstring())) == candidates.end(), "modified cache discovered");
        require(!plat::cloudflared_start(utf8(installed.wstring()), {"--version"}, {}, utf8(root.wstring()), error), "cache swap bypassed launch check");
        plat::Process* process = plat::cloudflared_start(utf8(asset.wstring()), {"--version"}, {}, utf8(root.wstring()), error);
        require(process != nullptr, error.c_str());
        char buffer[4096]; std::string output; int count;
        while ((count = plat::process_read(process, buffer, sizeof buffer)) > 0) output.append(buffer, count);
        require(plat::process_finish(process, 5000) == 0, "verified executable failed");
        require(output.find("2026.11.0") != std::string::npos, "wrong executable version");
        require(verified(asset), "original asset changed");
        std::cout << "PASS: pin, lock, tamper/truncation/unsigned rejection, install preservation, cached launch gate, --version and cleanup\n";
        return 0;
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
