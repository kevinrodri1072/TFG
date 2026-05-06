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
    baseline     = requests.get(f'{TWIN_API}/metrics/ping_fast?src={src}&dst={dst}').json()
    baseline_avg = baseline.get('avg')

    # Phase 2 — bring node down
    requests.post(f'{TWIN_API}/chaos/node_down', json={'node': node})
    t_down = time.time()

    # Phase 3 — measure pings while node is down
    lost_packets  = 0
    total_packets = 0

    deadline = time.time() + duration
    while time.time() < deadline:
        try:
            resp = requests.get(
                f'{TWIN_API}/metrics/ping_fast?src={src}&dst={dst}',
                timeout=3
            ).json()
            total_packets += 1
            if resp.get('avg') is None:
                lost_packets += 1
        except:
            lost_packets += 1
            total_packets += 1

    # Phase 4 — bring node back up
    requests.post(f'{TWIN_API}/chaos/node_up', json={'node': node})
    t_up = time.time()

    # Phase 5 — wait for recovery
    t_recovered  = None
    recovery_avg = None
    recovery_deadline = time.time() + 60
    while time.time() < recovery_deadline:
        try:
            resp = requests.get(
                f'{TWIN_API}/metrics/ping_fast?src={src}&dst={dst}',
                timeout=3
            ).json()
            if resp.get('avg') is not None:
                t_recovered  = round(time.time() - t_up, 2)
                recovery_avg = resp['avg']
                break
        except:
            pass
        time.sleep(0.5)

    return jsonify({
        'ok':              True,
        'node':            node,
        'src':             src,
        'dst':             dst,
        'duration_s':      duration,
        'baseline_avg_ms': baseline_avg,
        'lost_packets':    lost_packets,
        'total_packets':   total_packets,
        'loss_pct':        round(lost_packets / total_packets * 100, 1) if total_packets else 0,
        't_recovery_s':    t_recovered,
        'recovery_avg_ms': recovery_avg,
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)