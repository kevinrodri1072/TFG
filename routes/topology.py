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

import numpy as np
from flask import Blueprint, jsonify, render_template, request, send_file
from scipy.io import loadmat, savemat

from sync import sync_in_background

# Injected by app.py via init_blueprint()
_xarxa    = None
_IS_TWIN  = False

TYPE_TO_NUM = {0: 0, 'host': 1, 'router': 2, 'switch': 3}
NUM_TO_TYPE = {0: 0, 1: 'host', 2: 'router', 3: 'switch'}

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

    return jsonify({'nodes': _xarxa.nodes, 'links': links})


@bp.route('/matrix')
def matrix():
    names = list(_xarxa.nodes.keys())
    return jsonify({'names': names, 'matrix': _xarxa.network_matrix})


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

    threading.Thread(
        target=_xarxa.restart_network, args=(new_matrix, new_nodes)
    ).start()

    if not is_sync:
        sync_in_background('load_network', 0)

    return jsonify({'ok': True})

@bp.route('/xrfs')
def xrfs_page():
    return render_template('xrfs.html')

@bp.route('/is_twin')
def is_twin():
    return jsonify({'is_twin': _IS_TWIN})