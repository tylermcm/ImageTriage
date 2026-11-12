@echo off
rem Run from an x64 Visual Studio developer prompt. Argument: reviewed release asset.
setlocal
if "%~1"=="" (
    echo Usage: run_windows.bat path-to-pinned-cloudflared.exe
    exit /b 2
)
set "ASSET=%~f1"
set "OUT=%~dp0..\build\security-tests"
if not exist "%OUT%" mkdir "%OUT%"
pushd "%OUT%" || exit /b 1
cl /nologo /std:c++20 /O2 /EHsc /W4 /utf-8 /MT /DUNICODE /D_UNICODE "%~dp0cloudflared_win_test.cpp" /Fe:verify-cloudflared.exe /link user32.lib gdi32.lib shell32.lib ole32.lib oleaut32.lib uuid.lib ws2_32.lib iphlpapi.lib winhttp.lib bcrypt.lib advapi32.lib windowscodecs.lib runtimeobject.lib
if errorlevel 1 (
    popd
    exit /b 1
)
verify-cloudflared.exe "%ASSET%" "%OUT%\scratch"
set "RESULT=%ERRORLEVEL%"
popd
exit /b %RESULT%
