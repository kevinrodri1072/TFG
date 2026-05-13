from flask import Flask, jsonify, request
import requests
import os

app = Flask(__name__)
TWIN_API = os.environ.get('TWIN_API', 'http://localhost:5000')

@app.route('/health')
def health():
    return jsonify({'ok': True, 'xrf': 'latency_matrix'})

@app.route('/run', methods=['POST'])
def run():
    resp = requests.get(f'{TWIN_API}/metrics/global', timeout=120).json()
    if not resp.get('ok'):
        return jsonify({'ok': False, 'error': resp.get('error', 'Unknown error')})
    
    per_pair = resp.get('per_pair', {})
    ping = per_pair.get('latency', {})
    bandwidth = per_pair.get('bandwidth', {})
    
    hosts = set()
    for key in list(ping.keys()) + list(bandwidth.keys()):
        src, dst = key.split('->')
        hosts.add(src)
        hosts.add(dst)
    
    return jsonify({
        'ok': True,
        'ping': ping,
        'bandwidth': bandwidth,
        'hosts': sorted(list(hosts))
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)