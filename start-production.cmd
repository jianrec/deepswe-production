@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\prepare_windows_docker.ps1" %*
set "exit_code=%ERRORLEVEL%"
echo.
if not "%exit_code%"=="0" echo Production launcher stopped with exit code %exit_code%.
pause
exit /b %exit_code%
