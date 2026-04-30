from flask import Flask, render_template, jsonify, request, send_file
import xarxa
import threading
import time
import requests
import json
import io
import re
import os
import subprocess
import psutil
import copy
import numpy as np
from scipy.io import savemat, loadmat
from collections import deque

DIGITAL_TWIN_IP = '10.4.39.153'  # IP of the Twin
DIGITAL_TWIN_PORT = 5000

TYPE_TO_NUM = {0: 0, 'host': 1, 'router': 2, 'switch': 3}
NUM_TO_TYPE = {0: 0, 1: 'host', 2: 'router', 3: 'switch'}

# Sync latency history — only the Original writes to this.
# Each entry contains decomposed timing:
#   t_local_ms   — time spent by Mininet on the Original
#   t_network_ms — HTTP round-trip to the Twin (pure network)
#   t_twin_ms    — time spent by Mininet on the Twin (reported by Twin in response)
sync_latency_history = deque(maxlen=50)
sync_history_lock    = threading.Lock()

metrics_running = False

def synchronize(route, data, t_local_ms):
    """
    Send a sync POST to the Twin and record decomposed latency.
    t_local_ms: time already spent doing the operation on the Original (measured by caller).
    """
    try:
        data['sync'] = True
        t_net_start = time.time()
        response = requests.post(
            f'http://{DIGITAL_TWIN_IP}:{DIGITAL_TWIN_PORT}{route}',
            json=data, timeout=15
        )
        t_network_ms = round((time.time() - t_net_start) * 1000, 2)

        if response.status_code == 200:
            resp_json = response.json()
            # Twin reports how long its own Mininet operation took
            t_twin_ms = resp_json.get('t_local_ms', None)
            record_sync_latency(route.strip('/'), t_local_ms, t_network_ms, t_twin_ms)
            return resp_json
        return None
    except Exception as e:
        print(f'Sync error: {e}')
        return None

def record_sync_latency(operation, t_local_ms, t_network_ms, t_twin_ms):
    entry = {
        'operation':   operation,
        't_local_ms':  round(t_local_ms,   2) if t_local_ms  is not None else None,
        't_network_ms': round(t_network_ms, 2) if t_network_ms is not None else None,
        't_twin_ms':   round(t_twin_ms,    2) if t_twin_ms   is not None else None,
        # Keep a single 'latency_ms' for backwards compat with dashboard history list
        'latency_ms':  round(t_network_ms, 2) if t_network_ms is not None else None,
        'timestamp':   time.time()
    }
    with sync_history_lock:
        sync_latency_history.append(entry)
    # Push decomposed metrics to Twin dashboard too
    try:
        requests.post(
            f'http://{DIGITAL_TWIN_IP}:{DIGITAL_TWIN_PORT}/sync_metrics',
            json=entry,
            timeout=1
        )
    except:
        pass

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/topology')
def topology():
    node_names = list(xarxa.nodes.keys())
    links = []
    for i in range(len(xarxa.network_matrix)):
        for j in range(i + 1, len(xarxa.network_matrix[i])):
            if xarxa.network_matrix[i][j] != 0:
                node_i = node_names[i]
                node_j = node_names[j]
                if xarxa.nodes[node_i]['type'] == 'switch' or xarxa.nodes[node_j]['type'] == 'switch':
                    continue
                links.append({'from': node_i, 'to': node_j})
    for switch_name, props in xarxa.nodes.items():
        if props['type'] == 'switch':
            switch_idx = node_names.index(switch_name)
            router, hosts = None, []
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


@app.route('/matrix')
def matrix():
    names = list(xarxa.nodes.keys())
    return jsonify({'names': names, 'matrix': xarxa.network_matrix})


@app.route('/export')
def export():
    matrix_num = np.array(
        [[TYPE_TO_NUM[cell] for cell in row] for row in xarxa.network_matrix],
        dtype=np.int32
    )
    node_names = list(xarxa.nodes.keys())
    nodes_json = json.dumps(xarxa.nodes)
    buffer = io.BytesIO()
    savemat(buffer, {
        'matrix':     matrix_num,
        'node_names': np.array(node_names, dtype=object),
        'nodes_json': nodes_json
    })
    buffer.seek(0)
    return send_file(buffer, mimetype='application/octet-stream',
                     as_attachment=True, download_name='network.mat')


@app.route('/load_network', methods=['POST'])
def load_network():
    if request.is_json:
        data      = request.get_json()
        is_sync   = data.get('sync', False)
        new_matrix = data['matrix']
        new_nodes  = data['nodes']
    else:
        is_sync = False
        file = request.files.get('file')
        if not file:
            return jsonify({'ok': False, 'error': 'No file received'})
        buffer     = io.BytesIO(file.read())
        mat        = loadmat(buffer)
        matrix_num = mat['matrix'].tolist()
        new_matrix = [[NUM_TO_TYPE[int(cell)] for cell in row] for row in matrix_num]
        nodes_json = str(mat['nodes_json'][0]) if isinstance(mat['nodes_json'], np.ndarray) else mat['nodes_json']
        new_nodes  = json.loads(nodes_json)

    threading.Thread(target=xarxa.restart_network, args=(new_matrix, new_nodes)).start()

    if not is_sync:
        # Aquesta funció ara mesura el temps i crida a record_sync_latency correctament
        synchronize_full_network(new_matrix, new_nodes)

    return jsonify({'ok': True})



def synchronize_snapshot(operation, t_local_ms):
    """
    Sync the Twin by sending a full state snapshot (matrix + nodes).
    This is the Opció B approach: instead of replicating each individual
    Mininet operation, we send the current state and let the Twin rebuild.

    Always runs in a background thread so the browser is never blocked.
    Measures t_network_ms as the HTTP round-trip to /load_network.
    t_twin_ms is not available here because the Twin rebuilds asynchronously,
    so we record None — honest representation of snapshot-based sync.
    """
    serializable_matrix = [
        [cell if isinstance(cell, str) else int(cell) for cell in row]
        for row in xarxa.network_matrix
    ]
    try:
        t_net_start = time.time()
        requests.post(
            f'http://{DIGITAL_TWIN_IP}:{DIGITAL_TWIN_PORT}/load_network',
            json={'matrix': serializable_matrix, 'nodes': xarxa.nodes, 'sync': True},
            timeout=10
        )
        t_network_ms = round((time.time() - t_net_start) * 1000, 2)
        # t_twin_ms is None: Twin rebuilds async, we don't wait for it
        record_sync_latency(operation, t_local_ms, t_network_ms, None)
    except Exception as e:
        print(f'Snapshot sync error: {e}')

def sync_in_background(operation, t_local_ms):
    """Launch synchronize_snapshot in a daemon thread."""
    threading.Thread(
        target=synchronize_snapshot,
        args=(operation, t_local_ms),
        daemon=True
    ).start()

def synchronize_full_network(new_matrix, new_nodes):
    """Legacy: used by load_network route. Kept for backwards compat."""
    serializable_matrix = [
        [cell if isinstance(cell, str) else int(cell) for cell in row]
        for row in new_matrix
    ]
    try:
        t_net_start = time.time()
        requests.post(
            f'http://{DIGITAL_TWIN_IP}:{DIGITAL_TWIN_PORT}/load_network',
            json={'matrix': serializable_matrix, 'nodes': new_nodes, 'sync': True},
            timeout=10
        )
        t_network_ms = round((time.time() - t_net_start) * 1000, 2)
        record_sync_latency('load_network', 0, t_network_ms, None)
    except Exception as e:
        print(f'Full network synchronization error: {e}')

@app.route('/add_host', methods=['POST'])
def add_host():
    if not xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})
    data      = request.json
    name      = data['name']
    router    = data['router']
    is_sync   = data.get('sync', False)

    if name in xarxa.nodes:
        return jsonify({'ok': False, 'error': f'A node named {name} already exists'})

    switch   = xarxa.find_switch_of_router(router)
    ip       = xarxa.find_next_ip(router)
    lan_ip   = next(ip for ip in xarxa.nodes[router]['ips'].values() if '/24' in ip)
    gw       = lan_ip.split('/')[0]

    xarxa.nodes[name] = {'type': 'host', 'ip': ip, 'gw': gw}
    xarxa.update_matrix(name, switch)

    t_local_start = time.time()
    new_host     = xarxa.net.addHost(name, ip=ip)
    xarxa.mininet_nodes[name] = new_host
    sw_node      = xarxa.mininet_nodes[switch]
    num_intfs    = len(sw_node.intfList())
    sw_intf_name = f'{switch}-eth{num_intfs}'

    xarxa.net.addLink(new_host, sw_node, intfName1=f'{name}-eth0', intfName2=sw_intf_name)
    # Millora 1: agrupar les 5 cmds del host en una sola crida
    new_host.cmd(
        f'ifconfig {name}-eth0 {ip} ; '
        f'ip route add default via {gw} ; '
        f'ifconfig lo up ; '
        f'ip link set lo up ; '
        f'ip link set {name}-eth0 up'
    )
    # Millora 1: agrupar les 2 cmds del switch en una sola crida
    sw_node.cmd(
        f'ip link set {sw_intf_name} up ; '
        f'ovs-vsctl add-port {switch} {sw_intf_name}'
    )
    t_local_ms = round((time.time() - t_local_start) * 1000, 2)

    if not is_sync:
        sync_in_background('add_host', t_local_ms)
        return jsonify({'ok': True})
    return jsonify({'ok': True, 't_local_ms': t_local_ms})


@app.route('/remove_node', methods=['POST'])
def remove_node():
    if not xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})
    data    = request.json
    name    = data['name']
    is_sync = data.get('sync', False)

    if xarxa.nodes[name]['type'] == 'router':
        # 1. Clean p2p_links and IPs from neighboring routers
        for rname, props in xarxa.nodes.items():
            if props['type'] == 'router' and rname != name:
                if 'p2p_links' in props:
                    # Find which local interfaces connect to the router being removed
                    intfs_to_remove = {
                        l['local_intf'] for l in props['p2p_links'] if l['peer'] == name
                    }
                    # Remove those IPs
                    for intf in intfs_to_remove:
                        props['ips'].pop(intf, None)
                    # Remove the p2p_links entries
                    props['p2p_links'] = [
                        l for l in props['p2p_links'] if l['peer'] != name
                    ]

        # 2. Remove the router and its subnet
        t_local_start = time.time()
        nodes_to_remove = xarxa.find_router_subnet(name)
        nodes_to_remove.append(name)
        for node in nodes_to_remove:
            xarxa.remove_from_matrix(node)
            xarxa.net.delNode(xarxa.mininet_nodes[node])
            del xarxa.mininet_nodes[node]
            del xarxa.nodes[node]

        # 3. Recalculate routes for remaining routers
        _update_all_routes()
        t_local_ms = round((time.time() - t_local_start) * 1000, 2)
    else:
        t_local_start = time.time()
        xarxa.remove_from_matrix(name)
        xarxa.net.delNode(xarxa.mininet_nodes[name])
        del xarxa.mininet_nodes[name]
        del xarxa.nodes[name]
        t_local_ms = round((time.time() - t_local_start) * 1000, 2)

    if not is_sync:
        sync_in_background('remove_node', t_local_ms)
        return jsonify({'ok': True})
    return jsonify({'ok': True, 't_local_ms': t_local_ms})


@app.route('/add_router', methods=['POST'])
def add_router():
    if not xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})
    data              = request.json
    router_name       = data['name']
    connected_routers = data['connected_routers']
    is_sync           = data.get('sync', False)

    if router_name in xarxa.nodes:
        return jsonify({'ok': False, 'error': f'A node named {router_name} already exists'})

    switch_num  = len([n for n, p in xarxa.nodes.items() if p['type'] == 'switch']) + 1
    switch_name = f'sw{switch_num}'
    subnet_num  = xarxa.find_next_subnet()
    ip_eth1     = f'10.{subnet_num}.0.1/24'

    xarxa.nodes[router_name] = {
        'type': 'router', 'ips': {'lan': ip_eth1}, 'routes': [], 'p2p_links': []
    }
    xarxa.update_matrix_multi(router_name, connected_routers)
    xarxa.nodes[switch_name] = {'type': 'switch'}
    xarxa.update_matrix_multi(switch_name, [router_name])

    t_local_start = time.time()
    new_router  = xarxa.net.addHost(router_name, ip='127.0.0.1')
    new_switch  = xarxa.net.addSwitch(switch_name, failMode='standalone')
    xarxa.mininet_nodes[router_name] = new_router
    xarxa.mininet_nodes[switch_name] = new_switch
    new_switch.start([])

    eth_idx = 0
    for connected_router in connected_routers:
        p2p           = xarxa.find_next_p2p_subnet()
        intf_new      = f'{router_name}-eth{eth_idx}'
        existing_node = xarxa.mininet_nodes[connected_router]
        intf_existing = f'{connected_router}-eth{len(existing_node.intfList())}'

        xarxa.net.addLink(new_router, existing_node, intfName1=intf_new, intfName2=intf_existing)
        # Millora 1: agrupar totes les cmds de configuració d'aquesta interfície en una sola crida
        new_router.cmd(
            f'ifconfig {intf_new} {p2p["ip_a"]}/30 ; '
            f'ip link set {intf_new} up'
        )
        existing_node.cmd(
            f'ifconfig {intf_existing} {p2p["ip_b"]}/30 ; '
            f'ip link set {intf_existing} up'
        )

        xarxa.nodes[router_name]['ips'][f'eth{eth_idx}'] = f'{p2p["ip_a"]}/30'
        xarxa.nodes[router_name]['p2p_links'].append({
            'peer': connected_router, 'local_ip': p2p['ip_a'],
            'peer_ip': p2p['ip_b'], 'subnet': p2p['subnet'], 'local_intf': f'eth{eth_idx}'
        })

        existing_props     = xarxa.nodes[connected_router]
        existing_eth_idx   = len([k for k in existing_props['ips'] if k.startswith('eth')])
        existing_intf_name = f'eth{existing_eth_idx}'
        existing_props['ips'][existing_intf_name] = f'{p2p["ip_b"]}/30'
        if 'p2p_links' not in existing_props:
            existing_props['p2p_links'] = []
        existing_props['p2p_links'].append({
            'peer': router_name, 'local_ip': p2p['ip_b'],
            'peer_ip': p2p['ip_a'], 'subnet': p2p['subnet'], 'local_intf': existing_intf_name
        })
        eth_idx += 1

    intf_eth_lan = f'{router_name}-eth{eth_idx}'
    xarxa.net.addLink(new_router, new_switch, intfName1=intf_eth_lan)
    # Millora 1: agrupar les 4 cmds finals del router nou en una sola crida
    new_router.cmd(
        f'ifconfig {intf_eth_lan} {ip_eth1} ; '
        f'ip link set {intf_eth_lan} up ; '
        f'sysctl -w net.ipv4.ip_forward=1 ; '
        f'ifconfig lo up'
    )
    xarxa.nodes[router_name]['ips'][f'eth{eth_idx}'] = ip_eth1
    sw_intf = f'{switch_name}-eth1'
    # Millora 1: agrupar les 2 cmds del switch en una sola crida
    new_switch.cmd(
        f'ip link set {sw_intf} up ; '
        f'ovs-vsctl add-port {switch_name} {sw_intf}'
    )

    _start_ospf_router(router_name)
    t_local_ms = round((time.time() - t_local_start) * 1000, 2)

    if not is_sync:
        sync_in_background('add_router', t_local_ms)
        return jsonify({'ok': True})
    return jsonify({'ok': True, 't_local_ms': t_local_ms})


@app.route('/rename_node', methods=['POST'])
def rename_node():
    if not xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})
    data     = request.json
    old_name = data['old_name']
    new_name = data['new_name']
    is_sync  = data.get('sync', False)

    if not new_name.replace('_', '').replace('-', '').isalnum() or new_name[0].isupper():
        return jsonify({'ok': False, 'error': 'Name must be lowercase alphanumeric (e.g. h6, router1)'})
    if old_name not in xarxa.nodes:
        return jsonify({'ok': False, 'error': f'Node {old_name} not found'})
    if new_name in xarxa.nodes:
        return jsonify({'ok': False, 'error': f'A node named {new_name} already exists'})

    new_nodes = {(new_name if n == old_name else n): p for n, p in xarxa.nodes.items()}
    for props in new_nodes.values():
        if props['type'] == 'router':
            for link in props.get('p2p_links', []):
                if link['peer'] == old_name:
                    link['peer'] = new_name

    matrix_copy = copy.deepcopy(xarxa.network_matrix)
    t_local_start = time.time()
    threading.Thread(target=xarxa.restart_network, args=(matrix_copy, new_nodes)).start()
    t_local_ms = round((time.time() - t_local_start) * 1000, 2)

    if not is_sync:
        sync_in_background('rename_node', t_local_ms)
        return jsonify({'ok': True})
    return jsonify({'ok': True, 't_local_ms': t_local_ms})


def _start_ospf_router(router_name):
    """Start routing on a new router and restart it on all existing ones."""
    props = xarxa.nodes[router_name]
    node  = xarxa.mininet_nodes[router_name]
    xarxa._apply_routing(node, router_name, props)

    # Restart routing on existing routers so they discover the new neighbour
    for name, p in xarxa.nodes.items():
        if p['type'] == 'router' and name != router_name and name in xarxa.mininet_nodes:
            xarxa._apply_routing(xarxa.mininet_nodes[name], name, p)


def _update_all_routes():
    """Restart routing on all routers (called after remove_node)."""
    for name, props in xarxa.nodes.items():
        if props['type'] == 'router' and name in xarxa.mininet_nodes:
            xarxa._apply_routing(xarxa.mininet_nodes[name], name, props)


# ─────────────────────────────────────────────
#  METRICS ROUTES
# ─────────────────────────────────────────────

@app.route('/ip_dashboard')
def ip_dashboard():
    flat    = []
    subnets = {}  # subnet_str -> list of members

    for name, props in xarxa.nodes.items():
        t = props['type']
        if t == 'host':
            ip = props.get('ip', '—')
            gw = props.get('gw', None)
            flat.append({'node': name, 'type': t, 'intf': 'eth0', 'ip': ip, 'gw': gw})
            # Group by subnet
            subnet = ip.rsplit('.', 1)[0] + '.0/' + ip.split('/')[1] if '/' in ip else ip
            subnets.setdefault(subnet, []).append(
                {'node': name, 'type': t, 'intf': 'eth0', 'ip': ip, 'gw': gw})

        elif t == 'router':
            for intf, ip in props.get('ips', {}).items():
                if intf == 'lan':
                    continue
                gw = None
                flat.append({'node': name, 'type': t, 'intf': intf, 'ip': ip, 'gw': gw})
                subnet = ip.rsplit('.', 1)[0] + '.0/' + ip.split('/')[1] if '/' in ip else ip
                subnets.setdefault(subnet, []).append(
                    {'node': name, 'type': t, 'intf': intf, 'ip': ip, 'gw': gw})

    # Sort flat by type priority then name
    type_order = {'router': 0, 'host': 1, 'switch': 2}
    flat.sort(key=lambda r: (type_order.get(r['type'], 9), r['node']))

    # Build sorted subnet list
    subnet_list = [
        {'subnet': s, 'members': sorted(members, key=lambda m: (m['type'], m['node']))}
        for s, members in sorted(subnets.items())
    ]

    return jsonify({'ok': True, 'flat': flat, 'subnets': subnet_list})


@app.route('/router_routes')
def get_router_routes():
    router = request.args.get('router')
    if not router or router not in xarxa.nodes:
        return jsonify({'ok': False, 'error': 'Router not found'})
    if xarxa.nodes[router]['type'] != 'router':
        return jsonify({'ok': False, 'error': f'{router} is not a router'})
    if not xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})

    node = xarxa.mininet_nodes[router]
    raw  = node.cmd('ip route show')
    routes = []
    for line in raw.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        dst = parts[0]
        via = None
        if 'via' in parts:
            via = parts[parts.index('via') + 1]
        routes.append({'dst': dst, 'via': via, 'raw': line})
    return jsonify({'ok': True, 'router': router, 'routes': routes})


@app.route('/router_routes', methods=['POST'])
def modify_router_route():
    data   = request.json
    router = data.get('router')
    action = data.get('action')  # 'add' or 'delete'

    if not router or router not in xarxa.nodes:
        return jsonify({'ok': False, 'error': 'Router not found'})
    if xarxa.nodes[router]['type'] != 'router':
        return jsonify({'ok': False, 'error': f'{router} is not a router'})
    if not xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})

    node = xarxa.mininet_nodes[router]

    if action == 'add':
        dst = data.get('dst', '').strip()
        via = data.get('via', '').strip()
        if not dst or not via:
            return jsonify({'ok': False, 'error': 'dst and via are required'})
        result = node.cmd(f'ip route replace {dst} via {via} 2>&1')
        if 'error' in result.lower() or 'invalid' in result.lower():
            return jsonify({'ok': False, 'error': result.strip()})
        # Update nodes dict so it stays in sync
        route_str = f'{dst} via {via}'
        if route_str not in xarxa.nodes[router].get('routes', []):
            xarxa.nodes[router].setdefault('routes', []).append(route_str)
        return jsonify({'ok': True})

    elif action == 'delete':
        dst = data.get('dst', '').strip()
        if not dst:
            return jsonify({'ok': False, 'error': 'dst is required'})
        result = node.cmd(f'ip route del {dst} 2>&1')
        if 'error' in result.lower() or 'no such' in result.lower():
            return jsonify({'ok': False, 'error': result.strip()})
        # Remove from nodes dict
        xarxa.nodes[router]['routes'] = [
            r for r in xarxa.nodes[router].get('routes', [])
            if not r.startswith(dst)
        ]
        return jsonify({'ok': True})

    return jsonify({'ok': False, 'error': f'Unknown action: {action}'})


@app.route('/open_wireshark', methods=['POST'])
def open_wireshark():
    data  = request.json
    node  = data.get('node')
    intf  = data.get('intf')

    if not node or node not in xarxa.nodes:
        return jsonify({'ok': False, 'error': 'Node not found'})
    if not xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})

    # Full interface name as seen by the kernel (e.g. r1-eth0)
    intf_full = f'{node}-{intf}'

    # Get the sudo user to open wireshark with the correct display
    sudo_user = os.environ.get('SUDO_USER', 'root')
    display   = os.environ.get('DISPLAY', ':0')

    try:
        subprocess.Popen(
            ['sudo', '-u', sudo_user,
             'bash', '-c',
             f'DISPLAY={display} wireshark -i {intf_full} -k &'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return jsonify({'ok': True, 'intf': intf_full})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


@app.route('/get_routing_mode')
def get_routing_mode():
    return jsonify({'ok': True, 'mode': xarxa.ROUTING_MODE})


@app.route('/set_routing_mode', methods=['POST'])
def set_routing_mode():
    mode = request.json.get('mode')
    if mode not in ('ospf', 'ospf_bfd', 'mpls', 'mpls_bfd', 'manual'):
        return jsonify({'ok': False, 'error': f'Unknown mode: {mode}'})
    xarxa.ROUTING_MODE = mode
    for name, props in xarxa.nodes.items():
        if props['type'] == 'router' and name in xarxa.mininet_nodes:
            xarxa._stop_routing(xarxa.mininet_nodes[name], name)
            xarxa._apply_routing(xarxa.mininet_nodes[name], name, props)
    return jsonify({'ok': True, 'mode': mode})


@app.route('/metrics/system')
def metrics_system():
    cpu_percent = psutil.cpu_percent(interval=0.3)
    ram         = psutil.virtual_memory()
    return jsonify({
        'ok': True,
        'cpu_percent':  cpu_percent,
        'ram_used_mb':  round(ram.used  / 1024 / 1024, 1),
        'ram_total_mb': round(ram.total / 1024 / 1024, 1),
        'ram_percent':  ram.percent
    })


@app.route('/metrics/ping')
def metrics_ping():
    """Fast ping-only measurement (no iperf). ~3s."""
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
    dst_ip   = xarxa.nodes[dst]['ip'].split('/')[0]

    ping_result = src_node.cmd(f'ping -c 10 -i 0.2 {dst_ip}')
    latency = {'min': None, 'avg': None, 'max': None}
    jitter  = None
    match = re.search(r'rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)', ping_result)
    if match:
        latency['min'] = float(match.group(1))
        latency['avg'] = float(match.group(2))
        latency['max'] = float(match.group(3))
        jitter         = float(match.group(4))

    return jsonify({'ok': True, 'src': src, 'dst': dst,
                    'latency_ms': latency, 'jitter_ms': jitter})


@app.route('/metrics/internal')
def metrics_internal():
    global metrics_running
    if not xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})
    if metrics_running:
        return jsonify({'ok': False, 'error': 'A measurement is already running'})

    src = request.args.get('src')
    dst = request.args.get('dst')
    if not src or not dst:
        return jsonify({'ok': False, 'error': 'src and dst parameters required'})
    if src not in xarxa.nodes or dst not in xarxa.nodes:
        return jsonify({'ok': False, 'error': 'Node not found'})
    if xarxa.nodes[src]['type'] != 'host' or xarxa.nodes[dst]['type'] != 'host':
        return jsonify({'ok': False, 'error': 'Both nodes must be hosts'})

    metrics_running = True
    src_node = xarxa.mininet_nodes[src]
    dst_node = xarxa.mininet_nodes[dst]
    dst_ip   = xarxa.nodes[dst]['ip'].split('/')[0]

    ping_result = src_node.cmd(f'ping -c 10 -i 0.2 {dst_ip}')
    latency = {'min': None, 'avg': None, 'max': None}
    jitter  = None
    rtt_match = re.search(r'rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)', ping_result)
    if rtt_match:
        latency['min'] = float(rtt_match.group(1))
        latency['avg'] = float(rtt_match.group(2))
        latency['max'] = float(rtt_match.group(3))
        jitter         = float(rtt_match.group(4))

    bandwidth, bw_values = {'min': None, 'avg': None, 'max': None}, []
    try:
        dst_node.cmd('pkill -f iperf 2>/dev/null; sleep 0.2')
        dst_node.sendCmd('iperf -s')
        time.sleep(0.5)
        for _ in range(10):
            iperf_out = src_node.cmd(f'iperf -c {dst_ip} -t 1 -f m')
            bw_match  = re.search(r'([\d.]+)\s+Mbits/sec', iperf_out)
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

    cpu_percent = psutil.cpu_percent(interval=0.5)
    ram         = psutil.virtual_memory()
    metrics_running = False

    return jsonify({
        'ok': True, 'src': src, 'dst': dst,
        'latency_ms': latency, 'jitter_ms': jitter, 'bandwidth_mbps': bandwidth,
        'system': {
            'cpu_percent':  cpu_percent,
            'ram_used_mb':  round(ram.used  / 1024 / 1024, 1),
            'ram_total_mb': round(ram.total / 1024 / 1024, 1),
            'ram_percent':  ram.percent
        }
    })


@app.route('/metrics/sync')
def metrics_sync():
    with sync_history_lock:
        history = list(sync_latency_history)
    if not history:
        return jsonify({'ok': True, 'history': [], 'stats': None})

    def safe_stats(values):
        values = [v for v in values if v is not None]
        if not values:
            return {'min': None, 'avg': None, 'max': None}
        return {
            'min': round(min(values), 2),
            'avg': round(sum(values) / len(values), 2),
            'max': round(max(values), 2),
        }

    def jitter(values):
        values = [v for v in values if v is not None]
        if len(values) < 2:
            return 0.0
        diffs = [abs(values[i] - values[i-1]) for i in range(1, len(values))]
        return round(sum(diffs) / len(diffs), 2)

    t_local   = [e.get('t_local_ms')   for e in history]
    t_network = [e.get('t_network_ms') for e in history]
    t_twin    = [e.get('t_twin_ms')    for e in history]

    return jsonify({
        'ok': True,
        'history': history,
        'stats': {
            'count':      len(history),
            # Decomposed stats
            't_local':    safe_stats(t_local),
            't_network':  safe_stats(t_network),
            't_twin':     safe_stats(t_twin),
            # Legacy fields kept for backwards compat
            'avg_ms':     safe_stats(t_network)['avg'],
            'min_ms':     safe_stats(t_network)['min'],
            'max_ms':     safe_stats(t_network)['max'],
            'jitter_ms':  jitter(t_network),
        }
    })


@app.route('/metrics/hosts')
def metrics_hosts():
    hosts = [name for name, props in xarxa.nodes.items() if props['type'] == 'host']
    return jsonify({'hosts': hosts})


@app.route('/metrics/global')
def metrics_global():
    global metrics_running
    if not xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})
    if metrics_running:
        return jsonify({'ok': False, 'error': 'A measurement is already running'})

    hosts = [
        n for n, p in xarxa.nodes.items()
        if p['type'] == 'host'
        and n in xarxa.mininet_nodes
        and xarxa.mininet_nodes[n].shell is not None
    ]
    if len(hosts) < 2:
        return jsonify({'ok': False, 'error': 'Need at least 2 hosts'})

    metrics_running = True
    pairs = [(hosts[i], hosts[j]) for i in range(len(hosts)) for j in range(i+1, len(hosts))]

    ping_results, ping_lock = {}, threading.Lock()

    def ping_pair(src, dst):
        src_node = xarxa.mininet_nodes[src]
        dst_ip   = xarxa.nodes[dst]['ip'].split('/')[0]
        out      = src_node.cmd(f'ping -c 5 -i 0.2 {dst_ip}')
        match    = re.search(r'rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)', out)
        if match:
            with ping_lock:
                ping_results[f'{src}->{dst}'] = {
                    'min': float(match.group(1)), 'avg': float(match.group(2)),
                    'max': float(match.group(3)), 'jitter': float(match.group(4)),
                }

    def get_parallel_groups(pairs):
        groups, remaining = [], list(pairs)
        while remaining:
            group, used = [], set()
            for pair in remaining[:]:
                s, d = pair
                if s not in used and d not in used:
                    group.append(pair); used.add(s); used.add(d); remaining.remove(pair)
            groups.append(group)
        return groups

    for group in get_parallel_groups(pairs):
        threads = [threading.Thread(target=ping_pair, args=(s, d)) for s, d in group]
        for t in threads: t.start()
        for t in threads: t.join()

    bw_results = {}
    for src, dst in pairs:
        src_node = xarxa.mininet_nodes[src]
        dst_node = xarxa.mininet_nodes[dst]
        dst_ip   = xarxa.nodes[dst]['ip'].split('/')[0]
        try:
            dst_node.cmd('pkill -f iperf 2>/dev/null; sleep 0.1')
            dst_node.sendCmd('iperf -s')
            time.sleep(0.3)
            bw_values = []
            for _ in range(3):
                out     = src_node.cmd(f'iperf -c {dst_ip} -t 1 -f m')
                bw_match = re.search(r'([\d.]+)\s+Mbits/sec', out)
                if bw_match:
                    bw_values.append(float(bw_match.group(1)))
            dst_node.sendInt()
            dst_node.waitOutput()
            if bw_values:
                bw_results[f'{src}->{dst}'] = {
                    'min': round(min(bw_values), 2),
                    'avg': round(sum(bw_values)/len(bw_values), 2),
                    'max': round(max(bw_values), 2),
                }
        except Exception as e:
            print(f'iperf error {src}->{dst}: {e}')
            try:
                dst_node.sendInt(); dst_node.waitOutput()
            except:
                pass

    def safe_stats(values):
        if not values: return {'min': None, 'avg': None, 'max': None}
        return {'min': round(min(values), 2), 'avg': round(sum(values)/len(values), 2), 'max': round(max(values), 2)}

    all_avg_lat = [v['avg']    for v in ping_results.values()]
    all_min_lat = [v['min']    for v in ping_results.values()]
    all_max_lat = [v['max']    for v in ping_results.values()]
    all_jitter  = [v['jitter'] for v in ping_results.values()]
    all_avg_bw  = [v['avg']    for v in bw_results.values()]
    all_min_bw  = [v['min']    for v in bw_results.values()]
    all_max_bw  = [v['max']    for v in bw_results.values()]

    cpu_percent = psutil.cpu_percent(interval=0.5)
    ram         = psutil.virtual_memory()
    metrics_running = False

    return jsonify({
        'ok': True,
        'pairs_tested':       len(pairs),
        'latency_ms':         safe_stats(all_avg_lat),
        'latency_min_ms':     round(min(all_min_lat), 2) if all_min_lat else None,
        'latency_max_ms':     round(max(all_max_lat), 2) if all_max_lat else None,
        'jitter_ms':          safe_stats(all_jitter),
        'bandwidth_mbps':     safe_stats(all_avg_bw),
        'bandwidth_min_mbps': round(min(all_min_bw), 2) if all_min_bw else None,
        'bandwidth_max_mbps': round(max(all_max_bw), 2) if all_max_bw else None,
        'per_pair':           {'latency': ping_results, 'bandwidth': bw_results},
        'system': {
            'cpu_percent':  cpu_percent,
            'ram_used_mb':  round(ram.used  / 1024 / 1024, 1),
            'ram_total_mb': round(ram.total / 1024 / 1024, 1),
            'ram_percent':  ram.percent
        }
    })

@app.route('/sync_metrics', methods=['POST'])
def update_sync_metrics():
    data = request.json
    entry = {
        'operation':    data.get('operation', 'External Update'),
        'latency_ms':   data.get('latency_ms'),
        't_local_ms':   data.get('t_local_ms'),
        't_network_ms': data.get('t_network_ms'),
        't_twin_ms':    data.get('t_twin_ms'),
        'timestamp':    data.get('timestamp', time.time())
    }
    with sync_history_lock:
        sync_latency_history.append(entry)
    return jsonify({'ok': True})
if __name__ == '__main__':
    t = threading.Thread(target=xarxa.start_network)
    t.daemon = True
    t.start()
    time.sleep(3)
    app.run(debug=False)