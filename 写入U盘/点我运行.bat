@echo off
chcp 65001 >nul
powershell.exe -ExecutionPolicy Bypass -NoProfile -File "%~dp0Deploy-ToUsb.ps1"
echo.
echo ----------------------------------------
pause
