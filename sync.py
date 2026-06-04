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
  throughput_bps  = payload_bytes x 8 / (temps net de xarxa / 1000)
                    temps net = t_network_ms - t_twin_ms (aïlla el temps de xarxa
                    pur del temps de procés del Twin) — bits/s reals del link
  cpu_percent     = us de CPU del host en el moment de registrar l'operacio
  ops_per_sec     = capacitat de CPU: 1000 / t_local_ms (ops/s en serie) + recent (ultims 10s)
"""

import json
import threading
import time
from collections import deque

import psutil
import requests

# ─────────────────────────────────────────────────────────────────────────────
# UTILITATS
# ─────────────────────────────────────────────────────────────────────────────

def _get_own_ip():
    """Detecta la IP pròpia del host via un socket UDP (no envia res)."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓ DE CONNEXIÓ
# Sobreescrita per init_sync() amb els arguments CLI de app.py
# ─────────────────────────────────────────────────────────────────────────────
# TWINS: llista de dicts {ip, port} — un per cada PC Twin
TWINS       = []   # poblat dinàmicament quan els Twins arrenquen amb --twin --original-ip
ORIGINAL_IP = '10.4.39.104'  # IP de l'Original — usada pels Twins per fer ping de tornada

# ── Twin state tracking ───────────────────────────────────────────────────
# {ip: {status, policy, last_seen, diverged_at}}
# status : 'connected' | 'diverged' | 'disconnected'
# policy : 'resync' (default) | 'disconnect'
TWIN_STATUS       = {}
_twin_status_lock = threading.Lock()


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
    # No registrar la pròpia IP — sincronitzar-se a un mateix causa race conditions
    own_ip = _get_own_ip()
    if ip == own_ip or ip == '127.0.0.1':
        return

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

    # Filtra la pròpia IP de la llista de Twins — sincronitzar-se a un mateix
    # causa race conditions (dos threads fan node.cmd() al mateix node alhora
    # → AssertionError de Mininet: "assert self.shell and not self.waiting").
    own_ip = _get_own_ip()
    before = len(TWINS)
    TWINS = [t for t in TWINS if t['ip'] != own_ip and t['ip'] != '127.0.0.1']
    if len(TWINS) < before:
        print(f'[sync] Filtered own IP ({own_ip}) from TWINS list — self-sync disabled')

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

    En el flux HTTP actual, t_local, t_network i t_twin arriben SEMPRE junts en
    una sola crida des de _do_sync_to_all_twins, així que cada operació afegeix
    una entrada nova. Si cap Twin respon (offline/diverged/timeout), t_network i
    t_twin són None i l'entrada ho reflecteix — NO se sobreescriu cap entrada
    anterior (això evitava registrar correctament operacions amb el Twin caigut).

    payload_bytes : mida en bytes del JSON enviat al Twin (per calcular throughput)
    """
    # ── Throughput del sistema Original+Twin ──
    # Mesura la capacitat de transferència d'informació del sistema complet.
    # Usem t_total (= max(t_local, t_network)) perquè és el temps que el sistema
    # està realment "ocupat" processant un canvi: des que comença fins que TANT
    # l'Original COM el Twin l'han aplicat. Dividir payload per t_total dóna la
    # taxa de bytes útils per segon que el sistema pot processar de forma sostinguda.
    # Nota: payload_bytes és el body JSON enviat al Twin; les capçaleres HTTP
    # (~350 bytes) no s'inclouen, per tant és una estimació conservadora.
    throughput_bps = None
    if payload_bytes and latency_ms and latency_ms > 0:
        throughput_bps = round(payload_bytes * 8 / (latency_ms / 1000), 2)

    # CPU en el moment de registrar (non-blocking: usa la mesura anterior del SO)
    cpu_percent = psutil.cpu_percent(interval=None)

    # t_total = max(t_local, t_network) — execució paral·lela, no suma
    latency_ms = None
    if t_local_ms is not None and t_network_ms is not None:
        latency_ms = round(max(t_local_ms, t_network_ms), 2)
    elif t_network_ms is not None:
        latency_ms = round(t_network_ms, 2)
    elif t_local_ms is not None:
        latency_ms = round(t_local_ms, 2)

    with sync_history_lock:
        entry = {
            'operation':      operation,
            'latency_ms':     latency_ms,
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

    # Replica l'entrada a TOTS els Twins perquè els seus dashboards estiguin sincronitzats
    for twin in TWINS:
        try:
            requests.post(
                f'http://{twin["ip"]}:{twin["port"]}/sync_metrics',
                json=updated_entry,
                timeout=3,
            )
        except Exception as e:
            print(f'[sync_metrics] push error to {twin["ip"]}: {e}')


# ─────────────────────────────────────────────────────────────────────────────
# SINCRONITZACIÓ INCREMENTAL (EVENTS)
# Mecanisme principal: envia petits payloads JSON a tots els Twins
# ─────────────────────────────────────────────────────────────────────────────

def _do_sync_to_one_twin(twin, endpoint, payload, retries=3, delay=0.5):
    """
    Envia un event a UN Twin. Reintenta fins a `retries` vegades si falla.
    Retorna (t_network_ms, t_twin_ms) o (None, None) si falla definitivament.
    """
    for attempt in range(retries):
        try:
            t_start  = time.time()
            response = requests.post(
                f'http://{twin["ip"]}:{twin["port"]}{endpoint}',
                json=payload,
                timeout=30,   # espera fins a 30s (Mininet pot ser lent)
            )
            t_network_ms = round((time.time() - t_start) * 1000, 2)
            if response.status_code == 200:
                # t_twin_ms: temps que ha trigat el Twin a aplicar el canvi localment
                t_twin_ms = response.json().get('t_local_ms', None)
                print(f'[sync] → {twin["ip"]} {endpoint}  '
                      f'net={t_network_ms}ms  twin={t_twin_ms}ms')
                return t_network_ms, t_twin_ms
        except Exception as e:
            print(f'[sync_event] {endpoint} → {twin["ip"]} '
                  f'attempt {attempt+1}/{retries}: {e}')
            if attempt < retries - 1:
                time.sleep(delay)
    print(f'[sync_event] {endpoint} → {twin["ip"]} failed after {retries} attempts')
    set_twin_status(twin['ip'], 'diverged')
    policy = TWIN_STATUS.get(twin['ip'], {}).get('policy', 'resync')
    if policy == 'resync' and _xarxa is not None:
        print(f'[sync] Divergence policy=resync → resyncing {twin["ip"]}')
        threading.Thread(target=resync_one_twin, args=(_xarxa, twin), daemon=True).start()
    elif policy == 'disconnect':
        print(f'[sync] Divergence policy=disconnect → disconnecting {twin["ip"]}')
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

def _do_sync_snapshot(operation, t_local_holder):
    """
    Serialitza l'estat complet (matriu + nodes) i l'envia a tots els Twins via
    /load_network. El Twin fa un restart_network() complet.

    Espera el temps del restart local via t_local_holder (mateix patró que
    _do_sync_to_all_twins) perquè t_total = max(t_local, t_network).
    NOTA: el Twin retorna {'ok': True} immediatament després d'ARRENCAR el
    restart en un thread, així que t_network mesura el cost de transferir el
    snapshot + ACK, no el temps de reconstrucció del Twin (que és asíncron).
    """
    xarxa = _xarxa
    # La matriu conté strings i ints — cal assegurar que tot és serialitzable a JSON
    serializable_matrix = [
        [cell if isinstance(cell, str) else int(cell) for cell in row]
        for row in xarxa.network_matrix
    ]
    snapshot_payload = {
        'matrix': serializable_matrix,
        'nodes':  xarxa.nodes,
        'sync':   True,
    }
    payload_bytes = len(json.dumps(snapshot_payload).encode('utf-8'))
    valid_net = []
    for twin in TWINS:
        try:
            # t_net_start DINS del bucle: cada Twin es mesura independentment
            t_net_start = time.time()
            requests.post(
                f'http://{twin["ip"]}:{twin["port"]}/load_network',
                json=snapshot_payload,
                timeout=30,
            )
            t_network_ms = round((time.time() - t_net_start) * 1000, 2)
            valid_net.append(t_network_ms)
        except Exception as e:
            print(f'[sync_snapshot] error to {twin["ip"]}: {e}')
    t_network_ms = round(max(valid_net), 2) if valid_net else None

    # Espera el temps real del restart local (màx 30s — restart és lent)
    ready = t_local_holder.get('ready')
    if ready:
        ready.wait(timeout=30)
    t_local_ms = t_local_holder.get('value')

    record_sync_latency(operation, t_local_ms, t_network_ms, None,
                        payload_bytes=payload_bytes)


def sync_snapshot(operation, t_local_holder):
    """Llança la sincronització per snapshot en un thread de background.
    t_local_holder és un dict {value, ready: Event} senyalat pel thread que
    fa el restart_network() local."""
    threading.Thread(
        target=_do_sync_snapshot,
        args=(operation, t_local_holder),
        daemon=True,
    ).start()


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
def sync_in_background(operation, t_local_holder):
    sync_snapshot(operation, t_local_holder)