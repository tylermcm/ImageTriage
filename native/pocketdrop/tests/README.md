# Windows cloudflared verification

The Windows helper is pinned to Cloudflare's [2026.11.0 release](https://github.com/cloudflare/cloudflared/releases/tag/2026.11.0),
`cloudflared-windows-amd64.exe`, 55,397,592 bytes:

```
SHA-256 d5c1d82031da6253dcdcff64c944cd0fe063016edeb2e4fd088075fc9bd52d0a
```

The release notes, GitHub asset digest, and independently downloaded bytes matched
on 2026-11-12. The downloaded asset also had a valid Cloudflare, Inc. Authenticode
signature. Runtime authorization uses the exact SHA-256 pin: a signer display name
or an unsigned file is never an alternative acceptance path.

Update URL, size and digest in `src/win/cloudflared_pin.h` together after reviewing
a release. A release tag can be replaced upstream; changed bytes fail the pin.
Other versions in the application directory, managed cache or PATH are ignored,
and launch independently verifies the file again while denying writes/deletion
until process creation returns. This also covers files cached by older builds.
Existing downloads are not silently trusted or executed. A version bump therefore
requires a reviewed source update; automatic helper updates are disabled.

From an x64 Visual Studio developer prompt, with the reviewed asset downloaded
outside the repository:

```
native\pocketdrop\tests\run_windows.bat path-to-pinned-cloudflared.exe
```

The harness checks the official asset, a byte modification, truncation, unsigned
unrelated content, missing files, concurrent writes/deletion, rejection without
replacing a valid install, successful installation, discovery and launch after
cache tampering, and a verified `--version` process. It uses disposable files and
a process-local `LOCALAPPDATA`; it never opens a public tunnel. The asset passed
as input stays unchanged.

Build the complete DLL into a staging directory and use
`IMAGE_TRIAGE_POCKETDROP_LIB` to test it before replacing a working copy. The QML
engine tests honor that override on Windows. Exclude the `anywhere_needs` test
when no public tunnel should be created. The normal build script copies its
result into the repository, so do not use it merely to stage a candidate.

This pin policy is Windows-specific. The macOS/Linux policies are unchanged.
