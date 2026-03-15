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
                node_i = noms_nodes[i]
                node_j = noms_nodes[j]
                tipus_i = xarxa.nodes[node_i]['tipus']
                tipus_j = xarxa.nodes[node_j]['tipus']
                
                # Saltem els links que involucren switches
                if tipus_i == 'switch' or tipus_j == 'switch':
                    continue
                
                links.append({'from': node_i, 'to': node_j})
    
    # Afegim links directes router-host saltant el switch
    for nom_switch, props in xarxa.nodes.items():
        if props['tipus'] == 'switch':
            # Trobem el router i els hosts connectats a aquest switch
            idx_switch = noms_nodes.index(nom_switch)
            router = None
            hosts = []
            for i, val in enumerate(xarxa.matriu_xarxa[idx_switch]):
                if val == 1:
                    node = noms_nodes[i]
                    if xarxa.nodes[node]['tipus'] == 'router':
                        router = node
                    elif xarxa.nodes[node]['tipus'] == 'host':
                        hosts.append(node)
            # Creem links directes router-host
            if router:
                for host in hosts:
                    links.append({'from': router, 'to': host})
    
    return jsonify({'nodes': xarxa.nodes, 'links': links})

@app.route('/afegir_host', methods=['POST'])
def afegir_host():
    
    if not xarxa.xarxa_llesta:
        return jsonify({'ok': False, 'error': 'Xarxa no llesta'})
    
    dades = request.json
    nom = dades['nom']
    switch = dades['switch']
    
    if nom in xarxa.nodes:
        return jsonify({'ok': False, 'error': f'Ja existeix un node amb el nom {nom}'})

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
    
    # Si és un router, eliminem tota la subxarxa
    if xarxa.nodes[nom]['tipus'] == 'router':
        nodes_a_eliminar = xarxa.trobar_subxarxa_router(nom)
        nodes_a_eliminar.append(nom)  # afegim el router mateix
        for node in nodes_a_eliminar:
            xarxa.eliminar_de_matriu(node)
            node_mininet = xarxa.mininet_nodes[node]
            xarxa.net.delNode(node_mininet)
            del xarxa.mininet_nodes[node]
            del xarxa.nodes[node]
    else:
        # Eliminem només el node
        xarxa.eliminar_de_matriu(nom)
        node_mininet = xarxa.mininet_nodes[nom]
        xarxa.net.delNode(node_mininet)
        del xarxa.mininet_nodes[nom]
        del xarxa.nodes[nom]
    
    return jsonify({'ok': True})

@app.route('/afegir_router', methods=['POST'])
def afegir_router():
    
    if not xarxa.xarxa_llesta:
        return jsonify({'ok': False, 'error': 'Xarxa no llesta'})
    
    dades = request.json
    nom_router = dades['nom']
    router_connectat = dades['router_connectat']
    
    if nom_router in xarxa.nodes:
        return jsonify({'ok': False, 'error': f'Ja existeix un node amb el nom {nom_router}'})

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