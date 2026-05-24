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
    
    if node:
        traffic = requests.get(f'{TWIN_API}/metrics/traffic?node={node}', timeout=10).json()
        if traffic.get('ok'):
            return jsonify({'ok': True, 'traffic': {node: traffic['interfaces']}})
        return jsonify({'ok': False, 'error': traffic.get('error', 'Unknown error')})
    
    # All nodes in one call
    data = requests.get(f'{TWIN_API}/metrics/link_traffic', timeout=10).json()
    if not data.get('ok'):
        return jsonify({'ok': False, 'error': 'Failed to fetch traffic'})
    
    # Group by node
    result = {}
    for key, info in data.get('links', {}).items():
        node_name = info['node']
        if node_name not in result:
            result[node_name] = {}
        result[node_name][info['intf']] = {
            'rx_bytes': info['rx_bytes'],
            'tx_bytes': info['tx_bytes'],
        }
    
    return jsonify({'ok': True, 'traffic': result})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)