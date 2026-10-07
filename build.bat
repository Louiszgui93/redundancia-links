@echo off
echo ========================================================
echo COMPILADOR: GERENCIADOR DE REDUNDANCIA DE INTERNET E ROTAS
echo ========================================================
echo.
echo [1/2] Verificando e atualizando dependencias...
python -m pip install --upgrade pip
pip install pystray pillow customtkinter psutil pyinstaller --upgrade
echo.
echo [2/2] Compilando executavel (Single File + UAC Admin + No Console)...
pyinstaller --clean --noconsole --uac-admin --onefile --collect-all customtkinter --name "RedundanciaInternet" redundancia_links.py
echo.
echo ========================================================
echo COMPILACAO CONCLUIDA COM SUCESSO!
echo O executavel foi gerado na pasta: dist\RedundanciaInternet.exe
echo ========================================================
pause
