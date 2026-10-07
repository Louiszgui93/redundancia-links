@echo off
title Servidor Central - Redundancia
echo ========================================================
echo INICIANDO O SERVIDOR CENTRAL DE REDUNDANCIA E ROTAS...
echo ========================================================
echo.
echo Para acessar o painel de controle, abra no seu navegador:
echo http://localhost:5555
echo.
echo Para fechar o servidor, feche esta janela ou pressione Ctrl+C.
echo.
if exist dist\ServidorRedundancia.exe (
    echo [INFO] Iniciando a partir do executavel dist\ServidorRedundancia.exe
    dist\ServidorRedundancia.exe
) else (
    echo [INFO] Executavel nao encontrado. Iniciando script Python...
    python server/app.py
)
pause
