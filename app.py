"""
app.py — Application entry point.

Responsibilities:
  - Parse CLI arguments (--twin flag)
  - Create the Flask app and register all Blueprints
  - Initialise shared state (Xarxa instance, sync module)
  - Start the Mininet network in a background thread
  - Launch the Flask development server
"""

import argparse
import subprocess
import threading
import time

from flask import Flask

from xarxa import Xarxa
import sync as sync_module

# ── CLI arguments ──
parser = argparse.ArgumentParser()
parser.add_argument('--twin', action='store_true', help='Run as Digital Twin')
args, _ = parser.parse_known_args()
IS_TWIN = args.twin

# ── Flask app ──
app = Flask(__name__)

# ── Register Blueprints ──
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

# ── Entry point ──
if __name__ == '__main__':
    subprocess.run(['mn', '-c'], capture_output=True)

    xarxa = Xarxa()

    # Give the sync module access to the live Xarxa object
    sync_module.init_sync(xarxa)

    # Inject the Xarxa instance (and IS_TWIN where needed) into each blueprint
    init_topology(xarxa, IS_TWIN)
    init_nodes(xarxa)
    init_metrics(xarxa)
    init_routing(xarxa)
    init_xrfs(IS_TWIN)
    init_chaos(xarxa)

    # Start Mininet in a background thread so Flask can boot immediately
    t = threading.Thread(target=xarxa.start_network)
    t.daemon = True
    t.start()
    time.sleep(3)

    app.run(host='0.0.0.0', debug=False)
