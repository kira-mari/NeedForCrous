@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PS1_FILE=%SCRIPT_DIR%build_exe.ps1"

if not exist "%PS1_FILE%" (
    echo [ERREUR] Fichier introuvable: "%PS1_FILE%"
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1_FILE%" %*
set "EXIT_CODE=%ERRORLEVEL%"

exit /b %EXIT_CODE%
