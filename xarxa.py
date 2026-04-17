##### PYTHON SCRIPT THAT STARTS A MININET NETWORK #####

from mininet.net import Mininet
from mininet.log import setLogLevel
from mininet.cli import CLI

# Adjacency matrix representing the network.
network_matrix = [
  # h1       h2       h3       h4       h5       r1         r2        sw1       sw2
    [0,       0,       0,       0,       0,       0,         0,       'host',    0      ],  # h1
    [0,       0,       0,       0,       0,       0,         0,       'host',    0      ],  # h2
    [0,       0,       0,       0,       0,       0,         0,        0,       'host'  ],  # h3
    [0,       0,       0,       0,       0,       0,         0,        0,       'host'  ],  # h4
    [0,       0,       0,       0,       0,       0,         0,        0,       'host'  ],  # h5
    [0,       0,       0,       0,       0,       0,        'router', 'router',  0      ],  # r1
    [0,       0,       0,       0,       0,      'router',   0,        0,       'router'],  # r2
    ['switch','switch', 0,       0,       0,      'switch',  0,        0,        0      ],  # sw1
    [0,       0,      'switch','switch','switch',  0,       'switch',  0,        0      ],  # sw2
]

# Node dictionary.
# p2p_links: list of {peer, local_ip, peer_ip, subnet} for each point-to-point link with another router.
nodes = {
    'h1' : {'type': 'host', 'ip': '10.1.0.2/24', 'gw': '10.1.0.1'},
    'h2' : {'type': 'host', 'ip': '10.1.0.3/24', 'gw': '10.1.0.1'},
    'h3' : {'type': 'host', 'ip': '10.2.0.2/24', 'gw': '10.2.0.1'},
    'h4' : {'type': 'host', 'ip': '10.2.0.3/24', 'gw': '10.2.0.1'},
    'h5' : {'type': 'host', 'ip': '10.2.0.4/24', 'gw': '10.2.0.1'},
    'r1' : {
        'type': 'router',
        'ips': {'eth0': '10.0.0.1/30', 'eth1': '10.1.0.1/24', 'lan': '10.1.0.1/24'},
        'routes': ['10.2.0.0/24 via 10.0.0.2'],
        'p2p_links': [{'peer': 'r2', 'local_ip': '10.0.0.1', 'peer_ip': '10.0.0.2', 'subnet': '10.0.0.0/30', 'local_intf': 'eth0'}]
    },
    'r2' : {
        'type': 'router',
        'ips': {'eth0': '10.0.0.2/30', 'eth1': '10.2.0.1/24', 'lan': '10.2.0.1/24'},
        'routes': ['10.1.0.0/24 via 10.0.0.1'],
        'p2p_links': [{'peer': 'r1', 'local_ip': '10.0.0.2', 'peer_ip': '10.0.0.1', 'subnet': '10.0.0.0/30', 'local_intf': 'eth0'}]
    },
    'sw1' : {'type': 'switch'},
    'sw2' : {'type': 'switch'}
}

net = None
mininet_nodes = {}
network_ready = False

def start_network():
    global net, mininet_nodes, network_ready
    net = Mininet()
    for name, props in nodes.items():
        if props['type'] == 'host':
            mininet_nodes[name] = net.addHost(name, ip=props['ip'])
        elif props['type'] == 'router':
            mininet_nodes[name] = net.addHost(name, ip=props['ips']['eth0'])
        elif props['type'] == 'switch':
            mininet_nodes[name] = net.addSwitch(name, failMode='standalone')

    node_names = list(nodes.keys())
    for i in range(len(network_matrix)):
        for j in range(i + 1, len(network_matrix)):
            if network_matrix[i][j] != 0:
                net.addLink(mininet_nodes[node_names[i]], mininet_nodes[node_names[j]])

    net.start()

    for name, props in nodes.items():
        if props['type'] == 'router':
            for eth, ip in props['ips'].items():
                if eth == 'lan':
                    continue  
                mininet_nodes[name].cmd(f'ifconfig {name}-{eth} {ip}')

    for name, props in nodes.items():
        if props['type'] == 'router':
            mininet_nodes[name].cmd('sysctl -w net.ipv4.ip_forward=1')

    for name, props in nodes.items():
        if props['type'] == 'host':
            mininet_nodes[name].cmd(f'ip route add default via {props["gw"]}')

    for name, props in nodes.items():
        if props['type'] == 'router':
            for route in props['routes']:
                mininet_nodes[name].cmd(f'ip route add {route}')

    network_ready = True

def restart_network(new_matrix, new_nodes):
    global net, mininet_nodes, network_ready, network_matrix, nodes
    network_ready = False
    if net is not None:
        net.stop()
    mininet_nodes = {}
    network_matrix.clear()
    for row in new_matrix:
        network_matrix.append(row)
    nodes.clear()
    nodes.update(new_nodes)
    start_network()

def find_router_of_switch(switch):
    names = list(nodes.keys())
    switch_idx = names.index(switch)
    for i, val in enumerate(network_matrix[switch_idx]):
        if val != 0 and nodes[names[i]]['type'] == 'router':
            return names[i]
    return None

def find_next_ip(router):
    router_ip = next(ip for ip in nodes[router]['ips'].values() if '/24' in ip)
    base = router_ip.rsplit('.', 1)[0]
    mask = router_ip.split('/')[1]
    used_ips = []
    for name, props in nodes.items():
        if props['type'] == 'host' and 'ip' in props:
            ip = props['ip'].split('/')[0]
            if ip.startswith(base):
                used_ips.append(int(ip.split('.')[-1]))
    next_num = 2
    while next_num in used_ips:
        next_num += 1
    return f'{base}.{next_num}/{mask}'

def update_matrix(name, switch):
    names = list(nodes.keys())
    switch_idx = names.index(switch)
    for row in network_matrix:
        row.append(0)
    new_row = [0] * len(network_matrix[0])
    network_matrix.append(new_row)
    new_idx = len(network_matrix) - 1
    new_type    = nodes[name]['type']
    switch_type = nodes[switch]['type']
    network_matrix[new_idx][switch_idx] = new_type
    network_matrix[switch_idx][new_idx] = switch_type

def update_matrix_multi(name, connected):
    names = list(nodes.keys())
    for row in network_matrix:
        row.append(0)
    new_row = [0] * len(network_matrix[0])
    network_matrix.append(new_row)
    new_idx = len(network_matrix) - 1
    new_type = nodes[name]['type']
    for conn in connected:
        conn_idx = names.index(conn)
        conn_type = nodes[conn]['type']
        network_matrix[new_idx][conn_idx] = new_type
        network_matrix[conn_idx][new_idx] = conn_type

def remove_from_matrix(name):
    names = list(nodes.keys())
    idx = names.index(name)
    network_matrix.pop(idx)
    for row in network_matrix:
        row.pop(idx)

def find_next_subnet():
    used_subnets = []
    for name, props in nodes.items():
        if props['type'] == 'router':
            ip_lan = next(ip for ip in props['ips'].values() if '/24' in ip)
            second_octet = int(ip_lan.split('/')[0].split('.')[1])
            used_subnets.append(second_octet)
    next_num = 1
    while next_num in used_subnets:
        next_num += 1
    return next_num

def find_next_p2p_subnet():
    """
    Returns the next available /30 point-to-point subnet for router-router links.
    Subnets are in the form 10.0.X.0/30, X starting from 0.
    Each /30 has 4 IPs: .0 (network), .1 (router A), .2 (router B), .3 (broadcast).
    """
    used = set()
    for name, props in nodes.items():
        if props['type'] == 'router':
            for link in props.get('p2p_links', []):
                # Extract X from 10.0.X.0/30
                subnet = link['subnet']  # e.g. '10.0.0.0/30'
                third_octet = int(subnet.split('.')[2])
                used.add(third_octet)
    x = 0
    while x in used:
        x += 1
    return {
        'subnet': f'10.0.{x}.0/30',
        'ip_a': f'10.0.{x}.1',
        'ip_b': f'10.0.{x}.2',
    }

def find_router_subnet(router):
    lan_ip = next(ip for ip in nodes[router]['ips'].values() if '/24' in ip)
    base = lan_ip.split('/')[0].rsplit('.', 1)[0]
    nodes_to_remove = []
    for name, props in nodes.items():
        if props['type'] == 'host' and 'ip' in props:
            ip = props['ip'].split('/')[0]
            if ip.startswith(base):
                nodes_to_remove.append(name)
    names = list(nodes.keys())
    router_idx = names.index(router)
    for i, val in enumerate(network_matrix[router_idx]):
        if val != 0 and nodes[names[i]]['type'] == 'switch':
            nodes_to_remove.append(names[i])
    return nodes_to_remove

def find_switch_of_router(router):
    names = list(nodes.keys())
    router_idx = names.index(router)
    for i, val in enumerate(network_matrix[router_idx]):
        if val != 0 and nodes[names[i]]['type'] == 'switch':
            return names[i]
    return None

def get_all_host_subnets():
    """Returns list of all host subnets (eth1 subnets of all routers)."""
    subnets = []
    for name, props in nodes.items():
        if props['type'] == 'router':
            ip_eth1 = props['ips']['eth1'].split('/')[0]
            base = ip_eth1.rsplit('.', 1)[0]
            subnets.append({'subnet': f'{base}.0/24', 'router': name})
    return subnets

if __name__ == '__main__':
    setLogLevel('info')
    start_network()