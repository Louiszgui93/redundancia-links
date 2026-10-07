import os
import sys
import sqlite3
import json
import time
import logging
import asyncio
from typing import List, Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse

# Configuração do Logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ServidorRedundancia")

# Configura caminhos para templates e arquivos estáticos quando congelado pelo PyInstaller
if getattr(sys, 'frozen', False):
    template_folder = os.path.join(sys._MEIPASS, 'templates')
    static_folder = os.path.join(sys._MEIPASS, 'static')
    base_dir = os.path.dirname(sys.executable)
else:
    template_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
    static_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    base_dir = os.path.dirname(os.path.abspath(__file__))

DB_FILE = os.path.join(base_dir, "database.db")

app = FastAPI(title="Redundancia Central Server")
app.mount("/static", StaticFiles(directory=static_folder), name="static")
templates = Jinja2Templates(directory=template_folder)

# --- BANCO DE DADOS E SCHEMA ---
def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stores (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            active_link TEXT DEFAULT 'link1',
            online INTEGER DEFAULT 0,
            latency REAL DEFAULT 0,
            last_heartbeat REAL DEFAULT 0,
            command TEXT DEFAULT 'NONE',
            mode TEXT DEFAULT 'routes',
            interface TEXT DEFAULT 'Ethernet',
            ping_interval INTEGER DEFAULT 20,
            ping_timeout INTEGER DEFAULT 1000,
            fail_limit INTEGER DEFAULT 3,
            auto_fallback INTEGER DEFAULT 1,
            fallback_interval INTEGER DEFAULT 60,
            static_subnets TEXT DEFAULT '',
            link1_config TEXT,
            link2_config TEXT
        )
    ''')
    conn.commit()

    # Preenche com dados padrão de demonstração se estiver vazio
    cursor.execute('SELECT COUNT(*) FROM stores')
    if cursor.fetchone()[0] == 0:
        default_link1 = {
            "name": "Embratel (Principal)",
            "gateway": "192.168.4.1",
            "ip": "192.168.4.100",
            "mask": "255.255.255.0",
            "dns1": "8.8.8.8",
            "dns2": "8.8.4.4",
            "test_ips": "192.168.4.1\n172.20.153.254\n172.21.0.244\n172.21.0.254"
        }
        default_link2 = {
            "name": "Oi (Backup)",
            "gateway": "192.168.5.76",
            "ip": "192.168.5.100",
            "mask": "255.255.255.0",
            "dns1": "1.1.1.1",
            "dns2": "1.0.0.1",
            "test_ips": "192.168.5.76\n172.20.153.254\n172.21.0.244\n172.21.0.254"
        }
        default_subnets = "172.20.153.0/24\n172.21.0.0/16\n172.19.0.0/16"
        
        cursor.execute('''
            INSERT INTO stores (
                id, name, active_link, online, latency, last_heartbeat,
                mode, interface, ping_interval, ping_timeout, fail_limit,
                auto_fallback, fallback_interval, static_subnets,
                link1_config, link2_config
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            "LOJA_01", "Loja Centro (Principal)", "link1", 1, 15.0, time.time(),
            "routes", "Automático (Placa Ativa)", 20, 1000, 3, 1, 60, default_subnets,
            json.dumps(default_link1), json.dumps(default_link2)
        ))
        
        cursor.execute('''
            INSERT INTO stores (
                id, name, active_link, online, latency, last_heartbeat,
                mode, interface, ping_interval, ping_timeout, fail_limit,
                auto_fallback, fallback_interval, static_subnets,
                link1_config, link2_config
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            "LOJA_02", "Loja Shopping (Filial)", "link1", 0, 0.0, 0,
            "gateway", "Automático (Placa Ativa)", 15, 1000, 3, 1, 60, "",
            json.dumps(default_link1), json.dumps(default_link2)
        ))
        conn.commit()
    conn.close()

# --- GERENCIAMENTO DE CONEXÕES WEBSOCKET ---
class ConnectionManager:
    def __init__(self):
        # Mapeamento de id_loja -> WebSocket da loja
        self.active_stores: Dict[str, WebSocket] = {}
        # Lista de WebSockets de navegadores abertos
        self.active_browsers: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        logger.info("Nova conexão WebSocket estabelecida no barramento.")

    def disconnect(self, websocket: WebSocket):
        # Remove das lojas se bater
        for store_id, ws in list(self.active_stores.items()):
            if ws == websocket:
                del self.active_stores[store_id]
                logger.info(f"Loja {store_id} removida do barramento WebSocket.")
        # Remove dos navegadores se bater
        if websocket in self.active_browsers:
            self.active_browsers.remove(websocket)
            logger.info("Navegador removido do barramento WebSocket.")

    async def register_store(self, store_id: str, websocket: WebSocket):
        self.active_stores[store_id] = websocket
        logger.info(f"Loja {store_id} registrada com sucesso.")

    async def register_browser(self, websocket: WebSocket):
        if websocket not in self.active_browsers:
            self.active_browsers.append(websocket)
            logger.info("Navegador registrado para escuta de atualizações.")

    async def broadcast_to_browsers(self, message: dict):
        disconnected = []
        for ws in self.active_browsers:
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect(ws)

    async def send_command_to_store(self, store_id: str, command: dict):
        if store_id in self.active_stores:
            ws = self.active_stores[store_id]
            try:
                await ws.send_json(command)
                return True
            except Exception:
                self.disconnect(ws)
        return False

manager = ConnectionManager()

# --- ENDPOINT WEBSOCKET PRINCIPAL ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Recebe mensagens em formato texto e decodifica para JSON
            data = await websocket.receive_text()
            message = json.loads(data)
            msg_type = message.get("type")

            if msg_type == "register":
                store_id = message.get("store_id")
                if store_id:
                    await manager.register_store(store_id, websocket)
                    # Envia a configuração atualizada armazenada no banco SQLite imediatamente
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute('SELECT * FROM stores WHERE id = ?', (store_id,))
                    store = cursor.fetchone()
                    if store:
                        config_data = {
                            "type": "config_update",
                            "config": {
                                "interface": store['interface'],
                                "mode": store['mode'],
                                "ping_interval": store['ping_interval'],
                                "ping_timeout": store['ping_timeout'],
                                "fail_limit": store['fail_limit'],
                                "auto_fallback": bool(store['auto_fallback']),
                                "fallback_interval": store['fallback_interval'],
                                "static_subnets": store['static_subnets'],
                                "link1": json.loads(store['link1_config']),
                                "link2": json.loads(store['link2_config'])
                            }
                        }
                        await websocket.send_json(config_data)
                    conn.close()

            elif msg_type == "register_browser":
                await manager.register_browser(websocket)

            elif msg_type == "status_update":
                store_id = message.get("store_id")
                if not store_id:
                    continue
                active_link = message.get("active_link", "link1")
                online = 1 if message.get("online", False) else 0
                latency = message.get("latency", 0.0)
                iface_reported = message.get("interface")
                mode_reported = message.get("mode")
                current_time = time.time()

                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM stores WHERE id = ?', (store_id,))
                store = cursor.fetchone()
                
                if not store:
                    default_link1 = {
                        "name": "Link Principal", "gateway": "192.168.1.1", "ip": "192.168.1.100",
                        "mask": "255.255.255.0", "dns1": "8.8.8.8", "dns2": "8.8.4.4", "test_ips": "8.8.8.8"
                    }
                    default_link2 = {
                        "name": "Link Redundante", "gateway": "192.168.2.1", "ip": "192.168.2.100",
                        "mask": "255.255.255.0", "dns1": "1.1.1.1", "dns2": "1.0.0.1", "test_ips": "1.1.1.1"
                    }
                    cursor.execute('''
                        INSERT INTO stores (id, name, active_link, online, latency, last_heartbeat, interface, mode, link1_config, link2_config)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (store_id, f"Loja Cadastrada ({store_id})", active_link, online, latency, current_time,
                          iface_reported or "Automático (Placa Ativa)", mode_reported or "routes",
                          json.dumps(default_link1), json.dumps(default_link2)))
                    conn.commit()
                else:
                    if iface_reported:
                        cursor.execute('''
                            UPDATE stores SET active_link = ?, online = ?, latency = ?, last_heartbeat = ?, interface = ? WHERE id = ?
                        ''', (active_link, online, latency, current_time, iface_reported, store_id))
                    else:
                        cursor.execute('''
                            UPDATE stores SET active_link = ?, online = ?, latency = ?, last_heartbeat = ? WHERE id = ?
                        ''', (active_link, online, latency, current_time, store_id))
                    conn.commit()

                # Busca dados atualizados para fazer broadcast aos navegadores
                cursor.execute('SELECT * FROM stores WHERE id = ?', (store_id,))
                updated_store = cursor.fetchone()
                conn.close()

                card_data = {
                    "type": "store_update",
                    "store": {
                        "id": updated_store['id'],
                        "name": updated_store['name'],
                        "active_link": updated_store['active_link'],
                        "online": updated_store['online'],
                        "latency": updated_store['latency'],
                        "last_heartbeat": updated_store['last_heartbeat'],
                        "mode": updated_store['mode'],
                        "interface": updated_store['interface'],
                        "link1": json.loads(updated_store['link1_config']),
                        "link2": json.loads(updated_store['link2_config'])
                    }
                }
                await manager.broadcast_to_browsers(card_data)

            elif msg_type == "send_command":
                store_id = message.get("store_id")
                command = message.get("command") # FORCE_SWITCH_PRIMARY ou FORCE_SWITCH_BACKUP
                if store_id and command:
                    logger.info(f"Comando '{command}' enviado do painel Web para a loja '{store_id}'")
                    # Repassa via WebSocket nativo para a conexão da loja correspondente
                    await manager.send_command_to_store(store_id, {"type": "command", "action": command})
                    
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute('UPDATE stores SET command = ? WHERE id = ?', (command, store_id))
                    conn.commit()
                    conn.close()

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"Erro na conexão WebSocket: {e}")
        manager.disconnect(websocket)

# --- ROTAS HTTP PARA CONFIGURAÇÃO ---

@app.post("/api/config")
async def update_store_config(request: Request):
    data = await request.json()
    if not data or 'store_id' not in data:
        return JSONResponse(status_code=400, content={"success": False, "error": "store_id is required"})

    store_id = data['store_id']
    name = data.get('name')
    mode = data.get('mode')
    interface = data.get('interface')
    ping_interval = data.get('ping_interval')
    ping_timeout = data.get('ping_timeout')
    fail_limit = data.get('fail_limit')
    auto_fallback = 1 if data.get('auto_fallback', True) else 0
    fallback_interval = data.get('fallback_interval')
    static_subnets = data.get('static_subnets')
    link1 = data.get('link1')
    link2 = data.get('link2')

    conn = get_db_connection()
    cursor = conn.cursor()

    updates = []
    params = []

    if name is not None:
        updates.append("name = ?")
        params.append(name)
    if mode is not None:
        updates.append("mode = ?")
        params.append(mode)
    if interface is not None:
        updates.append("interface = ?")
        params.append(interface)
    if ping_interval is not None:
        updates.append("ping_interval = ?")
        params.append(int(ping_interval))
    if ping_timeout is not None:
        updates.append("ping_timeout = ?")
        params.append(int(ping_timeout))
    if fail_limit is not None:
        updates.append("fail_limit = ?")
        params.append(int(fail_limit))
    if auto_fallback is not None:
        updates.append("auto_fallback = ?")
        params.append(auto_fallback)
    if fallback_interval is not None:
        updates.append("fallback_interval = ?")
        params.append(int(fallback_interval))
    if static_subnets is not None:
        updates.append("static_subnets = ?")
        params.append(static_subnets)
    if link1 is not None:
        updates.append("link1_config = ?")
        params.append(json.dumps(link1))
    if link2 is not None:
        updates.append("link2_config = ?")
        params.append(json.dumps(link2))

    if not updates:
        conn.close()
        return JSONResponse(status_code=400, content={"success": False, "error": "No fields to update"})

    params.append(store_id)
    query = f"UPDATE stores SET {', '.join(updates)} WHERE id = ?"
    cursor.execute(query, tuple(params))
    conn.commit()

    # Busca a configuração atualizada para propagar via WebSocket nativo
    cursor.execute('SELECT * FROM stores WHERE id = ?', (store_id,))
    store = cursor.fetchone()
    if store:
        config_data = {
            "type": "config_update",
            "config": {
                "interface": store['interface'],
                "mode": store['mode'],
                "ping_interval": store['ping_interval'],
                "ping_timeout": store['ping_timeout'],
                "fail_limit": store['fail_limit'],
                "auto_fallback": bool(store['auto_fallback']),
                "fallback_interval": store['fallback_interval'],
                "static_subnets": store['static_subnets'],
                "link1": json.loads(store['link1_config']),
                "link2": json.loads(store['link2_config'])
            }
        }
        await manager.send_command_to_store(store_id, config_data)

        # Envia também a atualização do card para os navegadores
        card_data = {
            "type": "store_update",
            "store": {
                "id": store['id'],
                "name": store['name'],
                "active_link": store['active_link'],
                "online": store['online'],
                "latency": store['latency'],
                "last_heartbeat": store['last_heartbeat'],
                "mode": store['mode'],
                "link1": json.loads(store['link1_config']),
                "link2": json.loads(store['link2_config'])
            }
        }
        await manager.broadcast_to_browsers(card_data)

    conn.close()
    return {"success": True, "message": f"Configurações da loja {store_id} salvas e propagadas via WebSocket."}

@app.post("/api/store/delete")
async def delete_store(request: Request):
    data = await request.json()
    if not data or 'store_id' not in data:
        return JSONResponse(status_code=400, content={"success": False, "error": "store_id is required"})
    
    store_id = data['store_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM stores WHERE id = ?', (store_id,))
    conn.commit()
    conn.close()
    return {"success": True, "message": f"Loja {store_id} removida."}

# --- PÁGINA DO DASHBOARD INTERFACES WEB ---

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM stores')
    stores = []
    
    for row in cursor.fetchall():
        store = dict(row)
        store['link1'] = json.loads(store['link1_config']) if store['link1_config'] else {}
        store['link2'] = json.loads(store['link2_config']) if store['link2_config'] else {}
        
        time_since_heartbeat = time.time() - store['last_heartbeat']
        if store['last_heartbeat'] > 0 and time_since_heartbeat < 30:
            pass
        else:
            store['online'] = 0
            store['active_link'] = 'desconectado'
            
        stores.append(store)
        
    conn.close()
    return templates.TemplateResponse("index.html", {"request": request, "stores": stores, "current_time": time.time()})

if __name__ == '__main__':
    import uvicorn
    init_db()
    if getattr(sys, 'frozen', False):
        # Em modo executável congelado, passamos o objeto do app diretamente para evitar erros de importação
        uvicorn.run(app, host="0.0.0.0", port=5555)
    else:
        # Em modo de desenvolvimento (script), passamos a string para suportar autoreload
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        uvicorn.run("app:app", host="0.0.0.0", port=5555, reload=True)
