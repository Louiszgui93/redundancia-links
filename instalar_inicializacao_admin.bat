@echo off
setlocal
cd /d "%~dp0"

:: 1. Verifica se esta rodando como Administrador
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Solicitando elevacao de Administrador para registrar a tarefa...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process cmd.exe -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

set "EXE_PATH=%~dp0RedundanciaInternet.exe"
if not exist "%EXE_PATH%" (
    echo [ERRO] RedundanciaInternet.exe nao encontrado nesta pasta:
    echo "%~dp0"
    echo.
    pause
    exit /b
)

echo ==============================================================================
echo       CONFIGURADOR DE INICIALIZACAO COMO ADMINISTRADOR
echo                          Redundancia de Internet
echo ==============================================================================
echo.
echo Executavel localizado: "%EXE_PATH%"
echo.
echo Escolha como deseja configurar a inicializacao:
echo.
echo  [1] Usar privilegios de Administrador local (Recomendado)
echo      Inicia automaticamente a cada logon sem pedir senha nem UAC.
echo.
echo  [2] Informar Usuario e Senha do Administrador
echo      Util se o operador do Windows for um usuario restrito (ex: caixa)
echo      e voce deseja fixar a credencial do administrador comum a todas as lojas.
echo.
set /p OPCAO="Digite a opcao [1 ou 2, Padrao=1]: "

if "%OPCAO%"=="2" goto CONFIG_USUARIO
goto CONFIG_PADRAO

:CONFIG_USUARIO
echo.
set /p ADMIN_USER="Digite o usuario Administrador (ex: Administrador): "
set /p ADMIN_PASS="Digite a senha do Administrador: "
echo.
echo [*] Registrando no Agendador de Tarefas do Windows com credenciais salvas...
schtasks /create /tn "RedundanciaInternet" /tr "'%EXE_PATH%'" /sc onlogon /rl highest /ru "%ADMIN_USER%" /rp "%ADMIN_PASS%" /it /f
goto FINALIZAR

:CONFIG_PADRAO
echo.
echo [*] Registrando no Agendador de Tarefas do Windows com privilegios maximos (/rl highest)...
schtasks /create /tn "RedundanciaInternet" /tr "'%EXE_PATH%'" /sc onlogon /rl highest /f
goto FINALIZAR

:FINALIZAR
if %errorlevel% equ 0 goto SUCESSO
goto ERRO

:SUCESSO
echo.
echo ==============================================================================
echo [SUCESSO] Configuracao concluida!
echo.
echo O programa "RedundanciaInternet.exe" agora iniciara automaticamente
echo com privilegios de Administrador a cada inicializacao do Windows.
echo.
echo - Nenhuma tela de confirmacao (UAC) sera exibida.
echo - Todos os comandos de rede rodarao 100%% em segundo plano sem abrir telas.
echo ==============================================================================
echo.
pause
exit /b

:ERRO
echo.
echo ==============================================================================
echo [ERRO] Falha ao registrar a tarefa no Agendador de Tarefas do Windows.
echo Codigo do erro: %errorlevel%
echo Verifique se as credenciais fornecidas estao corretas.
echo ==============================================================================
echo.
pause
exit /b
