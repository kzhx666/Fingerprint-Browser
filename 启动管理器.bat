@echo off
chcp 65001 >nul
title KZ 多开管理器
cd /d "%~dp0"

if exist "KZManager.exe" (
    start "" "KZManager.exe"
    exit /b 0
)
if exist "out\KZManager.exe" (
    start "" "out\KZManager.exe"
    exit /b 0
)

echo 未找到 KZManager.exe，回退到 PowerShell 版...
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\manager.ps1"
