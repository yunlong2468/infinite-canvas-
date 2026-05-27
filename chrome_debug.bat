@echo off
title Chrome CDP Debug Mode

echo ============================================
echo   Chrome Debug Mode (CDP) Launcher
echo ============================================
echo.

echo [1/2] Closing all Chrome processes...
taskkill /F /IM chrome.exe >nul 2>&1
timeout /t 3 /nobreak >nul
echo Done.

echo.
echo [2/2] Starting Chrome with debug port 9222...
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\temp\chrome-debug-profile" --no-first-run --no-default-browser-check
timeout /t 4 /nobreak >nul

echo.
netstat -ano | find ":9222" >nul
if %errorlevel% equ 0 (
    echo SUCCESS: Chrome is listening on port 9222.
    echo Keep this window open. Go to writing page and retry.
) else (
    echo FAILED: Port 9222 is not open. Try running as Administrator.
)
echo.
pause
