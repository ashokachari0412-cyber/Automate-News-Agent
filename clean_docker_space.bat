@echo off
title Purge Docker 20GB Virtual Disk
echo ========================================================
echo Reclaiming 20.74 GB Docker Storage...
echo ========================================================
echo.

:: Attempt to shut down WSL
echo Shutting down WSL2 and releasing locked virtual disks...
wsl.exe --shutdown

:: Unregister docker-desktop distros
wsl.exe --unregister docker-desktop >nul 2>&1
wsl.exe --unregister docker-desktop-data >nul 2>&1

:: Remove docker virtual disk directory directly
set VHDX_PATH=%LOCALAPPDATA%\Docker\wsl\disk\docker_data.vhdx
if exist "%VHDX_PATH%" (
    echo Deleting %VHDX_PATH%...
    del /f /q "%VHDX_PATH%"
)

if exist "%LOCALAPPDATA%\Docker\wsl" (
    echo Cleaning Docker WSL cache folder...
    rmdir /s /q "%LOCALAPPDATA%\Docker\wsl" >nul 2>&1
)

echo.
echo ========================================================
echo SUCCESS! 20.74 GB of Docker disk space has been reclaimed.
echo ========================================================
pause
