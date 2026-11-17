@echo off
chcp 65001 >nul
rem Mở cổng 8000 trên tường lửa Windows để điện thoại / máy khác trong cùng Wi-Fi vào được app demo.
rem Tự xin quyền Administrator (bấm "Yes" khi Windows hỏi).
net session >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
netsh advfirewall firewall delete rule name="MVP demo 8000" >nul 2>&1
rem Chỉ cho máy trong cùng mạng nội bộ (localsubnet) – áp dụng cả khi Wi-Fi đang để Public.
netsh advfirewall firewall add rule name="MVP demo 8000" dir=in action=allow protocol=TCP localport=8000 profile=any remoteip=localsubnet
echo.
echo Da mo cong 8000 cho may cung mang noi bo.
echo Xoa luat sau khi demo: chay file dong_cong_8000.cmd
echo.
pause
