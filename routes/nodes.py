"""
routes/nodes.py — Live topology modification endpoints.

Endpoints:
  POST /add_host      → add a host to an existing router's subnet
  POST /remove_node   → remove a host or an entire router subnet
  POST /add_router    → add a new router with its switch and p2p links

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
"""

import re
import threading
import time

from flask import Blueprint, jsonify, request

from sync import sync_event

_xarxa = None

# ─────────────────────────────────────────────────────────────────────────────
# nodes.py — Operacions de modificació de topologia en temps real
#
# Endpoints:
#   POST /add_host    → afegeix un host a la subxarxa d'un router
#   POST /remove_node → elimina un host o un router complet
#   POST /add_router  → afegeix un router nou (amb switch i links p2p)
#   POST /load_network→ reconstrueix la xarxa des d'un snapshot complet
#
# PATRÓ COMÚ: Original path vs Twin path
#   - Original: calcula tot, sincronitza al Twin, aplica a Mininet
#   - Twin:     rep l'estat pre-calculat, aplica directament sense calcular
#
# CONCURRÈNCIA: topology_lock (RLock) protegeix les mutacions de l'estat Python.
# Les operacions lentes de Mininet (addLink, FRR) corren FORA del lock.
# ─────────────────────────────────────────────────────────────────────────────
bp = Blueprint('nodes', __name__)


def init_blueprint(xarxa_instance):
    global _xarxa
    _xarxa = xarxa_instance


# ── Node name validation ──
# El nom del node acaba dins comandes de shell (ifconfig {name}-eth0, etc.) i
# en noms d'interfície de Linux. Validem que només contingui lletres, dígits,
# guió i guió baix (evita injecció de comandes) i que comenci per lletra.
# Límit de 10 chars: les interfícies Linux són IFNAMSIZ-1 = 15 chars i el
# sufix més llarg és "-ethN" (5 chars), així que {name} ha de cabre en 10.
_NODE_NAME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,9}$')


def _valid_node_name(name):
    return bool(name and _NODE_NAME_RE.match(name))


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
# ─────────────────────────────────────────────────────────────────────────
# ADD HOST
# Afegeix un host nou a la subxarxa d'un router existent.
#
# Dins topology_lock:
#   - Valida nom únic i existència del router
#   - Calcula IP (find_next_ip) i switch corresponent
#   - Actualitza nodes + matriu
#   - addHost + addLink (dins lock per evitar nom d'intf duplicat)
#
# Fora del lock:
#   - ifconfig + default gateway
#   - ovs-vsctl add-port (usa os.system, no sw_node.cmd, perquè OVS corre
#     al host, no dins del namespace; evita assert self.waiting)
# ─────────────────────────────────────────────────────────────────────────
def add_host():
    if not _xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})

    import os
    data    = request.json
    name    = data['name']
    router  = data['router']
    is_sync = data.get('sync', False)

    if not _valid_node_name(name):
        return jsonify({'ok': False, 'error': f'Invalid host name: {name}'})

    # ── Critical section: validate + compute + mutate Python state + addLink ──
    # The lock covers the intfList() read and addLink together so two concurrent
    # add_host calls on the same switch never compute the same interface name.
    with _xarxa.topology_lock:
        if name in _xarxa.nodes:
            return jsonify({'ok': False, 'error': f'A node named {name} already exists'})
        if router not in _xarxa.nodes:
            return jsonify({'ok': False, 'error': f'Router {router} not found'})

        # Use pre-computed values if provided (sync from Original), else compute fresh
        switch = data.get('switch') or _xarxa.find_switch_of_router(router)
        ip     = data.get('ip')    or _xarxa.find_next_ip(router)
        gw     = data.get('gw')
        if not gw:
            lan_ip = next(i for i in _xarxa.nodes[router]['ips'].values() if '/24' in i)
            gw     = lan_ip.split('/')[0]

        _xarxa.nodes[name] = {'type': 'host', 'ip': ip, 'gw': gw}
        _xarxa.update_matrix(name, switch)

        # ── Apply to Mininet (inside lock: intfList read + addLink must be atomic) ──
        t_local_start = time.time()
        new_host = _xarxa.net.addHost(name, ip=ip)
        _xarxa.mininet_nodes[name] = new_host
        sw_node      = _xarxa.mininet_nodes[switch]
        sw_intf_name = f'{switch}-eth{len(sw_node.intfList())}'

        if not is_sync:
            # Send to Twin in parallel BEFORE addLink (all values pre-computed)
            from sync import sync_event, set_t_local
            holder = sync_event('/add_host', {
                'name': name, 'router': router,
                'switch': switch, 'ip': ip, 'gw': gw,
            }, None)

        _xarxa.net.addLink(new_host, sw_node,
                           intfName1=f'{name}-eth0', intfName2=sw_intf_name)
    # ── Outside lock: node.cmd calls don't need mutual exclusion ──
    new_host.cmd(
        f'ifconfig {name}-eth0 {ip} ; '
        f'ip route add default via {gw} ; '
        f'ifconfig lo up ; '
        f'ip link set lo up ; '
        f'ip link set {name}-eth0 up'
    )
    sw_node.cmd(f'ip link set {sw_intf_name} up')
    # ovs-vsctl runs on the HOST (OVS is not namespaced) — use os.system to
    # avoid going through the switch node's bash shell (thread-safety issue).
    os.system(f'ovs-vsctl add-port {switch} {sw_intf_name} 2>/dev/null')
    t_local_ms = round((time.time() - t_local_start) * 1000, 2)

    if not is_sync:
        set_t_local(holder, t_local_ms)
        return jsonify({'ok': True})
    return jsonify({'ok': True, 't_local_ms': t_local_ms})


@bp.route('/remove_node', methods=['POST'])
# ─────────────────────────────────────────────────────────────────────────
# REMOVE NODE
# Elimina un host o un router complet (hosts + switch).
#
# 3 FASES per evitar crashes en cascada:
#   Fase 1 (dins lock): snapshot de tipus i refs Mininet
#   Fase 2 (dins lock): elimina TOTS els dicts Python atòmicament
#                       (nodes, network_matrix, mininet_nodes)
#   Fase 3 (fora lock): delNode per ordre hosts→router→switch,
#                       cada un amb try/except independent
#
# Per qué l'ordre i el try/except?
#   Quan Mininet elimina un switch, destrueix els veth pairs dels hosts.
#   Això pot matar el shell del router → OSError al pròxim delNode.
#   try/except per node evita que un error aturi la resta.
# ─────────────────────────────────────────────────────────────────────────
def remove_node():
    if not _xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})

    data    = request.json
    name    = data['name']
    is_sync = data.get('sync', False)

    if name not in _xarxa.nodes:
        return jsonify({'ok': False, 'error': f'Node {name} not found'})

    if not is_sync:
        from sync import sync_event, set_t_local
        holder = sync_event('/remove_node', {'name': name}, None)

    t_local_start = time.time()

    if _xarxa.nodes[name]['type'] == 'router':
        # ── Critical section: clean Python state atomically ──
        with _xarxa.topology_lock:
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

            nodes_to_remove = _xarxa.find_router_subnet(name)
            nodes_to_remove.append(name)

            # PHASE 1: Snapshot types + Mininet refs before modifying dicts
            node_types = {n: _xarxa.nodes[n]['type']
                          for n in nodes_to_remove if n in _xarxa.nodes}
            mn_nodes = {n: _xarxa.mininet_nodes[n]
                        for n in nodes_to_remove if n in _xarxa.mininet_nodes}

            # PHASE 2: Remove ALL from matrix + dicts (pure Python, must be atomic)
            for node in nodes_to_remove:
                if node in _xarxa.nodes:
                    _xarxa.remove_from_matrix(node)
                    del _xarxa.nodes[node]
                _xarxa.mininet_nodes.pop(node, None)

        # PHASE 3: Delete from Mininet OUTSIDE the lock — delNode is slow and
        # can raise OSError when veth pairs break; no dict access needed here.
        ordered = (
            [n for n in nodes_to_remove if node_types.get(n) == 'host']
            + [n for n in nodes_to_remove if node_types.get(n) == 'router']
            + [n for n in nodes_to_remove if node_types.get(n) == 'switch']
        )
        for node in ordered:
            if node not in mn_nodes:
                continue
            try:
                _xarxa.net.delNode(mn_nodes[node])
            except Exception:
                pass  # shell dead or veth pair already gone — safe to ignore

        t_local_ms = round((time.time() - t_local_start) * 1000, 2)
        threading.Thread(target=_update_all_routes, daemon=True).start()

    else:  # host
        with _xarxa.topology_lock:
            _xarxa.remove_from_matrix(name)
            nd = _xarxa.mininet_nodes.pop(name, None)
            del _xarxa.nodes[name]
        # delNode outside the lock
        if nd:
            try:
                _xarxa.net.delNode(nd)
            except Exception:
                pass
        t_local_ms = round((time.time() - t_local_start) * 1000, 2)

    if not is_sync:
        set_t_local(holder, t_local_ms)
        return jsonify({'ok': True})
    return jsonify({'ok': True, 't_local_ms': t_local_ms})

@bp.route('/add_router', methods=['POST'])
# ─────────────────────────────────────────────────────────────────────────
# ADD ROUTER — l'endpoint més complex del projecte
#
# TÉ DOS PATHS:
#   1. Twin path (is_sync=True): rep l'estat pre-calculat de l'Original
#      i l'aplica directament a Mininet sense cap càlcul.
#   2. Original path (is_sync=False): 3 fases:
#
# FASE 1 (dins topology_lock) — càlcul pur Python:
#   - Número de switch, subxarxa LAN, subnets p2p per cada connexió
#   - Construeix router_state i connected_states_update (estats actualitzats
#     dels routers veïns amb la nova interfície p2p)
#   - Escriu a _xarxa.nodes i a la matriu d'adjacència
#   - use_pool i eth_base calculats aquí (eth_base=1 si pool, 0 si no)
#
# FASE 2 — envia event al Twin via sync_event() en thread background
#   (corre en paral·lel amb la Fase 3)
#
# FASE 3 (fora del lock) — aplica a Mininet:
#   - claim_from_pool() o addHost()+addSwitch() si pool buit
#   - addLink per cada connexió p2p
#   - ifconfig per configurar les IPs
#   - _apply_routing() arrenca OSPF al nou router
#   - _update_ospf_hot() en threads background per als routers existents
#   - AL FINAL: _pool_replenish() — no abans! Mininet no és thread-safe
#
# t_total = max(t_local, t_network) — Fase 2 i 3 corren en paral·lel
# ─────────────────────────────────────────────────────────────────────────
def add_router():
    if not _xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})

    import os
    data              = request.json
    router_name       = data['name']
    connected_routers = data['connected_routers']
    is_sync           = data.get('sync', False)

    if not _valid_node_name(router_name):
        return jsonify({'ok': False, 'error': f'Invalid router name: {router_name}'})

    if is_sync and 'router_state' in data:
        # ── Twin path: apply pre-computed state from Original ──
        switch_name      = data['switch_name']
        router_state     = data['router_state']
        connected_states = data['connected_states']

        # ── Critical section: validate + restore Python state atomically ──
        with _xarxa.topology_lock:
            if router_name in _xarxa.nodes:
                return jsonify({'ok': False, 'error': f'A node named {router_name} already exists'})
            _xarxa.nodes[router_name] = router_state
            _xarxa.update_matrix_multi(router_name, connected_routers)
            _xarxa.nodes[switch_name] = {'type': 'switch'}
            _xarxa.update_matrix_multi(switch_name, [router_name])
            for rname, rstate in connected_states.items():
                _xarxa.nodes[rname] = rstate

        # ── Mininet apply outside lock (slow ops: pool, addLink, FRR) ──
        t_local_start = time.time()
        n_p2p    = len(connected_routers)
        ip_lan   = router_state['ips'].get('lan', '10.254.0.1/24')
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
            with _xarxa.topology_lock:
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
            new_switch.cmd(f'ip link set {sw_intf} up')
            os.system(f'ovs-vsctl add-port {switch_name} {sw_intf} 2>/dev/null')

        # ── Batching: accumulate new router's commands into ONE cmd() call ──
        # Each node.cmd() has overhead (pipe write + prompt wait + read). With N
        # neighbours we save N-1 round-trips. Neighbour cmd()s stay in-loop —
        # each one targets a different node so batching is not possible.
        eth_base = 1 if use_pool else 0
        new_router_cmds = []
        for eth_idx, connected_router in enumerate(connected_routers):
            p2p_link      = router_state['p2p_links'][eth_idx]
            intf_new      = f'{router_name}-eth{eth_base + eth_idx}'
            existing_nd   = _xarxa.mininet_nodes[connected_router]
            ex_link       = next(
                l for l in connected_states[connected_router]['p2p_links']
                if l['peer'] == router_name
            )
            intf_existing = f'{connected_router}-{ex_link["local_intf"]}'
            _xarxa.net.addLink(new_router, existing_nd,
                               intfName1=intf_new, intfName2=intf_existing)
            new_router_cmds.append(
                f'ifconfig {intf_new} {p2p_link["local_ip"]}/30 ; '
                f'ip link set {intf_new} up'
            )
            existing_nd.cmd(
                f'ifconfig {intf_existing} {ex_link["local_ip"]}/30 ; '
                f'ip link set {intf_existing} up'
            )
        # Flush all new router p2p configs in a single cmd() invocation
        if new_router_cmds:
            new_router.cmd(' ; '.join(new_router_cmds))

        if use_pool:
            existing = {
                n: p for n, p in _xarxa.nodes.items()
                if p['type'] == 'router' and n != router_name
                and n in _xarxa.mininet_nodes
            }
            _xarxa._apply_routing(new_router, router_name, router_state)
            for n, p in existing.items():
                threading.Thread(
                    target=_xarxa._update_ospf_hot,
                    args=(_xarxa.mininet_nodes[n], n, p),
                    daemon=True
                ).start()
            # Replenish pool NOW — after all net.addLink calls are done.
            # Starting it earlier (inside claim_from_pool) causes concurrent
            # Mininet net operations that corrupt internal state.
            threading.Thread(target=_xarxa._pool_replenish, daemon=True).start()
        else:
            _start_routing_on_new_router(router_name)

        t_local_ms = round((time.time() - t_local_start) * 1000, 2)
        return jsonify({'ok': True, 't_local_ms': t_local_ms})

    else:
        # ── Original path ──

        # ── PHASE 1 (inside lock): validate + compute all values + mutate dicts ──
        # The lock ensures no other topology change races with our computation:
        # switch number, subnet, p2p octets — all read-then-write must be atomic.
        with _xarxa.topology_lock:
            if router_name in _xarxa.nodes:
                return jsonify({'ok': False, 'error': f'A node named {router_name} already exists'})

            switch_num  = len([n for n, p in _xarxa.nodes.items() if p['type'] == 'switch']) + 1
            switch_name = f'sw{switch_num}'
            subnet_num  = _xarxa.find_next_subnet()
            ip_lan      = f'10.{subnet_num}.0.1/24'
            use_pool    = _xarxa._router_pool_available()
            eth_base    = 1 if use_pool else 0
            lan_eth_idx = len(connected_routers)

            # Pre-compute all p2p subnets atomically
            p2p_subnets = []
            reserved = set()
            for _ in connected_routers:
                s = _xarxa.find_next_p2p_subnet(extra_used=reserved)
                reserved.add(int(s['subnet'].split('.')[2]))
                p2p_subnets.append(s)

            existing_eth_idxs = {
                cr: len([k for k in _xarxa.nodes[cr]['ips'] if k.startswith('eth')])
                for cr in connected_routers
            }

            new_ips, new_p2p_links, connected_states_update = {'lan': ip_lan}, [], {}
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

            lan_eth_key = 'eth0' if use_pool else f'eth{lan_eth_idx}'
            new_ips[lan_eth_key] = ip_lan
            router_state = {
                'type': 'router', 'ips': new_ips,
                'routes': [], 'p2p_links': new_p2p_links
            }

            # Write all state atomically (still inside lock)
            _xarxa.nodes[router_name] = router_state
            _xarxa.update_matrix_multi(router_name, connected_routers)
            _xarxa.nodes[switch_name] = {'type': 'switch'}
            _xarxa.update_matrix_multi(switch_name, [router_name])
            for cr, cr_state in connected_states_update.items():
                _xarxa.nodes[cr] = cr_state

        # ── PHASE 2: Send to Twin NOW, in parallel with Mininet apply ──
        from sync import sync_event, set_t_local
        holder = sync_event('/add_router', {
            'name':              router_name,
            'connected_routers': connected_routers,
            'router_state':      router_state,
            'switch_name':       switch_name,
            'connected_states':  connected_states_update,
        }, None)

        # ── PHASE 3: Apply to Mininet (outside lock — slow ops) ──
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
            with _xarxa.topology_lock:
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
            new_switch.cmd(f'ip link set {sw_intf} up')
            os.system(f'ovs-vsctl add-port {switch_name} {sw_intf} 2>/dev/null')

        # ── Batching: same approach as in the Twin path above ──
        new_router_cmds = []
        for idx, (cr, p2p) in enumerate(zip(connected_routers, p2p_subnets)):
            intf_new      = f'{router_name}-eth{eth_base + idx}'
            existing_node = _xarxa.mininet_nodes[cr]
            ex_intf       = f'{cr}-eth{existing_eth_idxs[cr]}'
            _xarxa.net.addLink(new_router, existing_node,
                               intfName1=intf_new, intfName2=ex_intf)
            new_router_cmds.append(
                f'ifconfig {intf_new} {p2p["ip_a"]}/30 ; ip link set {intf_new} up'
            )
            existing_node.cmd(
                f'ifconfig {ex_intf} {p2p["ip_b"]}/30 ; ip link set {ex_intf} up'
            )
        if new_router_cmds:
            new_router.cmd(' ; '.join(new_router_cmds))

        if use_pool:
            existing = {
                n: p for n, p in _xarxa.nodes.items()
                if p['type'] == 'router' and n != router_name
                and n in _xarxa.mininet_nodes
            }
            _xarxa._apply_routing(new_router, router_name, router_state)
            for n, p in existing.items():
                threading.Thread(
                    target=_xarxa._update_ospf_hot,
                    args=(_xarxa.mininet_nodes[n], n, p),
                    daemon=True
                ).start()
            # Replenish pool NOW — after all net.addLink calls are done.
            # Starting it earlier (inside claim_from_pool) causes concurrent
            # Mininet net operations that corrupt internal state.
            threading.Thread(target=_xarxa._pool_replenish, daemon=True).start()
        else:
            _start_routing_on_new_router(router_name)

        t_local_ms = round((time.time() - t_local_start) * 1000, 2)
        set_t_local(holder, t_local_ms)
        return jsonify({'ok': True})