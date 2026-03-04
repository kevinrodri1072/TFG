from flask import Flask, render_template, jsonify, request
import xarxa
import threading
import time

app = Flask(__name__)



@app.route('/')
def index():
    return render_template('index.html')

@app.route('/topologia')
def topologia():
    noms_nodes = list(xarxa.nodes.keys())
    links = []
    for i in range(len(xarxa.matriu_xarxa)):
        for j in range(i+1, len(xarxa.matriu_xarxa)):
            if xarxa.matriu_xarxa[i][j] == 1:
                links.append({'from': noms_nodes[i], 'to': noms_nodes[j]})
    return jsonify({'nodes': xarxa.nodes, 'links': links})

@app.route('/afegir_host', methods=['POST'])
def afegir_host():
    if not xarxa.xarxa_llesta:
        return jsonify({'ok': False, 'error': 'Xarxa no llesta'})
    
    dades = request.json
    nom = dades['nom']
    switch = dades['switch']
    
    # Calculem IP i gateway automàticament
    router = xarxa.trobar_router_del_switch(switch)
    ip = xarxa.trobar_seguent_ip(router)
    gw = xarxa.nodes[router]['ips']['eth1'].split('/')[0]
    
    # Afegim al diccionari de nodes
    xarxa.actualitzar_matriu(nom, switch)
    xarxa.nodes[nom] = {'tipus': 'host', 'ip': ip, 'gw': gw}
    
    # Afegim a Mininet
    nou_host = xarxa.net.addHost(nom, ip=ip)
    xarxa.mininet_nodes[nom] = nou_host
    
    # Calculem el nom de la nova interfície del switch
    sw_node = xarxa.mininet_nodes[switch]
    num_intfs = len(sw_node.intfList())
    sw_intf_name = f'{switch}-eth{num_intfs}'
    
    # Connectem al switch
    xarxa.net.addLink(nou_host, sw_node,
                      intfName1=f'{nom}-eth0',
                      intfName2=sw_intf_name)
    
    # Activem les interfícies
    nou_host.cmd(f'ifconfig {nom}-eth0 {ip}')
    nou_host.cmd(f'ip route add default via {gw}')
    
    return jsonify({'ok': True})

@app.route('/eliminar_node', methods=['POST'])
def eliminar_node():
    if not xarxa.xarxa_llesta:
        return jsonify({'ok': False, 'error': 'Xarxa no llesta'})
    
    dades = request.json
    nom = dades['nom']
    
    # Eliminem de la matriu i del diccionari
    xarxa.eliminar_de_matriu(nom)
    del xarxa.nodes[nom]
    
    # Eliminem de Mininet
    node = xarxa.mininet_nodes[nom]
    xarxa.net.delNode(node)
    del xarxa.mininet_nodes[nom]
    
    return jsonify({'ok': True})

@app.route('/afegir_router', methods=['POST'])
def afegir_router():
    if not xarxa.xarxa_llesta:
        return jsonify({'ok': False, 'error': 'Xarxa no llesta'})
    
    dades = request.json
    nom_router = dades['nom']
    router_connectat = dades['router_connectat']
    
    # Calculem el nom del nou switch
    num_switch = len([n for n, p in xarxa.nodes.items() if p['tipus'] == 'switch']) + 1
    nom_switch = f'sw{num_switch}'
    
    # Calculem les IPs
    ip_eth0 = xarxa.trobar_seguent_ip_router()
    seg_subxarxa = xarxa.trobar_seguent_subxarxa()
    ip_eth1 = f'10.{seg_subxarxa}.0.1/24'
    
    xarxa.actualitzar_matriu(nom_router, router_connectat)
    
    # Afegim al diccionari
    xarxa.nodes[nom_router] = {
        'tipus': 'router',
        'ips': {'eth0': ip_eth0, 'eth1': ip_eth1},
        'rutes': []
    }
    
    # Actualitzem la matriu
    xarxa.actualitzar_matriu(nom_switch, nom_router)
    xarxa.nodes[nom_switch] = {'tipus': 'switch'}
    
    # Afegim a Mininet
    nou_router = xarxa.net.addHost(nom_router, ip=ip_eth0)
    nou_switch = xarxa.net.addSwitch(nom_switch, failMode='standalone')
    xarxa.mininet_nodes[nom_router] = nou_router
    xarxa.mininet_nodes[nom_switch] = nou_switch
    nou_switch.start([])
    
    # Connectem a Mininet
    xarxa.net.addLink(nou_router, xarxa.mininet_nodes[router_connectat])
    xarxa.net.addLink(nou_router, nou_switch)
    
    # Configurem IPs i forwarding
    nou_router.cmd(f'ifconfig {nom_router}-eth0 {ip_eth0}')
    nou_router.cmd(f'ifconfig {nom_router}-eth1 {ip_eth1}')
    nou_router.cmd('sysctl -w net.ipv4.ip_forward=1')
    
    return jsonify({'ok': True})

if __name__ == '__main__':
    t = threading.Thread(target=xarxa.iniciar_xarxa)
    t.daemon = True
    t.start()
    time.sleep(3)  # Esperem que Mininet arrenqui
    app.run(debug=False)