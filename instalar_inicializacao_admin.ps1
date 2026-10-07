# Auto-elevação para Administrador se necessário
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[!] Solicitando permissões de Administrador..." -ForegroundColor Yellow
    Start-Process powershell.exe -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    Exit
}

$scriptDir = Split-Path -Parent $PSCommandPath
Set-Location $scriptDir

$exePath = Join-Path $scriptDir "RedundanciaInternet.exe"
if (-not (Test-Path $exePath)) {
    Write-Host "[ERRO] RedundanciaInternet.exe não encontrado em: $scriptDir" -ForegroundColor Red
    Pause
    Exit
}

Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host "       CONFIGURADOR DE INICIALIZAÇÃO COMO ADMINISTRADOR (PowerShell)" -ForegroundColor Cyan
Write-Host "                          Redundância de Internet" -ForegroundColor Cyan
Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Executável localizado: $exePath" -ForegroundColor Green
Write-Host ""
Write-Host "Escolha como deseja configurar a inicialização:"
Write-Host " [1] Usar privilégios de Administrador local (Recomendado - sem pedir senha)"
Write-Host " [2] Informar Usuário e Senha do Administrador (Mesmo login/senha em todas as máquinas)"
Write-Host ""

$opcao = Read-Host "Digite a opção [1 ou 2, Padrão=1]"
if ($opcao -eq "2") {
    $adminUser = Read-Host "Digite o usuário Administrador (ex: Administrador)"
    $adminPass = Read-Host "Digite a senha do Administrador" -AsSecureString
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($adminPass)
    $plainPass = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    
    Write-Host "[*] Registrando tarefa agendada para o usuário $adminUser..." -ForegroundColor Yellow
    $res = Start-Process "schtasks.exe" -ArgumentList "/create /tn `"RedundanciaInternet`" /tr `"`'$exePath`'`" /sc onlogon /rl highest /ru `"$adminUser`" /rp `"$plainPass`" /it /f" -NoNewWindow -Wait -PassThru
} else {
    Write-Host "[*] Registrando tarefa agendada com privilégios máximos (/rl highest)..." -ForegroundColor Yellow
    $res = Start-Process "schtasks.exe" -ArgumentList "/create /tn `"RedundanciaInternet`" /tr `"`'$exePath`'`" /sc onlogon /rl highest /f" -NoNewWindow -Wait -PassThru
}

if ($res.ExitCode -eq 0) {
    Write-Host ""
    Write-Host "==============================================================================" -ForegroundColor Green
    Write-Host "[SUCESSO] Configuração concluída com êxito!" -ForegroundColor Green
    Write-Host "O programa RedundanciaInternet.exe agora iniciará automaticamente" -ForegroundColor Green
    Write-Host "como Administrador a cada inicialização do Windows." -ForegroundColor Green
    Write-Host "- Nenhuma tela de confirmação (UAC) será exibida." -ForegroundColor Green
    Write-Host "- Todos os processos rodarão 100% em segundo plano sem abrir telas." -ForegroundColor Green
    Write-Host "==============================================================================" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "[ERRO] Falha ao registrar tarefa. Código de saída: $($res.ExitCode)" -ForegroundColor Red
}

Write-Host ""
Pause
