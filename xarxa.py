##### SCRIPT PYTHON QUE AIXECA UNA XARXA A MININET #####

from mininet.net import Mininet
from mininet.log import setLogLevel
from mininet.cli import CLI

# Si es desitja afegir més màquines (ja sigui hosts o routers), el procés és simple: S'afegeix una columna i una fila a la matriu d'adjacència que representarà la nova màquina i es definirà
# al diccionari de nodes aquesta nova màquina amb les seves propietats. 

# Matriu d'adjacència que representa la xarxa que tenim. Cada fila i columna representa una màquina i els uns indiquen que existeix un link entre ells.
matriu_xarxa = [
  # h1 h2 h3 h4 h5 r1 r2 sw1 sw2
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

# Diccionari de nodes de la xarxa (nodes i les seves propietats). 
nodes = {
    'h1' : {'tipus': 'host', 'ip': '10.1.0.2/24', 'gw': '10.1.0.1'},
    'h2' : {'tipus': 'host', 'ip': '10.1.0.3/24', 'gw': '10.1.0.1'},
    'h3' : {'tipus': 'host', 'ip': '10.2.0.2/24', 'gw': '10.2.0.1'},
    'h4' : {'tipus': 'host', 'ip': '10.2.0.3/24', 'gw': '10.2.0.1'},
    'h5' : {'tipus': 'host', 'ip': '10.2.0.4/24', 'gw': '10.2.0.1'},
    'r1' : {'tipus': 'router',
            'ips':{'eth0': '10.0.0.1/24', 'eth1': '10.1.0.1/24'},
            'rutes': ['10.2.0.0/24 via 10.0.0.2']},
    'r2' : {'tipus': 'router', 
            'ips':{'eth0': '10.0.0.2/24', 'eth1': '10.2.0.1/24'},
            'rutes': ['10.1.0.0/24 via 10.0.0.1']},
    'sw1' : {'tipus': 'switch'},
    'sw2' : {'tipus': 'switch'}
}

net = None
# Creem una llista buida on anirem guardant les diferents màquines.
mininet_nodes = {}

xarxa_llesta = False

# Funció que aixeca la xarxa
def iniciar_xarxa():
    global net, mininet_nodes, xarxa_llesta
    # Creem la xarxa buida.
    net = Mininet()
    # Creem les diferents màquines de la nostra xarxa.
    for nom, propietats in nodes.items():
        if propietats['tipus'] == 'host':
            mininet_nodes[nom] = net.addHost(nom, ip = propietats['ip'])
        elif propietats['tipus'] == 'router':
            mininet_nodes[nom] = net.addHost(nom, ip = propietats['ips']['eth0'])
        elif propietats['tipus'] == 'switch':
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
        if propietats['tipus'] == 'router':
            for eth, ip in propietats['ips'].items():
                mininet_nodes[nom].cmd(f'ifconfig {nom}-{eth} {ip}')
    # Activem IP Forwarding als routers
    for nom, propietats in nodes.items():
        if propietats['tipus'] == 'router':
            mininet_nodes[nom].cmd('sysctl -w net.ipv4.ip_forward=1')
    # Afegim les rutes per defecte als hosts
    for nom, propietats in nodes.items():
        if propietats['tipus'] == 'host':
            mininet_nodes[nom].cmd(f'ip route add default via {propietats["gw"]}')
    # Afegim les rutes als routers
    for nom, propietats in nodes.items():
        if propietats['tipus'] == 'router':
            for ruta in propietats['rutes']:
                mininet_nodes[nom].cmd(f'ip route add {ruta}')
    # Aturem la xarxa
    xarxa_llesta = True
    # net.stop()

def trobar_router_del_switch(switch):
    noms = list(nodes.keys())
    idx_switch = noms.index(switch)
    for i, val in enumerate(matriu_xarxa[idx_switch]):
        if val == 1 and nodes[noms[i]]['tipus'] == 'router':
            return noms[i]
    return None

def trobar_seguent_ip(router):
    # Agafem la IP del router cap a la subxarxa (eth1)
    ip_router = nodes[router]['ips']['eth1']  # ex: '10.1.0.1/24'
    # Agafem la base de la subxarxa
    base = ip_router.rsplit('.', 1)[0]  # ex: '10.1.0'
    mascara = ip_router.split('/')[1]   # ex: '24'
    
    # Recollim totes les IPs usades a aquesta subxarxa
    ips_usades = []
    for nom, propietats in nodes.items():
        if propietats['tipus'] == 'host' and 'ip' in propietats:
            ip = propietats['ip'].split('/')[0]  # ex: '10.1.0.2'
            if ip.startswith(base):
                ips_usades.append(int(ip.split('.')[-1]))  # ex: 2
    
    # Trobem el següent número lliure (comencem des del 2)
    seguent = 2
    while seguent in ips_usades:
        seguent += 1
    
    return f'{base}.{seguent}/{mascara}'

def actualitzar_matriu(nom, switch):
    noms = list(nodes.keys())  # encara sense el nou node
    idx_switch = noms.index(switch)
    
    # Afegim una columna de zeros a cada fila existent
    for fila in matriu_xarxa:
        fila.append(0)
    
    # Afegim una fila nova de zeros per al nou node
    nova_fila = [0] * (len(noms) + 1)
    matriu_xarxa.append(nova_fila)
    
    # L'índex del nou node és l'últim
    idx_nou = len(noms)  # ex: 9
    
    # Posem els 1s
    matriu_xarxa[idx_nou][idx_switch] = 1
    matriu_xarxa[idx_switch][idx_nou] = 1

def eliminar_de_matriu(nom):
    noms = list(nodes.keys())
    idx = noms.index(nom)
    matriu_xarxa.pop(idx)
    for fila in matriu_xarxa:
        fila.pop(idx)

def trobar_seguent_ip_router():
    base = '10.0.0'
    mascara = '24'
    ips_usades = []
    for nom, propietats in nodes.items():
        if propietats['tipus'] == 'router':    
            ip = propietats['ips']['eth0'].split('/')[0]
            ips_usades.append(int(ip.split('.')[-1]))
    seguent = 1
    while seguent in ips_usades:
        seguent += 1
    return f'{base}.{seguent}/{mascara}'

def trobar_seguent_subxarxa():
    subxarxes_usades = []
    for nom, propietats in nodes.items():
        if propietats['tipus'] == 'router':
            ip_eth1 = propietats['ips']['eth1'].split('/')[0]
            segon_octet = int(ip_eth1.split('.')[1])
            subxarxes_usades.append(segon_octet)
    seguent = 1
    while seguent in subxarxes_usades:
        seguent += 1
    return seguent

if __name__ == '__main__':
    setLogLevel('info')
    iniciar_xarxa()