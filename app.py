"""
app.py — Application entry point.

Two servers run simultaneously:
  - Port 5000: Flask HTTP server (threaded=True) for all API endpoints
  - Port 5001: Flask-SocketIO server for real-time WebSocket metrics
"""

import argparse
import subprocess
import threading
import time
import os

import psutil
from flask import Flask
from flask_socketio import SocketIO

from xarxa import Xarxa
import sync as sync_module

# ── CLI arguments ──
parser = argparse.ArgumentParser(
    description='Digital Twin Network',
    formatter_class=argparse.RawTextHelpFormatter,
)
parser.add_argument('--twin', action='store_true',
                    help='Run this instance as a Digital Twin')
parser.add_argument('--twins', nargs='+', metavar='IP[:PORT]',
                    help=(
                        'IPs of ALL Twin PCs (space-separated).\n'
                        'Examples:\n'
                        '  --twins 10.4.39.110              (one twin, default port 5000)\n'
                        '  --twins 10.4.39.110 10.4.39.120  (two twins)\n'
                        '  --twins 10.4.39.110:5000 10.4.39.120:5001  (custom ports)\n'
                    ))
parser.add_argument('--twin-ip', default=None, metavar='IP',
                    help='Single Twin IP — shortcut for --twins with one IP (legacy)')
parser.add_argument('--original-ip', default='10.4.39.102', metavar='IP',
                    help='IP of the Original PC (default: 10.4.39.102)')
parser.add_argument('--twin-port', default=5000, type=int, metavar='PORT',
                    help='Default port for all Twins (default: 5000)')
args, _ = parser.parse_known_args()
IS_TWIN = args.twin

# ── Two Flask apps ──
# Main app: handles all HTTP API endpoints (port 5000)
app = Flask(__name__)
app.config['PROPAGATE_EXCEPTIONS'] = True

# SocketIO app: handles WebSocket metrics only (port 5001)
metrics_app = Flask('metrics_ws')
socketio    = SocketIO(metrics_app, cors_allowed_origins='*',
                       async_mode='threading')

# ── Register Blueprints on main app ──
from routes.topology import bp as topology_bp, init_blueprint as init_topology
from routes.nodes    import bp as nodes_bp,    init_blueprint as init_nodes
from routes.metrics  import bp as metrics_bp,  init_blueprint as init_metrics
from routes.routing  import bp as routing_bp,  init_blueprint as init_routing
from routes.xrfs     import bp as xrfs_bp,     init_blueprint as init_xrfs
from routes.chaos    import bp as chaos_bp,    init_blueprint as init_chaos

app.register_blueprint(topology_bp)
app.register_blueprint(nodes_bp)
app.register_blueprint(metrics_bp)
app.register_blueprint(routing_bp)
app.register_blueprint(xrfs_bp)
app.register_blueprint(chaos_bp)


# ── WebSocket metrics broadcast ──

def _ping_one_target(target_ip):
    """Ping a single IP and return the WebSocket payload dict."""
    from utils import parse_ping
    try:
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
            'reachable':   latency['avg'] is not None,
            'target':      target_ip,
        }
    except Exception:
        return {
            'latency_min': None, 'latency_avg': None,
            'latency_max': None, 'jitter': None,
            'reachable': False, 'target': target_ip,
        }


def _ping_twin_channel():
    """
    Ping peer PCs every 5s and emit results via WebSocket.
    - Original: pings ALL Twins (one emit per Twin, tagged by IP).
    - Twin: pings the Original.
    Shows physical channel latency for each link in the dashboard.
    """
    from sync import TWINS, ORIGINAL_IP

    # Wait for WebSocket to be ready before first ping
    time.sleep(2)
    while True:
        if IS_TWIN:
            # Twin pings the Original only
            payload = _ping_one_target(ORIGINAL_IP)
            socketio.emit('twin_channel_ping', payload)
        else:
            # Original pings ALL Twins in parallel
            results = [None] * len(TWINS)

            def _do_ping(idx, twin):
                results[idx] = _ping_one_target(twin['ip'])

            threads = [
                threading.Thread(target=_do_ping, args=(i, t), daemon=True)
                for i, t in enumerate(TWINS)
            ]
            for th in threads: th.start()
            for th in threads: th.join()

            for payload in results:
                if payload:
                    socketio.emit('twin_channel_ping', payload)
        time.sleep(5)


def _read_net_dev(pid):
    """
    Read /proc/{pid}/net/dev directly from the host filesystem.
    Returns the file content as a string, or None on error.

    This avoids mn_node.cmd() entirely — no shell interaction, no thread-safety
    issues, no competition with Mininet operations. Each Mininet node runs in
    its own network namespace; /proc/{pid}/net/dev reflects that namespace's
    interfaces without any subprocess overhead.
    """
    try:
        with open(f'/proc/{pid}/net/dev', 'r') as f:
            return f.read()
    except OSError:
        return None


def _broadcast_metrics(xarxa):
    """Push CPU/RAM and link traffic via WebSocket every 500ms."""
    link_tick = 0
    while True:
        try:
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory()
            socketio.emit('metrics_system', {
                'cpu_percent':  cpu,
                'ram_percent':  ram.percent,
                'ram_used_mb':  round(ram.used  / 1024 / 1024, 1),
                'ram_total_mb': round(ram.total / 1024 / 1024, 1),
            })

            link_tick += 1
            if link_tick >= 2 and xarxa.network_ready:
                link_tick = 0
                links = {}
                # Snapshot to avoid dict-changed-during-iteration errors.
                # No topology_lock needed — stale metrics for one tick are fine.
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
                    raw = _read_net_dev(pid)
                    if not raw:
                        continue
                    for line in raw.strip().split('\n')[2:]:
                        parts = line.split(':')
                        if len(parts) < 2:
                            continue
                        intf = parts[0].strip()
                        if intf == 'lo':
                            continue
                        values = parts[1].split()
                        if len(values) < 9:
                            continue
                        intf_short = intf[len(name)+1:] if intf.startswith(name + '-') else intf
                        links[f'{name}-{intf_short}'] = {
                            'node':     name,
                            'intf':     intf_short,
                            'rx_bytes': int(values[0]),
                            'tx_bytes': int(values[8]),
                        }
                socketio.emit('metrics_link_traffic', {'links': links})

        except Exception as e:
            print(f'[broadcast] error: {e}')

        time.sleep(0.5)


def _run_socketio_server():
    """Run the SocketIO server on port 5001 in a background thread."""
    socketio.run(metrics_app, host='0.0.0.0', port=5001, debug=False,
                 use_reloader=False, allow_unsafe_werkzeug=True)


# ── Entry point ──
if __name__ == '__main__':
    subprocess.run(['mn', '-c'], capture_output=True)

    xarxa = Xarxa()

    # Kill any leftover FRR daemons from previous runs.
    # mn -c kills Mininet bash shells but NOT FRR daemons. Stale daemons
    # keep their namespace alive and hold PID file locks, preventing new
    # daemons from starting (causing the "Could not lock pid_file" error).
    import glob, shutil
    for frr_dir in glob.glob('/tmp/frr_*'):
        for pidfile in ['zebra.pid', 'ospfd.pid', 'ldpd.pid', 'bfdd.pid']:
            pidpath = f'{frr_dir}/{pidfile}'
            if os.path.exists(pidpath):
                try:
                    with open(pidpath) as _pf:
                        _pid = int(_pf.read().strip())
                    os.kill(_pid, 9)  # SIGKILL — no grace period needed
                except Exception:
                    pass
        shutil.rmtree(frr_dir, ignore_errors=True)

    sync_module.init_sync(xarxa,
                       twins=args.twins,
                       twin_ip=args.twin_ip,
                       original_ip=args.original_ip,
                       twin_port=args.twin_port)

    init_topology(xarxa, IS_TWIN)
    init_nodes(xarxa)
    init_metrics(xarxa, socketio)
    init_routing(xarxa)
    init_xrfs(IS_TWIN, socketio)
    init_chaos(xarxa, socketio)

    # Start Mininet
    t = threading.Thread(target=xarxa.start_network)
    t.daemon = True
    t.start()
    time.sleep(3)

    # Start WebSocket metrics broadcast thread
    b = threading.Thread(target=_broadcast_metrics, args=(xarxa,))
    b.daemon = True
    b.start()

    # Pre-warm router pool — wait for network_ready before creating pool nodes.
    # A fixed sleep(3) is fragile; polling network_ready is robust on any HW.
    def _start_pool_when_ready():
        while not xarxa.network_ready:
            time.sleep(0.5)
        xarxa.init_router_pool(pool_size=5)
    threading.Thread(target=_start_pool_when_ready, daemon=True).start()

    # Start physical channel ping (runs on both Original and Twin)
    p = threading.Thread(target=_ping_twin_channel)
    p.daemon = True
    p.start()

    # Start SocketIO server on port 5001 in background thread
    s = threading.Thread(target=_run_socketio_server)
    s.daemon = True
    s.start()

    # Start main HTTP server on port 5000 (threaded for concurrency)
    print(' * HTTP server running on port 5000')
    print(' * WebSocket server running on port 5001')
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)