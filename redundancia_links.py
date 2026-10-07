import os
import sys
import json
import time
import socket
import re
import ctypes
import logging
import threading
import subprocess
import winreg
from logging.handlers import RotatingFileHandler
from PIL import Image, ImageDraw

import psutil
import customtkinter as ctk
import pystray
import requests
import websockets
import queue

# Força o diretório de trabalho a ser o mesmo do executável/script (evita abrir no System32 como Admin)
if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))
else:
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

# --- CONFIGURAÇÃO DE LOGS ---
LOG_FILE = "controle_link.log"
logger = logging.getLogger("RedundanciaLinks")
logger.setLevel(logging.INFO)

# Formatter para o log em arquivo
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

# Handler para arquivo rotativo - Limite de 20MB (20 * 1024 * 1024 bytes)
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=20 * 1024 * 1024, backupCount=2, encoding='utf-8')
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

# --- VERIFICAÇÃO DE PRIVILÉGIOS ADMINISTRATIVOS ---
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False

# --- CONFIGURAÇÃO DO REGISTRO DO WINDOWS PARA PROXY ---
def disable_windows_proxy():
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0,
            winreg.KEY_WRITE
        )
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        winreg.CloseKey(key)
        
        # Notifica o SO para atualizar as configurações de proxy instantaneamente
        ctypes.windll.Wininet.InternetSetOptionW(0, 39, 0, 0)  # INTERNET_OPTION_SETTINGS_CHANGED
        ctypes.windll.Wininet.InternetSetOptionW(0, 37, 0, 0)  # INTERNET_OPTION_REFRESH
        logger.info("Proxy da máquina desativado com sucesso no registro do Windows.")
        return True
    except Exception as e:
        logger.error(f"Erro ao desativar proxy do Windows no registro: {e}")
        return False

# --- CONFIGURAÇÃO PADRÃO (JSON) ---
AUTO_INTERFACE_LABEL = "Automático (Placa Ativa)"

CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "store_id": "LOJA_01",
    "server_url": "http://localhost:5555",
    "interface": AUTO_INTERFACE_LABEL,
    "mode": "routes",  # Opções: "gateway", "full_ip", "routes"
    "ping_interval": 20,
    "ping_timeout": 1000,
    "fail_limit": 3,
    "auto_fallback": True,
    "fallback_interval": 60,
    "link1": {
        "name": "Embratel",
        "gateway": "192.168.4.1",
        "ip": "192.168.4.100",
        "mask": "255.255.255.0",
        "dns1": "8.8.8.8",
        "dns2": "8.8.4.4",
        "test_ips": "192.168.4.1\n172.20.153.254\n172.21.0.244\n172.21.0.254"
    },
    "link2": {
        "name": "Oi",
        "gateway": "192.168.5.76",
        "ip": "192.168.5.100",
        "mask": "255.255.255.0",
        "dns1": "1.1.1.1",
        "dns2": "1.0.0.1",
        "test_ips": "192.168.5.76\n172.20.153.254\n172.21.0.244\n172.21.0.254"
    },
    "static_subnets": "172.20.153.0/24\n172.21.0.0/16\n172.19.0.0/16"
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
                # Garante que chaves novas sejam preenchidas com padrão
                for k, v in DEFAULT_CONFIG.items():
                    if k not in config:
                        config[k] = v
                return config
        except Exception as e:
            logger.error(f"Erro ao carregar {CONFIG_FILE}: {e}. Usando padrões.")
    return DEFAULT_CONFIG.copy()

def save_config(config):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        logger.info("Configurações salvas com sucesso em config.json.")
        return True
    except Exception as e:
        logger.error(f"Erro ao salvar config.json: {e}")
        return False

# Flag do Windows para impedir a criação de janela de console (0x08000000)
CREATE_NO_WINDOW = 0x08000000

# Cache global para a interface de rede ativa detectada
_CACHED_ACTIVE_INTERFACE = None

# --- UTILS DE REDE E POWERSHELL ---
def run_powershell(command):
    """
    Executa comando PowerShell 100% em segundo plano (silent),
    sem abrir janelas de console e sem piscadas na tela.
    """
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE
        
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True,
            text=True,
            encoding='cp850',  # Codificação do Windows em Português
            startupinfo=startupinfo,
            creationflags=CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
            timeout=15
        )
        if result.returncode == 0:
            return result.stdout.strip(), True
        else:
            err = result.stderr.strip()
            logger.error(f"PowerShell retornou erro. Comando: {command}. Erro: {err}")
            return err, False
    except Exception as e:
        logger.error(f"Exceção ao rodar comando PowerShell: {e}")
        return str(e), False

def get_network_interfaces():
    interfaces = []
    try:
        stats = psutil.net_if_stats()
        for name, addrs in psutil.net_if_addrs().items():
            if any(ign in name.lower() for ign in ['loopback', 'pseudo', 'vethernet', 'teredo', 'isatap']):
                continue
            has_ipv4 = False
            for addr in addrs:
                if addr.family == socket.AF_INET and addr.address != "127.0.0.1":
                    has_ipv4 = True
                    break
            if has_ipv4:
                interfaces.append(name)
    except Exception as e:
        logger.error(f"Erro ao buscar interfaces de rede: {e}")
        interfaces = ["Ethernet", "Wi-Fi"]
    return interfaces

def detect_active_interface():
    """
    Detecta de forma dinâmica qual placa de rede está fisicamente conectada e ativa no Windows.
    Suporta máquinas com 'Ethernet 2', 'Ethernet 3', 'Conexão Local', etc.
    Prioriza psutil (em memória, 0ms, zero janelas e zero processos).
    """
    global _CACHED_ACTIVE_INTERFACE
    
    # 1. Tenta psutil nativo (instantâneo e 100% silencioso em memória)
    try:
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
        
        # Se já temos uma placa em cache e ela ainda está 'isup', mantém ela
        if _CACHED_ACTIVE_INTERFACE and _CACHED_ACTIVE_INTERFACE in stats:
            if stats[_CACHED_ACTIVE_INTERFACE].isup:
                return _CACHED_ACTIVE_INTERFACE

        candidates = []
        for iface_name, stat in stats.items():
            if not stat.isup:
                continue
            if any(ign in iface_name.lower() for ign in ["loopback", "pseudo", "vethernet", "teredo", "isatap"]):
                continue
            addr_list = addrs.get(iface_name, [])
            has_ipv4 = any(a.family == socket.AF_INET and not a.address.startswith("127.") and not a.address.startswith("169.254.") for a in addr_list)
            if has_ipv4:
                # Prioriza cabeadas (Ethernet / Conexão Local) sobre Wi-Fi
                priority = 10 if ("ethernet" in iface_name.lower() or "conexão local" in iface_name.lower()) else 5
                candidates.append((priority, stat.speed, iface_name))
        
        if candidates:
            candidates.sort(reverse=True)
            chosen = candidates[0][2]
            _CACHED_ACTIVE_INTERFACE = chosen
            logger.info(f"Interface ativa detectada nativamente (psutil): '{chosen}'")
            return chosen
    except Exception as e:
        logger.error(f"Erro ao detectar interface via psutil: {e}")

    # 2. Fallback inteligente via PowerShell com rota padrão (caso psutil não encontre)
    cmd = "(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway } | Select-Object -First 1).InterfaceAlias"
    stdout, ok = run_powershell(cmd)
    if ok and stdout:
        _CACHED_ACTIVE_INTERFACE = stdout
        logger.info(f"Interface ativa detectada via rota padrão do Windows: '{stdout}'")
        return stdout

    # 3. Fallback final: primeira interface disponível ou 'Ethernet'
    available = get_network_interfaces()
    if available:
        _CACHED_ACTIVE_INTERFACE = available[0]
        return available[0]

    return "Ethernet"

def install_startup_task(admin_user=None, admin_pass=None):
    """
    Registra o programa no Agendador de Tarefas do Windows para iniciar automaticamente
    com privilégios elevados de Administrador (/rl HIGHEST) a cada logon,
    sem exibir popups de UAC para o operador da máquina.
    Chama schtasks.exe diretamente de forma nativa e 100% silenciosa.
    """
    try:
        exe_path = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__)
        task_name = "RedundanciaInternet"
        
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE
        
        cmd = ["schtasks.exe", "/create", "/tn", task_name, "/tr", f"'{exe_path}'", "/sc", "onlogon", "/rl", "highest", "/f"]
        if admin_user and admin_pass:
            cmd.extend(["/ru", admin_user, "/rp", admin_pass, "/it"])
            
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='cp850',
            startupinfo=startupinfo,
            creationflags=CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
            timeout=15
        )
        if res.returncode == 0:
            logger.info(f"Tarefa de inicialização como Administrador '{task_name}' registrada com sucesso no Windows!")
            return True, "Inicialização automática como Administrador ativada com sucesso!\nO programa iniciará a cada logon com direitos de Administrador sem pedir senhas ou exibir UAC."
        else:
            err = res.stderr.strip() or res.stdout.strip()
            logger.error(f"Falha ao registrar tarefa no agendador: {err}")
            return False, f"Falha ao registrar no Agendador do Windows: {err}"
    except Exception as e:
        logger.error(f"Erro ao registrar tarefa: {e}")
        return False, str(e)

def remove_startup_task():
    try:
        task_name = "RedundanciaInternet"
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE
        
        res = subprocess.run(
            ["schtasks.exe", "/delete", "/tn", task_name, "/f"],
            capture_output=True,
            text=True,
            encoding='cp850',
            startupinfo=startupinfo,
            creationflags=CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
            timeout=15
        )
        return res.returncode == 0, (res.stdout.strip() or res.stderr.strip())
    except Exception as e:
        return False, str(e)
def resolve_interface(configured_name=None):
    """
    Resolve o nome real da interface de rede a ser manipulada:
    - Se configured_name for 'Automático (Placa Ativa)' ou vazio -> detecta a placa ativa.
    - Se configured_name for especificado (ex: 'Ethernet 2') e existir na máquina -> utiliza ela.
    - Se configured_name foi informado (ex: 'Ethernet') mas NÃO existe na máquina -> detecta e usa a placa ativa!
    """
    all_ifaces = get_network_interfaces()
    
    if not configured_name or configured_name == AUTO_INTERFACE_LABEL or configured_name.lower() == "auto":
        return detect_active_interface()
        
    if configured_name in all_ifaces:
        return configured_name
        
    # A interface configurada não existe nesta máquina (ex: 'Ethernet' veio no config, mas o PC usa 'Ethernet 2' ou 'Ethernet 3')
    active = detect_active_interface()
    logger.warning(f"Interface configurada '{configured_name}' NÃO encontrada nesta máquina! Redirecionando automaticamente para a placa ativa: '{active}'")
    return active

# --- CONTROLADOR DE CONFIGURAÇÃO DE REDE ---
class NetworkController:
    def __init__(self, interface):
        self.interface = resolve_interface(interface)

    def set_gateway_only(self, gateway):
        logger.info(f"Alterando apenas o gateway da interface '{self.interface}' para {gateway}...")
        # Remove rotas padrão existentes da interface específica
        cmd_del = f"Remove-NetRoute -InterfaceAlias '{self.interface}' -DestinationPrefix '0.0.0.0/0' -Confirm:$false -ErrorAction SilentlyContinue"
        run_powershell(cmd_del)
        # Adiciona nova rota padrão
        cmd_add = f"New-NetRoute -InterfaceAlias '{self.interface}' -DestinationPrefix '0.0.0.0/0' -NextHop '{gateway}' -RouteMetric 1 -Confirm:$false"
        stdout, ok = run_powershell(cmd_add)
        if ok:
            logger.info(f"Gateway alterado com sucesso para {gateway}.")
        return ok

    def set_full_ip(self, ip, mask, gateway, dns1, dns2):
        logger.info(f"Configurando IP Completo na interface '{self.interface}': IP={ip}, Máscara={mask}, Gateway={gateway}...")
        
        # Converte máscara para CIDR
        try:
            prefix = sum(bin(int(x)).count('1') for x in mask.split('.'))
        except Exception:
            prefix = 24  # Fallback padrão
            logger.warning(f"Falha ao converter máscara {mask} para prefixo. Usando /24.")

        # Remove IPs existentes da interface
        cmd_del = f"Remove-NetIPAddress -InterfaceAlias '{self.interface}' -AddressFamily IPv4 -Confirm:$false -ErrorAction SilentlyContinue"
        run_powershell(cmd_del)

        # Adiciona nova estrutura de IP e gateway
        cmd_add = f"New-NetIPAddress -InterfaceAlias '{self.interface}' -IPAddress '{ip}' -PrefixLength {prefix} -DefaultGateway '{gateway}' -Confirm:$false"
        stdout, ok = run_powershell(cmd_add)
        if not ok:
            logger.error("Falha ao configurar IP e Gateway.")
            return False

        # Configura DNS
        dns_servers = []
        if dns1.strip(): dns_servers.append(f"'{dns1.strip()}'")
        if dns2.strip(): dns_servers.append(f"'{dns2.strip()}'")
        
        if dns_servers:
            dns_str = ",".join(dns_servers)
            cmd_dns = f"Set-DnsClientServerAddress -InterfaceAlias '{self.interface}' -ServerAddresses ({dns_str}) -ErrorAction SilentlyContinue"
        else:
            cmd_dns = f"Set-DnsClientServerAddress -InterfaceAlias '{self.interface}' -ResetServerAddresses -ErrorAction SilentlyContinue"
        
        run_powershell(cmd_dns)
        logger.info("IP Completo e DNS configurados com sucesso.")
        return True

    def manage_static_routes(self, subnets, gateway, metric=1):
        logger.info(f"Gerenciando rotas estáticas na interface '{self.interface}' apontando para o gateway {gateway}...")
        clean_subnets = [s.strip() for s in subnets if s.strip()]
        if not clean_subnets:
            return True
            
        commands = []
        # Primeiro, remove todas as rotas estáticas das sub-redes informadas
        for subnet in clean_subnets:
            commands.append(f"Remove-NetRoute -DestinationPrefix '{subnet}' -InterfaceAlias '{self.interface}' -Confirm:$false -ErrorAction SilentlyContinue")
            
        # Adiciona as rotas atualizadas apontando para o gateway correto
        for subnet in clean_subnets:
            commands.append(f"New-NetRoute -DestinationPrefix '{subnet}' -InterfaceAlias '{self.interface}' -NextHop '{gateway}' -RouteMetric {metric} -Confirm:$false -ErrorAction SilentlyContinue")
            
        full_script = "; ".join(commands)
        stdout, ok = run_powershell(full_script)
        if ok:
            logger.info(f"Rotas estáticas atualizadas para {len(clean_subnets)} sub-redes via {gateway} (métrica {metric}).")
        else:
            logger.error(f"Falha ao atualizar rotas estáticas via {gateway}: {stdout}")
        return ok

    def reset_to_dhcp(self):
        logger.info(f"Redefinindo a interface '{self.interface}' para DHCP (Automático)...")
        
        # Ativa DHCP na interface
        cmd_ip_interface = f"Set-NetIPInterface -InterfaceAlias '{self.interface}' -Dhcp Enabled -ErrorAction SilentlyContinue"
        run_powershell(cmd_ip_interface)
        
        # Remove IPs estáticos para permitir o DHCP atuar
        cmd_del = f"Remove-NetIPAddress -InterfaceAlias '{self.interface}' -AddressFamily IPv4 -Confirm:$false -ErrorAction SilentlyContinue"
        run_powershell(cmd_del)
        
        # Reseta DNS para obter do DHCP
        cmd_dns = f"Set-DnsClientServerAddress -InterfaceAlias '{self.interface}' -ResetServerAddresses -ErrorAction SilentlyContinue"
        run_powershell(cmd_dns)
        
        logger.info(f"Interface '{self.interface}' redefinida para DHCP com sucesso.")
        return True


# --- ICONE DA BANDEJA DO SISTEMA (pystray) ---
def create_status_icon(color_name):
    # Gera um ícone circular dinamicamente em PIL
    image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    
    # Mapeamento de cores
    colors = {
        "green": (16, 185, 129, 255),  # Link 1 Online (Emerald)
        "blue": (59, 130, 246, 255),   # Link 2 Online (Blue)
        "yellow": (245, 158, 11, 255), # Alternando/Buscando (Amber)
        "red": (239, 68, 68, 255)      # Offline (Red)
    }
    color = colors.get(color_name, (156, 163, 175, 255))
    
    # Desenha um círculo com glow
    draw.ellipse([4, 4, 60, 60], fill=None, outline=color, width=4)
    draw.ellipse([14, 14, 50, 50], fill=color, outline=None)
    
    return image


# --- APP PRINCIPAL ---
class RedundancyApp:
    def __init__(self):
        self.config = load_config()
        self.active_link = "link1" # Inicia no Link 1 por padrão
        self.is_monitoring = False
        self.monitor_thread = None
        self.icon = None
        
        # Configuração do tema CustomTkinter
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        
        # Inicializa a interface gráfica
        self.root = ctk.CTk()
        self.root.title("Gerenciador de Redundância de Internet e Rotas")
        self.root.geometry("820x620")
        self.root.resizable(True, True)
        
        # Captura o fechamento da janela
        self.root.protocol("WM_DELETE_WINDOW", self.on_window_close)
        
        # Cria o layout
        self.create_widgets()
        self.load_settings_into_gui()
        
        # Inicializa a bandeja do sistema
        self.setup_system_tray()
        
        # Auto-inicializa o log na tela
        self.setup_gui_logger()
        
        # Inicializa variáveis de status e dispara gerenciamento do WebSocket nativo
        self.last_online_status = False
        self.last_latency_status = 0.0
        self.setup_websocket()
        
        logger.info("Aplicativo iniciado com sucesso.")
        
    def setup_gui_logger(self):
        class GUIHandler(logging.Handler):
            def __init__(self, app_instance):
                super().__init__()
                self.app = app_instance
            def emit(self, record):
                log_entry = self.format(record)
                self.app.append_log_to_textbox(log_entry, record.levelname)
                
        gui_formatter = logging.Formatter('%(asctime)s - %(message)s', datefmt='%H:%M:%S')
        gui_handler = GUIHandler(self)
        gui_handler.setFormatter(gui_formatter)
        logger.addHandler(gui_handler)
        
    def append_log_to_textbox(self, text, level):
        def write():
            self.txt_log.configure(state="normal")
            
            # Escolhe tag de cor com base no nível ou conteúdo
            tag = "info"
            lower_text = text.lower()
            if level == "WARNING":
                tag = "warning"
            elif level in ("ERROR", "CRITICAL"):
                tag = "error"
            elif "sucesso" in lower_text or "online" in lower_text or "adicionada" in lower_text or "redefinida" in lower_text:
                tag = "success"
                
            self.txt_log.insert("end", text + "\n", tag)
            
            # Limita a 500 linhas no console visual para performance
            num_lines = int(self.txt_log.index('end-1c').split('.')[0])
            if num_lines > 500:
                self.txt_log.delete("1.0", "100.0")
                
            self.txt_log.configure(state="disabled")
            self.txt_log.see("end")
            
        self.root.after(0, write)

    def create_widgets(self):
        # Configuração do Grid principal
        self.root.grid_columnconfigure(0, weight=0) # Sidebar
        self.root.grid_columnconfigure(1, weight=1) # Conteúdo
        self.root.grid_rowconfigure(0, weight=1)
        
        # --- SIDEBAR (STATUS & AÇÕES RÁPIDAS) ---
        self.sidebar = ctk.CTkFrame(self.root, width=220, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        self.sidebar.grid_rowconfigure(8, weight=1)
        
        lbl_app_title = ctk.CTkLabel(self.sidebar, text="REDUNDÂNCIA", font=ctk.CTkFont(size=20, weight="bold"))
        lbl_app_title.grid(row=0, column=0, padx=20, pady=(20, 10))
        
        # Frame de Status
        self.status_card = ctk.CTkFrame(self.sidebar, corner_radius=10, fg_color="#1E293B")
        self.status_card.grid(row=1, column=0, padx=15, pady=10, sticky="ew")
        
        self.lbl_status_indicator = ctk.CTkLabel(self.status_card, text="●", text_color="#EF4444", font=ctk.CTkFont(size=36))
        self.lbl_status_indicator.pack(side="left", padx=(15, 5), pady=5)
        
        status_text_frame = ctk.CTkFrame(self.status_card, fg_color="transparent")
        status_text_frame.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        
        self.lbl_status_state = ctk.CTkLabel(status_text_frame, text="Parado", font=ctk.CTkFont(size=14, weight="bold"))
        self.lbl_status_state.pack(anchor="w")
        
        self.lbl_status_link = ctk.CTkLabel(status_text_frame, text="Link: --", font=ctk.CTkFont(size=12))
        self.lbl_status_link.pack(anchor="w")
        
        self.lbl_status_latency = ctk.CTkLabel(status_text_frame, text="Ping: -- ms", font=ctk.CTkFont(size=12))
        self.lbl_status_latency.pack(anchor="w")
        
        self.lbl_server_status = ctk.CTkLabel(status_text_frame, text="Servidor: Offline", font=ctk.CTkFont(size=11, slant="italic"), text_color="#EF4444")
        self.lbl_server_status.pack(anchor="w")
        
        # Controles
        self.btn_toggle_monitor = ctk.CTkButton(self.sidebar, text="Iniciar Monitor", fg_color="#10B981", hover_color="#059669", command=self.toggle_monitoring)
        self.btn_toggle_monitor.grid(row=2, column=0, padx=15, pady=10, sticky="ew")
        
        self.btn_force_switch = ctk.CTkButton(self.sidebar, text="Alternar Link Manual", fg_color="#3B82F6", hover_color="#2563EB", command=self.switch_link_manual)
        self.btn_force_switch.grid(row=3, column=0, padx=15, pady=10, sticky="ew")
        
        self.btn_dhcp = ctk.CTkButton(self.sidebar, text="Redefinir para DHCP", fg_color="#6B7280", hover_color="#4B5563", command=self.reset_dhcp_manual)
        self.btn_dhcp.grid(row=4, column=0, padx=15, pady=10, sticky="ew")
        
        self.btn_disable_proxy = ctk.CTkButton(self.sidebar, text="Desativar Proxy", fg_color="#F59E0B", hover_color="#D97706", command=self.disable_proxy_manual)
        self.btn_disable_proxy.grid(row=5, column=0, padx=15, pady=10, sticky="ew")
        
        # Separador / Rodapé
        self.btn_exit = ctk.CTkButton(self.sidebar, text="Fechar Programa", fg_color="#EF4444", hover_color="#DC2626", command=self.exit_application)
        self.btn_exit.grid(row=9, column=0, padx=15, pady=20, sticky="ew")

        # --- ÁREA DE CONTEÚDO ---
        self.content_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        self.content_frame.grid(row=0, column=1, sticky="nsew", padx=15, pady=15)
        self.content_frame.grid_rowconfigure(0, weight=1)
        self.content_frame.grid_columnconfigure(0, weight=1)
        
        # Abas
        self.tabview = ctk.CTkTabview(self.content_frame)
        self.tabview.grid(row=0, column=0, sticky="nsew")
        
        self.tab_links = self.tabview.add("Configuração dos Links")
        self.tab_routes = self.tabview.add("Rotas Estáticas")
        self.tab_settings = self.tabview.add("Parâmetros do Monitor")
        
        self.tabview.set("Configuração dos Links")
        
        # --- ABA 1: CONFIGURAÇÃO DOS LINKS (LADO A LADO) ---
        self.tab_links.grid_columnconfigure(0, weight=1)
        self.tab_links.grid_columnconfigure(1, weight=1)
        self.tab_links.grid_rowconfigure(0, weight=1)
        
        # Link 1 (Primary) Card
        self.card_link1 = ctk.CTkFrame(self.tab_links)
        self.card_link1.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        self.build_link_form(self.card_link1, "Link 1 (Principal)", "link1")
        
        # Link 2 (Backup) Card
        self.card_link2 = ctk.CTkFrame(self.tab_links)
        self.card_link2.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")
        self.build_link_form(self.card_link2, "Link 2 (Backup)", "link2")
        
        # --- ABA 2: ROTAS ESTÁTICAS ---
        self.tab_routes.grid_columnconfigure(0, weight=1)
        self.tab_routes.grid_rowconfigure(1, weight=1)
        
        lbl_routes_desc = ctk.CTkLabel(self.tab_routes, text="Sub-redes para Gerenciamento de Rotas (Uma por linha. Ex: 172.20.153.0/24)", font=ctk.CTkFont(size=12, slant="italic"))
        lbl_routes_desc.grid(row=0, column=0, padx=10, pady=(10, 5), sticky="w")
        
        self.txt_routes = ctk.CTkTextbox(self.tab_routes, font=ctk.CTkFont(family="Consolas", size=12))
        self.txt_routes.grid(row=1, column=0, padx=10, pady=5, sticky="nsew")
        
        # --- ABA 3: PARÂMETROS DO MONITOR ---
        self.tab_settings.grid_columnconfigure(1, weight=1)
        
        # ID da Loja
        ctk.CTkLabel(self.tab_settings, text="ID da Loja:").grid(row=0, column=0, padx=20, pady=10, sticky="w")
        self.ent_store_id = ctk.CTkEntry(self.tab_settings, width=150)
        self.ent_store_id.grid(row=0, column=1, padx=20, pady=10, sticky="w")
        
        # URL do Servidor
        ctk.CTkLabel(self.tab_settings, text="URL do Servidor Central:").grid(row=1, column=0, padx=20, pady=10, sticky="w")
        self.ent_server_url = ctk.CTkEntry(self.tab_settings, width=300)
        self.ent_server_url.grid(row=1, column=1, padx=20, pady=10, sticky="w")
        
        # Interface Dropdown
        ctk.CTkLabel(self.tab_settings, text="Interface de Rede:").grid(row=2, column=0, padx=20, pady=10, sticky="w")
        iface_options = [AUTO_INTERFACE_LABEL] + [i for i in get_network_interfaces() if i != AUTO_INTERFACE_LABEL]
        self.opt_interfaces = ctk.CTkOptionMenu(self.tab_settings, values=iface_options, width=220)
        self.opt_interfaces.grid(row=2, column=1, padx=20, pady=10, sticky="w")
        
        # Modo
        ctk.CTkLabel(self.tab_settings, text="Modo de Operação:").grid(row=3, column=0, padx=20, pady=10, sticky="w")
        self.opt_mode = ctk.CTkOptionMenu(self.tab_settings, values=["Rotas Corporativas", "Apenas Gateway", "IP Completo"], command=self.on_mode_changed)
        self.opt_mode.grid(row=3, column=1, padx=20, pady=10, sticky="w")
        
        # Ping Interval
        ctk.CTkLabel(self.tab_settings, text="Intervalo de Teste (segundos):").grid(row=4, column=0, padx=20, pady=10, sticky="w")
        self.ent_ping_interval = ctk.CTkEntry(self.tab_settings, width=80)
        self.ent_ping_interval.grid(row=4, column=1, padx=20, pady=10, sticky="w")
        
        # Ping Timeout
        ctk.CTkLabel(self.tab_settings, text="Tempo Limite Ping (ms):").grid(row=5, column=0, padx=20, pady=10, sticky="w")
        self.ent_ping_timeout = ctk.CTkEntry(self.tab_settings, width=80)
        self.ent_ping_timeout.grid(row=5, column=1, padx=20, pady=10, sticky="w")
        
        # Fail Limit
        ctk.CTkLabel(self.tab_settings, text="Limite de Falhas antes da Troca:").grid(row=6, column=0, padx=20, pady=10, sticky="w")
        self.ent_fail_limit = ctk.CTkEntry(self.tab_settings, width=80)
        self.ent_fail_limit.grid(row=6, column=1, padx=20, pady=10, sticky="w")
        
        # Auto Fallback Checkbox
        self.chk_fallback = ctk.CTkCheckBox(self.tab_settings, text="Retorno Automático para Link 1")
        self.chk_fallback.grid(row=7, column=0, columnspan=2, padx=20, pady=10, sticky="w")
        
        # Botões de Ação
        btn_action_frame = ctk.CTkFrame(self.tab_settings, fg_color="transparent")
        btn_action_frame.grid(row=8, column=0, columnspan=2, padx=20, pady=20, sticky="w")
        
        self.btn_save_config = ctk.CTkButton(btn_action_frame, text="Salvar Configurações", fg_color="#10B981", hover_color="#059669", command=self.save_gui_config)
        self.btn_save_config.pack(side="left", padx=(0, 10))
        
        self.btn_install_task = ctk.CTkButton(btn_action_frame, text="Ativar Inicialização como Admin (Sem Telas)", fg_color="#6366F1", hover_color="#4F46E5", command=self.gui_install_startup_task)
        self.btn_install_task.pack(side="left", padx=10)
        
        # --- CONSOLE DE LOGS INTEGRADO (RODAPÉ DO CONTEÚDO) ---
        self.console_frame = ctk.CTkFrame(self.content_frame)
        self.console_frame.grid(row=1, column=0, sticky="ew", pady=(15, 0))
        self.console_frame.grid_columnconfigure(0, weight=1)
        self.console_frame.grid_rowconfigure(1, weight=1)
        
        lbl_console_title = ctk.CTkLabel(self.console_frame, text="Log do Sistema em Tempo Real", font=ctk.CTkFont(size=12, weight="bold"))
        lbl_console_title.grid(row=0, column=0, padx=10, pady=(5, 2), sticky="w")
        
        self.txt_log = ctk.CTkTextbox(self.console_frame, height=160, font=ctk.CTkFont(family="Consolas", size=11))
        self.txt_log.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="ew")
        self.txt_log.configure(state="disabled")
        
        # Cores para o Console
        self.txt_log.tag_config("info", foreground="#E2E8F0")
        self.txt_log.tag_config("success", foreground="#34D399")
        self.txt_log.tag_config("warning", foreground="#FBBF24")
        self.txt_log.tag_config("error", foreground="#F87171")

    def build_link_form(self, container, title, link_key):
        container.grid_columnconfigure(1, weight=1)
        
        lbl_title = ctk.CTkLabel(container, text=title, font=ctk.CTkFont(size=14, weight="bold"))
        lbl_title.grid(row=0, column=0, columnspan=2, padx=10, pady=10, sticky="w")
        
        # Nome do Link
        ctk.CTkLabel(container, text="Nome:").grid(row=1, column=0, padx=10, pady=5, sticky="w")
        ent_name = ctk.CTkEntry(container)
        ent_name.grid(row=1, column=1, padx=10, pady=5, sticky="ew")
        setattr(self, f"ent_{link_key}_name", ent_name)
        
        # Gateway
        ctk.CTkLabel(container, text="Gateway:").grid(row=2, column=0, padx=10, pady=5, sticky="w")
        ent_gw = ctk.CTkEntry(container)
        ent_gw.grid(row=2, column=1, padx=10, pady=5, sticky="ew")
        setattr(self, f"ent_{link_key}_gw", ent_gw)
        
        # IP Físico (Full IP mode)
        lbl_ip = ctk.CTkLabel(container, text="IP Físico:")
        lbl_ip.grid(row=3, column=0, padx=10, pady=5, sticky="w")
        ent_ip = ctk.CTkEntry(container)
        ent_ip.grid(row=3, column=1, padx=10, pady=5, sticky="ew")
        setattr(self, f"ent_{link_key}_ip", ent_ip)
        setattr(self, f"lbl_{link_key}_ip", lbl_ip)
        
        # Máscara (Full IP mode)
        lbl_mask = ctk.CTkLabel(container, text="Máscara:")
        lbl_mask.grid(row=4, column=0, padx=10, pady=5, sticky="w")
        ent_mask = ctk.CTkEntry(container)
        ent_mask.grid(row=4, column=1, padx=10, pady=5, sticky="ew")
        setattr(self, f"ent_{link_key}_mask", ent_mask)
        setattr(self, f"lbl_{link_key}_mask", lbl_mask)
        
        # DNS 1
        lbl_dns1 = ctk.CTkLabel(container, text="DNS 1:")
        lbl_dns1.grid(row=5, column=0, padx=10, pady=5, sticky="w")
        ent_dns1 = ctk.CTkEntry(container)
        ent_dns1.grid(row=5, column=1, padx=10, pady=5, sticky="ew")
        setattr(self, f"ent_{link_key}_dns1", ent_dns1)
        setattr(self, f"lbl_{link_key}_dns1", lbl_dns1)
        
        # DNS 2
        lbl_dns2 = ctk.CTkLabel(container, text="DNS 2:")
        lbl_dns2.grid(row=6, column=0, padx=10, pady=5, sticky="w")
        ent_dns2 = ctk.CTkEntry(container)
        ent_dns2.grid(row=6, column=1, padx=10, pady=5, sticky="ew")
        setattr(self, f"ent_{link_key}_dns2", ent_dns2)
        setattr(self, f"lbl_{link_key}_dns2", lbl_dns2)
        
        # IPs de Teste (Multi-linha)
        ctk.CTkLabel(container, text="IPs de Teste:").grid(row=7, column=0, padx=10, pady=(5, 0), sticky="nw")
        txt_test_ips = ctk.CTkTextbox(container, height=60, font=ctk.CTkFont(family="Consolas", size=11))
        txt_test_ips.grid(row=7, column=1, padx=10, pady=5, sticky="ew")
        setattr(self, f"txt_{link_key}_test_ips", txt_test_ips)

    # --- ATUALIZA VISIBILIDADE DE CAMPOS BASEADO NO MODO ---
    def on_mode_changed(self, mode):
        # Traduz exibição para chaves
        mode_key = self.get_mode_key(mode)
        
        # Mostra/Oculta campos de IP e DNS conforme modo
        if mode_key == "full_ip":
            state = "normal"
        else:
            state = "disabled"
            
        for link in ("link1", "link2"):
            getattr(self, f"ent_{link}_ip").configure(state=state)
            getattr(self, f"ent_{link}_mask").configure(state=state)
            getattr(self, f"ent_{link}_dns1").configure(state=state)
            getattr(self, f"ent_{link}_dns2").configure(state=state)
            
        if mode_key == "routes":
            self.tabview.set("Rotas Estáticas")
        else:
            self.tabview.set("Configuração dos Links")

    def get_mode_key(self, mode_display_name):
        mapping = {
            "Apenas Gateway": "gateway",
            "IP Completo": "full_ip",
            "Rotas Corporativas": "routes"
        }
        return mapping.get(mode_display_name, "routes")

    def get_mode_display_name(self, mode_key):
        mapping = {
            "gateway": "Apenas Gateway",
            "full_ip": "IP Completo",
            "routes": "Rotas Corporativas"
        }
        return mapping.get(mode_key, "Rotas Corporativas")

    # --- CONTROLADORES DE DADOS DA GUI ---
    def load_settings_into_gui(self):
        c = self.config
        
        # Parâmetros gerais (Limpa antes de inserir para evitar duplicações ao recarregar)
        self.ent_store_id.delete(0, "end")
        self.ent_store_id.insert(0, c.get("store_id", "LOJA_01"))
        
        self.ent_server_url.delete(0, "end")
        self.ent_server_url.insert(0, c.get("server_url", "http://localhost:5555"))
        
        cur_iface = c.get("interface", AUTO_INTERFACE_LABEL)
        iface_options = [AUTO_INTERFACE_LABEL] + [i for i in get_network_interfaces() if i != AUTO_INTERFACE_LABEL]
        self.opt_interfaces.configure(values=iface_options)
        if cur_iface in iface_options:
            self.opt_interfaces.set(cur_iface)
        else:
            self.opt_interfaces.set(AUTO_INTERFACE_LABEL)
        self.opt_mode.set(self.get_mode_display_name(c.get("mode", "routes")))
        
        self.ent_ping_interval.delete(0, "end")
        self.ent_ping_interval.insert(0, str(c.get("ping_interval", 20)))
        
        self.ent_ping_timeout.delete(0, "end")
        self.ent_ping_timeout.insert(0, str(c.get("ping_timeout", 1000)))
        
        self.ent_fail_limit.delete(0, "end")
        self.ent_fail_limit.insert(0, str(c.get("fail_limit", 3)))
        
        if c.get("auto_fallback", True):
            self.chk_fallback.select()
        else:
            self.chk_fallback.deselect()
            
        self.txt_routes.delete("1.0", "end")
        self.txt_routes.insert("0.0", c.get("static_subnets", ""))
        
        # Links
        for link in ("link1", "link2"):
            lc = c.get(link, {})
            getattr(self, f"ent_{link}_name").delete(0, "end")
            getattr(self, f"ent_{link}_name").insert(0, lc.get("name", ""))
            
            getattr(self, f"ent_{link}_gw").delete(0, "end")
            getattr(self, f"ent_{link}_gw").insert(0, lc.get("gateway", ""))
            
            getattr(self, f"ent_{link}_ip").delete(0, "end")
            getattr(self, f"ent_{link}_ip").insert(0, lc.get("ip", ""))
            
            getattr(self, f"ent_{link}_mask").delete(0, "end")
            getattr(self, f"ent_{link}_mask").insert(0, lc.get("mask", ""))
            
            getattr(self, f"ent_{link}_dns1").delete(0, "end")
            getattr(self, f"ent_{link}_dns1").insert(0, lc.get("dns1", ""))
            
            getattr(self, f"ent_{link}_dns2").delete(0, "end")
            getattr(self, f"ent_{link}_dns2").insert(0, lc.get("dns2", ""))
            
            getattr(self, f"txt_{link}_test_ips").delete("1.0", "end")
            getattr(self, f"txt_{link}_test_ips").insert("0.0", lc.get("test_ips", ""))
            
        # Atualiza o estado visual das caixas baseadas no modo
        self.on_mode_changed(self.opt_mode.get())

    def save_gui_config(self):
        try:
            # Validação rápida de números
            interval = int(self.ent_ping_interval.get())
            timeout = int(self.ent_ping_timeout.get())
            fails = int(self.ent_fail_limit.get())
        except ValueError:
            logger.error("Valores de intervalo, timeout ou falhas inválidos. Use apenas números inteiros.")
            return False
            
        c = self.config
        c["store_id"] = self.ent_store_id.get().strip()
        c["server_url"] = self.ent_server_url.get().strip()
        c["interface"] = self.opt_interfaces.get()
        c["mode"] = self.get_mode_key(self.opt_mode.get())
        c["ping_interval"] = interval
        c["ping_timeout"] = timeout
        c["fail_limit"] = fails
        c["auto_fallback"] = bool(self.chk_fallback.get())
        c["static_subnets"] = self.txt_routes.get("1.0", "end-1c").strip()
        
        for link in ("link1", "link2"):
            c[link] = {
                "name": getattr(self, f"ent_{link}_name").get().strip(),
                "gateway": getattr(self, f"ent_{link}_gw").get().strip(),
                "ip": getattr(self, f"ent_{link}_ip").get().strip(),
                "mask": getattr(self, f"ent_{link}_mask").get().strip(),
                "dns1": getattr(self, f"ent_{link}_dns1").get().strip(),
                "dns2": getattr(self, f"ent_{link}_dns2").get().strip(),
                "test_ips": getattr(self, f"txt_{link}_test_ips").get("1.0", "end-1c").strip()
            }
            
        if save_config(c):
            logger.info("Configurações atualizadas no programa.")
            return True
        return False

    def gui_install_startup_task(self):
        from tkinter import messagebox
        if not is_admin():
            messagebox.showerror("Acesso Negado", "Execute o programa como Administrador para configurar a inicialização automática no Windows.")
            return
            
        dialog = ctk.CTkInputDialog(
            text="Deseja fixar Usuário e Senha de Administrador?\n\n- Deixe em BRANCO para usar o Administrador local padrão (Recomendado)\n- Ou digite o nome do Administrador (ex: Administrador):",
            title="Inicialização como Administrador"
        )
        user_val = dialog.get_input()
        if user_val is None:
            return  # Cancelado
            
        user_val = user_val.strip()
        pwd_val = None
        if user_val:
            dialog_pwd = ctk.CTkInputDialog(
                text=f"Digite a senha do usuário '{user_val}':",
                title="Senha do Administrador"
            )
            pwd_val = dialog_pwd.get_input()
            if pwd_val is None:
                return  # Cancelado
                
        ok, msg = install_startup_task(admin_user=user_val if user_val else None, admin_pass=pwd_val if user_val else None)
        if ok:
            messagebox.showinfo("Inicialização Automática", msg)
        else:
            messagebox.showerror("Erro ao Configurar", msg)

    # --- BANDEJA DO SISTEMA (TRAY) ---
    def setup_system_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Abrir Configurações", self.tray_restore_window, default=True),
            pystray.MenuItem("Alternar Link Agora", self.tray_switch_link),
            pystray.MenuItem("Sair", self.tray_exit)
        )
        # Ícone inicial (cinza/offline até iniciar)
        self.icon = pystray.Icon(
            "RedundanciaInternet",
            create_status_icon("yellow"),
            title="Redundância de Internet e Rotas (Parado)",
            menu=menu
        )
        # Executa em thread separada
        threading.Thread(target=self.icon.run, daemon=True).start()

    def update_tray_status(self, online, latency=None):
        if not self.icon:
            return
        
        c = self.config
        active_link_config = c.get(self.active_link, {})
        link_name = active_link_config.get("name", self.active_link.upper())
        
        if not self.is_monitoring:
            self.icon.icon = create_status_icon("yellow")
            self.icon.title = "Redundância de Internet e Rotas (Parado)"
        elif online:
            color = "green" if self.active_link == "link1" else "blue"
            self.icon.icon = create_status_icon(color)
            latency_str = f" ({latency:.1f}ms)" if latency is not None else ""
            self.icon.title = f"Internet: ONLINE | Ativo: {link_name}{latency_str}"
        else:
            self.icon.icon = create_status_icon("red")
            self.icon.title = f"Internet: FORA DO AR! | Ativo: {link_name}"

    def tray_restore_window(self, icon, item):
        self.root.after(0, self.root.deiconify)
        self.root.after(0, self.root.focus_force)

    def tray_switch_link(self, icon, item):
        self.root.after(0, self.switch_link_manual)

    def tray_exit(self, icon, item):
        self.root.after(0, self.exit_application)

    # --- FECHAMENTO E MINIMIZAÇÃO ---
    def on_window_close(self):
        # Minimiza para a bandeja em vez de fechar
        self.root.withdraw()
        try:
            self.icon.notify("O programa continua rodando nos ícones ocultos.", "Redundância de Internet")
        except Exception:
            pass

    def exit_application(self):
        logger.info("Encerrando o aplicativo...")
        self.is_monitoring = False
        self.is_syncing = False
        if self.icon:
            self.icon.stop()
        self.root.destroy()
        sys.exit(0)

    # --- FUNÇÕES DE COMUNICAÇÃO CENTRAL E PROXY ---
    def disable_proxy_manual(self):
        if disable_windows_proxy():
            logger.info("Proxy desativado com sucesso através do painel lateral.")
        else:
            logger.error("Falha ao desativar o proxy através do painel lateral.")

    def update_server_connection_status(self, online):
        def update():
            if online:
                self.lbl_server_status.configure(text="Servidor: Online", text_color="#10B981")
            else:
                self.lbl_server_status.configure(text="Servidor: Offline", text_color="#EF4444")
        self.root.after(0, update)

    def setup_websocket(self):
        self.ws_queue = queue.Queue()
        self.is_syncing = True
        self.sync_thread = threading.Thread(target=self.run_websocket_client, daemon=True)
        self.sync_thread.start()

    def run_websocket_client(self):
        import asyncio
        import websockets

        async def handler():
            last_err_logged = False
            while self.is_syncing:
                c = self.config
                server_url = c.get("server_url", "").strip()
                if not server_url:
                    await asyncio.sleep(2)
                    continue

                ws_url = server_url.replace("http://", "ws://").replace("https://", "wss://")
                uri = f"{ws_url}/ws"

                try:
                    async with websockets.connect(uri, ping_interval=10, ping_timeout=10) as ws:
                        self.update_server_connection_status(True)
                        last_err_logged = False
                        logger.info("Conectado ao Servidor Central via WebSocket nativo!")

                        # Registra no servidor
                        register_msg = {
                            "type": "register",
                            "store_id": c.get("store_id", "LOJA_01")
                        }
                        await ws.send(json.dumps(register_msg))

                        # Loop de envio
                        async def send_loop():
                            try:
                                while self.is_syncing:
                                    try:
                                        msg = self.ws_queue.get_nowait()
                                        await ws.send(json.dumps(msg))
                                    except queue.Empty:
                                        await asyncio.sleep(0.2)
                            except Exception:
                                pass

                        # Loop de recepção
                        async def recv_loop():
                            try:
                                async for message in ws:
                                    data = json.loads(message)
                                    msg_type = data.get("type")
                                    if msg_type == "command":
                                        action = data.get("action")
                                        if action == "FORCE_SWITCH_PRIMARY" and self.active_link == "link2":
                                            logger.info("Comando WebSocket: Forçar link principal (Link 1).")
                                            self.root.after(0, self.switch_link_to, "link1")
                                        elif action == "FORCE_SWITCH_BACKUP" and self.active_link == "link1":
                                            logger.info("Comando WebSocket: Forçar link de backup (Link 2).")
                                            self.root.after(0, self.switch_link_to, "link2")
                                    elif msg_type == "config_update":
                                        remote_config = data.get("config")
                                        if remote_config:
                                            self.check_and_apply_remote_config(remote_config)
                            except Exception:
                                pass

                        await asyncio.gather(send_loop(), recv_loop())

                except Exception:
                    self.update_server_connection_status(False)
                    if not last_err_logged:
                        logger.warning("Falha ao conectar via WebSocket. Operando no modo autônomo local.")
                        last_err_logged = True
                    await asyncio.sleep(10)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(handler())

    def send_status_update_ws(self):
        try:
            c = self.config
            payload = {
                "type": "status_update",
                "store_id": c.get("store_id", "LOJA_01"),
                "active_link": self.active_link,
                "online": getattr(self, "last_online_status", False),
                "latency": getattr(self, "last_latency_status", 0.0) or 0.0,
                "mode": c.get("mode"),
                "interface": resolve_interface(c.get("interface"))
            }
            self.ws_queue.put(payload)
        except Exception as e:
            logger.warning(f"Erro ao empilhar status de atualização WebSocket: {e}")

    def check_and_apply_remote_config(self, rc):
        c = self.config
        changed = False
        
        # Parâmetros gerais
        for k in ["mode", "interface", "ping_interval", "ping_timeout", "fail_limit", "auto_fallback", "fallback_interval"]:
            if k in rc and c.get(k) != rc[k]:
                c[k] = rc[k]
                changed = True
                
        # Sub-redes estáticas
        if "static_subnets" in rc and c.get("static_subnets") != rc["static_subnets"]:
            c["static_subnets"] = rc["static_subnets"]
            changed = True
            
        # Links
        for link in ["link1", "link2"]:
            if link in rc:
                rc_link = rc[link]
                local_link = c.get(link, {})
                for k in ["name", "gateway", "ip", "mask", "dns1", "dns2", "test_ips"]:
                    if k in rc_link and local_link.get(k) != rc_link[k]:
                        local_link[k] = rc_link[k]
                        changed = True
                c[link] = local_link
                
        if changed:
            logger.info("⚠️ Nova configuração recebida do Servidor Central! Salvando localmente...")
            save_config(c)
            # Recarrega configurações na interface gráfica
            self.root.after(0, self.reload_gui_fields)

    def reload_gui_fields(self):
        self.load_settings_into_gui()
        logger.info("Campos da interface gráfica atualizados.")

    def switch_link_to(self, link_key):
        if not is_admin():
            logger.error("Ação negada: Necessário executar como Administrador.")
            return
            
        logger.info(f"Alternância de link direcionada para: {link_key.upper()}")
        self.active_link = link_key
        self.apply_link_configuration(self.active_link)
        self.update_status(True, None)
        self.send_status_update_ws()

    # --- LÓGICA DE MONITORAMENTO E AÇÕES ---
    def update_status(self, online, latency=None):
        def update_gui():
            c = self.config
            active_link_config = c.get(self.active_link, {})
            link_name = active_link_config.get("name", self.active_link.upper())
            
            if online:
                self.lbl_status_indicator.configure(text_color="#10B981") # Verde
                self.lbl_status_state.configure(text="Online")
                self.lbl_status_link.configure(text=f"Link: {link_name}")
                if latency is not None:
                    self.lbl_status_latency.configure(text=f"Ping: {latency:.1f} ms")
                else:
                    self.lbl_status_latency.configure(text="Ping: -- ms")
            else:
                self.lbl_status_indicator.configure(text_color="#EF4444") # Vermelho
                self.lbl_status_state.configure(text="Sem Conexão")
                self.lbl_status_link.configure(text=f"Link: {link_name}")
                self.lbl_status_latency.configure(text="Ping: Falhou")
                
            self.update_tray_status(online, latency)
            
        self.root.after(0, update_gui)

    def toggle_monitoring(self):
        if self.is_monitoring:
            # Parar
            self.is_monitoring = False
            self.btn_toggle_monitor.configure(text="Iniciar Monitor", fg_color="#10B981", hover_color="#059669")
            self.lbl_status_indicator.configure(text_color="#F59E0B") # Amarelo/Parado
            self.lbl_status_state.configure(text="Parado")
            self.update_tray_status(False)
            logger.info("Monitoramento de redundância PARADO pelo usuário.")
        else:
            # Iniciar
            if not self.save_gui_config():
                return
                
            self.is_monitoring = True
            self.btn_toggle_monitor.configure(text="Parar Monitor", fg_color="#EF4444", hover_color="#DC2626")
            logger.info("Monitoramento de redundância INICIADO.")
            
            # Aplica o link atual padrão ao iniciar
            self.apply_link_configuration(self.active_link)
            
            # Dispara thread de monitoramento
            self.monitor_thread = threading.Thread(target=self.monitoring_loop, daemon=True)
            self.monitor_thread.start()

    def reset_dhcp_manual(self):
        if not is_admin():
            logger.error("Ação negada: Necessário executar como Administrador.")
            return
            
        interface = resolve_interface(self.opt_interfaces.get())
        controller = NetworkController(interface)
        
        # Pergunta de confirmação rápida
        confirm = ctk.CTkInputDialog(text=f"Digite 'CONFIRMAR' para redefinir a interface '{controller.interface}' para DHCP:", title="Confirmar Redefinição")
        val = confirm.get_input()
        if val == "CONFIRMAR":
            # Para monitoramento se estiver ativo
            if self.is_monitoring:
                self.toggle_monitoring()
            controller.reset_to_dhcp()
        else:
            logger.info("Redefinição de DHCP cancelada.")

    def switch_link_manual(self):
        if not is_admin():
            logger.error("Ação negada: Necessário executar como Administrador.")
            return
            
        next_link = "link2" if self.active_link == "link1" else "link1"
        logger.info(f"Alternância manual de link solicitada para: {next_link.upper()}")
        self.active_link = next_link
        self.apply_link_configuration(self.active_link)
        self.update_status(True, None) # Reseta status visual para carregar novo ping

    def apply_link_configuration(self, link_key):
        if not is_admin():
            logger.error("Não foi possível aplicar configurações de rede: Aplicativo NÃO está rodando como Administrador!")
            return False
            
        c = self.config
        link_conf = c.get(link_key, {})
        interface = resolve_interface(c.get("interface"))
        mode = c.get("mode", "routes")
        
        controller = NetworkController(interface)
        
        logger.info(f"==> APLICANDO CONFIGURAÇÃO: Link {link_conf.get('name', link_key.upper())} ({link_key.upper()}) na interface '{controller.interface}' <==")
        
        success = False
        if mode == "gateway":
            success = controller.set_gateway_only(link_conf.get("gateway"))
        elif mode == "full_ip":
            success = controller.set_full_ip(
                link_conf.get("ip"),
                link_conf.get("mask"),
                link_conf.get("gateway"),
                link_conf.get("dns1"),
                link_conf.get("dns2")
            )
        elif mode == "routes":
            # No modo rotas, geralmente assume-se que o IP principal e gateways estão fixos
            # E adicionamos/removemos as sub-redes corporativas específicas.
            # O gateway e métrica variam por link.
            subnets = c.get("static_subnets", "").split("\n")
            metric = 1 if link_key == "link1" else 2 # Métrica menor para o principal (prioritário)
            success = controller.manage_static_routes(subnets, link_conf.get("gateway"), metric)
            
        if success:
            logger.info(f"Configurações do link {link_key.upper()} aplicadas com sucesso.")
        else:
            logger.error(f"Falha ao aplicar configurações do link {link_key.upper()}.")
            
        return success

    # --- LOOP DE MONITORAMENTO EM THREAD ---
    def ping_ip(self, ip, timeout_ms=1000):
        # Comando ping silencioso no Windows (-n 1 = 1 pacote, -w timeout = tempo limite em ms)
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), ip]
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # Oculta janela cmd de ping
            
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                startupinfo=startupinfo,
                creationflags=CREATE_NO_WINDOW,
                timeout=(timeout_ms / 1000.0) + 1.0
            )
            if result.returncode == 0:
                output = result.stdout.decode('cp850', errors='ignore')
                # Tenta capturar o tempo de latência do console
                match = re.search(r'(?:time|tempo)[=<]([0-9]+)\s*ms', output, re.IGNORECASE)
                if match:
                    return float(match.group(1))
                return 10.0  # Fallback de latência de 10ms se der sucesso mas não achar o número
            return None
        except Exception:
            return None

    def monitoring_loop(self):
        consecutive_failures = 0
        fallback_check_counter = 0
        
        while self.is_monitoring:
            c = self.config
            active_link_config = c.get(self.active_link, {})
            test_ips_str = active_link_config.get("test_ips", "")
            test_ips = [ip.strip() for ip in test_ips_str.split("\n") if ip.strip()]
            
            if not test_ips:
                # IPs padrão se não houver cadastrado
                test_ips = ["8.8.8.8", "1.1.1.1"]
                
            # Verifica o link atual (basta que 1 IP de teste responda para considerarmos ONLINE)
            link_ok = False
            latencies = []
            
            for ip in test_ips:
                latency = self.ping_ip(ip, timeout_ms=c.get("ping_timeout", 1000))
                if latency is not None:
                    link_ok = True
                    latencies.append(latency)
                    # Achou um respondendo, pode parar o teste deste ciclo para poupar rede
                    break
                    
            if link_ok:
                consecutive_failures = 0
                avg_latency = latencies[0] if latencies else 0.0
                self.last_online_status = True
                self.last_latency_status = avg_latency
                self.update_status(True, avg_latency)
                self.send_status_update_ws()
                logger.info(f"Status do Link Ativo: OK (IP: {test_ips[0]} - Latência: {avg_latency:.1f}ms)")
            else:
                consecutive_failures += 1
                self.last_online_status = False
                self.last_latency_status = 0.0
                self.update_status(False, None)
                self.send_status_update_ws()
                logger.warning(f"Conexão falhou no link ativo ({active_link_config.get('name', self.active_link.upper())}). Falhas consecutivas: {consecutive_failures}/{c.get('fail_limit', 3)}")
                
                if consecutive_failures >= c.get("fail_limit", 3):
                    logger.warning("Limite de falhas excedido! Alternando redundância de link...")
                    next_link = "link2" if self.active_link == "link1" else "link1"
                    self.active_link = next_link
                    self.apply_link_configuration(next_link)
                    consecutive_failures = 0
                    time.sleep(10) # Cooldown após alternar
                    continue
            
            # --- LÓGICA DE RETORNO AUTOMÁTICO (FALLBACK) ---
            # Se estamos rodando no Link 2 (Backup) e o retorno automático está ativo
            if self.active_link == "link2" and c.get("auto_fallback", True):
                fallback_check_counter += c.get("ping_interval", 20)
                if fallback_check_counter >= c.get("fallback_interval", 60):
                    fallback_check_counter = 0
                    
                    # Teste silencioso do Link 1
                    link1_ok = self.check_link1_health_silently()
                    if link1_ok:
                        logger.info("Link 1 (Principal) foi reestabelecido! Retornando conexão para o Link Principal...")
                        self.active_link = "link1"
                        self.apply_link_configuration("link1")
                        consecutive_failures = 0
                        time.sleep(10) # Cooldown
                        continue
                        
            time.sleep(c.get("ping_interval", 20))

    def check_link1_health_silently(self):
        c = self.config
        link1 = c.get("link1", {})
        interface = resolve_interface(c.get("interface"))
        gateway1 = link1.get("gateway")
        
        if not gateway1:
            return False
            
        test_ips_str = link1.get("test_ips", "")
        test_ips = [ip.strip() for ip in test_ips_str.split("\n") if ip.strip()]
        if not test_ips:
            test_ips = ["1.1.1.1"]
            
        # Para testar o Link 1 sem derrubar o Link 2 (que está servindo o sistema),
        # nós adicionamos uma rota estática temporária para um IP específico (ex: 8.8.4.4)
        # apontando para o Gateway do Link 1.
        test_ip = "8.8.4.4"
        
        added_ip = False
        added_route = False
        
        logger.info(f"Verificando saúde do Link 1 em segundo plano (Gateway: {gateway1})...")
        
        try:
            # Se for modo IP Completo e sub-redes diferentes, precisamos adicionar o IP temporário do Link 1
            if c.get("mode") == "full_ip" and link1.get("ip") and link1.get("mask"):
                prefix = sum(bin(int(x)).count('1') for x in link1["mask"].split('.'))
                cmd_ip = f"New-NetIPAddress -InterfaceAlias '{interface}' -IPAddress '{link1['ip']}' -PrefixLength {prefix} -Confirm:$false -ErrorAction SilentlyContinue"
                run_powershell(cmd_ip)
                added_ip = True
                
            # Adiciona a rota estática temporária do IP de testes para o gateway do Link 1
            cmd_route = f"New-NetRoute -DestinationPrefix '{test_ip}/32' -InterfaceAlias '{interface}' -NextHop '{gateway1}' -RouteMetric 1 -Confirm:$false -ErrorAction SilentlyContinue"
            run_powershell(cmd_route)
            added_route = True
            
            # Testa o ping no IP roteado e no gateway direto
            lat_gw = self.ping_ip(gateway1, timeout_ms=1000)
            lat_ip = self.ping_ip(test_ip, timeout_ms=1000)
            
            # O link é considerado recuperado se o gateway responder ou o IP público de teste responder
            link1_up = (lat_gw is not None) or (lat_ip is not None)
            logger.info(f"Resultado do teste Link 1 -> Gateway={lat_gw}ms, TestIP={lat_ip}ms. Saudável: {link1_up}")
            return link1_up
            
        except Exception as e:
            logger.error(f"Erro no teste em segundo plano do Link 1: {e}")
            return False
        finally:
            # Sempre limpa as configurações temporárias
            if added_route:
                cmd_del_route = f"Remove-NetRoute -DestinationPrefix '{test_ip}/32' -InterfaceAlias '{interface}' -Confirm:$false -ErrorAction SilentlyContinue"
                run_powershell(cmd_del_route)
            if added_ip:
                cmd_del_ip = f"Remove-NetIPAddress -InterfaceAlias '{interface}' -IPAddress '{link1['ip']}' -Confirm:$false -ErrorAction SilentlyContinue"
                run_powershell(cmd_del_ip)


# --- INICIALIZADOR DO SISTEMA ---
if __name__ == "__main__":
    # Força a execução como administrador para permitir alterações de rota
    if not is_admin():
        try:
            # Re-executa solicitando elevação UAC do Windows se não estiver como admin
            params = " ".join(f'"{arg}"' for arg in sys.argv[1:])
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, params, None, 1
            )
            if ret > 32:
                sys.exit(0)
        except Exception as e:
            logger.error(f"Erro ao solicitar elevação de privilégios de administrador: {e}")
        sys.exit(0)
        
    # Inicializa o app
    app = RedundancyApp()
    app.root.mainloop()
