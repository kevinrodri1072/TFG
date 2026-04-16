from flask import Flask, render_template, jsonify, request, send_file
import xarxa
import threading
import time
import requests
import json
import io
import re
import psutil
import numpy as np
from scipy.io import savemat, loadmat
from collections import deque

DIGITAL_TWIN_IP = '10.4.39.153'  # IP of the Twin.
DIGITAL_TWIN_PORT = 5000 

# Conversion map string <-> number for saving to .mat
TYPE_TO_NUM = {0: 0, 'host': 1, 'router': 2, 'switch': 3}
NUM_TO_TYPE = {0: 0, 1: 'host', 2: 'router', 3: 'switch'}

# Store last N sync latency measurements (for jitter calculation)
sync_latency_history = deque(maxlen=50)
sync_history_lock = threading.Lock()

# Function that sends an HTTP POST petition to the twin PC whenever a change is done in the original PC.
def synchronize(route, data):
    try:
        data['sync'] = True  # mark as synchronization
        data['timestamp'] = time.time()  # timestamp to measure latency
        response = requests.post(f'http://{DIGITAL_TWIN_IP}:{DIGITAL_TWIN_PORT}{route}', json=data)
        result = response.json()
        if 'latency_ms' in result:
            op = route.strip('/')
            record_sync_latency(op, result['latency_ms'])
    except Exception as e:
        print(f'Synchronization error: {e}')

def record_sync_latency(operation, latency_ms):
    """Store a sync latency measurement in history."""
    with sync_history_lock:
        sync_latency_history.append({
            'operation': operation,
            'latency_ms': round(latency_ms, 2),
            'timestamp': time.time()
        })

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

        if is_sync and 'timestamp' in data:
            latency_ms = (time.time() - data['timestamp']) * 1000
            record_sync_latency('load_network', latency_ms)
        
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

    if is_sync and 'latency_ms' in locals():
        return jsonify({'ok': True, 'latency_ms': round(latency_ms, 2)})

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
        ts = time.time()
        requests.post(
            f'http://{DIGITAL_TWIN_IP}:{DIGITAL_TWIN_PORT}/load_network',
            json={'matrix': serializable_matrix, 'nodes': new_nodes, 'sync': True, 'timestamp': time.time()}, timeout=10
        )
        latency_ms = (time.time() - ts) * 1000
        record_sync_latency('load_network', latency_ms)
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
    new_host.cmd('ifconfig lo up')
    new_host.cmd('ip link set lo up')
    new_host.cmd(f'ip link set {name}-eth0 up')
    sw_node.cmd(f'ip link set {sw_intf_name} up')
    sw_node.cmd(f'ovs-vsctl add-port {switch} {sw_intf_name}') 

    if is_sync and 'timestamp' in data:
        latency_ms = (time.time() - data['timestamp']) * 1000
        print(f'[LATENCY] add_host: {latency_ms:.2f} ms')
        record_sync_latency('add_host', latency_ms)
        return jsonify({'ok': True, 'latency_ms': round(latency_ms, 2)})

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
        latency_ms = (time.time() - data['timestamp']) * 1000
        print(f'[LATENCY] remove_node: {latency_ms:.2f} ms')
        record_sync_latency('remove_node', latency_ms)
        return jsonify({'ok': True, 'latency_ms': round(latency_ms, 2)})
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
        latency_ms = (time.time() - data['timestamp']) * 1000
        print(f'[LATENCY] add_router: {latency_ms:.2f} ms')
        record_sync_latency('add_router', latency_ms)
        return jsonify({'ok': True, 'latency_ms': round(latency_ms, 2)})

    if not is_sync:
        synchronize('/add_router', {'name': router_name, 'connected_routers': connected_routers})
    return jsonify({'ok': True})


# ─────────────────────────────────────────────
#  METRICS ROUTES
# ─────────────────────────────────────────────

@app.route('/metrics/internal')
def metrics_internal():
    """
    Measures internal Mininet network metrics between two hosts:
    - Latency (avg, min, max) via ping
    - Jitter (std dev of RTTs) via ping
    - Bandwidth (avg, min, max) via iperf
    Also returns system CPU and RAM usage.
    """
    if not xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})

    src = request.args.get('src')
    dst = request.args.get('dst')

    if not src or not dst:
        return jsonify({'ok': False, 'error': 'src and dst parameters required'})

    if src not in xarxa.nodes or dst not in xarxa.nodes:
        return jsonify({'ok': False, 'error': 'Node not found'})

    if xarxa.nodes[src]['type'] != 'host' or xarxa.nodes[dst]['type'] != 'host':
        return jsonify({'ok': False, 'error': 'Both nodes must be hosts'})

    src_node = xarxa.mininet_nodes[src]
    dst_node = xarxa.mininet_nodes[dst]
    dst_ip = xarxa.nodes[dst]['ip'].split('/')[0]

    # ── Latency + Jitter via ping (10 packets) ──
    ping_result = src_node.cmd(f'ping -c 10 -i 0.2 {dst_ip}')
    latency = {'min': None, 'avg': None, 'max': None}
    jitter = None

    # Parse "rtt min/avg/max/mdev = X/X/X/X ms"
    rtt_match = re.search(r'rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)', ping_result)
    if rtt_match:
        latency['min'] = float(rtt_match.group(1))
        latency['avg'] = float(rtt_match.group(2))
        latency['max'] = float(rtt_match.group(3))
        jitter        = float(rtt_match.group(4))  # mdev = jitter

    # ── Bandwidth via iperf (3 runs of 2 seconds each) ──
    bandwidth = {'min': None, 'avg': None, 'max': None}
    bw_values = []

    try:
        dst_node.cmd('pkill -f iperf 2>/dev/null; sleep 0.2')
        dst_node.sendCmd('iperf -s')
        time.sleep(0.5)

        for _ in range(3):
            iperf_out = src_node.cmd(f'iperf -c {dst_ip} -t 2 -f m')
            bw_match = re.search(r'([\d.]+)\s+Mbits/sec', iperf_out)
            if bw_match:
                bw_values.append(float(bw_match.group(1)))

        dst_node.sendInt()
        dst_node.waitOutput()
    except Exception as e:
        print(f'iperf error: {e}')
        try:
            dst_node.sendInt()
            dst_node.waitOutput()
        except:
            pass

    if bw_values:
        bandwidth['min'] = round(min(bw_values), 2)
        bandwidth['avg'] = round(sum(bw_values) / len(bw_values), 2)
        bandwidth['max'] = round(max(bw_values), 2)


    # ── System CPU and RAM ──
    cpu_percent = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory()

    return jsonify({
        'ok': True,
        'src': src,
        'dst': dst,
        'latency_ms': latency,
        'jitter_ms': jitter,
        'bandwidth_mbps': bandwidth,
        'system': {
            'cpu_percent': cpu_percent,
            'ram_used_mb': round(ram.used / 1024 / 1024, 1),
            'ram_total_mb': round(ram.total / 1024 / 1024, 1),
            'ram_percent': ram.percent
        }
    })


@app.route('/metrics/sync')
def metrics_sync():
    """
    Returns sync latency history and derived stats (avg, min, max, jitter).
    """
    with sync_history_lock:
        history = list(sync_latency_history)

    if not history:
        return jsonify({
            'ok': True,
            'history': [],
            'stats': None
        })

    latencies = [e['latency_ms'] for e in history]
    avg = round(sum(latencies) / len(latencies), 2)
    mn  = round(min(latencies), 2)
    mx  = round(max(latencies), 2)

    # Jitter = mean absolute difference between consecutive measurements
    if len(latencies) > 1:
        diffs = [abs(latencies[i] - latencies[i-1]) for i in range(1, len(latencies))]
        jitter = round(sum(diffs) / len(diffs), 2)
    else:
        jitter = 0.0

    return jsonify({
        'ok': True,
        'history': history,
        'stats': {
            'avg_ms': avg,
            'min_ms': mn,
            'max_ms': mx,
            'jitter_ms': jitter,
            'count': len(latencies)
        }
    })


@app.route('/metrics/hosts')
def metrics_hosts():
    """Returns list of hosts available for metric measurement."""
    hosts = [name for name, props in xarxa.nodes.items() if props['type'] == 'host']
    return jsonify({'hosts': hosts})

@app.route('/debug')
def debug():
    output = {}
    hosts = [name for name, props in xarxa.nodes.items() if props['type'] == 'host']
    if len(hosts) >= 2:
        src_name = hosts[-1]
        src = xarxa.mininet_nodes[src_name]
        gw = xarxa.nodes[src_name]['gw']
        sw_name = xarxa.find_switch_of_router('r1')
        sw = xarxa.mininet_nodes[sw_name]
        r1 = xarxa.mininet_nodes['r1']
        
        output['src'] = src_name
        output['src_ip_link'] = src.cmd('ip link show')
        output['sw_ip_link'] = sw.cmd('ip link show')
        output['r1_ip_link'] = r1.cmd('ip link show')
        output['arp_request'] = src.cmd(f'arping -c 3 -I {src_name}-eth0 {gw}')
    return jsonify(output)

if __name__ == '__main__':
    t = threading.Thread(target=xarxa.start_network)
    t.daemon = True
    t.start()
    time.sleep(3)
    app.run(debug=False)