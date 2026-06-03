"""
app.py — Punt d'entrada de l'aplicació Digital Twin Network.

Arrenca DOS servidors simultàniament:
  - Port 5000: Flask HTTP (API REST) — gestiona totes les peticions de topologia
  - Port 5001: Flask-SocketIO (WebSocket) — emet mètriques en temps real al navegador
"""

import argparse
import subprocess
import threading
import time

import psutil
from flask import Flask
from flask import request
from flask_socketio import SocketIO

from xarxa import Xarxa
import sync as sync_module

# ─────────────────────────────────────────────────────────────────────────────
# ARGUMENTS DE LÍNIA DE COMANDES
# Permet configurar el rol (original/twin) i les IPs sense tocar el codi.
# Exemples d'ús:
#   Original amb un twin:    sudo python3 app.py --twins 10.4.39.110
#   Original amb dos twins:  sudo python3 app.py --twins 10.4.39.110 10.4.39.120
#   Twin:                    sudo python3 app.py --twin --original-ip 10.4.39.102
# ─────────────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(
    description='Digital Twin Network',
    formatter_class=argparse.RawTextHelpFormatter,
)
parser.add_argument('--twin', action='store_true',
                    help='Executa aquesta instància com a Digital Twin')
parser.add_argument('--twins', nargs='+', metavar='IP[:PORT]',
                    help=(
                        'IPs de TOTS els PCs Twin (separades per espai).\n'
                        'Exemples:\n'
                        '  --twins 10.4.39.110              (un twin, port 5000 per defecte)\n'
                        '  --twins 10.4.39.110 10.4.39.120  (dos twins)\n'
                        '  --twins 10.4.39.110:5000 10.4.39.120:5001  (ports personalitzats)\n'
                    ))
parser.add_argument('--twin-ip', default=None, metavar='IP',
                    help='IP d\'un sol Twin — drecera per a --twins (compatibilitat enrere)')
parser.add_argument('--original-ip', default='10.4.39.102', metavar='IP',
                    help='IP del PC Original (default: 10.4.39.102)')
parser.add_argument('--twin-port', default=5000, type=int, metavar='PORT',
                    help='Port per defecte de tots els Twins (default: 5000)')
args, _ = parser.parse_known_args()
IS_TWIN = args.twin   # True si aquesta instància és el Twin, False si és l'Original

# ─────────────────────────────────────────────────────────────────────────────
# DOS SERVIDORS FLASK SEPARATS
# Es necessiten dos perquè WebSockets i HTTP no conviuen bé al mateix port.
# ─────────────────────────────────────────────────────────────────────────────

# Servidor principal: atén TOTES les peticions HTTP de l'API (port 5000)
app = Flask(__name__)
app.config['PROPAGATE_EXCEPTIONS'] = True  # propaga errors a Flask per poder veure-les

# Servidor de mètriques: NOMÉS WebSockets per al dashboard (port 5001)
metrics_app = Flask('metrics_ws')
socketio    = SocketIO(metrics_app, cors_allowed_origins='*', async_mode='threading')

@socketio.on('register_twin_ws')
def handle_register_twin_ws(data):
    twin_ip = data.get('ip')
    sid = request.sid
    # Registrar en las tablas de estado de sync
    sync_module.map_twin_sid(twin_ip, sid)
    sync_module.register_twin(twin_ip)

@socketio.on('twin_ack_ws')
def handle_twin_ack_ws(data):
    sync_module.handle_twin_ack_internal(data)

# ─────────────────────────────────────────────────────────────────────────────
# BLUEPRINTS
# Un Blueprint és un grup d'endpoints Flask agrupats per funcionalitat.
# Cada fitxer de routes/ és un blueprint independent.
# init_blueprint() li injecta la referència a l'objecte Xarxa (la xarxa Mininet)
# perquè cada endpoint pugui operar sobre ella.
# ─────────────────────────────────────────────────────────────────────────────
from routes.topology import bp as topology_bp, init_blueprint as init_topology
from routes.nodes    import bp as nodes_bp,    init_blueprint as init_nodes
from routes.metrics  import bp as metrics_bp,  init_blueprint as init_metrics
from routes.routing  import bp as routing_bp,  init_blueprint as init_routing
from routes.xrfs     import bp as xrfs_bp,     init_blueprint as init_xrfs
from routes.chaos     import bp as chaos_bp,     init_blueprint as init_chaos
from routes.proposals import bp as proposals_bp, init_blueprint as init_proposals

app.register_blueprint(topology_bp)   # GET /topology, /matrix, /export, POST /load_network
app.register_blueprint(nodes_bp)      # POST /add_host, /add_router, /remove_node
app.register_blueprint(metrics_bp)    # GET /metrics/ping, /metrics/sync, /ip_dashboard...
app.register_blueprint(routing_bp)    # GET/POST /get_routing_mode, /set_routing_mode, /router_routes
app.register_blueprint(xrfs_bp)       # XRF microservices (Kubernetes, només al Twin)
app.register_blueprint(chaos_bp)
app.register_blueprint(proposals_bp)      # POST /chaos/cut_link, /chaos/restore_link


# ─────────────────────────────────────────────────────────────────────────────
# FUNCIONS DE MÈTRIQUES EN TEMPS REAL
# ─────────────────────────────────────────────────────────────────────────────

def _ping_one_target(target_ip):
    """
    Fa un ping real a una IP i retorna un dict amb min/avg/max/jitter.
    S'usa per mesurar la latència del canal físic entre PCs de laboratori.
    """
    from utils import parse_ping
    try:
        # -c 3: tres paquets | -i 0.2: interval de 200ms entre paquets
        result = subprocess.run(
            ['ping', '-c', '3', '-i', '0.2', target_ip],
            capture_output=True, text=True, timeout=10
        )
        latency, jitter = parse_ping(result.stdout)
        return {
            'latency_min': latency['min'],
            'latency_avg': latency['avg'],
            'latency_max': latency['max'],
            'jitter':      jitter,
            'reachable':   latency['avg'] is not None,  # False si els paquets es perden
            'target':      target_ip,
        }
    except Exception:
        # Si el ping falla (timeout, host unreachable), retorna tot a None
        return {
            'latency_min': None, 'latency_avg': None,
            'latency_max': None, 'jitter': None,
            'reachable': False, 'target': target_ip,
        }


def _ping_twin_channel():
    """
    Thread en background que cada 5 segons fa ping al(s) peer(s) i emet el
    resultat via WebSocket ('twin_channel_ping') al dashboard del navegador.

    Comportament:
    - Original: pinga TOTS els Twins en paral·lel (un event per Twin, identificat per IP)
    - Twin:     pinga l'Original (un sol event)
    """
    from sync import TWINS, ORIGINAL_IP

    time.sleep(2)   # espera que el servidor WebSocket estigui llest
    while True:
        if IS_TWIN:
            # El Twin pinga l'Original per mostrar la latència del canal cap a ell
            payload = _ping_one_target(ORIGINAL_IP)
            socketio.emit('twin_channel_ping', payload)
        else:
            # L'Original pinga tots els Twins simultàniament (threads en paral·lel)
            results = [None] * len(TWINS)

            def _do_ping(idx, twin):
                results[idx] = _ping_one_target(twin['ip'])

            threads = [
                threading.Thread(target=_do_ping, args=(i, t), daemon=True)
                for i, t in enumerate(TWINS)
            ]
            for th in threads: th.start()
            for th in threads: th.join()

            # Emet un event per cada Twin (el JS identifica cada canal per la IP)
            for payload in results:
                if payload:
                    socketio.emit('twin_channel_ping', payload)
        time.sleep(5)   # freqüència del ping: cada 5 segons


def _read_net_dev(pid):
    """
    Llegeix /proc/{pid}/net/dev directament del filesystem del host.

    Cada node Mininet corre en el seu propi network namespace de Linux.
    /proc/{PID}/net/dev conté les estadístiques de les interfícies d'aquell
    namespace (bytes tx/rx, paquets) sense necessitat d'obrir un shell.

    Avantatge vs node.cmd('cat /proc/net/dev'):
    - No bloqueja el shell del node (no hi ha concurrència)
    - ~0.1ms per lectura vs ~3ms amb shell
    - Thread-safe: no usa pipe del bash
    """
    try:
        with open(f'/proc/{pid}/net/dev', 'r') as f:
            return f.read()
    except OSError:
        return None   # el node ha mort o el PID ja no existeix


def _broadcast_metrics(xarxa):
    """
    Thread en background que cada 500ms:
    1. Emet CPU i RAM via WebSocket ('metrics_system')
    2. Cada segon (tick >= 2), llegeix estadístiques de tràfic de tots els nodes
       i emet ('metrics_link_traffic') amb rx/tx bytes per interfície

    No usa topology_lock: les mètriques obsoletes d'un tick no fan mal.
    Usa snapshots dels dicts per evitar "dict changed size during iteration".
    """
    link_tick = 0
    while True:
        try:
            # ── CPU i RAM del sistema host ──
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory()
            socketio.emit('metrics_system', {
                'cpu_percent':  cpu,
                'ram_percent':  ram.percent,
                'ram_used_mb':  round(ram.used  / 1024 / 1024, 1),
                'ram_total_mb': round(ram.total / 1024 / 1024, 1),
            })

            # ── Tràfic de xarxa (cada segon, no cada 500ms) ──
            link_tick += 1
            if link_tick >= 2 and xarxa.network_ready:
                link_tick = 0
                links = {}
                # Snapshot per evitar errors si la topologia canvia mentre iterem
                nodes_snap    = dict(xarxa.nodes)
                mn_nodes_snap = dict(xarxa.mininet_nodes)
                for name, props in nodes_snap.items():
                    if props['type'] not in ('router', 'host'):
                        continue
                    mn_node = mn_nodes_snap.get(name)
                    if not mn_node:
                        continue
                    pid = getattr(mn_node, 'pid', None)
                    if not pid:
                        continue
                    raw = _read_net_dev(pid)   # llegeix del filesystem sense shell
                    if not raw:
                        continue
                    # Parseja el format de /proc/net/dev (salta les 2 línies de capçalera)
                    for line in raw.strip().split('\n')[2:]:
                        parts = line.split(':')
                        if len(parts) < 2:
                            continue
                        intf = parts[0].strip()
                        if intf == 'lo':
                            continue   # ignora loopback
                        values = parts[1].split()
                        if len(values) < 9:
                            continue
                        # Mininet nombra les interfícies com "r1-eth0" → strip del prefix del node
                        intf_short = intf[len(name)+1:] if intf.startswith(name + '-') else intf
                        links[f'{name}-{intf_short}'] = {
                            'node':     name,
                            'intf':     intf_short,
                            'rx_bytes': int(values[0]),   # columna 1 = bytes rebuts
                            'tx_bytes': int(values[8]),   # columna 9 = bytes enviats
                        }
                socketio.emit('metrics_link_traffic', {'links': links})

        except Exception as e:
            print(f'[broadcast] error: {e}')

        time.sleep(0.5)   # freqüència del broadcast: cada 500ms


def _run_socketio_server():
    """Arrenca el servidor SocketIO al port 5001 en un thread de background."""
    socketio.run(metrics_app, host='0.0.0.0', port=5001, debug=False,
                 use_reloader=False, allow_unsafe_werkzeug=True)


# ─────────────────────────────────────────────────────────────────────────────
# PUNT D'ENTRADA PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    # 1. Neteja restes de sessions Mininet anteriors
    subprocess.run(['mn', '-c'], capture_output=True)

    # 2. Crea l'objecte Xarxa (inicialitza estructures de dades, no arrenca res encara)
    xarxa = Xarxa()

    # 3. Mata daemons FRR antics de runs anteriors.
    #    PROBLEMA: mn -c mata els bash shells de Mininet però NO els daemons FRR
    #    (zebra, ospfd). Si deixen fitxers PID bloquejats a /tmp/frr_*/,
    #    els nous daemons no poden arrencar ("Could not lock pid_file").
    #    SOLUCIÓ: SIGKILL tots els daemons vius i esborra els directoris.
    import glob, shutil, os
    for frr_dir in glob.glob('/tmp/frr_*'):
        for pidfile in ['zebra.pid', 'ospfd.pid', 'ldpd.pid', 'bfdd.pid']:
            pidpath = f'{frr_dir}/{pidfile}'
            if os.path.exists(pidpath):
                try:
                    with open(pidpath) as _pf:
                        _pid = int(_pf.read().strip())
                    os.kill(_pid, 9)   # SIGKILL — no grace period, mor immediatament
                except Exception:
                    pass   # PID ja mort o fitxer corrupte → ignora
        shutil.rmtree(frr_dir, ignore_errors=True)   # esborra el directori sencer

    # 4. Inicialitza el mòdul de sincronització amb les IPs dels Twins i de l'Original
    sync_module._xarxa = xarxa
    sync_module._socketio_server = socketio  # Inyección del servidor para el Original
    sync_module._flask_app_ref = app         # Inyección de la app para el test_client del Twin
    
    sync_module.init_sync(xarxa,
                          twins=args.twins,
                          twin_ip=args.twin_ip,
                          original_ip=args.original_ip,
                          twin_port=args.twin_port)

    # 5. Inicialitza tots els blueprints amb la referència a la xarxa
    init_topology(xarxa, IS_TWIN)
    init_nodes(xarxa)
    init_metrics(xarxa, socketio)
    init_routing(xarxa)
    init_xrfs(IS_TWIN, socketio)
    init_chaos(xarxa, socketio)
    init_proposals(xarxa, IS_TWIN)

    # 6. Arrenca Mininet en un thread de background
    #    (start_network() és bloquejant: crea nodes, links, arrenca OSPF)
    t = threading.Thread(target=xarxa.start_network)
    t.daemon = True
    t.start()
    time.sleep(3)   # espera que Mininet estigui llest (OVS, FRR, etc.)

    # 7. Arrenca el broadcast de mètriques (CPU, RAM, tràfic) via WebSocket
    b = threading.Thread(target=_broadcast_metrics, args=(xarxa,))
    b.daemon = True
    b.start()

    # 8. Pre-escalfa el pool de routers quan la xarxa estigui llesta.
    #    Usa polling de network_ready en lloc d'un sleep fix perquè en HW lent
    #    la xarxa pot trigar més de 3s i un sleep fix fallaria.
    def _start_pool_when_ready():
        while not xarxa.network_ready:
            time.sleep(0.5)
        xarxa.init_router_pool(pool_size=5)   # crea 5 routers pre-escalfats en paral·lel
    threading.Thread(target=_start_pool_when_ready, daemon=True).start()

    # 9. Twin: register with Original and start heartbeat
    if IS_TWIN:
        def _start_twin_registration():
            while not xarxa.network_ready:
                time.sleep(0.5)
            # Arrancar el cliente persistente de WebSocket hacia el puerto 5001 del Original
            sync_module.start_twin_websocket_client(args.original_ip, original_ws_port=5001)
            # Mantener el heartbeat HTTP clásico como sistema de respaldo secundario
            sync_module.start_twin_heartbeat()
        threading.Thread(target=_start_twin_registration, daemon=True).start()

    # 10. Arrenca el ping del canal físic entre PCs (Original ↔ Twins)
    p = threading.Thread(target=_ping_twin_channel)
    p.daemon = True
    p.start()

    # 10. Arrenca el servidor SocketIO al port 5001 en background
    s = threading.Thread(target=_run_socketio_server)
    s.daemon = True
    s.start()

    # 11. Arrenca el servidor HTTP Flask al port 5000 (threaded=True per atendre
    #     múltiples requests simultànies — imprescindible quan l'Original i el Twin
    #     processen operacions concurrents)
    print(' * HTTP server running on port 5000')
    print(' * WebSocket server running on port 5001')
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)