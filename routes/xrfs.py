"""
routes/xrfs.py — Extended Reality Functions (XRF) management.

XRFs are microservices running in Kubernetes (Minikube) that extend the
Digital Twin with extra analytics. Only available when IS_TWIN=True.

Endpoints:
  GET  /xrf/status         → deployment status of all XRFs
  POST /xrf/deploy         → kubectl apply an XRF
  POST /xrf/undeploy       → kubectl delete an XRF
  POST /xrf/query          → proxy a query to a running XRF's /run endpoint
"""

import os
import subprocess

import requests
from flask import Blueprint, jsonify, request

_IS_TWIN = False

bp = Blueprint('xrfs', __name__)

# ── Kubernetes config ──

KUBECONFIG = os.path.expanduser('~/.kube/config')
if os.environ.get('SUDO_USER'):
    KUBECONFIG = f'/home/{os.environ["SUDO_USER"]}/.kube/config'

try:
    MINIKUBE_IP = subprocess.check_output(
        ['minikube', 'ip'], text=True, stderr=subprocess.DEVNULL
    ).strip()
except Exception:
    MINIKUBE_IP = '192.168.49.2'

XRF_BASE = os.path.dirname(os.path.abspath(__file__))

XRF_REGISTRY = {
    'neighbors': {
        'name':        'Neighbors',
        'description': 'Lists directly connected nodes for each node',
        'yaml':        os.path.join(XRF_BASE, '..', 'XRFs/neighbors/neighbors.yaml'),
        'service':     'xrf-neighbors-svc',
        'deployment':  'xrf-neighbors',
    },
    'traffic': {
        'name':        'Traffic Monitor',
        'description': 'Counts bytes/packets per interface',
        'yaml':        os.path.join(XRF_BASE, '..', 'XRFs/traffic/traffic.yaml'),
        'service':     'xrf-traffic-svc',
        'deployment':  'xrf-traffic',
    },
    'hops': {
        'name':        'Hop Counter',
        'description': 'Calculates hop distance between nodes',
        'yaml':        os.path.join(XRF_BASE, '..', 'XRFs/hops/hops.yaml'),
        'service':     'xrf-hops-svc',
        'deployment':  'xrf-hops',
    },
    'chaos': {
        'name':        'Chaos — Node Failure',
        'description': 'Simulates router failure and measures recovery time',
        'yaml':        os.path.join(XRF_BASE, '..', 'XRFs/chaos/chaos.yaml'),
        'service':     'xrf-chaos-svc',
        'deployment':  'xrf-chaos',
    },
    'latency_matrix': {
        'name':        'Latency Matrix',
        'description': 'Measures latency and bandwidth between all host pairs',
        'yaml':        os.path.join(XRF_BASE, '..', 'XRFs/latency_matrix/latency_matrix.yaml'),
        'service':     'xrf-latency-matrix-svc',
        'deployment':  'xrf-latency-matrix',
    },
}


def init_blueprint(is_twin):
    global _IS_TWIN
    _IS_TWIN = is_twin


def _kubectl(args):
    """Build a kubectl command with the correct kubeconfig."""
    return ['kubectl', f'--kubeconfig={KUBECONFIG}'] + args


def _get_xrf_url(service_name):
    """Return the NodePort URL for a running XRF service, or None."""
    try:
        port = subprocess.check_output(
            _kubectl(['get', 'svc', service_name,
                      '-o', 'jsonpath={.spec.ports[0].nodePort}']),
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        return f'http://{MINIKUBE_IP}:{port}' if port else None
    except Exception:
        return None


def _get_xrf_status(deployment_name):
    """Return 'running' if the deployment has 1 ready replica, else 'stopped'."""
    try:
        result = subprocess.check_output(
            _kubectl(['get', 'deployment', deployment_name,
                      '-o', 'jsonpath={.status.readyReplicas}']),
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        return 'running' if result == '1' else 'stopped'
    except Exception:
        return 'stopped'


# ── Routes ──

@bp.route('/xrf/status')
def xrf_status():
    if not _IS_TWIN:
        return jsonify({'ok': False, 'error': 'Only available on Twin'})
    status = {}
    for xrf_id, xrf in XRF_REGISTRY.items():
        s   = _get_xrf_status(xrf['deployment'])
        url = _get_xrf_url(xrf['service']) if s == 'running' else None
        status[xrf_id] = {
            'name':        xrf['name'],
            'description': xrf['description'],
            'status':      s,
            'url':         url,
        }
    return jsonify({'ok': True, 'xrfs': status})


@bp.route('/xrf/deploy', methods=['POST'])
def xrf_deploy():
    if not _IS_TWIN:
        return jsonify({'ok': False, 'error': 'Only available on Twin'})
    xrf_id = request.json.get('xrf')
    if xrf_id not in XRF_REGISTRY:
        return jsonify({'ok': False, 'error': f'Unknown XRF: {xrf_id}'})
    try:
        subprocess.check_call(
            _kubectl(['apply', '-f', XRF_REGISTRY[xrf_id]['yaml']]),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return jsonify({'ok': True, 'xrf': xrf_id})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


@bp.route('/xrf/undeploy', methods=['POST'])
def xrf_undeploy():
    if not _IS_TWIN:
        return jsonify({'ok': False, 'error': 'Only available on Twin'})
    xrf_id = request.json.get('xrf')
    if xrf_id not in XRF_REGISTRY:
        return jsonify({'ok': False, 'error': f'Unknown XRF: {xrf_id}'})
    try:
        subprocess.check_call(
            _kubectl(['delete', '-f', XRF_REGISTRY[xrf_id]['yaml']]),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return jsonify({'ok': True, 'xrf': xrf_id})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


@bp.route('/xrf/query', methods=['POST'])
def xrf_query():
    if not _IS_TWIN:
        return jsonify({'ok': False, 'error': 'Only available on Twin'})
    xrf_id = request.json.get('xrf')
    params = request.json.get('params', {})
    if xrf_id not in XRF_REGISTRY:
        return jsonify({'ok': False, 'error': f'Unknown XRF: {xrf_id}'})
    url = _get_xrf_url(XRF_REGISTRY[xrf_id]['service'])
    if not url:
        return jsonify({'ok': False, 'error': 'XRF not running'})
    try:
        if xrf_id in ('chaos', 'latency_matrix'):
            resp = requests.post(f'{url}/run', json=params, timeout=120)
        else:
            resp = requests.get(f'{url}/run', params=params, timeout=10)
        return jsonify({'ok': True, 'result': resp.json()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})
