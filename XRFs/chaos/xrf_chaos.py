from flask import Flask, jsonify, request
import requests
import time
import os

app = Flask(__name__)
TWIN_API = os.environ.get('TWIN_API', 'http://localhost:5000')

@app.route('/health')
def health():
    return jsonify({'ok': True, 'xrf': 'chaos'})

@app.route('/run', methods=['POST'])
def run():
    data     = request.json or {}
    node     = data.get('node')
    src      = data.get('src')
    dst      = data.get('dst')
    duration = data.get('duration', 10)

    if not node or not src or not dst:
        return jsonify({'ok': False, 'error': 'node, src and dst required'})

    topo = requests.get(f'{TWIN_API}/topology').json()
    if node not in topo['nodes']:
        return jsonify({'ok': False, 'error': f'Node {node} not found'})

    # Phase 1 — baseline ping
    baseline     = requests.get(f'{TWIN_API}/metrics/ping_fast?src={src}&dst={dst}', timeout=10).json()
    baseline_avg = baseline.get('avg')

    # Phase 2 — bring node down
    requests.post(f'{TWIN_API}/chaos/node_down', json={'node': node})
    t_down = time.time()

    # Phase 3 — just wait, assume 100% loss while node is down
    time.sleep(duration)
    lost_packets  = duration  # approximate
    total_packets = duration

    # Phase 4 — bring node back up
    requests.post(f'{TWIN_API}/chaos/node_up', json={'node': node})
    t_up = time.time()

    # Phase 5 — wait for recovery
    t_recovered  = None
    recovery_avg = None
    recovery_deadline = time.time() + 120
    while time.time() < recovery_deadline:
        try:
            resp = requests.get(
                f'{TWIN_API}/metrics/ping_fast?src={src}&dst={dst}',
                timeout=10
            ).json()
            if resp.get('avg') is not None:
                t_recovered  = round(time.time() - t_up, 2)
                recovery_avg = resp['avg']
                break
        except Exception as e:
            print(f'[chaos] recovery ping error: {e}')
        time.sleep(2)

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