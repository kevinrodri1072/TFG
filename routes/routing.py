"""
routes/routing.py — Routing protocol and route management endpoints.

Endpoints:
  GET  /get_routing_mode  → return current routing mode
  POST /set_routing_mode  → switch protocol (ospf / ospf_bfd / mpls / mpls_bfd / manual)
  GET  /router_routes     → show kernel routing table of a router
  POST /router_routes     → add or delete a static route on a router
  POST /open_wireshark    → launch Wireshark capturing a node interface
"""

import os
import subprocess
import threading

from flask import Blueprint, jsonify, request

from sync import sync_event

_xarxa = None

# ─────────────────────────────────────────────────────────────────────────────
# routing.py — Gestió del protocol de routing
#
# Endpoints:
#   GET  /get_routing_mode → mode actual (ospf/ospf_bfd/mpls/mpls_bfd/manual)
#   POST /set_routing_mode → canvia el protocol a tots els routers
#   GET  /router_routes    → taula de rutes kernel d'un router (ip route show)
#   POST /router_routes    → afegeix o elimina una ruta estàtica
#   POST /open_wireshark   → obre Wireshark capturant una interfície
#
# MODES DE ROUTING:
#   ospf      → OSPF (zebra + ospfd)         ← mode per defecte
#   ospf_bfd  → OSPF + BFD (detecció ràpida de fallades en ms)
#   mpls      → MPLS (zebra + ospfd + ldpd)
#   mpls_bfd  → MPLS + BFD
#   manual    → rutes estàtiques (ip route add), sense daemons FRR
# ─────────────────────────────────────────────────────────────────────────────
bp = Blueprint('routing', __name__)

VALID_MODES = ('ospf', 'ospf_bfd', 'mpls', 'mpls_bfd', 'manual')


def init_blueprint(xarxa_instance):
    global _xarxa
    _xarxa = xarxa_instance


# ── Routes ──

@bp.route('/get_routing_mode')
def get_routing_mode():
    return jsonify({'ok': True, 'mode': _xarxa.routing_mode})


@bp.route('/set_routing_mode', methods=['POST'])
# Canvia el mode de routing a TOTS els routers.
# Para els daemons actuals (_stop_routing) i arrenca els nous (_apply_routing).
#
# PARAL·LELITZACIÓ: un thread per router, tots corren simultàniament.
# Cada node té el seu propi shell bash → no hi ha concurrència al node.cmd().
# Amb N routers: temps = max(per-router) en lloc de N × per-router.
def set_routing_mode():
    mode = request.json.get('mode')
    if mode not in VALID_MODES:
        return jsonify({'ok': False, 'error': f'Unknown mode: {mode}'})

    is_sync = request.json.get('sync', False)
    _xarxa.routing_mode = mode

    # Snapshot routers before starting threads to avoid dict-changed-during-iteration.
    routers = [
        (name, props, _xarxa.mininet_nodes[name])
        for name, props in _xarxa.nodes.items()
        if props['type'] == 'router' and name in _xarxa.mininet_nodes
    ]

    def _restart_one(name, props, node):
        """Stop + restart routing on a single router (sequential per node)."""
        _xarxa._stop_routing(node, name)
        _xarxa._apply_routing(node, name, props)

    # Run one thread per router — each node has its own shell so this is safe.
    # With N routers this runs in ~max(per-router time) instead of N × per-router.
    threads = [
        threading.Thread(target=_restart_one, args=(n, p, nd), daemon=True)
        for n, p, nd in routers
    ]
    for t in threads: t.start()
    for t in threads: t.join()

    if not is_sync:
        sync_event('/set_routing_mode', {'mode': mode}, 0)
    return jsonify({'ok': True, 'mode': mode})


@bp.route('/router_routes')
# Retorna la taula de rutes del kernel d'un router via "ip route show".
# Inclou rutes OSPF apreses, rutes directament connectades i rutes estàtiques.
def get_router_routes():
    router = request.args.get('router')
    if not router or router not in _xarxa.nodes:
        return jsonify({'ok': False, 'error': 'Router not found'})
    if _xarxa.nodes[router]['type'] != 'router':
        return jsonify({'ok': False, 'error': f'{router} is not a router'})
    if not _xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})

    node   = _xarxa.mininet_nodes[router]
    raw    = node.cmd('ip route show')
    routes = []
    for line in raw.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        dst   = parts[0]
        via   = parts[parts.index('via') + 1] if 'via' in parts else None
        routes.append({'dst': dst, 'via': via, 'raw': line})
    return jsonify({'ok': True, 'router': router, 'routes': routes})


@bp.route('/router_routes', methods=['POST'])
# Afegeix (action='add') o elimina (action='delete') una ruta estàtica.
# Usa 'ip route replace' per add (idempotent) i 'ip route del' per delete.
# Actualitza _xarxa.nodes[router]['routes'] per mantenir l'estat Python consistent.
def modify_router_route():
    data   = request.json
    router = data.get('router')
    action = data.get('action')  # 'add' or 'delete'

    if not router or router not in _xarxa.nodes:
        return jsonify({'ok': False, 'error': 'Router not found'})
    if _xarxa.nodes[router]['type'] != 'router':
        return jsonify({'ok': False, 'error': f'{router} is not a router'})
    if not _xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})

    node = _xarxa.mininet_nodes[router]

    if action == 'add':
        dst = data.get('dst', '').strip()
        via = data.get('via', '').strip()
        if not dst or not via:
            return jsonify({'ok': False, 'error': 'dst and via are required'})
        result = node.cmd(f'ip route replace {dst} via {via} 2>&1')
        if 'error' in result.lower() or 'invalid' in result.lower():
            return jsonify({'ok': False, 'error': result.strip()})
        route_str = f'{dst} via {via}'
        if route_str not in _xarxa.nodes[router].get('routes', []):
            _xarxa.nodes[router].setdefault('routes', []).append(route_str)
        return jsonify({'ok': True})

    elif action == 'delete':
        dst = data.get('dst', '').strip()
        if not dst:
            return jsonify({'ok': False, 'error': 'dst is required'})
        result = node.cmd(f'ip route del {dst} 2>&1')
        if 'error' in result.lower() or 'no such' in result.lower():
            return jsonify({'ok': False, 'error': result.strip()})
        _xarxa.nodes[router]['routes'] = [
            r for r in _xarxa.nodes[router].get('routes', [])
            if not r.startswith(dst)
        ]
        return jsonify({'ok': True})

    return jsonify({'ok': False, 'error': f'Unknown action: {action}'})


@bp.route('/open_wireshark', methods=['POST'])
# Obre Wireshark capturant una interfície d'un node Mininet.
# Usa mnexec per entrar al namespace del node i tcpdump per capturar.
# Fa streaming via pipe al Wireshark del desktop (DISPLAY necessari).
def open_wireshark():
    data = request.json
    node = data.get('node')
    intf = data.get('intf')

    if not node or node not in _xarxa.nodes:
        return jsonify({'ok': False, 'error': 'Node not found'})
    if not _xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})

    intf_full = f'{node}-{intf}'
    sudo_user = os.environ.get('SUDO_USER', 'root')
    display   = os.environ.get('DISPLAY', ':0')

    pid_out = subprocess.check_output(
        ['pgrep', '-f', f'mininet:{node}'], text=True
    ).strip().split('\n')[0]

    if not pid_out:
        return jsonify({'ok': False, 'error': f'Cannot find process for {node}'})

    try:
        cmd = (
            f'DISPLAY={display} '
            f'mnexec -a {pid_out} '
            f'tcpdump -i {intf_full} -U -w - 2>/dev/null | '
            f'sudo -u {sudo_user} DISPLAY={display} wireshark -k -i - &'
        )
        subprocess.Popen(['bash', '-c', cmd],
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return jsonify({'ok': True, 'intf': intf_full})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})