"""
xrf_latency_matrix.py — Latency Matrix XRF.

Modes:
  fast → ping only
  full → ping + iperf

Options (full mode):
  protocol   : tcp / udp
  duration   : seconds per iperf run
  parallel   : parallel streams (-P)
  iterations : runs per pair
  bandwidth  : target Mbps (UDP only, -b)
  reverse    : measure server→client (-R)

Ping options (both modes):
  ping_count : packets per pair (default 5)
  ping_size  : packet size in bytes (default 64)
"""

from flask import Flask, jsonify, request
import requests
import os

app      = Flask(__name__)
TWIN_API = os.environ.get('TWIN_API', 'http://localhost:5000')


@app.route('/health')
def health():
    return jsonify({'ok': True, 'xrf': 'latency_matrix'})


@app.route('/run', methods=['POST'])
def run():
    data       = request.json or {}
    mode       = data.get('mode', 'fast')
    protocol   = data.get('protocol', 'tcp')
    duration   = int(data.get('duration', 1))
    parallel   = int(data.get('parallel', 1))
    iterations = int(data.get('iterations', 3))
    bandwidth  = data.get('bandwidth', None)
    reverse    = bool(data.get('reverse', False))
    ping_count = int(data.get('ping_count', 5))
    ping_size  = int(data.get('ping_size', 64))

    # Dynamic timeout based on params
    if mode == 'full':
        # Conservative estimate: 20 pairs max × iterations × duration × 1.5 + 30s margin
        timeout = int(20 * iterations * duration * 1.5 + 30)
    else:
        timeout = 60

    params  = (f'mode={mode}&protocol={protocol}&duration={duration}'
               f'&parallel={parallel}&iterations={iterations}'
               f'&ping_count={ping_count}&ping_size={ping_size}'
               f'&reverse={str(reverse).lower()}')
    if bandwidth:
        params += f'&bandwidth={bandwidth}'

    try:
        resp = requests.get(
            f'{TWIN_API}/metrics/global?{params}',
            timeout=timeout,
        ).json()
    except requests.exceptions.Timeout:
        return jsonify({'ok': False, 'error': f'Timeout after {timeout}s. Reduce duration or iterations.'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

    if not resp.get('ok'):
        return jsonify({'ok': False, 'error': resp.get('error', 'Unknown error')})

    per_pair  = resp.get('per_pair', {})
    ping      = per_pair.get('latency', {})
    bandwidth_res = per_pair.get('bandwidth', {})

    hosts = set()
    for key in list(ping.keys()) + list(bandwidth_res.keys()):
        src, dst = key.split('->')
        hosts.add(src)
        hosts.add(dst)

    return jsonify({
        'ok':        True,
        'mode':      mode,
        'ping':      ping,
        'bandwidth': bandwidth_res,
        'hosts':     sorted(list(hosts)),
        'iperf_cmd': resp.get('iperf_cmd', ''),
        'ping_cmd':  resp.get('ping_cmd', ''),
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)