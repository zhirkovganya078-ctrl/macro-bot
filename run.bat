@echo off
chcp 65001 >nul
cd /d "%~dp0"
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
echo AHK Bot: zapusk...
python macro_bot.py
if %errorlevel% neq 0 (
    py macro_bot.py
)
pause
