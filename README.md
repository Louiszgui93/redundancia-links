# 🌐 Redundância de Links & Failover Automático de Internet

Sistema corporativo de alta disponibilidade e contingência de conexão de internet para ambientes críticos (frentes de caixa/PDV, filiais e redes locais). 

O projeto monitora continuamente a conectividade através de múltiplos testes de latência/DNS e efetua a **comutação automática de gateway de rede (failover e failback)** no Windows via `netsh`, minimizando o tempo de inatividade da operação.

---

## 🚀 Funcionalidades

- **Monitoramento Contínuo:** Verificação em tempo real de conectividade via múltiplos servidores DNS e rotas externas com métricas de perda de pacotes e latência.
- **Failover & Failback Automático:** Troca imediata para o link secundário/redundante em caso de queda do link principal e retorno automático assim que o link primário é restabelecido.
- **Interface Desktop Moderna:** Construída em Python com `CustomTkinter` para acompanhamento visual do status da rede, latência e histórico de eventos.
- **Operação Silenciosa em Background:** Ícone dinâmico na bandeja do sistema (`System Tray`) com indicação de cores em tempo real do estado da conexão.
- **Servidor Centralizado (FastAPI + WebSockets):** Painel web central para acompanhamento e telemetria em tempo real das máquinas e status de links das lojas/filiais.
- **Persistência e Auto-inicialização:** Scripts de configuração para inicialização como serviço/tarefa agendada do Windows com privilégios administrativos sem interrupção de UAC.

---

## 📁 Estrutura do Projeto

```text
├── redundancia_links.py         # Aplicação cliente desktop (monitoramento, GUI e failover)
├── requirements.txt             # Dependências do projeto (cliente e servidor)
├── build.bat                    # Script para compilação do executável (.exe)
├── instalar_inicializacao_admin.bat / .ps1  # Scripts de instalação como tarefa agendada
├── remover_inicializacao_admin.bat          # Script para desinstalar inicialização automática
├── run_server.bat               # Script para iniciar o servidor central
└── server/                      # Servidor de telemetria centralizado
    ├── app.py                   # API FastAPI com suporte a WebSockets
    ├── static/                  # Arquivos estáticos da interface web
    └── templates/               # Templates HTML (Dashboard em tempo real)
```

---

## 🛠️ Tecnologias Utilizadas

- **Python 3.10+**
- **Interface Gráfica:** `CustomTkinter`, `Pillow`, `pystray`
- **Rede & Sistema:** `psutil`, `netsh`, `subprocess`, `winreg`
- **Comunicação em Tempo Real:** `WebSockets`, `requests`
- **Backend Servidor:** `FastAPI`, `Uvicorn`, `Jinja2`, `SQLite`

---

## ⚙️ Pré-requisitos e Instalação

1. Clone o repositório:
   ```bash
   git clone https://github.com/SEU-USUARIO/redundancia-links.git
   cd redundancia-links
   ```

2. Crie e ative um ambiente virtual:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   ```

---

## 🖥️ Como Executar

### Executando o Cliente Desktop
Como a troca de gateway na placa de rede requer privilégios elevados no Windows, execute o terminal como Administrador:

```powershell
python redundancia_links.py
```

### Executando o Servidor Central
```powershell
python server/app.py
# ou executando o script:
.\run_server.bat
```
Acesse o painel web no navegador em: `http://localhost:5555`.

---

## 📦 Compilação para Executável (.exe)

O projeto pode ser empacotado em executáveis independentes usando o **PyInstaller**:

```powershell
.\build.bat
```

Os binários compilados serão gerados na pasta `dist/`.

---

## 👤 Autor

Desenvolvido por **Luiz Guilherme de Almeida**  
- LinkedIn: [linkedin.com/in/seu-perfil](https://linkedin.com)  
- GitHub: [github.com/seu-usuario](https://github.com)  
