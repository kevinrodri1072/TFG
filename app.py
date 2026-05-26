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

import psutil
from flask import Flask
from flask_socketio import SocketIO

from xarxa import Xarxa
import sync as sync_module

# ── CLI arguments ──
parser = argparse.ArgumentParser()
parser.add_argument('--twin', action='store_true', help='Run as Digital Twin')
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
                for name, props in xarxa.nodes.items():
                    if props['type'] != 'router':
                        continue
                    mn_node = xarxa.mininet_nodes.get(name)
                    if not mn_node or mn_node.shell is None or mn_node.waiting:
                        continue
                    try:
                        raw = mn_node.cmd('cat /proc/net/dev')
                    except Exception:
                        continue
                    for line in raw.strip().split('\n')[2:]:
                        parts = line.split(':')
                        if len(parts) < 2:
                            continue
                        intf = parts[0].strip()
                        if intf == 'lo':
                            continue
                        values = parts[1].split()
                        links[f'{name}-{intf}'] = {
                            'node':     name,
                            'intf':     intf,
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

    sync_module.init_sync(xarxa)

    init_topology(xarxa, IS_TWIN)
    init_nodes(xarxa)
    init_metrics(xarxa, socketio)
    init_routing(xarxa)
    init_xrfs(IS_TWIN, socketio)
    init_chaos(xarxa)

    # Start Mininet
    t = threading.Thread(target=xarxa.start_network)
    t.daemon = True
    t.start()
    time.sleep(3)

    # Start WebSocket metrics broadcast thread
    b = threading.Thread(target=_broadcast_metrics, args=(xarxa,))
    b.daemon = True
    b.start()

    # Start SocketIO server on port 5001 in background thread
    s = threading.Thread(target=_run_socketio_server)
    s.daemon = True
    s.start()

    # Start main HTTP server on port 5000 (threaded for concurrency)
    print(' * HTTP server running on port 5000')
    print(' * WebSocket server running on port 5001')
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)