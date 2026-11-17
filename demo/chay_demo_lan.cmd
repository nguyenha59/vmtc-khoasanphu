@echo off
chcp 65001 >nul
rem Chạy app cho máy khác trong cùng Wi-Fi / LAN truy cập (chỉ demo nội bộ, HTTP).
cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" (set PY=.venv\Scripts\python.exe) else (set PY=python)
echo.
echo Dia chi tren dien thoai (cung Wi-Fi):
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do for /f "tokens=*" %%b in ("%%a") do echo    http://%%b:8000
echo (Bo qua dia chi 192.168.56.x - do la card mang ao VirtualBox)
echo Tat app: bam Ctrl+C trong cua so nay.
echo.
%PY% -m uvicorn api.main:app --host 0.0.0.0 --port 8000
pause
