"""
routes/chaos.py — Chaos engineering: simulate router failures.

Endpoints:
  POST /chaos/node_down → bring all interfaces of a router down
  POST /chaos/node_up   → bring all interfaces of a router back up

Used to measure OSPF (and OSPF+BFD) convergence time after a failure.
"""

from flask import Blueprint, jsonify, request

_xarxa = None

bp = Blueprint('chaos', __name__)


def init_blueprint(xarxa_instance):
    global _xarxa
    _xarxa = xarxa_instance


def _set_router_interfaces(node_name, state):
    """Bring all non-LAN interfaces of a router up or down."""
    mn_node = _xarxa.mininet_nodes[node_name]
    props   = _xarxa.nodes[node_name]
    for intf in props['ips']:
        if intf == 'lan':
            continue
        mn_node.cmd(f'ip link set {node_name}-{intf} {state}')


def _validate_router(node_name):
    """Return an error JSON string if the node is invalid, else None."""
    if not node_name or node_name not in _xarxa.nodes:
        return jsonify({'ok': False, 'error': 'Node not found'})
    if _xarxa.nodes[node_name]['type'] != 'router':
        return jsonify({'ok': False, 'error': 'Only routers supported'})
    return None


# ── Routes ──

@bp.route('/chaos/node_down', methods=['POST'])
def chaos_node_down():
    if not _xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})
    node  = request.json.get('node')
    error = _validate_router(node)
    if error:
        return error
    _set_router_interfaces(node, 'down')
    return jsonify({'ok': True, 'node': node, 'action': 'down'})


@bp.route('/chaos/node_up', methods=['POST'])
def chaos_node_up():
    if not _xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})
    node  = request.json.get('node')
    error = _validate_router(node)
    if error:
        return error
    _set_router_interfaces(node, 'up')
    return jsonify({'ok': True, 'node': node, 'action': 'up'})
