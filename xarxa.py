##### SCRIPT PYTHON QUE AIXECA UNA XARXA A MININET #####

from mininet.net import Mininet
from mininet.log import setLogLevel
from mininet.cli import CLI

# Si es desitja afegir més màquines (ja sigui hosts o routers), el procés és simple: S'afegeix una columna i una fila a la matriu d'adjacència que representarà la nova màquina i es definirà
# al diccionari de nodes aquesta nova màquina amb les seves propietats. 

# Matriu d'adjacència que representa la xarxa que tenim. Cada fila i columna representa una màquina.
# Les cel·les amb valor 0 indiquen que no hi ha connexió. Les cel·les amb un string indiquen el tipus
# del node de la fila quan hi ha connexió (ex: 'host', 'router', 'switch').
matriu_xarxa = [
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
    # Comprovem != 0 en lloc de == 1 perquè ara les cel·les contenen strings o 0.
    for i in range(len(matriu_xarxa)):
        for j in range(i+1, len(matriu_xarxa)):
            if matriu_xarxa[i][j] != 0:
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
    xarxa_llesta = True
    # net.stop()

def trobar_router_del_switch(switch):
    noms = list(nodes.keys())
    idx_switch = noms.index(switch)
    for i, val in enumerate(matriu_xarxa[idx_switch]):
        # Comprovem != 0 per detectar connexió, i després mirem el tipus
        if val != 0 and nodes[noms[i]]['tipus'] == 'router':
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
    idx_nou = len(noms)

    # Posem el tipus de cada node a la cel·la corresponent
    tipus_nou    = nodes[nom]['tipus']     # tipus del nou node (ex: 'host')
    tipus_switch = nodes[switch]['tipus']  # tipus del switch (ex: 'switch')

    matriu_xarxa[idx_nou][idx_switch] = tipus_nou
    matriu_xarxa[idx_switch][idx_nou] = tipus_switch

def actualitzar_matriu_multi(nom, connectats):
    noms = list(nodes.keys())
    
    # Afegim una columna de zeros a cada fila existent
    for fila in matriu_xarxa:
        fila.append(0)
    
    # Afegim una fila nova de zeros per al nou node
    nova_fila = [0] * (len(noms) + 1)
    matriu_xarxa.append(nova_fila)
    
    # L'índex del nou node és l'últim
    idx_nou = len(noms)
    
    # Posem el tipus de cada node a la cel·la corresponent per a cada connexió
    tipus_nou = nodes[nom]['tipus']  # tipus del nou node (ex: 'router')
    for connectat in connectats:
        idx_connectat = noms.index(connectat)
        tipus_connectat = nodes[connectat]['tipus']  # tipus del node connectat
        matriu_xarxa[idx_nou][idx_connectat] = tipus_nou
        matriu_xarxa[idx_connectat][idx_nou] = tipus_connectat

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

def trobar_subxarxa_router(router):
    base = nodes[router]['ips']['eth1'].rsplit('.', 1)[0]
    nodes_a_eliminar = []
    for nom, propietats in nodes.items():
        if propietats['tipus'] == 'host' and 'ip' in propietats:
            ip = propietats['ip'].split('/')[0]
            if ip.startswith(base):
                nodes_a_eliminar.append(nom)
    noms = list(nodes.keys())
    idx_router = noms.index(router)
    for i, val in enumerate(matriu_xarxa[idx_router]):
        # Comprovem != 0 per detectar connexió, i després mirem el tipus
        if val != 0 and nodes[noms[i]]['tipus'] == 'switch':
            nodes_a_eliminar.append(noms[i])
    return nodes_a_eliminar

def trobar_switch_del_router(router):
    noms = list(nodes.keys())
    idx_router = noms.index(router)
    for i, val in enumerate(matriu_xarxa[idx_router]):
        # Comprovem != 0 per detectar connexió, i després mirem el tipus
        if val != 0 and nodes[noms[i]]['tipus'] == 'switch':
            return noms[i]
    return None


if __name__ == '__main__':
    setLogLevel('info')
    iniciar_xarxa()
