"""
routes/nodes.py — Live topology modification endpoints.

Endpoints:
  POST /add_host      → add a host to an existing router's subnet
  POST /remove_node   → remove a host or an entire router subnet
  POST /add_router    → add a new router with its switch and p2p links
  POST /rename_node   → rename any node (triggers a full network restart)

Sync strategy
-------------
  add_host, remove_node → incremental event sync.
      The Original sends a small pre-computed JSON payload to the Twin's
      matching endpoint. The Twin applies the change in-place without
      rebuilding Mininet.

  add_router → incremental event sync with pre-computed state.
      The Original sends the fully-resolved router/switch/p2p data so the
      Twin applies exactly the same configuration without independent
      recalculation (which could diverge if states differ by even one node).

  rename_node → full snapshot sync.
      Mininet does not support renaming nodes in-place; the Twin still
      needs a full rebuild for this operation.
"""

import copy
import threading
import time

from flask import Blueprint, jsonify, request

from sync import sync_event, sync_snapshot

_xarxa = None

bp = Blueprint('nodes', __name__)


def init_blueprint(xarxa_instance):
    global _xarxa
    _xarxa = xarxa_instance


# ── Internal helpers ──

def _start_routing_on_new_router(router_name):
    """
    Start routing on a new router, then hot-update existing routers.
    New router: full daemon start (zebra + ospfd).
    Existing routers: vtysh hot update — no daemon restart needed.
    """
    props = _xarxa.nodes[router_name]
    node  = _xarxa.mininet_nodes[router_name]
    _xarxa._apply_routing(node, router_name, props)

    existing = {
        n: p for n, p in _xarxa.nodes.items()
        if p['type'] == 'router' and n != router_name and n in _xarxa.mininet_nodes
    }

    def update_existing():
        mode = _xarxa.routing_mode
        threads = []
        for name, p in existing.items():
            if mode in ('ospf', 'ospf_bfd'):
                t = threading.Thread(
                    target=_xarxa._update_ospf_hot,
                    args=(_xarxa.mininet_nodes[name], name, p),
                    daemon=True
                )
                threads.append(t)
            else:
                _xarxa._stop_routing(_xarxa.mininet_nodes[name], name)
                _xarxa._apply_routing(_xarxa.mininet_nodes[name], name, p)
        for t in threads: t.start()
        for t in threads: t.join()

    threading.Thread(target=update_existing, daemon=True).start()


def _update_all_routes():
    """Hot-update OSPF on all remaining routers after a router removal."""
    routers = dict(_xarxa.nodes)  # snapshot to avoid dict-changed-during-iteration
    mode = _xarxa.routing_mode
    threads = []
    for name, props in routers.items():
        if props['type'] != 'router' or name not in _xarxa.mininet_nodes:
            continue
        if mode in ('ospf', 'ospf_bfd'):
            t = threading.Thread(
                target=_xarxa._update_ospf_hot,
                args=(_xarxa.mininet_nodes[name], name, props),
                daemon=True
            )
            threads.append(t)
        else:
            _xarxa._stop_routing(_xarxa.mininet_nodes[name], name)
            _xarxa._apply_routing(_xarxa.mininet_nodes[name], name, props)
    for t in threads: t.start()
    for t in threads: t.join()


# ── Routes ──

@bp.route('/add_host', methods=['POST'])
def add_host():
    if not _xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})

    data    = request.json
    name    = data['name']
    router  = data['router']
    is_sync = data.get('sync', False)

    if name in _xarxa.nodes:
        return jsonify({'ok': False, 'error': f'A node named {name} already exists'})

    # Use pre-computed values if provided (sync from Original), else compute fresh
    switch = data.get('switch') or _xarxa.find_switch_of_router(router)
    ip     = data.get('ip')    or _xarxa.find_next_ip(router)
    gw     = data.get('gw')
    if not gw:
        lan_ip = next(i for i in _xarxa.nodes[router]['ips'].values() if '/24' in i)
        gw     = lan_ip.split('/')[0]

    _xarxa.nodes[name] = {'type': 'host', 'ip': ip, 'gw': gw}
    _xarxa.update_matrix(name, switch)

    t_local_start = time.time()
    new_host      = _xarxa.net.addHost(name, ip=ip)
    _xarxa.mininet_nodes[name] = new_host
    sw_node      = _xarxa.mininet_nodes[switch]
    sw_intf_name = f'{switch}-eth{len(sw_node.intfList())}'

    _xarxa.net.addLink(new_host, sw_node, intfName1=f'{name}-eth0', intfName2=sw_intf_name)
    new_host.cmd(
        f'ifconfig {name}-eth0 {ip} ; '
        f'ip route add default via {gw} ; '
        f'ifconfig lo up ; '
        f'ip link set lo up ; '
        f'ip link set {name}-eth0 up'
    )
    sw_node.cmd(f'ip link set {sw_intf_name} up')
    t_local_ms = round((time.time() - t_local_start) * 1000, 2)
    # ovs-vsctl is slow — run in background after timing
    threading.Thread(
        target=lambda: sw_node.cmd(f'ovs-vsctl add-port {switch} {sw_intf_name}'),
        daemon=True
    ).start()

    if not is_sync:
        # Send pre-computed values so Twin uses identical IP/switch/gw
        sync_event('/add_host', {
            'name':   name,
            'router': router,
            'switch': switch,
            'ip':     ip,
            'gw':     gw,
        }, t_local_ms)
        return jsonify({'ok': True})
    return jsonify({'ok': True, 't_local_ms': t_local_ms})


@bp.route('/remove_node', methods=['POST'])
def remove_node():
    if not _xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})

    data    = request.json
    name    = data['name']
    is_sync = data.get('sync', False)

    if name not in _xarxa.nodes:
        return jsonify({'ok': False, 'error': f'Node {name} not found'})

    if _xarxa.nodes[name]['type'] == 'router':
        # Clean p2p_links and IPs from neighbouring routers
        for rname, props in _xarxa.nodes.items():
            if props['type'] == 'router' and rname != name and 'p2p_links' in props:
                intfs_to_remove = {
                    l['local_intf'] for l in props['p2p_links'] if l['peer'] == name
                }
                for intf in intfs_to_remove:
                    props['ips'].pop(intf, None)
                props['p2p_links'] = [
                    l for l in props['p2p_links'] if l['peer'] != name
                ]

        t_local_start   = time.time()
        nodes_to_remove = _xarxa.find_router_subnet(name)
        nodes_to_remove.append(name)
        for node in nodes_to_remove:
            _xarxa.remove_from_matrix(node)
            _xarxa.net.delNode(_xarxa.mininet_nodes[node])
            del _xarxa.mininet_nodes[node]
            del _xarxa.nodes[node]

        t_local_ms = round((time.time() - t_local_start) * 1000, 2)
        # Update routes in background — don't block the response
        threading.Thread(target=_update_all_routes, daemon=True).start()

    else:
        t_local_start = time.time()
        _xarxa.remove_from_matrix(name)
        _xarxa.net.delNode(_xarxa.mininet_nodes[name])
        del _xarxa.mininet_nodes[name]
        del _xarxa.nodes[name]
        t_local_ms = round((time.time() - t_local_start) * 1000, 2)

    if not is_sync:
        sync_event('/remove_node', {'name': name}, t_local_ms)
        return jsonify({'ok': True})
    return jsonify({'ok': True, 't_local_ms': t_local_ms})


@bp.route('/add_router', methods=['POST'])
def add_router():
    if not _xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})

    data              = request.json
    router_name       = data['name']
    connected_routers = data['connected_routers']
    is_sync           = data.get('sync', False)

    if router_name in _xarxa.nodes:
        return jsonify({'ok': False, 'error': f'A node named {router_name} already exists'})

    if is_sync and 'router_state' in data:
        # ── Twin path: apply pre-computed state from Original ──
        switch_name      = data['switch_name']
        router_state     = data['router_state']
        connected_states = data['connected_states']

        # Restore state in nodes + matrix
        _xarxa.nodes[router_name] = router_state
        _xarxa.update_matrix_multi(router_name, connected_routers)
        _xarxa.nodes[switch_name] = {'type': 'switch'}
        _xarxa.update_matrix_multi(switch_name, [router_name])
        for rname, rstate in connected_states.items():
            _xarxa.nodes[rname] = rstate

        t_local_start = time.time()

        # ── Try pool first (Twin also has pool) ──
        # LAN IP is the last eth in router_state
        n_p2p    = len(connected_routers)
        ip_lan   = router_state['ips'].get(f'eth{n_p2p}', '10.254.0.1/24')
        use_pool = _xarxa._router_pool_available()
        if use_pool:
            new_router, new_switch = _xarxa.claim_from_pool(
                router_name, switch_name, ip_lan
            )
            if new_router is None:
                use_pool = False

        if not use_pool:
            new_router = _xarxa.net.addHost(router_name, ip='127.0.0.1')
            new_switch = _xarxa.net.addSwitch(switch_name, failMode='standalone')
            _xarxa.mininet_nodes[router_name] = new_router
            _xarxa.mininet_nodes[switch_name] = new_switch
            new_switch.start([])
            intf_eth_lan = f'{router_name}-eth{n_p2p}'
            _xarxa.net.addLink(new_router, new_switch, intfName1=intf_eth_lan)
            new_router.cmd(
                f'ifconfig {intf_eth_lan} {ip_lan} ; '
                f'ip link set {intf_eth_lan} up ; '
                f'sysctl -w net.ipv4.ip_forward=1 ; '
                f'ifconfig lo up'
            )
            sw_intf = f'{switch_name}-eth1'
            new_switch.cmd(
                f'ip link set {sw_intf} up ; '
                f'ovs-vsctl add-port {switch_name} {sw_intf}'
            )

        # ── Build p2p links ──
        # Pool: eth0=LAN already, p2p start at eth1
        # Normal: p2p start at eth0
        eth_base = 1 if use_pool else 0
        for eth_idx, connected_router in enumerate(connected_routers):
            p2p_link    = router_state['p2p_links'][eth_idx]
            intf_new    = f'{router_name}-eth{eth_base + eth_idx}'
            existing_nd = _xarxa.mininet_nodes[connected_router]
            ex_link     = next(
                l for l in connected_states[connected_router]['p2p_links']
                if l['peer'] == router_name
            )
            intf_existing = f'{connected_router}-{ex_link["local_intf"]}'

            _xarxa.net.addLink(new_router, existing_nd,
                               intfName1=intf_new, intfName2=intf_existing)
            new_router.cmd(
                f'ifconfig {intf_new} {p2p_link["local_ip"]}/30 ; '
                f'ip link set {intf_new} up'
            )
            existing_nd.cmd(
                f'ifconfig {intf_existing} {ex_link["local_ip"]}/30 ; '
                f'ip link set {intf_existing} up'
            )

        # ── Start routing ──
        if use_pool:
            _xarxa._update_ospf_hot(new_router, router_name, router_state)
            existing = {
                n: p for n, p in _xarxa.nodes.items()
                if p['type'] == 'router' and n != router_name
                and n in _xarxa.mininet_nodes
            }
            ths = [
                threading.Thread(
                    target=_xarxa._update_ospf_hot,
                    args=(_xarxa.mininet_nodes[n], n, p),
                    daemon=True
                )
                for n, p in existing.items()
            ]
            for th in ths: th.start()
            for th in ths: th.join()
        else:
            _start_routing_on_new_router(router_name)

        t_local_ms = round((time.time() - t_local_start) * 1000, 2)
        return jsonify({'ok': True, 't_local_ms': t_local_ms})

    else:
        # ── Original path ──

        # ── PHASE 1: Pure Python — calculate everything before touching Mininet ──
        switch_num  = len([n for n, p in _xarxa.nodes.items() if p['type'] == 'switch']) + 1
        switch_name = f'sw{switch_num}'
        subnet_num  = _xarxa.find_next_subnet()
        ip_lan      = f'10.{subnet_num}.0.1/24'
        use_pool    = _xarxa._router_pool_available()
        eth_base    = 1 if use_pool else 0
        lan_eth_idx = len(connected_routers)

        # Pre-compute all p2p subnets (no Mininet involved)
        p2p_subnets = [_xarxa.find_next_p2p_subnet() for _ in connected_routers]

        # Pre-compute existing router eth indices
        existing_eth_idxs = {
            cr: len([k for k in _xarxa.nodes[cr]['ips'] if k.startswith('eth')])
            for cr in connected_routers
        }

        # Build complete router_state and connected_states
        new_ips       = {'lan': ip_lan}
        new_p2p_links = []
        connected_states_update = {}

        for idx, (cr, p2p) in enumerate(zip(connected_routers, p2p_subnets)):
            local_intf = f'eth{eth_base + idx}'
            ex_intf    = f'eth{existing_eth_idxs[cr]}'
            new_ips[local_intf] = f'{p2p["ip_a"]}/30'
            new_p2p_links.append({
                'peer': cr, 'local_ip': p2p['ip_a'],
                'peer_ip': p2p['ip_b'], 'subnet': p2p['subnet'],
                'local_intf': local_intf,
            })
            cr_state = {
                'type': _xarxa.nodes[cr]['type'],
                'ips':  dict(_xarxa.nodes[cr]['ips']),
                'routes': list(_xarxa.nodes[cr].get('routes', [])),
                'p2p_links': list(_xarxa.nodes[cr].get('p2p_links', [])),
            }
            cr_state['ips'][ex_intf] = f'{p2p["ip_b"]}/30'
            cr_state['p2p_links'].append({
                'peer': router_name, 'local_ip': p2p['ip_b'],
                'peer_ip': p2p['ip_a'], 'subnet': p2p['subnet'],
                'local_intf': ex_intf,
            })
            connected_states_update[cr] = cr_state

        if not use_pool:
            new_ips[f'eth{lan_eth_idx}'] = ip_lan

        router_state = {
            'type': 'router', 'ips': new_ips,
            'routes': [], 'p2p_links': new_p2p_links
        }

        # Update self.nodes with computed state (pure Python, instant)
        _xarxa.nodes[router_name] = router_state
        _xarxa.update_matrix_multi(router_name, connected_routers)
        _xarxa.nodes[switch_name] = {'type': 'switch'}
        _xarxa.update_matrix_multi(switch_name, [router_name])
        for cr, cr_state in connected_states_update.items():
            _xarxa.nodes[cr] = cr_state

        # ── PHASE 2: Send to Twin NOW — parallel with Mininet apply ──
        sync_event('/add_router', {
            'name':              router_name,
            'connected_routers': connected_routers,
            'router_state':      router_state,
            'switch_name':       switch_name,
            'connected_states':  connected_states_update,
        }, 0)

        # ── PHASE 3: Apply to Mininet (runs in parallel with Twin) ──
        t_local_start = time.time()

        if use_pool:
            new_router, new_switch = _xarxa.claim_from_pool(
                router_name, switch_name, ip_lan
            )
            if new_router is None:
                use_pool = False

        if not use_pool:
            new_router = _xarxa.net.addHost(router_name, ip='127.0.0.1')
            new_switch = _xarxa.net.addSwitch(switch_name, failMode='standalone')
            _xarxa.mininet_nodes[router_name] = new_router
            _xarxa.mininet_nodes[switch_name] = new_switch
            new_switch.start([])
            intf_lan_name = f'{router_name}-eth{lan_eth_idx}'
            _xarxa.net.addLink(new_router, new_switch, intfName1=intf_lan_name)
            new_router.cmd(
                f'ifconfig {intf_lan_name} {ip_lan} ; '
                f'ip link set {intf_lan_name} up ; '
                f'sysctl -w net.ipv4.ip_forward=1 ; '
                f'ifconfig lo up'
            )
            sw_intf = f'{switch_name}-eth1'
            new_switch.cmd(
                f'ip link set {sw_intf} up ; '
                f'ovs-vsctl add-port {switch_name} {sw_intf}'
            )

        # Connect p2p links using pre-computed subnets
        for idx, (cr, p2p) in enumerate(zip(connected_routers, p2p_subnets)):
            intf_new      = f'{router_name}-eth{eth_base + idx}'
            existing_node = _xarxa.mininet_nodes[cr]
            ex_intf       = f'{cr}-eth{len(existing_node.intfList())}'
            _xarxa.net.addLink(new_router, existing_node,
                               intfName1=intf_new, intfName2=ex_intf)
            new_router.cmd(
                f'ifconfig {intf_new} {p2p["ip_a"]}/30 ; ip link set {intf_new} up'
            )
            existing_node.cmd(
                f'ifconfig {ex_intf} {p2p["ip_b"]}/30 ; ip link set {ex_intf} up'
            )

        # Start routing
        if use_pool:
            _xarxa._update_ospf_hot(new_router, router_name, router_state)
            existing = {
                n: p for n, p in _xarxa.nodes.items()
                if p['type'] == 'router' and n != router_name
                and n in _xarxa.mininet_nodes
            }
            ths = [
                threading.Thread(
                    target=_xarxa._update_ospf_hot,
                    args=(_xarxa.mininet_nodes[n], n, p),
                    daemon=True
                )
                for n, p in existing.items()
            ]
            for th in ths: th.start()
            for th in ths: th.join()
        else:
            _start_routing_on_new_router(router_name)

        t_local_ms = round((time.time() - t_local_start) * 1000, 2)
        return jsonify({'ok': True})


@bp.route('/rename_node', methods=['POST'])
def rename_node():
    if not _xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})

    data     = request.json
    old_name = data['old_name']
    new_name = data['new_name']
    is_sync  = data.get('sync', False)

    if not new_name.replace('_', '').replace('-', '').isalnum() or new_name[0].isupper():
        return jsonify({'ok': False,
                        'error': 'Name must be lowercase alphanumeric (e.g. h6, router1)'})
    if old_name not in _xarxa.nodes:
        return jsonify({'ok': False, 'error': f'Node {old_name} not found'})
    if new_name in _xarxa.nodes:
        return jsonify({'ok': False, 'error': f'A node named {new_name} already exists'})

    new_nodes = {(new_name if n == old_name else n): p for n, p in _xarxa.nodes.items()}
    for props in new_nodes.values():
        if props['type'] == 'router':
            for link in props.get('p2p_links', []):
                if link['peer'] == old_name:
                    link['peer'] = new_name

    matrix_copy   = copy.deepcopy(_xarxa.network_matrix)
    t_local_start = time.time()
    threading.Thread(
        target=_xarxa.restart_network, args=(matrix_copy, new_nodes)
    ).start()
    t_local_ms = round((time.time() - t_local_start) * 1000, 2)

    if not is_sync:
        # rename_node cannot be done in-place on the Twin — full rebuild needed
        sync_snapshot('rename_node', t_local_ms)
        return jsonify({'ok': True})
    return jsonify({'ok': True, 't_local_ms': t_local_ms})