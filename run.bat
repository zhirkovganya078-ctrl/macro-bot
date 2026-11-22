@echo off
cd /d "%~dp0"
python macro_bot.py 2>nul || py macro_bot.py
pause
