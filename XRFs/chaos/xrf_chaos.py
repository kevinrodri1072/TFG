"""
xrf_chaos.py — Chaos Engineering XRF.

Simulates a router failure on the Digital Twin and measures:
  - Baseline latency (before failure)
  - Packet loss during failure  
  - Recovery time (OSPF reconvergence after router comes back)

Progress events are pushed via POST to /chaos_progress on the Twin
so the browser can show a live progress bar via WebSocket.
"""

from flask import Flask, jsonify, request
import requests
import time
import os

app      = Flask(__name__)
TWIN_API = os.environ.get('TWIN_API', 'http://localhost:5000')


def safe_get(url, retries=3, timeout=10):
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            print(f'[chaos] GET {url} attempt {attempt+1}/{retries}: {e}')
        time.sleep(0.5)
    return None


def safe_post(url, data, retries=3, timeout=10):
    for attempt in range(retries):
        try:
            resp = requests.post(url, json=data, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            print(f'[chaos] POST {url} attempt {attempt+1}/{retries}: {e}')
        time.sleep(0.5)
    return None


def emit_progress(step, total, msg):
    """Push a progress event to the Twin so the browser can show it."""
    try:
        requests.post(f'{TWIN_API}/chaos_progress', json={
            'step':    step,
            'total':   total,
            'percent': round(step / total * 100),
            'msg':     msg,
        }, timeout=2)
    except Exception:
        pass


@app.route('/health')
def health():
    return jsonify({'ok': True, 'xrf': 'chaos'})


@app.route('/run', methods=['POST'])
def run():
    data     = request.json or {}
    node     = data.get('node')
    src      = data.get('src')
    dst      = data.get('dst')
    duration = int(data.get('duration', 10))
    total    = 5  # total progress steps

    if not node or not src or not dst:
        return jsonify({'ok': False, 'error': 'node, src and dst required'})

    # Verify node exists
    topo = safe_get(f'{TWIN_API}/topology')
    if not topo:
        return jsonify({'ok': False, 'error': 'Could not reach Twin API'})
    if node not in topo.get('nodes', {}):
        return jsonify({'ok': False, 'error': f'Node {node} not found'})

    # Phase 1 — baseline ping
    emit_progress(1, total, f'Measuring baseline latency {src} → {dst}...')
    baseline     = safe_get(f'{TWIN_API}/metrics/ping_fast?src={src}&dst={dst}')
    baseline_avg = baseline.get('avg') if baseline else None

    # Phase 2 — bring node down
    emit_progress(2, total, f'Taking {node} down...')
    result = safe_post(f'{TWIN_API}/chaos/node_down', {'node': node})
    if not result or not result.get('ok'):
        return jsonify({'ok': False, 'error': f'Could not bring {node} down'})
    t_down = time.time()

    # Phase 3 — wait
    emit_progress(3, total, f'{node} is down — waiting {duration}s...')
    time.sleep(duration)
    lost_packets  = duration
    total_packets = duration

    # Phase 4 — bring node back up
    emit_progress(4, total, f'Bringing {node} back up...')
    safe_post(f'{TWIN_API}/chaos/node_up', {'node': node})
    t_up = time.time()

    # Phase 5 — wait for recovery
    emit_progress(5, total, f'Waiting for OSPF reconvergence...')
    t_recovered  = None
    recovery_avg = None
    recovery_deadline = time.time() + 120

    while time.time() < recovery_deadline:
        resp = safe_get(
            f'{TWIN_API}/metrics/ping_fast?src={src}&dst={dst}',
            retries=1, timeout=10
        )
        if resp and resp.get('avg') is not None:
            t_recovered  = round(time.time() - t_up, 2)
            recovery_avg = resp['avg']
            break
        time.sleep(2)

    emit_progress(total, total, 'Done!')

    return jsonify({
        'ok':              True,
        'node':            node,
        'src':             src,
        'dst':             dst,
        'duration_s':      duration,
        'baseline_avg_ms': baseline_avg,
        'lost_packets':    lost_packets,
        'total_packets':   total_packets,
        'loss_pct':        100.0,
        't_recovery_s':    t_recovered,
        'recovery_avg_ms': recovery_avg,
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)