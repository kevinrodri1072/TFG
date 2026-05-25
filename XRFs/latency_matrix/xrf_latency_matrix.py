"""
xrf_latency_matrix.py — Latency Matrix XRF.

Calls /metrics/global on the Twin and returns a structured
latency + bandwidth matrix for all host pairs.

Modes:
  fast → ping only (~3 s)
  full → ping + iperf, parallelised (~15 s)
"""

from flask import Flask, jsonify, request
import requests
import os

app     = Flask(__name__)
TWIN_API = os.environ.get('TWIN_API', 'http://localhost:5000')


@app.route('/health')
def health():
    return jsonify({'ok': True, 'xrf': 'latency_matrix'})


@app.route('/run', methods=['POST'])
def run():
    mode = request.json.get('mode', 'fast')  # 'fast' or 'full'
    timeout = 30 if mode == 'fast' else 120

    try:
        resp = requests.get(
            f'{TWIN_API}/metrics/global?mode={mode}',
            timeout=timeout,
        ).json()
    except requests.exceptions.Timeout:
        return jsonify({'ok': False, 'error': f'Timeout after {timeout}s. Try mode=fast.'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

    if not resp.get('ok'):
        return jsonify({'ok': False, 'error': resp.get('error', 'Unknown error')})

    per_pair  = resp.get('per_pair', {})
    ping      = per_pair.get('latency', {})
    bandwidth = per_pair.get('bandwidth', {})

    hosts = set()
    for key in list(ping.keys()) + list(bandwidth.keys()):
        src, dst = key.split('->')
        hosts.add(src)
        hosts.add(dst)

    return jsonify({
        'ok':        True,
        'mode':      mode,
        'ping':      ping,
        'bandwidth': bandwidth,
        'hosts':     sorted(list(hosts)),
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)