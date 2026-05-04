from flask import Flask, jsonify, request
import requests
import os

app = Flask(__name__)
TWIN_API = os.environ.get('TWIN_API', 'http://localhost:5000')

@app.route('/health')
def health():
    return jsonify({'ok': True, 'xrf': 'traffic'})

@app.route('/run')
def run():
    node = request.args.get('node')
    topo = requests.get(f'{TWIN_API}/topology').json()
    nodes = topo['nodes']

    if node and node not in nodes:
        return jsonify({'ok': False, 'error': f'Node {node} not found'})

    # Si no s'especifica node, agafem tots excepte switches
    targets = [node] if node else [
        n for n, p in nodes.items() if p['type'] != 'switch'
    ]

    result = {}
    for n in targets:
        traffic = requests.get(f'{TWIN_API}/metrics/traffic?node={n}').json()
        if traffic.get('ok'):
            result[n] = traffic['interfaces']

    return jsonify({'ok': True, 'traffic': result})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)