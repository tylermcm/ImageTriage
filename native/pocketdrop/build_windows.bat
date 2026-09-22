@echo off
rem Builds image_triage\pocketdrop\pocketdrop.dll: PocketDrop's engine and UI
rem (src\, vendored by scripts\sync_pocketdrop.py) behind the C interface in
rem capi\. Static CRT, so the DLL has no runtime dependencies.
rem Requires Visual Studio 2022 (or Build Tools) with the C++ workload.
setlocal
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
for /f "usebackq delims=" %%i in (`call "%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSDIR=%%i"
if not defined VSDIR (
    echo Visual Studio C++ Build Tools not found.
    exit /b 1
)
call "%VSDIR%\VC\Auxiliary\Build\vcvars64.bat" >nul 2>nul || exit /b 1
cd /d "%~dp0"
set "OUT=..\..\image_triage\pocketdrop"
if not exist build\obj mkdir build\obj

set SRC=src\core\qr.cpp src\core\deflate.cpp src\core\bundle.cpp src\core\http.cpp src\core\tunnel.cpp ^
 src\ui\ui.cpp src\ui\icons.cpp src\win\platform_win.cpp src\win\hotspot_win.cpp capi\pocketdrop_capi.cpp
set LIBS=user32.lib gdi32.lib shell32.lib ole32.lib oleaut32.lib uuid.lib ws2_32.lib iphlpapi.lib winhttp.lib ^
 wintrust.lib crypt32.lib bcrypt.lib advapi32.lib windowscodecs.lib runtimeobject.lib

cl /nologo /std:c++20 /O2 /GL /Gy /EHsc /W4 /utf-8 /MT /LD /DUNICODE /D_UNICODE /DNDEBUG ^
   /Fo:build\obj\ /Fe:build\pocketdrop.dll %SRC% ^
   /link /LTCG /OPT:REF /OPT:ICF %LIBS% || exit /b 1
copy /y build\pocketdrop.dll "%OUT%\pocketdrop.dll" >nul || exit /b 1
echo Built %OUT%\pocketdrop.dll
