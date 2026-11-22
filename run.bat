@echo off
cd /d "%~dp0"
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Start-Process cmd -ArgumentList '/k cd /d \"%~dp0\" && (python macro_bot.py 2>nul || py macro_bot.py) & pause' -Verb RunAs"
) else (
    python macro_bot.py 2>nul || py macro_bot.py
    pause
)
