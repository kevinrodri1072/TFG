##### PYTHON SCRIPT THAT STARTS A MININET NETWORK #####

# imports
from mininet.net import Mininet
from mininet.log import setLogLevel
from mininet.cli import CLI
import threading
import os

# Default topology (5 hosts with two routers)
DEFAULT_MATRIX = [
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

DEFAULT_NODES = {
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

# FRRouting paths
OSPFD = '/usr/lib/frr/ospfd'
ZEBRA = '/usr/lib/frr/zebra'
LDPD  = '/usr/lib/frr/ldpd'
BFDD  = '/usr/lib/frr/bfdd'


class Xarxa:
    def __init__(self):
        self.network_matrix = [row[:] for row in DEFAULT_MATRIX]
        self.nodes = {k: dict(v) for k, v in DEFAULT_NODES.items()}
        self.net = None
        self.mininet_nodes = {}
        self.network_ready = False
        self.routing_mode = 'ospf'  # 'ospf', 'ospf_bfd', 'mpls', 'mpls_bfd', 'manual'
        self._restart_lock = threading.Lock()
        self._restart_pending = [None]
        # ── Topology lock ──────────────────────────────────────────────────────
        # RLock (reentrant) protects all mutations of self.nodes,
        # self.network_matrix and self.mininet_nodes.
        # Must be held during: validation checks, Python-state writes, and the
        # Mininet addLink/addHost that reads intfList() — so two concurrent
        # add_host calls on the same switch cannot compute the same intf name.
        # Long Mininet operations (FRR start, delNode) run outside the lock
        # so they do not block reads or other topology queries.
        self.topology_lock = threading.RLock()

    #  ROUTING PROTOCOLS

    def _write_zebra_conf(self, node, name, conf_path):
        """Writes the zebra configuration file directly on host filesystem."""
        os.makedirs(conf_path, exist_ok=True)
        with open(f'{conf_path}/zebra.conf', 'w') as f:
            f.write(f'hostname {name}\nlog syslog informational\n!\n')
        os.chmod(f'{conf_path}/zebra.conf', 0o644)

    def _write_ospfd_conf(self, node, name, props, conf_path):
        """Writes the ospfd configuration file directly on host filesystem."""
        router_id = props['ips'].get('eth0', '1.1.1.1/30').split('/')[0]
        networks = []
        for intf, ip in props['ips'].items():
            if intf == 'lan':
                continue
            base = ip.split('/')[0].rsplit('.', 1)[0]
            mask = ip.split('/')[1]
            networks.append(f'{base}.0/{mask}')

        lines = [
            f'hostname {name}', 'log syslog informational', '!',
            'router ospf', f'  ospf router-id {router_id}',
            '  timers throttle spf 0 50 200',
        ] + [f'  network {n} area 0' for n in networks] + ['!']
        for intf, ip in props['ips'].items():
            if intf == 'lan':
                continue
            lines += [f'interface {name}-{intf}',
                      '  ip ospf hello-interval 1',
                      '  ip ospf dead-interval 4', '!']
        lines += ['line vty', '!']

        os.makedirs(conf_path, exist_ok=True)
        with open(f'{conf_path}/ospfd.conf', 'w') as f:
            f.write('\n'.join(lines) + '\n')
        os.chmod(f'{conf_path}/ospfd.conf', 0o644)

    def _write_ldpd_conf(self, node, name, props, conf_path, router_id):
        """Writes the ldpd configuration file."""
        ldp_lines = [
            f'hostname {name}',
            'log syslog informational',
            '!',
            'mpls ldp',
            f'  router-id {router_id}',
            '  !',
            '  address-family ipv4',
            f'    discovery transport-address {router_id}',
        ]
        for intf, ip in props['ips'].items():
            if intf == 'lan':
                continue
            ldp_lines.append(f'    interface {name}-{intf}')
        ldp_lines += ['  exit-address-family', '!', 'line vty', '!']

        os.makedirs(conf_path, exist_ok=True)
        with open(f'{conf_path}/ldpd.conf', 'w') as f:
            f.write('\n'.join(ldp_lines) + '\n')
        os.chmod(f'{conf_path}/ldpd.conf', 0o644)

    def _kill_daemons(self, node, name, daemons):
        """Kills the specified FRRouting daemons for a given router."""
        for daemon in daemons:
            node.cmd(f'pkill -f "{daemon}.*{name}" 2>/dev/null')
        node.cmd('sleep 0.02')  # SIGTERM kills within ms; 20ms is safe

    def _launch_daemon(self, node, name, binary, conf_path):
        """Launches an FRRouting daemon in background mode."""
        daemon = os.path.basename(binary)  # 'zebra', 'ospfd', etc.
        # --socket especifica el path del socket zserv (connexió entre
        # ospfd/ldpd/bfdd i zebra). Sense aquest paràmetre, els daemons
        # busquen /var/run/frr/zserv.api (el FRR del sistema) en lloc
        # del zebra del namespace correcte.
        node.cmd(
            f'{binary} -d '
            f'--config_file {conf_path}/{daemon}.conf '
            f'--pid_file {conf_path}/{daemon}.pid '
            f'--vty_socket {conf_path}/ '
            f'--socket {conf_path}/zserv.api '
            f'> {conf_path}/{daemon}.log 2>&1'
        )

    def _start_ospf(self, node, name, props, skip_kill=False):
        """
        Starts zebra + ospfd inside the router's network namespace.
        skip_kill=True skips killing old daemons (used when pool already killed them).
        """
        conf_path = f'/tmp/frr_{name}'
        os.makedirs(conf_path, exist_ok=True)
        os.system(f'chmod 777 {conf_path}')

        self._write_zebra_conf(node, name, conf_path)
        self._write_ospfd_conf(node, name, props, conf_path)
        if not skip_kill:
            self._kill_daemons(node, name, ['zebra', 'ospfd'])

        self._launch_daemon(node, name, ZEBRA, conf_path)
        node.cmd('sleep 0.02')  # zebra binds socket in <10ms; 20ms is safe
        self._launch_daemon(node, name, OSPFD, conf_path)
    
    def _start_mpls(self, node, name, props):
        """
        Starts zebra + ospfd + ldpd inside the router's network namespace.
        OSPF provides IP routing, LDP distributes MPLS labels on top.
        """
        conf_path = f'/tmp/frr_{name}'
        node.cmd(f'mkdir -p {conf_path} && chmod 777 {conf_path}')

        # Enable MPLS on all interfaces
        node.cmd('sysctl -w net.mpls.platform_labels=100 2>/dev/null')
        for intf in props['ips']:
            if intf == 'lan':
                continue
            node.cmd(f'sysctl -w net.mpls.conf.{name}-{intf}.input=1 2>/dev/null')

        self._write_zebra_conf(node, name, conf_path)
        self._write_ospfd_conf(node, name, props, conf_path)

        router_id = props['ips'].get('eth0', '1.1.1.1/30').split('/')[0]
        self._write_ldpd_conf(node, name, props, conf_path, router_id)

        self._kill_daemons(node, name, ['zebra', 'ospfd', 'ldpd'])

        self._launch_daemon(node, name, ZEBRA, conf_path)
        node.cmd('sleep 0.02')  # zebra socket ready in <10ms
        self._launch_daemon(node, name, OSPFD, conf_path)
        node.cmd('sleep 0.05')  # ospfd needs a moment before ldpd
        self._launch_daemon(node, name, LDPD, conf_path)

    def _stop_routing(self, node, name):
        """Stop all routing daemons for a router."""
        # Ordre correcte: primers els protocols (ospfd/ldpd/bfdd),
        # després zebra. Si zebra mor primer, ospfd perd la connexió
        # i pot deixar el socket vtysh bloquejat.
        conf_path = f'/tmp/frr_{name}'
        node.cmd(f'pkill -f "bfdd.*{name}" 2>/dev/null')
        node.cmd(f'pkill -f "ldpd.*{name}" 2>/dev/null')
        node.cmd(f'pkill -f "ospfd.*{name}" 2>/dev/null')
        node.cmd('sleep 0.1')  # espera que ospfd alliberi el socket vtysh
        node.cmd(f'pkill -f "zebra.*{name}" 2>/dev/null')
        node.cmd('sleep 0.1')  # espera que zebra alliberi el socket
        # Elimina els PID files antics perquè el proper arrencada
        # no trobi fitxers bloquejats del run anterior.
        for pidfile in ['zebra.pid', 'ospfd.pid', 'ldpd.pid', 'bfdd.pid']:
            node.cmd(f'rm -f {conf_path}/{pidfile} 2>/dev/null')
        # Elimina també el socket vtysh per garantir un arrencada neta.
        node.cmd(f'rm -f {conf_path}/*.vty 2>/dev/null')
        node.cmd(f'rm -f {conf_path}/zebra.sock 2>/dev/null')
    def _update_ospf_hot(self, node, name, props):
        """
        Inject OSPF networks into a running ospfd WITHOUT restarting daemons.
        Also configures hello/dead intervals on ALL interfaces so new p2p
        links match the timers on the new router (1s/4s).

        ORDER IS CRITICAL:
        Interface timers must be configured BEFORE the network statement.
        When ospfd first starts OSPF on a new interface (triggered by the
        network statement), it reads the current dead-interval to set the
        Wait Timer. If hello/dead are configured AFTER the network statement,
        ospfd uses the FRR default (hello=10s, dead=40s) → Wait Timer = 40s.
        Configuring timers FIRST ensures Wait Timer = 4s → convergence in ~6s.

        Runs vtysh via node.cmd() inside the node's namespace so it connects
        ONLY to the node's FRR daemons, not the system FRR.
        """
        conf_path  = f'/tmp/frr_{name}'
        vtysh_file = f'{conf_path}/hot_update.vtysh'
        lines = ['configure terminal']

        # STEP 1: Configure hello/dead on ALL interfaces FIRST.
        # For interfaces already in OSPF (established neighbours), this is a
        # no-op for the Wait Timer (already expired). For NEW interfaces, it
        # sets the timers before ospfd starts OSPF on them (step 2).
        for intf, ip in props['ips'].items():
            if intf == 'lan':
                continue
            lines += [f'interface {name}-{intf}',
                      ' ip ospf hello-interval 1',
                      ' ip ospf dead-interval 4',
                      'exit']

        # STEP 2: Update network statements.
        # Adding a new network causes ospfd to start OSPF on the matching
        # interface — at this point it reads the timers set in step 1.
        lines.append('router ospf')
        for intf, ip in props['ips'].items():
            if intf == 'lan':
                continue
            base = ip.split('/')[0].rsplit('.', 1)[0]
            mask = ip.split('/')[1]
            lines.append(f' network {base}.0/{mask} area 0')
        lines += ['exit', 'end']

        with open(vtysh_file, 'w') as f:
            f.write('\n'.join(lines) + '\n')
        node.cmd(f'vtysh --vty_socket {conf_path} -f {vtysh_file} 2>/dev/null')

    # ── Router pool ──────────────────────────────────────────────────────────

    def _pool_create_entry(self, pool_name, pool_switch_name):
        """
        Pre-create a router + switch + zebra + ospfd in background.
        IMPORTANT:
          - NOT added to self.nodes (invisible to topology/matrix)
          - NOT added to self.mininet_nodes
          - Only exists inside Mininet's net object and pool dicts
        """
        pool_lan_ip = '10.254.0.1/24'
        pool_props  = {
            'type': 'router',
            'ips':  {'eth0': pool_lan_ip, 'lan': pool_lan_ip},
            'routes': [], 'p2p_links': []
        }
        new_router = self.net.addHost(pool_name, ip='127.0.0.1')
        new_switch = self.net.addSwitch(pool_switch_name, failMode='standalone')
        new_switch.start([])

        intf_lan = f'{pool_name}-eth0'
        self.net.addLink(new_router, new_switch, intfName1=intf_lan)
        new_router.cmd(
            f'ifconfig {intf_lan} {pool_lan_ip} ; '
            f'ip link set {intf_lan} up ; '
            f'sysctl -w net.ipv4.ip_forward=1 ; '
            f'ifconfig lo up'
        )
        sw_intf = f'{pool_switch_name}-eth1'
        new_switch.cmd(
            f'ip link set {sw_intf} up ; '
            f'ovs-vsctl add-port {pool_switch_name} {sw_intf}'
        )
        self._start_ospf(new_router, pool_name, pool_props)
        return new_router, new_switch

    def init_router_pool(self, pool_size=5):
        """
        Pre-create pool_size routers in background after network starts.
        Creates all routers IN PARALLEL so the pool is ready faster.
        Called once from app.py after start_network().
        """
        import threading
        self._router_pool      = []
        self._router_pool_lock = threading.Lock()
        self._pool_counter     = 0
        self._pool_target_size = pool_size

        def fill():
            # Create all pool entries in parallel for faster warm-up
            threads = [
                threading.Thread(target=self._pool_add_one, daemon=True)
                for _ in range(pool_size)
            ]
            for t in threads: t.start()
            for t in threads: t.join()
            print(f'[pool] fully warmed up ({pool_size} routers ready)')

        threading.Thread(target=fill, daemon=True).start()

    def _pool_replenish(self):
        """
        Replenish pool up to target size in parallel.
        Called after each claim — fills ALL missing slots simultaneously.
        This way if 3 routers are claimed rapidly, all 3 replenish at once.
        """
        with self._router_pool_lock:
            current = len(self._router_pool)
            missing = max(0, self._pool_target_size - current)
        threads = [
            threading.Thread(target=self._pool_add_one, daemon=True)
            for _ in range(missing)
        ]
        for t in threads: t.start()

    def _pool_add_one(self):
        """Create one pre-warmed router and add it to the pool."""
        with self._router_pool_lock:
            self._pool_counter += 1
            idx = self._pool_counter
        pool_name        = f'__pool_r{idx}'
        pool_switch_name = f'__pool_s{idx}'   # max 15 chars: __pool_s99-eth1 = 14 ✓
        try:
            router_node, switch_node = self._pool_create_entry(
                pool_name, pool_switch_name
            )
            with self._router_pool_lock:
                self._router_pool.append(
                    (router_node, switch_node, pool_name, pool_switch_name)
                )
            print(f'[pool] pre-warmed router ready ({pool_name})')
        except Exception as e:
            print(f'[pool] failed to create entry: {e}')

    def claim_from_pool(self, router_name, switch_name, lan_ip):
        """
        Claim a pre-warmed router from the pool.
        - Renames pool nodes to final names
        - Renames LAN interface from __pool_rN-eth0 to router_name-eth0
        - Renames OVS bridge from __pool_sN to swN
        - Registers in net.nameToNode and mininet_nodes
        - Replenishes pool in background
        Returns (router_node, switch_node) or (None, None) if pool empty.
        """
        import threading
        with self._router_pool_lock:
            if not self._router_pool:
                return None, None
            router_node, switch_node, pool_name, pool_switch_name = \
                self._router_pool.pop(0)

        # Rename nodes in Mininet's nameToNode
        router_node.name = router_name
        switch_node.name = switch_name
        self.net.nameToNode[router_name] = router_node
        self.net.nameToNode[switch_name] = switch_node
        self.net.nameToNode.pop(pool_name, None)
        self.net.nameToNode.pop(pool_switch_name, None)

        # Register in mininet_nodes
        self.mininet_nodes[router_name] = router_node
        self.mininet_nodes[switch_name] = switch_node

        # Rename LAN interface: __pool_rN-eth0 → router_name-eth0
        old_lan = f'{pool_name}-eth0'
        new_lan = f'{router_name}-eth0'
        router_node.cmd(f'ip link set {old_lan} name {new_lan} 2>/dev/null || true')
        router_node.cmd(
            f'ifconfig {new_lan} {lan_ip} ; '
            f'ip link set {new_lan} up ; '
            f'sysctl -w net.ipv4.ip_forward=1 ; '
            f'ifconfig lo up ; ip link set lo up'
        )

        # ── Rename OVS bridge: __pool_sN → swN ──
        # OVS has no rename command: detach port → delete old bridge →
        # create new bridge → re-attach port with its ORIGINAL name.
        # We do NOT rename the switch interface (__pool_sN-eth1) — it stays
        # with its pool name inside the new bridge. This avoids:
        #   - naming conflict when add_host later creates swN-eth1
        #   - KeyError in remove_node (intf.name matches nameToIntf key)
        #
        # All 4 OVS operations are batched into ONE atomic transaction with `--`,
        # which saves ~30-80ms compared to 4 separate ovs-vsctl invocations
        # (each one opens/commits/closes its own OVSDB connection).
        old_sw_intf = f'{pool_switch_name}-eth1'
        switch_node.cmd(
            f'ovs-vsctl '
            f'--if-exists del-port {pool_switch_name} {old_sw_intf} -- '
            f'--if-exists del-br {pool_switch_name} -- '
            f'add-br {switch_name} -- '
            f'add-port {switch_name} {old_sw_intf} ; '
            f'ip link set {switch_name} up ; '
            f'ip link set {old_sw_intf} up'
        )

        # Fix the router's LAN interface in Mininet's nameToIntf.
        # The Linux interface was already renamed above (ip link set),
        # so nameToIntf must match for remove_node/delIntf to work.
        old_r_lan = f'{pool_name}-eth0'
        new_r_lan = f'{router_name}-eth0'
        for intf in router_node.intfList():
            if intf.name == old_r_lan:
                router_node.nameToIntf.pop(old_r_lan, None)
                intf.name = new_r_lan
                router_node.nameToIntf[new_r_lan] = intf
                break

        # Kill pool FRR daemons reliably via their PID files.
        # Must use router_node.cmd() to run INSIDE the node's namespace —
        # os.system() runs on the host and may not reach the daemons.
        import os
        frr_dir = f'/tmp/frr_{pool_name}'
        router_node.cmd(
            f'kill -9 $(cat {frr_dir}/ospfd.pid 2>/dev/null) 2>/dev/null ; '
            f'kill -9 $(cat {frr_dir}/zebra.pid 2>/dev/null) 2>/dev/null ; '
            f'sleep 0.02 ; '
            f'rm -rf {frr_dir}'
        )

        # NOTE: _pool_replenish is NOT started here.
        # It must be started by the caller (add_router Phase 3) AFTER all
        # Mininet net.addLink / net.addHost calls complete. Starting it here
        # causes concurrent net operations that corrupt Mininet's internal
        # state, putting zebra in a different namespace than the interfaces.
        return router_node, switch_node

    def _router_pool_available(self):
        """Check if pool has at least one entry ready."""
        if not hasattr(self, '_router_pool'):
            return False
        with self._router_pool_lock:
            return len(self._router_pool) > 0

    def _start_bfd(self, node, name, props):
        """
        Start bfdd inside the router's namespace and enable BFD on OSPF interfaces.
        BFD detects link failures in milliseconds, triggering faster OSPF convergence.
        Requires zebra + ospfd already running.
        """
        conf_path = f'/tmp/frr_{name}'

        # bfdd config
        bfd_lines = [f'hostname {name}', 'log syslog informational', '!', 'line vty', '!']
        os.makedirs(conf_path, exist_ok=True)
        with open(f'{conf_path}/bfdd.conf', 'w') as f:
            f.write('\n'.join(bfd_lines) + '\n')
        os.chmod(f'{conf_path}/bfdd.conf', 0o644)

        # Kill previous bfdd
        node.cmd(f'pkill -f "bfdd.*{name}" 2>/dev/null')
        node.cmd('sleep 0.05')  # wait for bfdd to exit

        # Start bfdd
        # Start bfdd — usa _launch_daemon perquè inclou --socket
        # {conf_path}/zserv.api i així bfdd es connecta al zebra del
        # namespace correcte i no al FRR del sistema (/var/run/frr/).
        self._launch_daemon(node, name, BFDD, conf_path)
        node.cmd('sleep 0.05')  # bfdd binds socket in <20ms

        # Enable BFD on all OSPF interfaces via vtysh
        for intf, ip in props['ips'].items():
            if intf == 'lan':
                continue
            intf_name = f'{name}-{intf}'
            node.cmd(
                f'vtysh --vty_socket /tmp/frr_{name} '
                f'-c "configure terminal" '
                f'-c "interface {intf_name}" '
                f'-c "ip ospf bfd" '
                f'-c "end" 2>/dev/null'
            )

    # This applies the routing mode set by the user (self.routing_mode)
    def _apply_routing(self, node, name, props, skip_kill=False):
        if self.routing_mode == 'ospf':
            self._start_ospf(node, name, props, skip_kill=skip_kill)
        elif self.routing_mode == 'ospf_bfd':
            self._start_ospf(node, name, props, skip_kill=skip_kill)
            self._start_bfd(node, name, props)
        elif self.routing_mode == 'mpls':
            self._start_mpls(node, name, props)
        elif self.routing_mode == 'mpls_bfd':
            self._start_mpls(node, name, props)
            self._start_bfd(node, name, props)
        elif self.routing_mode == 'manual':
            for route in props.get('routes', []):
                node.cmd(f'ip route add {route}')

    #  NETWORK LIFECYCLE

    def start_network(self):
        self.net = Mininet()
        for name, props in self.nodes.items():
            '''Crea els nodes a Mininet segons el tipus.'''
            if props['type'] == 'host':
                self.mininet_nodes[name] = self.net.addHost(name, ip=props['ip'])
            elif props['type'] == 'router':
                self.mininet_nodes[name] = self.net.addHost(name, ip=props['ips']['eth0'])
            elif props['type'] == 'switch':
                self.mininet_nodes[name] = self.net.addSwitch(name, failMode='standalone')

        # Create the links with explicit interface names so IPs are
        # always assigned to the correct interface after restart.
        node_names = list(self.nodes.keys())

        # Pre-build p2p intf map: {(routerA, routerB): (intfA, intfB)}
        p2p_map = {}
        for name, props in self.nodes.items():
            if props['type'] != 'router':
                continue
            for link in props.get('p2p_links', []):
                key = tuple(sorted([name, link['peer']]))
                if key not in p2p_map:
                    p2p_map[key] = {}
                p2p_map[key][name] = link['local_intf']

        # Pre-build router→switch intf map: find the LAN intf of each router
        # (the intf that is not a p2p intf and not 'lan')
        router_lan_intf = {}
        for name, props in self.nodes.items():
            if props['type'] != 'router':
                continue
            p2p_intfs = {l['local_intf'] for l in props.get('p2p_links', [])}
            lan_intfs = [k for k in props['ips'] if k != 'lan' and k not in p2p_intfs]
            router_lan_intf[name] = lan_intfs[0] if lan_intfs else 'eth0'

        for i in range(len(self.network_matrix)):
            for j in range(i + 1, len(self.network_matrix)):
                if self.network_matrix[i][j] == 0:
                    continue
                node_i = node_names[i]
                node_j = node_names[j]
                type_i = self.nodes[node_i]['type']
                type_j = self.nodes[node_j]['type']

                # router ↔ router (p2p)
                if type_i == 'router' and type_j == 'router':
                    key = tuple(sorted([node_i, node_j]))
                    intfs = p2p_map.get(key, {})
                    intf_i = intfs.get(node_i, 'eth0')
                    intf_j = intfs.get(node_j, 'eth0')
                    self.net.addLink(self.mininet_nodes[node_i],
                                     self.mininet_nodes[node_j],
                                     intfName1=f'{node_i}-{intf_i}',
                                     intfName2=f'{node_j}-{intf_j}')

                # router ↔ switch (LAN)
                elif type_i == 'router' and type_j == 'switch':
                    intf_i = router_lan_intf.get(node_i, 'eth0')
                    self.net.addLink(self.mininet_nodes[node_i],
                                     self.mininet_nodes[node_j],
                                     intfName1=f'{node_i}-{intf_i}')
                elif type_i == 'switch' and type_j == 'router':
                    intf_j = router_lan_intf.get(node_j, 'eth0')
                    self.net.addLink(self.mininet_nodes[node_i],
                                     self.mininet_nodes[node_j],
                                     intfName2=f'{node_j}-{intf_j}')

                # host ↔ switch
                elif type_i == 'host' and type_j == 'switch':
                    self.net.addLink(self.mininet_nodes[node_i],
                                     self.mininet_nodes[node_j],
                                     intfName1=f'{node_i}-eth0')
                elif type_i == 'switch' and type_j == 'host':
                    self.net.addLink(self.mininet_nodes[node_i],
                                     self.mininet_nodes[node_j],
                                     intfName2=f'{node_j}-eth0')

                # fallback (no debería ocurrir)
                else:
                    self.net.addLink(self.mininet_nodes[node_i],
                                     self.mininet_nodes[node_j])
        self.net.start() # Mininet aixeca la xarxa.

        # Configure router interfaces
        for name, props in self.nodes.items():
            if props['type'] == 'router':
                for eth, ip in props['ips'].items():
                    if eth == 'lan':
                        continue
                    self.mininet_nodes[name].cmd(f'ifconfig {name}-{eth} {ip}')

        # Enable IP forwarding on routers
        for name, props in self.nodes.items():
            if props['type'] == 'router':
                self.mininet_nodes[name].cmd('sysctl -w net.ipv4.ip_forward=1')

        # Configure default gateway on hosts
        for name, props in self.nodes.items():
            if props['type'] == 'host':
                self.mininet_nodes[name].cmd(f'ip route add default via {props["gw"]}')

        # Start routing protocol on all routers
        for name, props in self.nodes.items():
            if props['type'] == 'router':
                self._apply_routing(self.mininet_nodes[name], name, props)

        self.network_ready = True

    def restart_network(self, new_matrix, new_nodes):
        """
        Restart the network with a new state snapshot.
        Uses a lock so only one restart runs at a time.
        If multiple snapshots arrive while one is running, only the LATEST is applied!
        """
        self._restart_pending[0] = (new_matrix, new_nodes)

        if not self._restart_lock.acquire(blocking=False):
            return

        try:
            while True:
                snapshot = self._restart_pending[0]
                self._restart_pending[0] = None
                if snapshot is None:
                    break

                new_matrix, new_nodes = snapshot
                self.network_ready = False

                if self.net is not None:
                    try:
                        self.net.stop()
                    except Exception:
                        pass
                    finally:
                        os.system('mn -c > /dev/null 2>&1')

                self.mininet_nodes = {}
                self.network_matrix.clear()
                for row in new_matrix:
                    self.network_matrix.append(row)
                self.nodes.clear()
                self.nodes.update(new_nodes)
                self.start_network()

                if self._restart_pending[0] is None:
                    break
        finally:
            self._restart_lock.release()

    #  TOPOLOGY HELPERS (These are functions used by app.py whenever the user commit changes in the interface)

    def find_router_of_switch(self, switch):
        names = list(self.nodes.keys())
        switch_idx = names.index(switch)
        for i, val in enumerate(self.network_matrix[switch_idx]):
            if val != 0 and self.nodes[names[i]]['type'] == 'router':
                return names[i]
        return None

    def find_next_ip(self, router):
        router_ip = next(ip for ip in self.nodes[router]['ips'].values() if '/24' in ip)
        base = router_ip.rsplit('.', 1)[0]
        mask = router_ip.split('/')[1]
        used_ips = []
        for name, props in self.nodes.items():
            if props['type'] == 'host' and 'ip' in props:
                ip = props['ip'].split('/')[0]
                if ip.startswith(base):
                    used_ips.append(int(ip.split('.')[-1]))
        next_num = 2
        while next_num in used_ips:
            next_num += 1
        return f'{base}.{next_num}/{mask}'

    def update_matrix(self, name, switch):
        names = list(self.nodes.keys())
        switch_idx = names.index(switch)
        for row in self.network_matrix:
            row.append(0)
        new_row = [0] * len(self.network_matrix[0])
        self.network_matrix.append(new_row)
        new_idx = len(self.network_matrix) - 1
        new_type = self.nodes[name]['type']
        switch_type = self.nodes[switch]['type']
        self.network_matrix[new_idx][switch_idx] = new_type
        self.network_matrix[switch_idx][new_idx] = switch_type

    def update_matrix_multi(self, name, connected):
        names = list(self.nodes.keys())
        for row in self.network_matrix:
            row.append(0)
        new_row = [0] * len(self.network_matrix[0])
        self.network_matrix.append(new_row)
        new_idx = len(self.network_matrix) - 1
        new_type = self.nodes[name]['type']
        for conn in connected:
            conn_idx = names.index(conn)
            conn_type = self.nodes[conn]['type']
            self.network_matrix[new_idx][conn_idx] = new_type
            self.network_matrix[conn_idx][new_idx] = conn_type

    def remove_from_matrix(self, name):
        names = list(self.nodes.keys())
        idx = names.index(name)
        self.network_matrix.pop(idx)
        for row in self.network_matrix:
            row.pop(idx)

    def find_next_subnet(self):
        used_subnets = []
        for name, props in self.nodes.items():
            if props['type'] == 'router':
                ip_lan = next(ip for ip in props['ips'].values() if '/24' in ip)
                second_octet = int(ip_lan.split('/')[0].split('.')[1])
                used_subnets.append(second_octet)
        next_num = 1
        while next_num in used_subnets:
            next_num += 1
        return next_num

    def find_next_p2p_subnet(self, extra_used=None):
        """
        Returns the next available /30 point-to-point subnet for router-router links.
        Subnets are in the form 10.0.X.0/30, X starting from 0.
        Each /30 has 4 IPs: .0 (network), .1 (router A), .2 (router B), .3 (broadcast).
        extra_used: set of third-octet ints already reserved in this batch
                    (needed when called N times before self.nodes is updated).
        """
        used = set(extra_used or [])
        for name, props in self.nodes.items():
            if props['type'] == 'router':
                for link in props.get('p2p_links', []):
                    subnet = link['subnet']
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

    def find_router_subnet(self, router):
        lan_ip = next(ip for ip in self.nodes[router]['ips'].values() if '/24' in ip)
        base = lan_ip.split('/')[0].rsplit('.', 1)[0]
        nodes_to_remove = []
        for name, props in self.nodes.items():
            if props['type'] == 'host' and 'ip' in props:
                ip = props['ip'].split('/')[0]
                if ip.startswith(base):
                    nodes_to_remove.append(name)
        names = list(self.nodes.keys())
        router_idx = names.index(router)
        for i, val in enumerate(self.network_matrix[router_idx]):
            if val != 0 and self.nodes[names[i]]['type'] == 'switch':
                nodes_to_remove.append(names[i])
        return nodes_to_remove

    def find_switch_of_router(self, router):
        names = list(self.nodes.keys())
        router_idx = names.index(router)
        for i, val in enumerate(self.network_matrix[router_idx]):
            if val != 0 and self.nodes[names[i]]['type'] == 'switch':
                return names[i]
        return None

    def get_all_host_subnets(self):
        """Returns list of all /24 host subnets for all routers.
        Uses the 'lan' key which is always present regardless of whether
        the router was created from the pool (eth0=LAN) or not (ethN=LAN).
        """
        subnets = []
        for name, props in self.nodes.items():
            if props['type'] == 'router':
                lan_ip = props['ips'].get('lan', '')
                if not lan_ip:
                    continue
                base = lan_ip.split('/')[0].rsplit('.', 1)[0]
                subnets.append({'subnet': f'{base}.0/24', 'router': name})
        return subnets


if __name__ == '__main__':
    setLogLevel('info')
    xarxa = Xarxa()
    xarxa.start_network()