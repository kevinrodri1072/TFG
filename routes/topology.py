"""
routes/topology.py — Topology read/write endpoints.

Endpoints:
  GET  /          → serve index.html
  GET  /topology  → nodes + links (switches hidden, router↔host direct links)
  GET  /matrix    → raw adjacency matrix + node names
  GET  /export    → download network as .mat file
  POST /load_network → load topology from JSON body or uploaded .mat file
  GET  /is_twin   → whether this instance is the Digital Twin
"""

import io
import json
import threading
import time

import numpy as np
from flask import Blueprint, jsonify, render_template, request, send_file
from scipy.io import loadmat, savemat

from sync import sync_in_background
from routes.nodes import _valid_node_name

# Injected by app.py via init_blueprint()
_xarxa    = None
_IS_TWIN  = False

TYPE_TO_NUM = {0: 0, 'host': 1, 'router': 2, 'switch': 3}
NUM_TO_TYPE = {0: 0, 1: 'host', 2: 'router', 3: 'switch'}

# ─────────────────────────────────────────────────────────────────────────────
# topology.py — Endpoints de lectura i gestió de la topologia
#
# Endpoints:
#   GET  /topology     → topologia completa (nodes + links) per al dashboard vis.js
#   GET  /matrix       → matriu d'adjacència NxN (per "View Matrix")
#   GET  /export       → exporta estat complet en JSON (per "Save Network")
#   POST /load_network → carrega un estat des de JSON (per "Load Network")
#
# SNAPSHOT CONSISTENT:
#   /topology fa un snapshot de _xarxa.nodes abans de calcular els links.
#   D'aquesta manera, si un remove_node concurrent modifica nodes mentre
#   calculem els links, usem el mateix snapshot per a tots dos — no hi ha
#   inconsistències entre la llista de nodes i la llista de links.
# ─────────────────────────────────────────────────────────────────────────────
bp = Blueprint('topology', __name__)


def init_blueprint(xarxa_instance, is_twin):
    global _xarxa, _IS_TWIN
    _xarxa   = xarxa_instance
    _IS_TWIN = is_twin


# ── Routes ──

@bp.route('/')
def index():
    return render_template('index.html')


@bp.route('/topology')
# Retorna la topologia completa per al dashboard.
# Genera la llista de links recorrent la matriu d'adjacència.
# Usa nodes_snap per garantir que nodes i links siguin consistents.
def topology():
    # Snapshot to avoid race conditions with pool/remove operations
    nodes_snap = dict(_xarxa.nodes)
    matrix_snap = [row[:] for row in _xarxa.network_matrix]
    node_names = list(nodes_snap.keys())
    # Safety: ensure matrix size matches node count
    if len(matrix_snap) != len(node_names):
        return jsonify({'nodes': {}, 'links': []})
    links = []

    # Direct router↔router links (skip switches)
    for i in range(len(matrix_snap)):
        for j in range(i + 1, len(matrix_snap[i])):
            if matrix_snap[i][j] != 0:
                node_i = node_names[i]
                node_j = node_names[j]
                if (nodes_snap[node_i]['type'] == 'switch' or
                        nodes_snap[node_j]['type'] == 'switch'):
                    continue
                links.append({'from': node_i, 'to': node_j})

    # Replace switch with direct router↔host links for the frontend graph
    for switch_name, props in nodes_snap.items():
        if props['type'] != 'switch':
            continue
        switch_idx    = node_names.index(switch_name)
        if switch_idx >= len(matrix_snap): continue
        router, hosts = None, []
        for i, val in enumerate(matrix_snap[switch_idx]):
            if val != 0:
                node = node_names[i]
                if nodes_snap[node]['type'] == 'router':
                    router = node
                elif nodes_snap[node]['type'] == 'host':
                    hosts.append(node)
        if router:
            for host in hosts:
                links.append({'from': router, 'to': host})

    return jsonify({'nodes': nodes_snap, 'links': links})


@bp.route('/matrix')
# Retorna la matriu d'adjacència per al popup "View Matrix".
# Snapshot atòmic de names + matrix per evitar inconsistències de mides.
def matrix():
    # Snapshot both together so names and matrix are always consistent
    names  = list(_xarxa.nodes.keys())
    matrix = [row[:] for row in _xarxa.network_matrix]
    if len(matrix) != len(names):
        return jsonify({'names': [], 'matrix': []})
    return jsonify({'names': names, 'matrix': matrix})


@bp.route('/export')
def export():
    matrix_num = np.array(
        [[TYPE_TO_NUM[cell] for cell in row] for row in _xarxa.network_matrix],
        dtype=np.int32,
    )
    node_names = list(_xarxa.nodes.keys())
    nodes_json = json.dumps(_xarxa.nodes)
    buffer     = io.BytesIO()
    savemat(buffer, {
        'matrix':     matrix_num,
        'node_names': np.array(node_names, dtype=object),
        'nodes_json': nodes_json,
    })
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype='application/octet-stream',
        as_attachment=True,
        download_name='network.mat',
    )


@bp.route('/load_network', methods=['POST'])
# Carrega un estat complet des de JSON i reconstrueix la xarxa.
# Si és una sincronització del Twin (sync=True), aplica directament.
# Si és l'usuari (sync=False), fa restart_network() i sincronitza al Twin.
def load_network():
    if request.is_json:
        data       = request.get_json()
        is_sync    = data.get('sync', False)
        new_matrix = data['matrix']
        new_nodes  = data['nodes']
    else:
        is_sync = False
        file    = request.files.get('file')
        if not file:
            return jsonify({'ok': False, 'error': 'No file received'})
        buffer     = io.BytesIO(file.read())
        mat        = loadmat(buffer)
        matrix_num = mat['matrix'].tolist()
        new_matrix = [[NUM_TO_TYPE[int(cell)] for cell in row] for row in matrix_num]
        nodes_json = (
            str(mat['nodes_json'][0])
            if isinstance(mat['nodes_json'], np.ndarray)
            else mat['nodes_json']
        )
        new_nodes = json.loads(nodes_json)

    # ── Validació del snapshot abans d'aplicar res ──
    # Els noms de node acaben dins comandes de shell a start_network()
    # (ifconfig {name}-eth0, etc.). add_host/add_router ja validen amb
    # _valid_node_name, però aquí els noms venen d'un JSON o d'un .mat
    # pujat per l'usuari — sense aquesta guarda, un .mat manipulat amb un
    # nom tipus "h1; rm -rf /" s'executaria com a root al restart.
    invalid = [n for n in new_nodes if not _valid_node_name(n)]
    if invalid:
        return jsonify({'ok': False,
                        'error': f'Invalid node name(s): {", ".join(invalid)}'})
    if len(new_matrix) != len(new_nodes) or \
            any(len(row) != len(new_nodes) for row in new_matrix):
        return jsonify({'ok': False,
                        'error': 'Matrix dimensions do not match node count'})

    # Mesura el temps real del restart local i el comparteix amb la
    # sincronització via un holder (mateix patró que add_host/add_router):
    # el thread de restart senyala 'ready' quan acaba i deixa el temps a 'value'.
    holder = {'value': None, 'ready': threading.Event()}

    def _timed_restart():
        t0 = time.time()
        _xarxa.restart_network(new_matrix, new_nodes)
        holder['value'] = round((time.time() - t0) * 1000, 2)
        holder['ready'].set()

    threading.Thread(target=_timed_restart).start()

    if not is_sync:
        sync_in_background('load_network', holder)

    return jsonify({'ok': True})

@bp.route('/xrfs')
def xrfs_page():
    return render_template('xrfs.html')

@bp.route('/network_snapshot')
def network_snapshot():
    """
    Retorna l'estat complet de la xarxa (nodes + matriu + routing_mode).
    Usat pels Twins a l'arrencada per sincronitzar-se amb l'estat actual
    de l'Original ABANS d'arrencar Mininet, evitant que un Twin nou
    comenci amb la topologia per defecte quan l'Original ja ha canviat.
    """
    nodes_snap  = dict(_xarxa.nodes)
    matrix_snap = [row[:] for row in _xarxa.network_matrix]
    return jsonify({
        'nodes':        nodes_snap,
        'matrix':       matrix_snap,
        'routing_mode': _xarxa.routing_mode,
    })


@bp.route('/is_twin')
def is_twin():
    return jsonify({'is_twin': _IS_TWIN})