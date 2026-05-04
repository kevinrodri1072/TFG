from flask import Flask, jsonify, request
from collections import deque
import requests
import os

app = Flask(__name__)
TWIN_API = os.environ.get('TWIN_API', 'http://localhost:5000')

def build_graph(nodes, links):
    """Build adjacency graph from topology."""
    graph = {n: [] for n in nodes}
    for link in links:
        src, dst = link['from'], link['to']
        if src in graph and dst in graph:
            graph[src].append(dst)
            graph[dst].append(src)
    return graph

def bfs(graph, src, dst=None, max_hops=None):
    """
    BFS from src.
    - If dst specified: returns shortest path to dst.
    - If max_hops specified: returns all nodes reachable within max_hops.
    """
    visited = {src: None}  # node: parent
    queue   = deque([(src, 0)])

    reachable = {}  # node: hops (for max_hops mode)

    while queue:
        current, hops = queue.popleft()

        if dst and current == dst:
            # Reconstruct path
            path = []
            node = dst
            while node is not None:
                path.append(node)
                node = visited[node]
            path.reverse()
            return {'hops': hops, 'path': path}

        if max_hops is not None and hops < max_hops:
            for neighbor in graph.get(current, []):
                if neighbor not in visited:
                    visited[neighbor] = current
                    reachable[neighbor] = hops + 1
                    queue.append((neighbor, hops + 1))
        elif dst:
            for neighbor in graph.get(current, []):
                if neighbor not in visited:
                    visited[neighbor] = current
                    queue.append((neighbor, hops + 1))

    if dst:
        return {'hops': None, 'path': [], 'error': f'No path from {src} to {dst}'}
    return reachable


@app.route('/health')
def health():
    return jsonify({'ok': True, 'xrf': 'hops'})


@app.route('/run')
def run():
    src      = request.args.get('src')
    dst      = request.args.get('dst')
    max_hops = request.args.get('max_hops', type=int)

    if not src:
        return jsonify({'ok': False, 'error': 'src parameter required'})
    if not dst and max_hops is None:
        return jsonify({'ok': False, 'error': 'dst or max_hops required'})

    topo  = requests.get(f'{TWIN_API}/topology').json()
    nodes = topo['nodes']
    links = topo['links']

    if src not in nodes:
        return jsonify({'ok': False, 'error': f'Node {src} not found'})
    if dst and dst not in nodes:
        return jsonify({'ok': False, 'error': f'Node {dst} not found'})

    graph  = build_graph(nodes, links)
    result = bfs(graph, src, dst=dst, max_hops=max_hops)

    return jsonify({'ok': True, 'src': src, 'dst': dst,
                    'max_hops': max_hops, 'result': result})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)