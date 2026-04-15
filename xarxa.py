##### PYTHON SCRIPT THAT STARTS A MININET NETWORK #####

from mininet.net import Mininet
from mininet.log import setLogLevel
from mininet.cli import CLI

# To add more machines (hosts or routers), the process is simple: add a column and a row to the
# adjacency matrix representing the new machine, and define the new machine in the node dictionary
# with its properties.

# Adjacency matrix representing the network. Each row and column represents a machine.
# Cells with value 0 indicate no connection. Cells with a string indicate the type
# of the row node when there is a connection (e.g. 'host', 'router', 'switch').
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

# Node dictionary with each node's properties.
nodes = {
    'h1' : {'type': 'host', 'ip': '10.1.0.2/24', 'gw': '10.1.0.1'},
    'h2' : {'type': 'host', 'ip': '10.1.0.3/24', 'gw': '10.1.0.1'},
    'h3' : {'type': 'host', 'ip': '10.2.0.2/24', 'gw': '10.2.0.1'},
    'h4' : {'type': 'host', 'ip': '10.2.0.3/24', 'gw': '10.2.0.1'},
    'h5' : {'type': 'host', 'ip': '10.2.0.4/24', 'gw': '10.2.0.1'},
    'r1' : {'type': 'router',
            'ips': {'eth0': '10.0.0.1/24', 'eth1': '10.1.0.1/24'},
            'routes': ['10.2.0.0/24 via 10.0.0.2']},
    'r2' : {'type': 'router',
            'ips': {'eth0': '10.0.0.2/24', 'eth1': '10.2.0.1/24'},
            'routes': ['10.1.0.0/24 via 10.0.0.1']},
    'sw1' : {'type': 'switch'},
    'sw2' : {'type': 'switch'}
}

net = None
# Empty dictionary to store Mininet node objects.
mininet_nodes = {}

network_ready = False

# Function that starts the network
def start_network():
    global net, mininet_nodes, network_ready
    # Create empty network.
    net = Mininet()
    # Create all network machines.
    for name, props in nodes.items():
        if props['type'] == 'host':
            mininet_nodes[name] = net.addHost(name, ip=props['ip'])
        elif props['type'] == 'router':
            mininet_nodes[name] = net.addHost(name, ip=props['ips']['eth0'])
        elif props['type'] == 'switch':
            mininet_nodes[name] = net.addSwitch(name, failMode='standalone')
    # Create list of node names in matrix order.
    node_names = list(nodes.keys())
    # Create links between machines based on the adjacency matrix.
    # We check != 0 instead of == 1 because cells now contain strings or 0.
    for i in range(len(network_matrix)):
        for j in range(i + 1, len(network_matrix)):
            if network_matrix[i][j] != 0:
                net.addLink(mininet_nodes[node_names[i]], mininet_nodes[node_names[j]])
    # Start the network
    net.start()
    # Assign IPs to router interfaces
    for name, props in nodes.items():
        if props['type'] == 'router':
            for eth, ip in props['ips'].items():
                mininet_nodes[name].cmd(f'ifconfig {name}-{eth} {ip}')
    # Enable IP forwarding on routers
    for name, props in nodes.items():
        if props['type'] == 'router':
            mininet_nodes[name].cmd('sysctl -w net.ipv4.ip_forward=1')
    # Add default routes on hosts
    for name, props in nodes.items():
        if props['type'] == 'host':
            mininet_nodes[name].cmd(f'ip route add default via {props["gw"]}')
    # Add routes on routers
    for name, props in nodes.items():
        if props['type'] == 'router':
            for route in props['routes']:
                mininet_nodes[name].cmd(f'ip route add {route}')
    network_ready = True
    # net.stop()

# Function that restarts the network with a new topology (matrix + nodes)
def restart_network(new_matrix, new_nodes):
    global net, mininet_nodes, network_ready, network_matrix, nodes

    # Mark network as not ready while restarting
    network_ready = False

    # Stop current network
    if net is not None:
        net.stop()

    # Clear Mininet nodes
    mininet_nodes = {}

    # Replace matrix and node dictionary
    network_matrix.clear()
    for row in new_matrix:
        network_matrix.append(row)

    nodes.clear()
    nodes.update(new_nodes)

    # Restart network with new topology
    start_network()

def find_router_of_switch(switch):
    names = list(nodes.keys())
    switch_idx = names.index(switch)
    for i, val in enumerate(network_matrix[switch_idx]):
        # Check != 0 to detect connection, then check type
        if val != 0 and nodes[names[i]]['type'] == 'router':
            return names[i]
    return None

def find_next_ip(router):
    # Get the router IP towards the subnet (eth1)
    router_ip = nodes[router]['ips']['eth1']  # e.g. '10.1.0.1/24'
    # Get subnet base
    base = router_ip.rsplit('.', 1)[0]  # e.g. '10.1.0'
    mask = router_ip.split('/')[1]      # e.g. '24'

    # Collect all used IPs in this subnet
    used_ips = []
    for name, props in nodes.items():
        if props['type'] == 'host' and 'ip' in props:
            ip = props['ip'].split('/')[0]  # e.g. '10.1.0.2'
            if ip.startswith(base):
                used_ips.append(int(ip.split('.')[-1]))  # e.g. 2

    # Find next available number (starting from 2)
    next_num = 2
    while next_num in used_ips:
        next_num += 1

    return f'{base}.{next_num}/{mask}'

def update_matrix(name, switch):
    names = list(nodes.keys())
    switch_idx = names.index(switch)

    # Add a zero column to each existing row
    for row in network_matrix:
        row.append(0)

    # Add a new zero row for the new node
    new_row = [0] * len(network_matrix[0])
    network_matrix.append(new_row)

    # The new node index is the last one
    new_idx = len(network_matrix) - 1

    # Set the type of each node in the corresponding cell
    new_type    = nodes[name]['type']
    switch_type = nodes[switch]['type']
    network_matrix[new_idx][switch_idx] = new_type
    network_matrix[switch_idx][new_idx] = switch_type

def update_matrix_multi(name, connected):
    names = list(nodes.keys())

    # Add a zero column to each existing row
    for row in network_matrix:
        row.append(0)

    # Add a new zero row for the new node
    new_row = [0] * len(network_matrix[0])
    network_matrix.append(new_row)

    # The new node index is the last one
    new_idx = len(network_matrix) - 1

    # Set the type of each node in the corresponding cell for each connection
    new_type = nodes[name]['type']  # type of new node (e.g. 'router')
    for conn in connected:
        conn_idx = names.index(conn)
        conn_type = nodes[conn]['type']  # type of connected node
        network_matrix[new_idx][conn_idx] = new_type
        network_matrix[conn_idx][new_idx] = conn_type

def remove_from_matrix(name):
    names = list(nodes.keys())
    idx = names.index(name)
    network_matrix.pop(idx)
    for row in network_matrix:
        row.pop(idx)

def find_next_router_ip():
    base = '10.0.0'
    mask = '24'
    used_ips = []
    for name, props in nodes.items():
        if props['type'] == 'router':
            ip = props['ips']['eth0'].split('/')[0]
            used_ips.append(int(ip.split('.')[-1]))
    next_num = 1
    while next_num in used_ips:
        next_num += 1
    return f'{base}.{next_num}/{mask}'

def find_next_subnet():
    used_subnets = []
    for name, props in nodes.items():
        if props['type'] == 'router':
            ip_eth1 = props['ips']['eth1'].split('/')[0]
            second_octet = int(ip_eth1.split('.')[1])
            used_subnets.append(second_octet)
    next_num = 1
    while next_num in used_subnets:
        next_num += 1
    return next_num

def find_router_subnet(router):
    base = nodes[router]['ips']['eth1'].rsplit('.', 1)[0]
    nodes_to_remove = []
    for name, props in nodes.items():
        if props['type'] == 'host' and 'ip' in props:
            ip = props['ip'].split('/')[0]
            if ip.startswith(base):
                nodes_to_remove.append(name)
    names = list(nodes.keys())
    router_idx = names.index(router)
    for i, val in enumerate(network_matrix[router_idx]):
        # Check != 0 to detect connection, then check type
        if val != 0 and nodes[names[i]]['type'] == 'switch':
            nodes_to_remove.append(names[i])
    return nodes_to_remove

def find_switch_of_router(router):
    names = list(nodes.keys())
    router_idx = names.index(router)
    for i, val in enumerate(network_matrix[router_idx]):
        # Check != 0 to detect connection, then check type
        if val != 0 and nodes[names[i]]['type'] == 'switch':
            return names[i]
    return None


if __name__ == '__main__':
    setLogLevel('info')
    start_network()