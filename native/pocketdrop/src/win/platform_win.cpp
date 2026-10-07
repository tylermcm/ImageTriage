// Windows implementation of core/platform.h.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include "../core/platform.h"
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <bcrypt.h>
#include <iphlpapi.h>
#include <objbase.h>
#include <shlobj.h>
#include <shobjidl.h>
#include <wincodec.h>
#include <wincrypt.h>
#include <winhttp.h>
#include <wintrust.h>
#include <softpub.h>
#include <wrl/client.h>
#include <algorithm>
#include <ctime>

using Microsoft::WRL::ComPtr;

namespace {

std::wstring wide(const std::string& s) {
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), nullptr, 0);
    std::wstring w(n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), w.data(), n);
    return w;
}

std::string utf8(const std::wstring& w) {
    if (w.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, w.data(), (int)w.size(), nullptr, 0, nullptr, nullptr);
    std::string s(n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.data(), (int)w.size(), s.data(), n, nullptr, nullptr);
    return s;
}

int64_t unixFromFileTime(const FILETIME& ft) {
    ULONGLONG t = ((ULONGLONG)ft.dwHighDateTime << 32) | ft.dwLowDateTime;
    return (int64_t)(t / 10000000ull) - 11644473600ll;
}

bool isLinkTag(DWORD attrs, DWORD tag) {
    // Only real links; cloud placeholders (OneDrive etc.) are also reparse points and must be included.
    return (attrs & FILE_ATTRIBUTE_REPARSE_POINT) && (tag == IO_REPARSE_TAG_SYMLINK || tag == IO_REPARSE_TAG_MOUNT_POINT);
}

// ---- thumbnails
bool encodeJpeg(const std::vector<uint8_t>& bgra, int w, int h, std::vector<uint8_t>& out) {
    ComPtr<IWICImagingFactory> f;
    if (FAILED(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&f)))) return false;
    ComPtr<IWICBitmap> bmp;
    if (FAILED(f->CreateBitmapFromMemory(w, h, GUID_WICPixelFormat32bppBGR, w * 4, (UINT)bgra.size(),
                                         (BYTE*)bgra.data(), &bmp)))
        return false;
    ComPtr<IWICFormatConverter> conv;
    if (FAILED(f->CreateFormatConverter(&conv)) ||
        FAILED(conv->Initialize(bmp.Get(), GUID_WICPixelFormat24bppBGR, WICBitmapDitherTypeNone, nullptr, 0,
                                WICBitmapPaletteTypeCustom)))
        return false;
    ComPtr<IStream> stream;
    if (FAILED(CreateStreamOnHGlobal(nullptr, TRUE, &stream))) return false;
    ComPtr<IWICBitmapEncoder> enc;
    if (FAILED(f->CreateEncoder(GUID_ContainerFormatJpeg, nullptr, &enc)) ||
        FAILED(enc->Initialize(stream.Get(), WICBitmapEncoderNoCache)))
        return false;
    ComPtr<IWICBitmapFrameEncode> frame;
    ComPtr<IPropertyBag2> props;
    if (FAILED(enc->CreateNewFrame(&frame, &props))) return false;
    PROPBAG2 opt{};
    opt.pstrName = (LPOLESTR)L"ImageQuality";
    VARIANT v;
    VariantInit(&v);
    v.vt = VT_R4;
    v.fltVal = 0.82f;
    props->Write(1, &opt, &v);
    WICPixelFormatGUID fmt = GUID_WICPixelFormat24bppBGR;
    if (FAILED(frame->Initialize(props.Get())) || FAILED(frame->SetSize(w, h)) ||
        FAILED(frame->SetPixelFormat(&fmt)) || FAILED(frame->WriteSource(conv.Get(), nullptr)) ||
        FAILED(frame->Commit()) || FAILED(enc->Commit()))
        return false;
    ULARGE_INTEGER pos{};
    stream->Seek({}, STREAM_SEEK_CUR, &pos);
    HGLOBAL hg = nullptr;
    if (FAILED(GetHGlobalFromStream(stream.Get(), &hg))) return false;
    auto* p = (uint8_t*)GlobalLock(hg);
    if (!p) return false;
    out.assign(p, p + pos.QuadPart);
    GlobalUnlock(hg);
    return true;
}

std::vector<uint8_t> shellThumb(const std::wstring& path, int maxSide) {
    std::vector<uint8_t> out;
    ComPtr<IShellItemImageFactory> factory;
    if (FAILED(SHCreateItemFromParsingName(path.c_str(), nullptr, IID_PPV_ARGS(&factory)))) return out;
    HBITMAP hb = nullptr;
    if (FAILED(factory->GetImage({maxSide, maxSide}, SIIGBF_THUMBNAILONLY | SIIGBF_RESIZETOFIT, &hb)) || !hb)
        return out;
    BITMAP bm{};
    GetObject(hb, sizeof bm, &bm);
    int w = bm.bmWidth, h = abs(bm.bmHeight);
    if (w <= 0 || h <= 0) {
        DeleteObject(hb);
        return out;
    }
    std::vector<uint8_t> px((size_t)w * h * 4);
    BITMAPINFO bi{};
    bi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bi.bmiHeader.biWidth = w;
    bi.bmiHeader.biHeight = -h;
    bi.bmiHeader.biPlanes = 1;
    bi.bmiHeader.biBitCount = 32;
    bi.bmiHeader.biCompression = BI_RGB;
    HDC dc = GetDC(nullptr);
    int lines = GetDIBits(dc, hb, 0, h, px.data(), &bi, DIB_RGB_COLORS);
    ReleaseDC(nullptr, dc);
    DeleteObject(hb);
    if (lines != h) return out;
    bool alpha = false;
    for (size_t i = 3; i < px.size(); i += 4)
        if (px[i]) {
            alpha = true;
            break;
        }
    if (alpha) { // premultiplied BGRA over the page's card colour
        const int bg[3] = {0x26, 0x20, 0x1E};
        for (size_t i = 0; i < px.size(); i += 4) {
            int a = px[i + 3];
            for (int c = 0; c < 3; c++) px[i + c] = (uint8_t)std::min(255, px[i + c] + bg[c] * (255 - a) / 255);
        }
    }
    encodeJpeg(px, w, h, out);
    return out;
}

// ---- cloudflared signature: must be signed by Cloudflare (unsigned is tolerated; fetched over HTTPS)
bool verifySigner(const std::wstring& path, std::string& err) {
    WINTRUST_FILE_INFO fi{sizeof fi};
    fi.pcwszFilePath = path.c_str();
    WINTRUST_DATA wd{sizeof wd};
    wd.dwUIChoice = WTD_UI_NONE;
    wd.fdwRevocationChecks = WTD_REVOKE_NONE;
    wd.dwUnionChoice = WTD_CHOICE_FILE;
    wd.pFile = &fi;
    wd.dwStateAction = WTD_STATEACTION_VERIFY;
    GUID action = WINTRUST_ACTION_GENERIC_VERIFY_V2;
    LONG st = WinVerifyTrust((HWND)INVALID_HANDLE_VALUE, &action, &wd);
    bool ok = false;
    if (st == ERROR_SUCCESS) {
        CRYPT_PROVIDER_DATA* pd = WTHelperProvDataFromStateData(wd.hWVTStateData);
        CRYPT_PROVIDER_SGNR* sg = pd ? WTHelperGetProvSignerFromChain(pd, 0, FALSE, 0) : nullptr;
        CRYPT_PROVIDER_CERT* pc = sg ? WTHelperGetProvCertFromChain(sg, 0) : nullptr;
        if (pc) {
            wchar_t name[256] = L"";
            CertGetNameStringW(pc->pCert, CERT_NAME_SIMPLE_DISPLAY_TYPE, 0, nullptr, name, 256);
            std::wstring n = name;
            for (auto& ch : n) ch = (wchar_t)towlower(ch);
            ok = n.find(L"cloudflare") != std::wstring::npos;
            if (!ok) err = "Unexpected signer: " + utf8(name);
        }
    } else if (st == TRUST_E_NOSIGNATURE) {
        ok = true;
    } else {
        err = "Signature check failed";
    }
    wd.dwStateAction = WTD_STATEACTION_CLOSE;
    WinVerifyTrust((HWND)INVALID_HANDLE_VALUE, &action, &wd);
    return ok;
}

std::wstring quoteArg(const std::wstring& a) {
    if (!a.empty() && a.find_first_of(L" \t\"") == std::wstring::npos) return a;
    std::wstring q = L"\"";
    size_t slashes = 0;
    for (wchar_t c : a) {
        if (c == L'\\') {
            slashes++;
        } else {
            if (c == L'"') q.append(slashes + 1, L'\\');
            slashes = 0;
        }
        q.push_back(c);
    }
    q.append(slashes, L'\\');
    return q + L"\"";
}

} // namespace

namespace plat {

const char PATH_SEP = '\\';

// ---- sockets
bool net_init() {
    static const bool ok = [] {
        WSADATA w;
        return WSAStartup(MAKEWORD(2, 2), &w) == 0;
    }();
    return ok;
}

Sock tcp_listen(int port, bool loopbackOnly, int* boundPort) {
    SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s == INVALID_SOCKET) return BAD_SOCK;
    SetHandleInformation((HANDLE)s, HANDLE_FLAG_INHERIT, 0);
    int one = 1;
    setsockopt(s, SOL_SOCKET, SO_EXCLUSIVEADDRUSE, (char*)&one, sizeof one);
    sockaddr_in a{};
    a.sin_family = AF_INET;
    a.sin_addr.s_addr = htonl(loopbackOnly ? INADDR_LOOPBACK : INADDR_ANY);
    a.sin_port = htons((u_short)port);
    if (bind(s, (sockaddr*)&a, sizeof a) != 0 || listen(s, SOMAXCONN) != 0) {
        closesocket(s);
        return BAD_SOCK;
    }
    int len = sizeof a;
    getsockname(s, (sockaddr*)&a, &len);
    if (boundPort) *boundPort = ntohs(a.sin_port);
    return (Sock)s;
}

Sock tcp_accept(Sock s, int timeoutMs) {
    fd_set rs;
    FD_ZERO(&rs);
    FD_SET((SOCKET)s, &rs);
    timeval tv{timeoutMs / 1000, (timeoutMs % 1000) * 1000};
    if (select(0, &rs, nullptr, nullptr, &tv) <= 0) return BAD_SOCK;
    SOCKET c = accept((SOCKET)s, nullptr, nullptr);
    if (c == INVALID_SOCKET) return BAD_SOCK;
    SetHandleInformation((HANDLE)c, HANDLE_FLAG_INHERIT, 0);
    return (Sock)c;
}

void tcp_configure(Sock s, int recvTimeoutMs) {
    DWORD tv = (DWORD)recvTimeoutMs;
    setsockopt((SOCKET)s, SOL_SOCKET, SO_RCVTIMEO, (char*)&tv, sizeof tv);
    int one = 1;
    setsockopt((SOCKET)s, IPPROTO_TCP, TCP_NODELAY, (char*)&one, sizeof one);
}

int tcp_recv(Sock s, char* buf, int len) { return recv((SOCKET)s, buf, len, 0); }
int tcp_send(Sock s, const char* buf, int len) { return send((SOCKET)s, buf, len, 0); }

bool tcp_peer_is_loopback(Sock s) {
    sockaddr_in peer{};
    int len = sizeof peer;
    return getpeername((SOCKET)s, (sockaddr*)&peer, &len) == 0 && ntohl(peer.sin_addr.s_addr) == INADDR_LOOPBACK;
}

void tcp_shutdown(Sock s) { shutdown((SOCKET)s, SD_BOTH); }
void tcp_close(Sock s) {
    if (s != BAD_SOCK) closesocket((SOCKET)s);
}

// ---- files
FileStat file_stat(const std::string& path) {
    FileStat st;
    WIN32_FIND_DATAW fd;
    std::wstring w = wide(path);
    WIN32_FILE_ATTRIBUTE_DATA fa;
    if (!GetFileAttributesExW(w.c_str(), GetFileExInfoStandard, &fa)) return st;
    st.exists = true;
    st.isDir = (fa.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
    st.size = ((uint64_t)fa.nFileSizeHigh << 32) | fa.nFileSizeLow;
    st.mtime = unixFromFileTime(fa.ftLastWriteTime);
    if (fa.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) {
        HANDLE h = FindFirstFileW(w.c_str(), &fd);
        if (h != INVALID_HANDLE_VALUE) {
            st.isLink = isLinkTag(fd.dwFileAttributes, fd.dwReserved0);
            FindClose(h);
        }
    }
    return st;
}

std::vector<DirEntry> list_dir(const std::string& dir) {
    std::vector<DirEntry> out;
    WIN32_FIND_DATAW fd;
    std::wstring pattern = wide(dir);
    if (!pattern.empty() && pattern.back() != L'\\' && pattern.back() != L'/') pattern += L'\\';
    pattern += L'*';
    HANDLE h = FindFirstFileExW(pattern.c_str(), FindExInfoBasic, &fd, FindExSearchNameMatch, nullptr,
                                FIND_FIRST_EX_LARGE_FETCH);
    if (h == INVALID_HANDLE_VALUE) return out;
    do {
        std::wstring name = fd.cFileName;
        if (name == L"." || name == L"..") continue;
        DirEntry e;
        e.name = utf8(name);
        e.st.exists = true;
        e.st.isDir = (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
        e.st.isLink = isLinkTag(fd.dwFileAttributes, fd.dwReserved0);
        e.st.size = ((uint64_t)fd.nFileSizeHigh << 32) | fd.nFileSizeLow;
        e.st.mtime = unixFromFileTime(fd.ftLastWriteTime);
        out.push_back(std::move(e));
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    return out;
}

File file_open_read(const std::string& path) {
    HANDLE h = CreateFileW(wide(path).c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                           nullptr, OPEN_EXISTING, FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    return h == INVALID_HANDLE_VALUE ? BAD_FILE : (File)h;
}

File file_create(const std::string& path) {
    HANDLE h = CreateFileW(wide(path).c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_ALWAYS, 0, nullptr);
    return h == INVALID_HANDLE_VALUE ? BAD_FILE : (File)h;
}

int64_t file_read(File f, void* buf, size_t len) {
    DWORD n = 0;
    if (!ReadFile((HANDLE)f, buf, (DWORD)std::min<size_t>(len, 1u << 30), &n, nullptr)) return -1;
    return n;
}

bool file_write(File f, const void* buf, size_t len) {
    auto* p = (const char*)buf;
    while (len) {
        DWORD n = 0;
        if (!WriteFile((HANDLE)f, p, (DWORD)std::min<size_t>(len, 1u << 30), &n, nullptr) || n == 0) return false;
        p += n;
        len -= n;
    }
    return true;
}

bool file_seek(File f, uint64_t offset) {
    LARGE_INTEGER li;
    li.QuadPart = (LONGLONG)offset;
    return SetFilePointerEx((HANDLE)f, li, nullptr, FILE_BEGIN) != 0;
}

int64_t file_size(File f) {
    LARGE_INTEGER sz;
    return GetFileSizeEx((HANDLE)f, &sz) ? sz.QuadPart : -1;
}

void file_close(File f) {
    if (f != BAD_FILE) CloseHandle((HANDLE)f);
}

bool file_rename(const std::string& from, const std::string& to) {
    return MoveFileExW(wide(from).c_str(), wide(to).c_str(), MOVEFILE_REPLACE_EXISTING) != 0;
}

void file_remove(const std::string& path) { DeleteFileW(wide(path).c_str()); }
void make_dir(const std::string& path) { CreateDirectoryW(wide(path).c_str(), nullptr); }

std::string app_data_dir() {
    wchar_t buf[MAX_PATH];
    DWORD n = GetEnvironmentVariableW(L"LOCALAPPDATA", buf, MAX_PATH);
    std::wstring d = (n && n < MAX_PATH) ? std::wstring(buf) : L".";
    d += L"\\PocketDrop";
    CreateDirectoryW(d.c_str(), nullptr);
    return utf8(d);
}

std::string downloads_dir() {
    PWSTR p = nullptr;
    std::string out;
    if (SUCCEEDED(SHGetKnownFolderPath(FOLDERID_Downloads, 0, nullptr, &p))) out = utf8(p);
    CoTaskMemFree(p);
    if (out.empty()) {
        wchar_t buf[MAX_PATH];
        DWORD n = GetEnvironmentVariableW(L"USERPROFILE", buf, MAX_PATH);
        if (n && n < MAX_PATH) out = utf8(std::wstring(buf, n)) + "\\Downloads";
    }
    return out;
}

uint64_t free_disk_space(const std::string& path) {
    ULARGE_INTEGER avail;
    return GetDiskFreeSpaceExW(wide(path).c_str(), &avail, nullptr, nullptr) ? avail.QuadPart : UINT64_MAX;
}

std::string exe_dir() {
    wchar_t buf[MAX_PATH];
    DWORD n = GetModuleFileNameW(nullptr, buf, MAX_PATH);
    std::wstring p(buf, n);
    return utf8(p.substr(0, p.find_last_of(L"\\/")));
}

// ---- time and misc
int64_t unix_time() { return (int64_t)std::time(nullptr); }
uint64_t tick_ms() { return GetTickCount64(); }
void sleep_ms(int ms) { Sleep((DWORD)ms); }

void dos_datetime(int64_t unixTime, uint16_t& time, uint16_t& date) {
    ULONGLONG t = (ULONGLONG)(unixTime + 11644473600ll) * 10000000ull;
    FILETIME ft{(DWORD)t, (DWORD)(t >> 32)}, local;
    WORD d = 0x21, tm = 0;
    if (!FileTimeToLocalFileTime(&ft, &local) || !FileTimeToDosDateTime(&local, &d, &tm)) {
        d = 0x21;
        tm = 0;
    }
    time = tm;
    date = d;
}

void random_bytes(uint8_t* out, size_t len) {
    BCryptGenRandom(nullptr, out, (ULONG)len, BCRYPT_USE_SYSTEM_PREFERRED_RNG);
}

std::string host_name() {
    wchar_t name[256];
    DWORD n = 256;
    return GetComputerNameExW(ComputerNamePhysicalDnsHostname, name, &n) ? utf8(std::wstring(name, n)) : "PC";
}

// ---- network interfaces
std::vector<LanAddr> lan_addresses() {
    ULONG len = 32768;
    std::vector<uint8_t> buf(len);
    const ULONG flags = GAA_FLAG_INCLUDE_GATEWAYS | GAA_FLAG_SKIP_ANYCAST | GAA_FLAG_SKIP_MULTICAST |
                        GAA_FLAG_SKIP_DNS_SERVER;
    ULONG r = GetAdaptersAddresses(AF_INET, flags, nullptr, (PIP_ADAPTER_ADDRESSES)buf.data(), &len);
    if (r == ERROR_BUFFER_OVERFLOW) {
        buf.resize(len);
        r = GetAdaptersAddresses(AF_INET, flags, nullptr, (PIP_ADAPTER_ADDRESSES)buf.data(), &len);
    }
    if (r != NO_ERROR) return {};
    struct Cand {
        LanAddr a;
        int score;
    };
    std::vector<Cand> cands;
    for (auto* ad = (PIP_ADAPTER_ADDRESSES)buf.data(); ad; ad = ad->Next) {
        if (ad->OperStatus != IfOperStatusUp || ad->IfType == IF_TYPE_SOFTWARE_LOOPBACK) continue;
        std::wstring name = ad->FriendlyName ? ad->FriendlyName : L"";
        std::wstring hay = std::wstring(ad->Description ? ad->Description : L"") + L" " + name;
        for (auto& c : hay) c = (wchar_t)towlower(c);
        int score = 0;
        if (ad->FirstGatewayAddress) score += 100;
        if (ad->IfType == IF_TYPE_IEEE80211 || ad->IfType == IF_TYPE_ETHERNET_CSMACD) score += 20;
        for (const wchar_t* kw : {L"virtual", L"hyper-v", L"vmware", L"virtualbox", L"vethernet", L"wsl", L"tap-",
                                  L"wintun", L"zerotier", L"wireguard", L"bluetooth", L"loopback"})
            if (hay.find(kw) != std::wstring::npos) score -= 80;
        if (hay.find(L"tailscale") != std::wstring::npos) score -= 40;
        for (auto* u = ad->FirstUnicastAddress; u; u = u->Next) {
            auto* sin = (sockaddr_in*)u->Address.lpSockaddr;
            uint32_t ip = ntohl(sin->sin_addr.s_addr);
            if ((ip >> 24) == 127 || (ip >> 16) == 0xA9FE) continue;
            int s = score;
            if ((ip >> 24) == 10 || (ip >> 20) == 0xAC1 || (ip >> 16) == 0xC0A8) s += 10;
            char str[INET_ADDRSTRLEN];
            inet_ntop(AF_INET, &sin->sin_addr, str, sizeof str);
            cands.push_back({{str, utf8(name), ad->IfType == IF_TYPE_IEEE80211}, s});
        }
    }
    std::stable_sort(cands.begin(), cands.end(), [](const Cand& a, const Cand& b) { return a.score > b.score; });
    std::vector<LanAddr> out;
    for (auto& c : cands) out.push_back(c.a);
    return out;
}

// ---- thumbnails
std::vector<uint8_t> thumbnail_jpeg(const std::string& path, int maxSide) {
    HRESULT hr = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    std::vector<uint8_t> out = shellThumb(wide(path), maxSide);
    if (SUCCEEDED(hr)) CoUninitialize();
    return out;
}

// ---- HTTP client
HttpResult http_get(const std::string& url, const std::vector<std::string>& headers, File out,
                    const std::function<bool(uint64_t, uint64_t)>& progress, int timeoutMs) {
    HttpResult res;
    std::wstring wurl = wide(url);
    URL_COMPONENTS uc{sizeof uc};
    wchar_t host[256], path[2048], extra[1024];
    uc.lpszHostName = host;
    uc.dwHostNameLength = 256;
    uc.lpszUrlPath = path;
    uc.dwUrlPathLength = 2048;
    uc.lpszExtraInfo = extra;
    uc.dwExtraInfoLength = 1024;
    if (!WinHttpCrackUrl(wurl.c_str(), 0, 0, &uc)) {
        res.err = "Bad URL";
        return res;
    }
    std::wstring target = std::wstring(path, uc.dwUrlPathLength) + std::wstring(extra, uc.dwExtraInfoLength);
    std::wstring hdrs;
    for (const auto& h : headers) hdrs += wide(h) + L"\r\n";
    HINTERNET s = WinHttpOpen(L"PocketDrop/1.0", WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY, nullptr, nullptr, 0);
    HINTERNET c = s ? WinHttpConnect(s, std::wstring(host, uc.dwHostNameLength).c_str(), uc.nPort, 0) : nullptr;
    HINTERNET r = c ? WinHttpOpenRequest(c, L"GET", target.c_str(), nullptr, WINHTTP_NO_REFERER,
                                         WINHTTP_DEFAULT_ACCEPT_TYPES,
                                         uc.nScheme == INTERNET_SCHEME_HTTPS ? WINHTTP_FLAG_SECURE : 0)
                    : nullptr;
    if (r) {
        WinHttpSetTimeouts(r, timeoutMs, timeoutMs, timeoutMs, timeoutMs);
        if (WinHttpSendRequest(r, hdrs.empty() ? WINHTTP_NO_ADDITIONAL_HEADERS : hdrs.c_str(),
                               hdrs.empty() ? 0 : (DWORD)-1L, nullptr, 0, 0, 0) &&
            WinHttpReceiveResponse(r, nullptr)) {
            DWORD code = 0, sz = sizeof code;
            WinHttpQueryHeaders(r, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER, nullptr, &code, &sz, nullptr);
            res.status = (int)code;
            wchar_t lenBuf[32];
            DWORD lenSz = sizeof lenBuf;
            uint64_t total = 0, got = 0;
            if (WinHttpQueryHeaders(r, WINHTTP_QUERY_CONTENT_LENGTH, nullptr, lenBuf, &lenSz, nullptr))
                total = _wcstoui64(lenBuf, nullptr, 10);
            std::vector<char> buf(1 << 16);
            for (;;) {
                DWORD n = 0;
                if (!WinHttpReadData(r, buf.data(), (DWORD)buf.size(), &n)) {
                    res.err = "Download interrupted";
                    res.status = 0;
                    break;
                }
                if (n == 0) break;
                if (out != BAD_FILE) {
                    if (!file_write(out, buf.data(), n)) {
                        res.err = "Disk write failed";
                        res.status = 0;
                        break;
                    }
                } else if (res.body.size() < 65536) {
                    res.body.append(buf.data(), n);
                }
                got += n;
                if (progress && !progress(got, total)) {
                    res.err = "Cancelled";
                    res.status = 0;
                    break;
                }
            }
        } else {
            res.err = "Network error " + std::to_string(GetLastError());
        }
    } else {
        res.err = "Network unavailable";
    }
    if (r) WinHttpCloseHandle(r);
    if (c) WinHttpCloseHandle(c);
    if (s) WinHttpCloseHandle(s);
    return res;
}

// ---- child processes
struct Process {
    HANDLE proc = nullptr, job = nullptr, rd = nullptr;
};

Process* process_start(const std::string& exe, const std::vector<std::string>& args,
                       const std::vector<std::pair<std::string, std::string>>& envOverrides, const std::string& cwd,
                       std::string& err) {
    std::wstring cmd = quoteArg(wide(exe));
    for (const auto& a : args) cmd += L" " + quoteArg(wide(a));

    std::wstring env;
    LPWCH e = GetEnvironmentStringsW();
    for (LPWCH p = e; *p; p += wcslen(p) + 1) {
        std::wstring kv = p;
        std::wstring key = kv.substr(0, kv.find(L'=', 1));
        bool overridden = std::any_of(envOverrides.begin(), envOverrides.end(),
                                      [&](const auto& o) { return _wcsicmp(wide(o.first).c_str(), key.c_str()) == 0; });
        if (overridden) continue;
        env += kv;
        env.push_back(L'\0');
    }
    FreeEnvironmentStringsW(e);
    for (const auto& o : envOverrides) {
        env += wide(o.first) + L"=" + wide(o.second);
        env.push_back(L'\0');
    }
    env.push_back(L'\0');

    SECURITY_ATTRIBUTES sa{sizeof sa, nullptr, TRUE};
    HANDLE rd = nullptr, wr = nullptr;
    if (!CreatePipe(&rd, &wr, &sa, 0)) {
        err = "pipe failed";
        return nullptr;
    }
    SetHandleInformation(rd, HANDLE_FLAG_INHERIT, 0);
    HANDLE nul = CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, &sa, OPEN_EXISTING, 0, nullptr);

    // Job object: the child dies with us, even if PocketDrop crashes.
    HANDLE job = CreateJobObjectW(nullptr, nullptr);
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION li{};
    li.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    SetInformationJobObject(job, JobObjectExtendedLimitInformation, &li, sizeof li);

    // Inherit only the pipe and NUL.
    STARTUPINFOEXW si{};
    si.StartupInfo.cb = sizeof si;
    si.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    si.StartupInfo.hStdInput = nul;
    si.StartupInfo.hStdOutput = wr;
    si.StartupInfo.hStdError = wr;
    HANDLE inherit[2] = {wr, nul};
    DWORD inheritCount = nul != INVALID_HANDLE_VALUE ? 2 : 1;
    SIZE_T attrSize = 0;
    InitializeProcThreadAttributeList(nullptr, 1, 0, &attrSize);
    std::vector<BYTE> attr(attrSize);
    si.lpAttributeList = (LPPROC_THREAD_ATTRIBUTE_LIST)attr.data();
    bool haveList = InitializeProcThreadAttributeList(si.lpAttributeList, 1, 0, &attrSize) &&
                    UpdateProcThreadAttribute(si.lpAttributeList, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, inherit,
                                              sizeof(HANDLE) * inheritCount, nullptr, nullptr);

    PROCESS_INFORMATION pi{};
    std::wstring wcwd = wide(cwd);
    BOOL ok = CreateProcessW(nullptr, cmd.data(), nullptr, nullptr, TRUE,
                             CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT | CREATE_SUSPENDED |
                                 (haveList ? EXTENDED_STARTUPINFO_PRESENT : 0),
                             env.data(), wcwd.empty() ? nullptr : wcwd.c_str(),
                             haveList ? &si.StartupInfo : (STARTUPINFOW*)&si, &pi);
    DWORD createErr = GetLastError();
    if (haveList) DeleteProcThreadAttributeList(si.lpAttributeList);
    CloseHandle(wr);
    if (nul != INVALID_HANDLE_VALUE) CloseHandle(nul);
    if (!ok) {
        CloseHandle(rd);
        CloseHandle(job);
        err = "error " + std::to_string(createErr);
        return nullptr;
    }
    AssignProcessToJobObject(job, pi.hProcess);
    ResumeThread(pi.hThread);
    CloseHandle(pi.hThread);
    return new Process{pi.hProcess, job, rd};
}

int process_read(Process* p, char* buf, int len) {
    DWORD n = 0;
    if (!ReadFile(p->rd, buf, (DWORD)len, &n, nullptr)) return -1;
    return (int)n;
}

void process_kill(Process* p) { TerminateJobObject(p->job, 1); }

int process_finish(Process* p, int timeoutMs) {
    WaitForSingleObject(p->proc, (DWORD)timeoutMs);
    DWORD code = STILL_ACTIVE;
    GetExitCodeProcess(p->proc, &code);
    CloseHandle(p->rd);
    CloseHandle(p->proc);
    CloseHandle(p->job); // kills anything still running
    delete p;
    return code == STILL_ACTIVE ? -1 : (int)code;
}

// ---- cloudflared
std::string cloudflared_download_url() {
    return "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe";
}

std::string cloudflared_install_path() { return app_data_dir() + "\\cloudflared.exe"; }

std::vector<std::string> cloudflared_candidates() {
    std::vector<std::string> c = {exe_dir() + "\\cloudflared.exe", cloudflared_install_path()};
    wchar_t buf[MAX_PATH];
    if (SearchPathW(nullptr, L"cloudflared.exe", nullptr, MAX_PATH, buf, nullptr)) c.push_back(utf8(buf));
    return c;
}

bool cloudflared_install(const std::string& downloadedFile, std::string& err) {
    if (!verifySigner(wide(downloadedFile), err)) return false;
    if (!file_rename(downloadedFile, cloudflared_install_path())) {
        err = "Cannot install cloudflared";
        return false;
    }
    return true;
}

} // namespace plat
