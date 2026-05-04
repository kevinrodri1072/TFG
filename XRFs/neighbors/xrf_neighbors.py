from flask import Flask, jsonify
import requests
import os

app = Flask(__name__)
TWIN_API = os.environ.get('TWIN_API', 'http://localhost:5000')

@app.route('/health')
def health():
    return jsonify({'ok': True, 'xrf': 'neighbors'})

@app.route('/run')
def run():
    node = request.args.get('node')
    topo  = requests.get(f'{TWIN_API}/topology').json()
    nodes = topo['nodes']
    links = topo['links']

    if node and node not in nodes:
        return jsonify({'ok': False, 'error': f'Node {node} not found'})

    targets = [node] if node else list(nodes.keys())
    result  = {}
    for n in targets:
        result[n] = [
            l['to'] if l['from'] == n else l['from']
            for l in links
            if l['from'] == n or l['to'] == n
        ]
    return jsonify({'ok': True, 'neighbors': result})