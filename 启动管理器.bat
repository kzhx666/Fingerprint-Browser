@echo off
chcp 65001 >nul
title KZ 多开管理器
cd /d "%~dp0"
if exist "KZManager.exe" (
    start "" "KZManager.exe"
) else (
    echo 找不到 KZManager.exe，回退到 PowerShell 版...
    powershell -NoProfile -ExecutionPolicy Bypass -File "manager.ps1"
)