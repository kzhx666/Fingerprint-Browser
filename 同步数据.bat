@echo off
chcp 65001 >nul
cd /d "%~dp0"
python "scripts\sync-menu.py"
echo.
pause
