from mininet.net import Mininet
from mininet.log import setLogLevel
from mininet.cli import CLI

# Matriu d'adjacència que representa la xarxa que tenim. Cada fila i columna representa una màquina i els uns representen que existeix una connexió entre ells.
matriu_xarxa = [
    [0, 0, 0, 0, 0, 0, 0, 1, 0],  # h1
    [0, 0, 0, 0, 0, 0, 0, 1, 0],  # h2
    [0, 0, 0, 0, 0, 0, 0, 0, 1],  # h3
    [0, 0, 0, 0, 0, 0, 0, 0, 1],  # h4
    [0, 0, 0, 0, 0, 0, 0, 0, 1],  # h5
    [0, 0, 0, 0, 0, 0, 1, 1, 0],  # r1
    [0, 0, 0, 0, 0, 1, 0, 0, 1],  # r2
    [1, 1, 0, 0, 0, 1, 0, 0, 0],  # sw1
    [0, 0, 1, 1, 1, 0, 1, 0, 0],  # sw2
]

# Diccionari dels nodes de la xarxa.
nodes = {
    'h1' : {'Tipus': 'host', 'ip': '10.1.0.2/24'},
    'h2' : {'Tipus': 'host', 'ip': '10.1.0.3/24'},
    'h3' : {'Tipus': 'host', 'ip': '10.2.0.2/24'},
    'h4' : {'Tipus': 'host', 'ip': '10.2.0.3/24'},
    'h5' : {'Tipus': 'host', 'ip': '10.2.0.4/24'},
    'r1' : {'Tipus': 'router', 'ips':{'eth0': '10.0.0.1/24', 'eth1': '10.1.0.1/24'}},
    'r2' : {'Tipus': 'router', 'ips':{'eth0': '10.0.0.2/24', 'eth1': '10.2.0.1/24'}},
    'sw1' : {'Tipus': 'switch'},
    'sw2' : {'Tipus': 'switch'}
}

# Funció que aixeca la xarxa
def iniciar_xarxa():
    # Creem la xarxa buida.
    net = Mininet()
    # Creem una llista buida on anirem guardant les diferents màquines.
    mininet_nodes = {}
    # Creem les diferents màquines de la nostra xarxa.
    for nom, propietats in nodes.items():
        if propietats['Tipus'] == 'host':
            mininet_nodes[nom] = net.addHost(nom, ip = propietats['ip'])
        elif propietats['Tipus'] == 'router':
            mininet_nodes[nom] = net.addHost(nom, ip = propietats['ips']['eth0'])
        elif propietats['Tipus'] == 'switch':
            mininet_nodes[nom] = net.addSwitch(nom, failMode = 'standalone')
    # Creem una llista amb els noms dels nodes en el ordre de la matriu.
    noms_nodes = list(nodes.keys())
    # Creem els links entre les diferents màquines a partir de la matriu.
    for i in range(len(matriu_xarxa)):
        for j in range(i+1, len(matriu_xarxa)):
            if matriu_xarxa[i][j] == 1:
                net.addLink(mininet_nodes[noms_nodes[i]], mininet_nodes[noms_nodes[j]])
    # Arranquem la xarxa
    net.start()
    # Definim les IPs de les interfícies dels routers
    for nom, propietats in nodes.items():
        if propietats['Tipus'] == 'router':
            for eth, ip in propietats['ips'].items():
                mininet_nodes[nom].cmd(f'ifconfig {nom}-{eth} {ip}')
    # Activem IP Forwarding als routers
    for nom, propietats in nodes.items():
        if propietats['Tipus'] == 'router':
            mininet_nodes[nom].cmd('sysctl -w net.ipv4.ip_forward=1')
    # Afegim les rutes per defecte als hosts
    mininet_nodes['h1'].cmd('ip route add default via 10.1.0.1')
    mininet_nodes['h2'].cmd('ip route add default via 10.1.0.1')
    mininet_nodes['h3'].cmd('ip route add default via 10.2.0.1')
    mininet_nodes['h4'].cmd('ip route add default via 10.2.0.1')
    mininet_nodes['h5'].cmd('ip route add default via 10.2.0.1')
    # Afegim les rutes als routers
    mininet_nodes['r1'].cmd('ip route add 10.2.0.0/24 via 10.0.0.2')
    mininet_nodes['r2'].cmd('ip route add 10.1.0.0/24 via 10.0.0.1')
    CLI(net)
    # Aturem la xarxa
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    iniciar_xarxa()