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
  t_local_ms  = temps que triga l'Original a aplicar el canvi a Mininet
  t_network_ms = temps HTTP total (anada + procés Twin + tornada) — el màxim dels Twins
  t_twin_ms    = temps que triga el Twin a aplicar el canvi — el màxim dels Twins
  t_total      = max(t_local, t_network)  — execució paral·lela, no suma
"""

import threading
import time
from collections import deque

import requests

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓ DE CONNEXIÓ
# Sobreescrita per init_sync() amb els arguments CLI de app.py
# ─────────────────────────────────────────────────────────────────────────────
# TWINS: llista de dicts {ip, port} — un per cada PC Twin
TWINS       = [{'ip': '10.4.39.110', 'port': 5000}]
ORIGINAL_IP = '10.4.39.102'  # IP de l'Original — usada pels Twins per fer ping de tornada

# ─────────────────────────────────────────────────────────────────────────────
# HISTORIAL DE LATÈNCIES
# deque amb capacitat màxima de 50 entrades (les més antigues es descarten)
# sync_history_lock protegeix l'accés concurrent des de múltiples threads
# ─────────────────────────────────────────────────────────────────────────────
sync_latency_history = deque(maxlen=50)
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


# ─────────────────────────────────────────────────────────────────────────────
# REGISTRE DE LATÈNCIES
# ─────────────────────────────────────────────────────────────────────────────

def record_sync_latency(operation, t_local_ms, t_network_ms, t_twin_ms):
    """
    Guarda una entrada de latència a l'historial i la replica a tots els Twins
    perquè els seus dashboards mostrin les mateixes dades que l'Original.

    Hi ha dos casos d'ús:
    1. Entrada nova: t_local, t_network i t_twin tots disponibles (o algun None)
    2. Actualització tardana: t_local_ms disponible però t_network_ms=None.
       Passa quan el thread de sync ha acabat però Mininet local encara no.
       En aquest cas s'actualitza l'última entrada coincident en lloc d'afegir-ne una.
    """
    updated_entry = None

    with sync_history_lock:
        # Cas 2: actualització tardana → modifica l'última entrada coincident
        if t_local_ms is not None and t_network_ms is None and t_twin_ms is None:
            for entry in reversed(sync_latency_history):
                if entry.get('operation') == operation:
                    entry['t_local_ms'] = round(t_local_ms, 2)
                    # Recalcula t_total = max(t_local, t_network) — execució paral·lela
                    t_net = entry.get('t_network_ms')
                    if t_net is not None:
                        entry['latency_ms'] = round(max(t_local_ms, t_net), 2)
                    updated_entry = dict(entry)
                    break

        # Cas 1: entrada nova
        if updated_entry is None:
            entry = {
                'operation':    operation,
                't_local_ms':   round(t_local_ms,   2) if t_local_ms   is not None else None,
                't_network_ms': round(t_network_ms, 2) if t_network_ms is not None else None,
                't_twin_ms':    round(t_twin_ms,    2) if t_twin_ms    is not None else None,
                'timestamp':    time.time(),
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
    payload  = {**data, 'sync': True}   # afegeix flag 'sync:True' perquè el Twin ho sàpiga
    results  = [None] * len(TWINS)       # resultats indexats per posició a TWINS
    lock     = threading.Lock()

    def send_to(idx, twin):
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
    record_sync_latency(operation, t_local_ms, t_network_ms, t_twin_ms)


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
    """
    Serialitza l'estat complet (matriu + nodes) i l'envia a tots els Twins via
    /load_network. El Twin fa un restart_network() complet.
    """
    xarxa = _xarxa
    # La matriu conté strings i ints — cal assegurar que tot és serialitzable a JSON
    serializable_matrix = [
        [cell if isinstance(cell, str) else int(cell) for cell in row]
        for row in xarxa.network_matrix
    ]
    t_net_start = time.time()
    valid_net = []
    for twin in TWINS:
        try:
            requests.post(
                f'http://{twin["ip"]}:{twin["port"]}/load_network',
                json={
                    'matrix': serializable_matrix,
                    'nodes':  xarxa.nodes,
                    'sync':   True,
                },
                timeout=10,
            )
            t_network_ms = round((time.time() - t_net_start) * 1000, 2)
            valid_net.append(t_network_ms)
        except Exception as e:
            print(f'[sync_snapshot] error to {twin["ip"]}: {e}')
    t_network_ms = round(max(valid_net), 2) if valid_net else None
    record_sync_latency(operation, t_local_ms, t_network_ms, None)


def sync_snapshot(operation, t_local_ms):
    """Llança la sincronització per snapshot en un thread de background."""
    threading.Thread(
        target=_do_sync_snapshot,
        args=(operation, t_local_ms),
        daemon=True,
    ).start()


# Àlies per compatibilitat enrere (usat per topology.py /load_network)
def sync_in_background(operation, t_local_ms):
    sync_snapshot(operation, t_local_ms)