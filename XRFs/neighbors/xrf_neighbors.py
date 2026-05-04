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
    topo = requests.get(f'{TWIN_API}/topology').json()
    nodes = topo['nodes']
    links = topo['links']
    
    result = {}
    for node in nodes:
        result[node] = [
            l['to'] if l['from'] == node else l['from']
            for l in links
            if l['from'] == node or l['to'] == node
        ]
    return jsonify({'ok': True, 'neighbors': result})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)