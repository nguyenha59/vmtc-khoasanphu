@echo off
chcp 65001 >nul
rem Xóa luật tường lửa mở cổng 8000 sau khi demo xong.
net session >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
netsh advfirewall firewall delete rule name="MVP demo 8000"
echo Da dong cong 8000.
pause
