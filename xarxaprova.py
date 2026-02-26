from mininet.net import Mininet
from mininet.log import setLogLevel
from mininet.cli import CLI

# Funció que aixeca la xarxa
def iniciar_xarxa():
    # Creem la xarxa buida.
    net = Mininet()
    # Afegim els switches, hosts i routers a la xarxa.
    sw1 = net.addSwitch('sw1', failMode='standalone')
    sw2 = net.addSwitch('sw2', failMode='standalone')
    h1 = net.addHost('h1', ip='10.1.0.2/24')
    h2 = net.addHost('h2', ip='10.1.0.3/24')
    h3 = net.addHost('h3', ip='10.2.0.2/24')
    h4 = net.addHost('h4', ip='10.2.0.3/24')
    h5 = net.addHost('h5', ip='10.2.0.4/24')
    r1 = net.addHost('r1', ip='10.0.0.1/24')
    r2 = net.addHost('r2', ip='10.0.0.2/24')
    # Afegim els links entre els components de la xarxa.
    net.addLink(r1, r2)
    net.addLink(r1, sw1)
    net.addLink(r2, sw2)
    net.addLink(sw1, h1)
    net.addLink(sw1, h2)
    net.addLink(sw2, h3)
    net.addLink(sw2, h4)
    net.addLink(sw2, h5)
    # Arranquem la xarxa
    net.start()
    # Definim les IPs de les diferentes interfícies
    r1.cmd('ifconfig r1-eth1 10.1.0.1/24')
    r2.cmd('ifconfig r2-eth1 10.2.0.1/24')
    # Activem IP Forwarding als routers
    r1.cmd('sysctl -w net.ipv4.ip_forward=1')
    r2.cmd('sysctl -w net.ipv4.ip_forward=1')
    # Afegim les rutes per defecte als hosts
    h1.cmd('ip route add default via 10.1.0.1')
    h2.cmd('ip route add default via 10.1.0.1')
    h3.cmd('ip route add default via 10.2.0.1')
    h4.cmd('ip route add default via 10.2.0.1')
    h5.cmd('ip route add default via 10.2.0.1')
    # Afegim les rutes als routers
    r1.cmd('ip route add 10.2.0.0/24 via 10.0.0.2')
    r2.cmd('ip route add 10.1.0.0/24 via 10.0.0.1')
    CLI(net)
    # Aturem la xarxa
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    iniciar_xarxa()