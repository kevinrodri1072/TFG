"""
sync.py — Sincronització amb el(s) Digital Twin(s).

ESTRATÈGIA:
En lloc d'enviar un snapshot complet de Mininet en cada canvi (que obliga el Twin
a fer net.stop() + reconstrucció completa, ~6-10s), s'envien EVENTS INCREMENTALS:
petits payloads JSON que descriuen exactament què ha canviat. El Twin aplica cada
event in-place, mantenint els daemons FRR vius i reduint la latència de ~10s a ~100ms.

SUPORT MULTI-TWIN:
TWINS és una llista de dicts {ip, port}. Tots els events s'envien a TOTS els Twins
en paral·lel (un thread per Twin). La latència es mesura com el màxim de tots.

MESURA DE LATÈNCIES:
  t_local_ms   = temps que triga l'Original a aplicar el canvi a Mininet
  t_network_ms = temps HTTP total (anada + proces Twin + tornada) - el maxim dels Twins
  t_twin_ms    = temps que triga el Twin a aplicar el canvi - el maxim dels Twins
  t_total      = max(t_local, t_network)  - execucio paral-lela, no suma

MESURA DE THROUGHPUT I CPU:
  payload_bytes   = mida del JSON enviat al Twin per cada operacio
  throughput_bps  = payload_bytes x 8 / (t_network_ms / 1000)  - bits/s reals del link
  cpu_percent     = us de CPU del host en el moment de registrar l'operacio
  ops_per_sec     = capacitat de CPU: 1000 / t_local_ms (ops/s en serie) + recent (ultims 10s)
"""

import json
import threading
import time
from collections import deque
import socketio as sio_client

import psutil
import requests

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓ DE CONNEXIÓ
# Sobreescrita per init_sync() amb els arguments CLI de app.py
# ─────────────────────────────────────────────────────────────────────────────
# TWINS: llista de dicts {ip, port} — un per cada PC Twin
TWINS       = [{'ip': '10.4.39.102', 'port': 5000}]
ORIGINAL_IP = '10.4.39.104'  # IP de l'Original — usada pels Twins per fer ping de tornada

# ── Twin state tracking ───────────────────────────────────────────────────
# {ip: {status, policy, last_seen, diverged_at}}
# status : 'connected' | 'diverged' | 'disconnected'
# policy : 'resync' (default) | 'disconnect'
TWIN_STATUS       = {}
_twin_status_lock = threading.Lock()

# ─────────────────────────────────────────────────────────────────────────────
# CONTROL DE SESIONES WEBSOCKET (NUEVO)
# ─────────────────────────────────────────────────────────────────────────────
_socketio_server = None  # Inyectado por app.py (Original)
_flask_app_ref   = None  # Inyectado por app.py (Twin)

TWIN_SIDS         = {}   # Mapeo de {ip: websocket_sid}
_sid_lock         = threading.Lock()
PENDING_ACKS      = {}   # Control de transacciones {tx_id: {event, net_ms, twin_ms}}
PENDING_ACKS_LOCK = threading.Lock()

def map_twin_sid(ip, sid):
    with _sid_lock:
        TWIN_SIDS[ip] = sid
        print(f"[sync_ws] Mapeado Twin IP {ip} al SID {sid}")

def get_sid_by_ip(ip):
    with _sid_lock:
        return TWIN_SIDS.get(ip)

def handle_twin_ack_internal(data):
    """Procesa la confirmación asíncrona enviada por el Twin."""
    tx_id = data.get('tx_id')
    t_twin_local = data.get('t_local_ms')
    
    with PENDING_ACKS_LOCK:
        if tx_id in PENDING_ACKS:
            # Calcular tiempo de red total (ida + vuelta)
            t_network_ms = round((time.time() - PENDING_ACKS[tx_id]['start_time']) * 1000, 2)
            PENDING_ACKS[tx_id]['net_ms'] = t_network_ms
            PENDING_ACKS[tx_id]['twin_ms'] = t_twin_local
            PENDING_ACKS[tx_id]['event'].set()  # Desbloquea el thread de envío

def _init_twin(ip):
    if ip not in TWIN_STATUS:
        TWIN_STATUS[ip] = {'status': 'connected', 'policy': 'resync',
                            'last_seen': None, 'diverged_at': None}


def _touch_twin(ip):
    with _twin_status_lock:
        _init_twin(ip)
        TWIN_STATUS[ip]['last_seen'] = round(time.time(), 2)


def set_twin_status(ip, status):
    with _twin_status_lock:
        _init_twin(ip)
        TWIN_STATUS[ip]['status'] = status
        if status == 'diverged':
            TWIN_STATUS[ip]['diverged_at'] = round(time.time(), 2)


def set_twin_policy(ip, policy):
    with _twin_status_lock:
        _init_twin(ip)
        TWIN_STATUS[ip]['policy'] = policy


def get_twin_statuses():
    with _twin_status_lock:
        return {ip: dict(s) for ip, s in TWIN_STATUS.items()}


def _is_twin_active(ip):
    """Return True only if Twin is connected or diverged — not offline or disconnected."""
    with _twin_status_lock:
        status = TWIN_STATUS.get(ip, {}).get('status', 'connected')
        return status in ('connected', 'diverged')


def register_twin(ip, port=5000):
    """
    Dynamically register a Twin that has contacted the Original.
    Called when a Twin sends POST /twin/register or POST /twin/heartbeat.
    If the Twin's IP is not in the TWINS list yet, add it so future
    sync events reach it.
    """
    with _twin_status_lock:
        _init_twin(ip)
        TWIN_STATUS[ip]['last_seen'] = round(time.time(), 2)
        TWIN_STATUS[ip]['port']      = port
        if TWIN_STATUS[ip]['status'] in ('offline', 'unknown'):
            TWIN_STATUS[ip]['status'] = 'connected'

    # Dynamically add to TWINS if not already present (e.g. Twin started
    # with --twin-ip but was not listed in the Original's --twins argument)
    if not any(t['ip'] == ip for t in TWINS):
        TWINS.append({'ip': ip, 'port': port})
        print(f'[sync] New Twin auto-registered: {ip}:{port}')


def _start_heartbeat_checker():
    """
    Background thread: marks a Twin as 'offline' if no heartbeat received
    in the last HEARTBEAT_TIMEOUT seconds.
    Only runs on the Original (is_twin=False).
    """
    HEARTBEAT_TIMEOUT = 8   # seconds — Twin sends every 3s, so ~2.5 missed = offline

    def _check():
        while True:
            time.sleep(3)
            now = time.time()
            with _twin_status_lock:
                for ip, s in TWIN_STATUS.items():
                    last = s.get('last_seen')
                    if last and (now - last) > HEARTBEAT_TIMEOUT:
                        if s['status'] not in ('offline', 'disconnected'):
                            s['status'] = 'offline'
                            print(f'[sync] Twin {ip} marked offline (no heartbeat)')

    t = threading.Thread(target=_check, daemon=True)
    t.start()


def resync_one_twin(xarxa, twin):
    """Send full snapshot to a single Twin to restore Original state."""
    try:
        serializable_matrix = [
            [cell if isinstance(cell, str) else int(cell) for cell in row]
            for row in xarxa.network_matrix
        ]
        r = requests.post(
            f'http://{twin["ip"]}:{twin["port"]}/load_network',
            json={'matrix': serializable_matrix, 'nodes': xarxa.nodes, 'sync': True},
            timeout=15,
        )
        if r.status_code == 200:
            print(f'[sync] Resync OK → {twin["ip"]}')
            set_twin_status(twin['ip'], 'connected')
        else:
            print(f'[sync] Resync failed → {twin["ip"]}: HTTP {r.status_code}')
    except Exception as e:
        print(f'[sync] Resync error → {twin["ip"]}: {e}')

# ─────────────────────────────────────────────────────────────────────────────
# HISTORIAL DE LATÈNCIES
# deque amb capacitat màxima de 400 entrades (les més antigues es descarten)
# sync_history_lock protegeix l'accés concurrent des de múltiples threads
# ─────────────────────────────────────────────────────────────────────────────
sync_latency_history = deque(maxlen=400)
sync_history_lock    = threading.Lock()

# Referència a l'objecte Xarxa, injectada per app.py a l'arrencada
_xarxa = None


def init_sync(xarxa_instance, twins=None, original_ip=None, twin_port=None,
              twin_ip=None):
    """
    Configura el mòdul de sincronització.

    twins       : llista d'IPs o dicts {ip,port} dels PCs Twin
    original_ip : IP d'aquest PC (l'Original)
    twin_port   : port per defecte per als Twins (5000 si no s'especifica)
    twin_ip     : drecera per a un sol Twin (compatibilitat enrere amb --twin-ip)
    """
    global _xarxa, TWINS, ORIGINAL_IP
    _xarxa = xarxa_instance

    port = twin_port if twin_port is not None else 5000

    if twins:
        # Parseja cada entrada: pot ser string "IP" o "IP:PORT" o dict {ip, port}
        parsed = []
        for t in twins:
            if isinstance(t, dict):
                parsed.append({'ip': t['ip'], 'port': t.get('port', port)})
            else:
                if ':' in str(t):
                    ip, p = str(t).rsplit(':', 1)
                    parsed.append({'ip': ip, 'port': int(p)})
                else:
                    parsed.append({'ip': str(t), 'port': port})
        TWINS = parsed
    elif twin_ip:
        # Compatibilitat enrere: --twin-ip IP equival a --twins IP
        TWINS = [{'ip': twin_ip, 'port': port}]

    if original_ip:
        ORIGINAL_IP = original_ip

    twins_str = ', '.join(f'{t["ip"]}:{t["port"]}' for t in TWINS)
    print(f'[sync] Original={ORIGINAL_IP}  Twins=[{twins_str}]')

    # Start heartbeat checker always — harmless on Twin (TWIN_STATUS stays empty).
    # Must not depend on --twins being set: Twins can register dynamically later.
    _start_heartbeat_checker()




# ─────────────────────────────────────────────────────────────────────────────
# REGISTRE DE LATÈNCIES
# ─────────────────────────────────────────────────────────────────────────────

def record_sync_latency(operation, t_local_ms, t_network_ms, t_twin_ms,
                         payload_bytes=None):
    """
    Guarda una entrada de latència a l'historial i la replica a tots els Twins
    perquè els seus dashboards mostrin les mateixes dades que l'Original.

    Hi ha dos casos d'ús:
    1. Entrada nova: t_local, t_network i t_twin tots disponibles (o algun None)
    2. Actualització tardana: t_local_ms disponible però t_network_ms=None.
       Passa quan el thread de sync ha acabat però Mininet local encara no.
       En aquest cas s'actualitza l'última entrada coincident en lloc d'afegir-ne una.

    payload_bytes : mida en bytes del JSON enviat al Twin (per calcular throughput)
    """
    # Throughput del link: bits transferits / temps de xarxa
    throughput_bps = None
    if payload_bytes and t_network_ms and t_network_ms > 0:
        throughput_bps = round(payload_bytes * 8 / (t_network_ms / 1000), 2)

    cpu_percent = psutil.cpu_percent(interval=None)
    updated_entry = None

    with sync_history_lock:
        if t_local_ms is not None and t_network_ms is None and t_twin_ms is None:
            for entry in reversed(sync_latency_history):
                if entry.get('operation') == operation:
                    entry['t_local_ms'] = round(t_local_ms, 2)
                    t_net = entry.get('t_network_ms')
                    if t_net is not None:
                        entry['latency_ms'] = round(max(t_local_ms, t_net), 2)
                    updated_entry = dict(entry)
                    break

        if updated_entry is None:
            entry = {
                'operation':      operation,
                't_local_ms':     round(t_local_ms,   2) if t_local_ms   is not None else None,
                't_network_ms':   round(t_network_ms, 2) if t_network_ms is not None else None,
                't_twin_ms':      round(t_twin_ms,    2) if t_twin_ms    is not None else None,
                'payload_bytes':  payload_bytes,
                'throughput_bps': throughput_bps,
                'cpu_percent':    round(cpu_percent, 1) if cpu_percent is not None else None,
                'timestamp':      time.time(),
            }
            sync_latency_history.append(entry)
            updated_entry = dict(entry)

    # ── REPLICA VÍA WEBSOCKET O HTTP FALLBACK ──
    for twin in TWINS:
        sid = get_sid_by_ip(twin['ip'])
        if sid and _socketio_server:
            # Envío directo asíncrono (Fire and Forget) para métricas
            _socketio_server.emit('ws_sync', {
                'endpoint': '/sync_metrics',
                'payload': updated_entry,
                'tx_id': 'metrics_fire_and_forget'
            }, to=sid)
        else:
            try:
                requests.post(f'http://{twin["ip"]}:{twin["port"]}/sync_metrics', json=updated_entry, timeout=1)
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# SINCRONITZACIÓ INCREMENTAL (EVENTS)
# Mecanisme principal: envia petits payloads JSON a tots els Twins
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# NUEVO TUNEL DE ENVÍO POR WEBSOCKET
# ─────────────────────────────────────────────────────────────────────────────

def _do_sync_to_one_twin(twin, endpoint, payload):
    """
    Sustituye la comunicación HTTP por emisión de eventos WebSocket directos.
    Si el canal WebSocket no está listo, realiza un fallback a HTTP.
    """
    sid = get_sid_by_ip(twin['ip'])
    
    # FALLBACK: Si el Twin no se ha conectado por socket, usamos el HTTP original
    if not sid:
        try:
            t_start = time.time()
            response = requests.post(f'http://{twin["ip"]}:{twin["port"]}{endpoint}', json=payload, timeout=5)
            t_network_ms = round((time.time() - t_start) * 1000, 2)
            if response.status_code == 200:
                return t_network_ms, response.json().get('t_local_ms', None)
        except Exception:
            pass
        return None, None

    # CANAL PRINCIPAL: WebSockets distribuidos
    tx_id = f"{endpoint}_{twin['ip']}_{time.time()}"
    evt = threading.Event()
    
    with PENDING_ACKS_LOCK:
        PENDING_ACKS[tx_id] = {
            'event': evt,
            'net_ms': None,
            'twin_ms': None,
            'start_time': time.time()
        }
    
    # Emitir paquete encapsulado al canal del Twin
    ws_packet = {'endpoint': endpoint, 'payload': payload, 'tx_id': tx_id}
    _socketio_server.emit('ws_sync', ws_packet, to=sid)
    
    # Esperar confirmación (bloqueo controlado del hilo paralelo de este Twin)
    success = evt.wait(timeout=10)
    
    with PENDING_ACKS_LOCK:
        res = PENDING_ACKS.pop(tx_id, None)
        
    if success and res:
        print(f'[sync_ws] → {twin["ip"]} {endpoint} net={res["net_ms"]}ms twin={res["twin_ms"]}ms')
        return res['net_ms'], res['twin_ms']
        
    # Lógica de gestión de divergencia intacta si se agota el timeout
    print(f'[sync_ws] Timeout en canal WebSocket para {twin["ip"]}')
    set_twin_status(twin['ip'], 'diverged')
    policy = TWIN_STATUS.get(twin['ip'], {}).get('policy', 'resync')
    if policy == 'resync' and _xarxa is not None:
        threading.Thread(target=resync_one_twin, args=(_xarxa, twin), daemon=True).start()
    elif policy == 'disconnect':
        set_twin_status(twin['ip'], 'disconnected')
    return None, None


def _do_sync_to_all_twins(endpoint, data, t_local_holder):
    """
    Envia un event a TOTS els Twins en paral·lel (un thread per Twin).

    MESURA DE LATÈNCIA MULTI-TWIN:
    - t_network = max(tots els round-trips) — el Twin més lent marca el ritme
    - t_twin    = max(tots els temps de procés) — el Twin més lent marca el ritme
    - t_total   = max(t_local, t_network) — Original i Twins treballen en paral·lel

    Espera que el thread principal senyali t_local_ms (via t_local_holder)
    per poder registrar tots els temps en una sola entrada consistent.
    """
    payload      = {**data, 'sync': True}   # afegeix flag 'sync:True' perquè el Twin ho sàpiga
    payload_bytes = len(json.dumps(payload).encode('utf-8'))  # mida real del missatge JSON
    results      = [None] * len(TWINS)       # resultats indexats per posició a TWINS
    lock         = threading.Lock()

    def send_to(idx, twin):
        if not _is_twin_active(twin['ip']):
            print(f'[sync] Skipping disconnected Twin {twin["ip"]}')
            with lock:
                results[idx] = (None, None)
            return
        net_ms, twin_ms = _do_sync_to_one_twin(twin, endpoint, payload)
        with lock:
            results[idx] = (net_ms, twin_ms)

    # Llança un thread per cada Twin — corren simultàniament
    threads = [
        threading.Thread(target=send_to, args=(i, twin), daemon=True)
        for i, twin in enumerate(TWINS)
    ]
    for t in threads: t.start()
    for t in threads: t.join()   # espera que TOTS hagin acabat

    # Agrega: usa el pitjor cas (màxim) de tots els Twins
    valid_net   = [r[0] for r in results if r and r[0] is not None]
    valid_twin  = [r[1] for r in results if r and r[1] is not None]
    t_network_ms = round(max(valid_net),  2) if valid_net  else None
    t_twin_ms    = round(max(valid_twin), 2) if valid_twin else None

    # Espera que el thread principal hagi acabat d'aplicar el canvi a Mininet (max 10s)
    # El thread principal senyala via set_t_local() quan acaba
    ready = t_local_holder.get('ready')
    if ready:
        ready.wait(timeout=10)
    t_local_ms = t_local_holder.get('value')

    operation = endpoint.strip('/')
    record_sync_latency(operation, t_local_ms, t_network_ms, t_twin_ms,
                        payload_bytes=payload_bytes)


def sync_event(endpoint, data, t_local_ms):
    """
    Envia un event incremental a TOTS els Twins en un thread de background.

    Retorna un 't_local_holder' — un dict amb un threading.Event que permet
    senyalar quan Mininet local ha acabat (patró producer/consumer):

    Ús típic (add_router):
      holder = sync_event('/add_router', payload, None)  # llança thread sync
      # ... aplica canvi a Mininet (triga ~300ms) ...
      set_t_local(holder, 300.5)   # senyala que Mininet ha acabat

    El thread de sync espera aquest senyal per registrar t_local correctament.
    Si t_local_ms no és None, s'estableix immediatament (ruta ràpida).
    """
    ready  = threading.Event()
    holder = {'value': t_local_ms, 'ready': ready}
    if t_local_ms is not None:
        ready.set()   # ja sabem el temps — no cal esperar
    threading.Thread(
        target=_do_sync_to_all_twins,
        args=(endpoint, data, holder),
        daemon=True,
    ).start()
    return holder


def set_t_local(holder, t_local_ms):
    """
    Senyala al thread de sync que Mininet local ha acabat.
    Crida des del thread principal un cop _apply_routing / addLink han acabat.
    """
    holder['value'] = round(t_local_ms, 2)
    holder['ready'].set()   # desbloqueja el ready.wait() del thread de sync


# ─────────────────────────────────────────────────────────────────────────────
# SINCRONITZACIÓ PER SNAPSHOT (COMPLET)
# Menys eficient (~6-10s) però garanteix consistència total.
# Reservat per a operacions que no es poden fer incrementalment.
# ─────────────────────────────────────────────────────────────────────────────

def _do_sync_snapshot(operation, t_local_ms):
    xarxa = _xarxa
    serializable_matrix = [
        [cell if isinstance(cell, str) else int(cell) for cell in row]
        for row in xarxa.network_matrix
    ]
    snapshot_payload = {'matrix': serializable_matrix, 'nodes': xarxa.nodes, 'sync': True}
    payload_bytes = len(json.dumps(snapshot_payload).encode('utf-8'))
    valid_net = []
    
    for twin in TWINS:
        net_ms, twin_ms = _do_sync_to_one_twin(twin, '/load_network', snapshot_payload)
        if net_ms is not None:
            valid_net.append(net_ms)
            
    t_network_ms = round(max(valid_net), 2) if valid_net else None
    record_sync_latency(operation, t_local_ms, t_network_ms, None, payload_bytes=payload_bytes)


def sync_snapshot(operation, t_local_ms):
    """Llança la sincronització per snapshot en un thread de background."""
    threading.Thread(
        target=_do_sync_snapshot,
        args=(operation, t_local_ms),
        daemon=True,
    ).start()

# ─────────────────────────────────────────────────────────────────────────────
# CLIENTE WEBSOCKET PERSISTENTE DEL TWIN (NUEVO)
# ─────────────────────────────────────────────────────────────────────────────

def start_twin_websocket_client(original_ip, original_ws_port=5001):
    """
    Lazo de ejecución del cliente del Twin. Escucha el canal permanente
    e inyecta localmente las operaciones usando el test_client de Flask.
    """
    sio = sio_client.Client()
    
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        own_ip = s.getsockname()[0]
        s.close()
    except Exception:
        own_ip = '127.0.0.1'

    @sio.event
    def connect():
        print(f"[sync_ws] Conectado al túnel del Original. Registrando IP {own_ip}...")
        sio.emit('register_twin_ws', {'ip': own_ip})

    @sio.on('ws_sync')
    def on_ws_sync(data):
        endpoint = data['endpoint']
        payload  = data['payload']
        tx_id    = data['tx_id']
        
        t_twin_local = None
        
        if _flask_app_ref:
            # Invocar la API localmente saltándose la interfaz física de red
            with _flask_app_ref.test_client() as client:
                res = client.post(endpoint, json=payload)
                if res.status_code == 200:
                    try:
                        t_twin_local = res.get_json().get('t_local_ms', None)
                    except Exception:
                        pass
                        
        # Enviar el ACK asíncrono inmediato si no es una operación desatendida
        if tx_id != 'metrics_fire_and_forget':
            sio.emit('twin_ack_ws', {'tx_id': tx_id, 't_local_ms': t_twin_local})

    def _connect_loop():
        while True:
            try:
                if not sio.connected:
                    sio.connect(f'http://{original_ip}:{original_ws_port}')
                    sio.wait()
            except Exception as e:
                time.sleep(3)

    threading.Thread(target=_connect_loop, daemon=True).start()

def send_heartbeat():
    """
    Called by the Twin every 3s to tell the Original it is still alive.
    Also used as the initial registration message.
    """
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        own_ip = s.getsockname()[0]
        s.close()
    except Exception:
        own_ip = '127.0.0.1'

    try:
        requests.post(
            f'http://{ORIGINAL_IP}:5000/twin/heartbeat',
            json={'ip': own_ip, 'port': 5000},
            timeout=5,
        )
    except Exception as e:
        pass  # Original may not be reachable yet — heartbeat will retry


def start_twin_heartbeat():
    """Start the heartbeat loop in a daemon thread (Twin side)."""
    def _loop():
        # Send registration immediately on start
        send_heartbeat()
        # Then send periodic heartbeats
        while True:
            time.sleep(3)
            send_heartbeat()
    threading.Thread(target=_loop, daemon=True).start()
    print(f'[sync] Twin heartbeat started → {ORIGINAL_IP}:5000')


# Àlies per compatibilitat enrere (usat per topology.py /load_network)
def sync_in_background(operation, t_local_ms):
    sync_snapshot(operation, t_local_ms)