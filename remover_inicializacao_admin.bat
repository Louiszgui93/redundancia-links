@echo off
setlocal
cd /d "%~dp0"

echo ==============================================================================
echo       REMOVEDOR DE INICIALIZACAO AUTOMATICA - Redundancia de Internet
echo ==============================================================================
echo.

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Solicitando elevacao de Administrador...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process cmd.exe -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

schtasks /delete /tn "RedundanciaInternet" /f >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Tarefa agendada 'RedundanciaInternet' removida com sucesso!
) else (
    echo [INFO] A tarefa agendada nao existia ou ja foi removida.
)

echo.
pause
