from flask import Flask, render_template, jsonify, request, send_file
import xarxa
import threading
import time
import requests
import json
import io
import numpy as np
from scipy.io import savemat, loadmat

DIGITAL_TWIN_IP = '10.4.39.153'  # IP of the Twin.
DIGITAL_TWIN_PORT = 5000 

# Conversion map string <-> number for saving to .mat
TYPE_TO_NUM = {0: 0, 'host': 1, 'router': 2, 'switch': 3}
NUM_TO_TYPE = {0: 0, 1: 'host', 2: 'router', 3: 'switch'}


# Function that sends an HTTP POST petition to the twin PC whenever a change is done in the original PC.
def synchronize(route, data):
    try:
        data['sync'] = True  # mark as synchronization
        data['timestamp'] = time.time()  # timestamp to measure latency
        requests.post(f'http://{DIGITAL_TWIN_IP}:{DIGITAL_TWIN_PORT}{route}', json=data)
    except Exception as e:
        print(f'Synchronization error: {e}')

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

# Function that builds the topology of the network.
@app.route('/topology')
def topology():
    node_names = list(xarxa.nodes.keys())
    links = []
    for i in range(len(xarxa.network_matrix)):
        for j in range(i + 1, len(xarxa.network_matrix[i])):
            if xarxa.network_matrix[i][j] != 0:
                node_i = node_names[i]
                node_j = node_names[j]
                type_i = xarxa.nodes[node_i]['type']
                type_j = xarxa.nodes[node_j]['type']
                if type_i == 'switch' or type_j == 'switch':
                    continue
                links.append({'from': node_i, 'to': node_j})

    for switch_name, props in xarxa.nodes.items():
        if props['type'] == 'switch':
            switch_idx = node_names.index(switch_name)
            router = None
            hosts = []
            for i, val in enumerate(xarxa.network_matrix[switch_idx]):
                if val != 0:
                    node = node_names[i]
                    if xarxa.nodes[node]['type'] == 'router':
                        router = node
                    elif xarxa.nodes[node]['type'] == 'host':
                        hosts.append(node)
            if router:
                for host in hosts:
                    links.append({'from': router, 'to': host})

    return jsonify({'nodes': xarxa.nodes, 'links': links})

# Function that returns the current matrix and the nodes names in JSON format.
@app.route('/matrix')
def matrix():
    names = list(xarxa.nodes.keys())
    return jsonify({
        'names': names,
        'matrix': xarxa.network_matrix
    })

# Function that exports the actual matrix to a .mat archive.
@app.route('/export')
def export():
    # Convert matrix from strings to numbers
    matrix_num = np.array([
        [TYPE_TO_NUM[cell] for cell in row]
        for row in xarxa.network_matrix
    ], dtype=np.int32)

    # Save node names and full dictionary as JSON
    node_names = list(xarxa.nodes.keys())
    nodes_json = json.dumps(xarxa.nodes)

    # Create .mat file in memory
    buffer = io.BytesIO()
    savemat(buffer, {
        'matrix': matrix_num,
        'node_names': np.array(node_names, dtype=object),
        'nodes_json': nodes_json
    })
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype='application/octet-stream',
        as_attachment=True,
        download_name='network.mat'
    )

# Function that loads a new network from a .mat archive.
@app.route('/load_network', methods=['POST'])
def load_network():
    """
    Loads a new network topology from a .mat file (Original) or JSON (Twin).
    """
    # Check if the request is a JSON synchronization from the Original PC
    if request.is_json:
        data = request.get_json()
        is_sync = data.get('sync', False)
        new_matrix = data['matrix']
        new_nodes = data['nodes']
    else:
        # Request comes from the UI as a .mat file upload
        is_sync = False
        file = request.files.get('file')
        if not file:
            return jsonify({'ok': False, 'error': 'No file received'})

        buffer = io.BytesIO(file.read())
        mat = loadmat(buffer)

        # Convert numeric matrix back to string types (host, router, switch)
        matrix_num = mat['matrix'].tolist()
        new_matrix = [
            [NUM_TO_TYPE[int(cell)] for cell in row]
            for row in matrix_num
        ]

        # Deserialize the node dictionary
        nodes_json = str(mat['nodes_json'][0]) if isinstance(mat['nodes_json'], np.ndarray) else mat['nodes_json']
        new_nodes = json.loads(nodes_json)

    # Restart the network in a separate thread to prevent Flask timeout
    threading.Thread(target=xarxa.restart_network, args=(new_matrix, new_nodes)).start()

    # If this is the Original PC, push the new topology to the Twin
    if not is_sync:
        synchronize_full_network(new_matrix, new_nodes)

    return jsonify({'ok': True})

# Function that sends the new matrix and the new nodes to the twin PC when a .mat archive is load to the original PC.
def synchronize_full_network(new_matrix, new_nodes):
    """
    Sends the processed topology to the Twin PC to ensure consistency.
    """
    serializable_matrix = [
        [cell if isinstance(cell, str) else int(cell) for cell in row]
        for row in new_matrix
    ]
    try:
        # Send data as JSON to the same endpoint
        requests.post(
            f'http://{DIGITAL_TWIN_IP}:{DIGITAL_TWIN_PORT}/load_network',
            json={'matrix': serializable_matrix, 'nodes': new_nodes, 'sync': True},
            timeout=10
        )
    except Exception as e:
        print(f'Full network synchronization error: {e}')

# Function that adds a new host to the network. 
@app.route('/add_host', methods=['POST'])
def add_host():
    if not xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})

    data = request.json
    name = data['name']
    router = data['router']
    is_sync = data.get('sync', False)

    if name in xarxa.nodes:
        return jsonify({'ok': False, 'error': f'A node named {name} already exists'})

    switch = xarxa.find_switch_of_router(router)
    ip = xarxa.find_next_ip(router)
    gw = xarxa.nodes[router]['ips']['eth1'].split('/')[0]

    xarxa.nodes[name] = {'type': 'host', 'ip': ip, 'gw': gw}
    xarxa.update_matrix(name, switch)

    new_host = xarxa.net.addHost(name, ip=ip)
    xarxa.mininet_nodes[name] = new_host

    sw_node = xarxa.mininet_nodes[switch]
    num_intfs = len(sw_node.intfList())
    sw_intf_name = f'{switch}-eth{num_intfs}'

    xarxa.net.addLink(new_host, sw_node,
                      intfName1=f'{name}-eth0',
                      intfName2=sw_intf_name)

    new_host.cmd(f'ifconfig {name}-eth0 {ip}')
    new_host.cmd(f'ip route add default via {gw}')

    if is_sync and 'timestamp' in data:
        latency = time.time() - data['timestamp']
        print(f'[LATENCY] add_host: {latency*1000:.2f} ms')

    if not is_sync:
        synchronize('/add_host', {'name': name, 'router': router})

    return jsonify({'ok': True})

# Function that removes a node of the network. 
@app.route('/remove_node', methods=['POST'])
def remove_node():
    if not xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})

    data = request.json
    name = data['name']
    is_sync = data.get('sync', False)

    if xarxa.nodes[name]['type'] == 'router':
        # Remove entire subnet
        nodes_to_remove = xarxa.find_router_subnet(name)
        nodes_to_remove.append(name)
        for node in nodes_to_remove:
            xarxa.remove_from_matrix(node)
            mininet_node = xarxa.mininet_nodes[node]
            xarxa.net.delNode(mininet_node)
            del xarxa.mininet_nodes[node]
            del xarxa.nodes[node]
    else:
        xarxa.remove_from_matrix(name)
        mininet_node = xarxa.mininet_nodes[name]
        xarxa.net.delNode(mininet_node)
        del xarxa.mininet_nodes[name]
        del xarxa.nodes[name]

    if is_sync and 'timestamp' in data:
        latency = time.time() - data['timestamp']
        print(f'[LATENCY] remove_node: {latency*1000:.2f} ms')

    if not is_sync:
        synchronize('/remove_node', {'name': name})
    return jsonify({'ok': True})

# Function that adds a new router to the network. 
@app.route('/add_router', methods=['POST'])
def add_router():
    if not xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})

    data = request.json
    router_name = data['name']
    connected_routers = data['connected_routers']
    is_sync = data.get('sync', False)

    if router_name in xarxa.nodes:
        return jsonify({'ok': False, 'error': f'A node named {router_name} already exists'})

    # Calculate switch name
    switch_num = len([n for n, p in xarxa.nodes.items() if p['type'] == 'switch']) + 1
    switch_name = f'sw{switch_num}'

    # Calculate IPs
    ip_eth0 = xarxa.find_next_router_ip()
    subnet_num = xarxa.find_next_subnet()
    ip_eth1 = f'10.{subnet_num}.0.1/24'

    xarxa.nodes[router_name] = {
        'type': 'router',
        'ips': {'eth0': ip_eth0, 'eth1': ip_eth1},
        'routes': []
    }
    xarxa.update_matrix_multi(router_name, connected_routers)

    xarxa.nodes[switch_name] = {'type': 'switch'}
    xarxa.update_matrix_multi(switch_name, [router_name])

    new_router = xarxa.net.addHost(router_name, ip=ip_eth0)
    new_switch = xarxa.net.addSwitch(switch_name, failMode='standalone')
    xarxa.mininet_nodes[router_name] = new_router
    xarxa.mininet_nodes[switch_name] = new_switch
    new_switch.start([])

    for connected_router in connected_routers:
        xarxa.net.addLink(new_router, xarxa.mininet_nodes[connected_router])
    xarxa.net.addLink(new_router, new_switch)

    new_router.cmd(f'ifconfig {router_name}-eth0 {ip_eth0}')
    new_router.cmd(f'ifconfig {router_name}-eth1 {ip_eth1}')
    new_router.cmd('sysctl -w net.ipv4.ip_forward=1')

    if is_sync and 'timestamp' in data:
        latency = time.time() - data['timestamp']
        print(f'[LATENCY] add_router: {latency*1000:.2f} ms')

    if not is_sync:
        synchronize('/add_router', {'name': router_name, 'connected_routers': connected_routers})
    return jsonify({'ok': True})

if __name__ == '__main__':
    t = threading.Thread(target=xarxa.start_network)
    t.daemon = True
    t.start()
    time.sleep(3)
    app.run(debug=False)