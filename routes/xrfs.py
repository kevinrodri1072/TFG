"""
routes/xrfs.py — Extended Reality Functions (XRF) management.
"""

import os
import subprocess
import threading
import uuid

import requests
from flask import Blueprint, jsonify, request

_IS_TWIN  = False
_socketio = None

bp = Blueprint('xrfs', __name__)

# ── Job store for async XRF results ──
_jobs      = {}
_jobs_lock = threading.Lock()
import time as _time

def _cleanup_old_jobs():
    """Remove jobs older than 10 minutes that were never polled."""
    now = _time.time()
    with _jobs_lock:
        expired = [jid for jid, j in _jobs.items()
                   if j.get('created_at', now) < now - 600]
        for jid in expired:
            _jobs.pop(jid, None)

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

ASYNC_XRFS = {'chaos', 'latency_matrix'}


def init_blueprint(is_twin, socketio_instance=None):
    global _IS_TWIN, _socketio
    _IS_TWIN  = is_twin
    _socketio = socketio_instance


def _kubectl(args):
    return ['kubectl', f'--kubeconfig={KUBECONFIG}'] + args


def _get_xrf_url(service_name):
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
    try:
        result = subprocess.check_output(
            _kubectl(['get', 'deployment', deployment_name,
                      '-o', 'jsonpath={.status.readyReplicas}']),
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        return 'running' if result == '1' else 'stopped'
    except Exception:
        return 'stopped'


def _run_xrf_job(job_id, xrf_id, url, params):
    """Run XRF in background thread, store result in _jobs dict."""
    try:
        resp = requests.post(f'{url}/run', json=params, timeout=180)
        result = resp.json()
        with _jobs_lock:
            _jobs[job_id] = {'ready': True, 'ok': True, 'result': result}
    except Exception as e:
        with _jobs_lock:
            _jobs[job_id] = {'ready': True, 'ok': False, 'error': str(e)}


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

    if xrf_id in ASYNC_XRFS:
        job_id = str(uuid.uuid4())[:8]
        with _jobs_lock:
            _jobs[job_id] = {'ready': False, 'created_at': _time.time()}
        _cleanup_old_jobs()
        threading.Thread(
            target=_run_xrf_job,
            args=(job_id, xrf_id, url, params),
            daemon=True,
        ).start()
        return jsonify({'ok': True, 'async': True, 'job_id': job_id})
    else:
        try:
            resp = requests.get(f'{url}/run', params=params, timeout=10)
            return jsonify({'ok': True, 'result': resp.json()})
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)})


@bp.route('/xrf/result/<job_id>')
def xrf_result(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return jsonify({'ok': False, 'error': 'Job not found'})
    if not job['ready']:
        return jsonify({'ok': True, 'ready': False})
    with _jobs_lock:
        _jobs.pop(job_id, None)
    if job['ok']:
        return jsonify({'ok': True, 'ready': True, 'result': job['result']})
    return jsonify({'ok': False, 'ready': True, 'error': job.get('error', 'Unknown error')})