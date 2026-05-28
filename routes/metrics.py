"""
routes/metrics.py — Network and system measurement endpoints.

Endpoints:
  GET /metrics/system       → CPU + RAM of the host machine
  GET /metrics/ping         → ping (10 pkts) between two hosts
  GET /metrics/ping_fast    → ping (1 pkt) for live dashboard graph
  GET /metrics/internal     → full ping + iperf measurement between two hosts
  GET /metrics/global       → Global Scan: all host pairs in parallel
  GET /metrics/sync         → sync latency history + stats
  POST /sync_metrics        → receive sync metrics pushed by the Original
  GET /metrics/hosts        → list of host node names
  GET /metrics/traffic      → per-interface byte/packet counters for a node
  GET /metrics/link_traffic → byte counters for all router interfaces
  GET /ip_dashboard         → flat IP list + subnet grouping for all nodes
"""

import threading
import time

import psutil
from flask import Blueprint, jsonify, request

from sync import sync_history_lock, sync_latency_history, record_sync_latency
from utils import (
    get_ping_lock,
    jitter_of,
    measure_bandwidth,
    parse_ping,
    safe_stats,
    system_stats,
)

_xarxa           = None
_metrics_running = False
_socketio        = None   # injected by app.py after SocketIO is created

bp = Blueprint('metrics', __name__)


def init_blueprint(xarxa_instance, socketio_instance=None):
    global _xarxa, _socketio
    _xarxa    = xarxa_instance
    _socketio = socketio_instance


# ── Routes ──

@bp.route('/metrics/system')
def metrics_system():
    cpu = psutil.cpu_percent(interval=0.3)
    ram = psutil.virtual_memory()
    return jsonify({
        'ok':           True,
        'cpu_percent':  cpu,
        'ram_used_mb':  round(ram.used  / 1024 / 1024, 1),
        'ram_total_mb': round(ram.total / 1024 / 1024, 1),
        'ram_percent':  ram.percent,
    })


@bp.route('/metrics/ping')
def metrics_ping():
    """Ping measurement between two hosts with configurable options."""
    if not _xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})

    src     = request.args.get('src')
    dst     = request.args.get('dst')
    count   = int(request.args.get('count', 10))     # number of packets
    size    = int(request.args.get('size', 64))       # packet size in bytes
    interval = float(request.args.get('interval', 0.2))  # interval between pings

    if not src or not dst:
        return jsonify({'ok': False, 'error': 'src and dst parameters required'})
    if src not in _xarxa.nodes or dst not in _xarxa.nodes:
        return jsonify({'ok': False, 'error': 'Node not found'})
    if _xarxa.nodes[src]['type'] != 'host' or _xarxa.nodes[dst]['type'] != 'host':
        return jsonify({'ok': False, 'error': 'Both nodes must be hosts'})

    # Build ping command
    size_flag = f'-s {size}' if size != 64 else ''
    cmd       = f'ping -c {count} -i {interval} {size_flag} {{}}'.strip()
    cmd_str   = f'ping -c {count} -i {interval}' + (f' -s {size}' if size != 64 else '') + ' <dst_ip>'

    lock = get_ping_lock(src)
    with lock:
        src_node        = _xarxa.mininet_nodes[src]
        dst_ip          = _xarxa.nodes[dst]['ip'].split('/')[0]
        ping_cmd        = f'ping -c {count} -i {interval}' + (f' -s {size}' if size != 64 else '') + f' {dst_ip}'
        out             = src_node.cmd(ping_cmd)
        latency, jitter = parse_ping(out)

    return jsonify({'ok': True, 'src': src, 'dst': dst,
                    'latency_ms': latency, 'jitter_ms': jitter,
                    'cmd': cmd_str, 'count': count, 'size': size})


@bp.route('/metrics/ping_fast')
def metrics_ping_fast():
    """Single-packet ping for the live dashboard graph."""
    if not _xarxa.network_ready:
        return jsonify({'ok': False, 'avg': None})

    src = request.args.get('src')
    dst = request.args.get('dst')
    if not src or not dst or src not in _xarxa.nodes or dst not in _xarxa.nodes:
        return jsonify({'ok': False, 'avg': None})

    lock = get_ping_lock(src)
    if not lock.acquire(blocking=False):
        return jsonify({'ok': False, 'avg': None, 'busy': True})
    try:
        src_node   = _xarxa.mininet_nodes[src]
        dst_ip     = _xarxa.nodes[dst]['ip'].split('/')[0]
        out        = src_node.cmd(f'ping -c 1 -W 2 {dst_ip}')
        latency, _ = parse_ping(out)
        return jsonify({'ok': True, 'avg': latency['avg']})
    finally:
        lock.release()


@bp.route('/metrics/internal')
def metrics_internal():
    """Full ping + iperf measurement between two hosts."""
    global _metrics_running
    if not _xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})
    if _metrics_running:
        return jsonify({'ok': False, 'error': 'A measurement is already running'})

    src = request.args.get('src')
    dst = request.args.get('dst')
    if not src or not dst:
        return jsonify({'ok': False, 'error': 'src and dst parameters required'})
    if src not in _xarxa.nodes or dst not in _xarxa.nodes:
        return jsonify({'ok': False, 'error': 'Node not found'})
    if _xarxa.nodes[src]['type'] != 'host' or _xarxa.nodes[dst]['type'] != 'host':
        return jsonify({'ok': False, 'error': 'Both nodes must be hosts'})

    _metrics_running = True
    try:
        src_node        = _xarxa.mininet_nodes[src]
        dst_node        = _xarxa.mininet_nodes[dst]
        dst_ip          = _xarxa.nodes[dst]['ip'].split('/')[0]
        out             = src_node.cmd(f'ping -c 10 -i 0.2 {dst_ip}')
        latency, jitter = parse_ping(out)
        bandwidth       = measure_bandwidth(src_node, dst_node, dst_ip, iterations=10)

        return jsonify({
            'ok': True, 'src': src, 'dst': dst,
            'latency_ms':     latency,
            'jitter_ms':      jitter,
            'bandwidth_mbps': bandwidth,
            'system':         system_stats(),
        })
    finally:
        _metrics_running = False


def _emit_progress(step, total, msg):
    """Emit a progress event via WebSocket if SocketIO is available."""
    if _socketio:
        _socketio.emit('latency_matrix_progress', {
            'step':    step,
            'total':   total,
            'percent': round(step / total * 100),
            'msg':     msg,
        })


def _build_iperf_cmd(protocol, duration, parallel, bandwidth, reverse):
    flags = [f'-t {duration}', '-f m']
    if protocol == 'udp':
        flags.append('-u')
        if bandwidth:
            flags.append(f'-b {bandwidth}M')
    if parallel > 1:
        flags.append(f'-P {parallel}')
    if reverse:
        flags.append('-R')
    return 'iperf -c <dst_ip> ' + ' '.join(flags)


@bp.route('/metrics/global')
def metrics_global():
    """
    Global Scan — ping (and optionally iperf) all host pairs.

    Query params:
      mode=fast   ping only (~3 s)   [default]
      mode=full   ping + iperf, parallelised by groups (~15 s)

    Progress events are pushed via WebSocket ('latency_matrix_progress')
    so the browser can show a live progress bar.
    """
    global _metrics_running
    if not _xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})
    if _metrics_running:
        return jsonify({'ok': False, 'error': 'A measurement is already running'})

    mode              = request.args.get('mode', 'fast')
    iperf_protocol    = request.args.get('protocol', 'tcp')
    iperf_duration    = int(request.args.get('duration', 1))
    iperf_parallel    = int(request.args.get('parallel', 1))
    iperf_iterations  = int(request.args.get('iterations', 3))
    iperf_bandwidth   = request.args.get('bandwidth', None)   # UDP only, Mbps
    iperf_reverse     = request.args.get('reverse', 'false').lower() == 'true'
    ping_count        = int(request.args.get('ping_count', 5))
    ping_size         = int(request.args.get('ping_size', 64))

    # Dynamic timeout based on iperf params
    if mode == 'full':
        n_pairs      = len(hosts) * (len(hosts) - 1) // 2 if 'hosts' in dir() else 20
        est_time     = n_pairs * iperf_iterations * iperf_duration * 1.5 + 30
        iperf_timeout = int(est_time)
    else:
        iperf_timeout = 60

    hosts = [
        n for n, p in _xarxa.nodes.items()
        if p['type'] == 'host'
        and n in _xarxa.mininet_nodes
        and _xarxa.mininet_nodes[n].shell is not None
    ]
    if len(hosts) < 2:
        return jsonify({'ok': False, 'error': 'Need at least 2 hosts'})

    _metrics_running = True
    try:
        pairs = [
            (hosts[i], hosts[j])
            for i in range(len(hosts))
            for j in range(i + 1, len(hosts))
        ]
        total_steps = len(pairs) + (len(pairs) if mode == 'full' else 0)
        step        = 0

        # ── Helper: group pairs so no node appears twice per group ──
        def get_parallel_groups(pairs):
            groups, remaining = [], list(pairs)
            while remaining:
                group, used = [], set()
                for pair in remaining[:]:
                    s, d = pair
                    if s not in used and d not in used:
                        group.append(pair)
                        used.add(s)
                        used.add(d)
                        remaining.remove(pair)
                groups.append(group)
            return groups

        # ── Phase 1: Ping all pairs in parallel groups ──
        ping_results, ping_lock = {}, threading.Lock()

        def ping_pair(src, dst):
            nonlocal step
            src_node        = _xarxa.mininet_nodes[src]
            dst_ip          = _xarxa.nodes[dst]['ip'].split('/')[0]
            size_flag       = f'-s {ping_size}' if ping_size != 64 else ''
            ping_cmd        = f'ping -c {ping_count} -i 0.2 {size_flag} {dst_ip}'.strip()
            out             = src_node.cmd(ping_cmd)
            latency, jitter = parse_ping(out)
            if latency['avg'] is not None:
                with ping_lock:
                    ping_results[f'{src}->{dst}'] = {
                        'min':    latency['min'],
                        'avg':    latency['avg'],
                        'max':    latency['max'],
                        'jitter': jitter,
                    }
                    step += 1
                    _emit_progress(step, total_steps, f'Ping {src} → {dst}')

        _emit_progress(0, total_steps, 'Starting ping measurements...')
        for group in get_parallel_groups(pairs):
            threads = [threading.Thread(target=ping_pair, args=(s, d)) for s, d in group]
            for t in threads: t.start()
            for t in threads: t.join()

        # ── Phase 2: Bandwidth (full mode only, parallel groups) ──
        bw_results, bw_lock = {}, threading.Lock()

        if mode == 'full':
            _emit_progress(step, total_steps, 'Starting bandwidth measurements...')

            def bw_pair(src, dst):
                nonlocal step
                src_node = _xarxa.mininet_nodes[src]
                dst_node = _xarxa.mininet_nodes[dst]
                dst_ip   = _xarxa.nodes[dst]['ip'].split('/')[0]
                bw       = measure_bandwidth(
                    src_node, dst_node, dst_ip,
                    iterations=iperf_iterations,
                    protocol=iperf_protocol,
                    duration=iperf_duration,
                    parallel=iperf_parallel,
                    bandwidth_mbps=iperf_bandwidth,
                    reverse=iperf_reverse,
                )
                with bw_lock:
                    if bw['avg'] is not None:
                        bw_results[f'{src}->{dst}'] = bw
                    step += 1
                    _emit_progress(step, total_steps, f'Bandwidth {src} → {dst}')

            for group in get_parallel_groups(pairs):
                threads = [threading.Thread(target=bw_pair, args=(s, d)) for s, d in group]
                for t in threads: t.start()
                for t in threads: t.join()

        _emit_progress(total_steps, total_steps, 'Done!')

        all_avg_lat = [v['avg']    for v in ping_results.values()]
        all_min_lat = [v['min']    for v in ping_results.values()]
        all_max_lat = [v['max']    for v in ping_results.values()]
        all_jitter  = [v['jitter'] for v in ping_results.values()]
        all_avg_bw  = [v['avg']    for v in bw_results.values()]
        all_min_bw  = [v['min']    for v in bw_results.values()]
        all_max_bw  = [v['max']    for v in bw_results.values()]

        return jsonify({
            'ok':                 True,
            'mode':               mode,
            'pairs_tested':       len(pairs),
            'latency_ms':         safe_stats(all_avg_lat),
            'latency_min_ms':     round(min(all_min_lat), 2) if all_min_lat else None,
            'latency_max_ms':     round(max(all_max_lat), 2) if all_max_lat else None,
            'jitter_ms':          safe_stats(all_jitter),
            'bandwidth_mbps':     safe_stats(all_avg_bw),
            'bandwidth_min_mbps': round(min(all_min_bw), 2) if all_min_bw else None,
            'bandwidth_max_mbps': round(max(all_max_bw), 2) if all_max_bw else None,
            'per_pair':           {'latency': ping_results, 'bandwidth': bw_results},
            'iperf_cmd':          _build_iperf_cmd(iperf_protocol, iperf_duration, iperf_parallel, iperf_bandwidth, iperf_reverse),
            'ping_cmd':           f'ping -c {ping_count} -i 0.2' + (f' -s {ping_size}' if ping_size != 64 else '') + ' <dst_ip>',
            'system':             system_stats(),
        })
    finally:
        _metrics_running = False


@bp.route('/metrics/sync')
def metrics_sync():
    with sync_history_lock:
        history = list(sync_latency_history)
    if not history:
        return jsonify({'ok': True, 'history': [], 'stats': None})

    t_local   = [e.get('t_local_ms')   for e in history]
    t_network = [e.get('t_network_ms') for e in history]
    t_twin    = [e.get('t_twin_ms')    for e in history]

    # t_total = real end-to-end latency
    # For sequential ops: t_local + t_network
    # For parallel ops (add_router): max(t_local, t_network)
    # latency_ms in the entry stores the correct value when available
    t_total = []
    for e in history:
        tl = e.get('t_local_ms')
        tn = e.get('t_network_ms')
        lm = e.get('latency_ms')
        if lm is not None:
            t_total.append(round(lm, 2))
        elif tl is not None and tn is not None:
            t_total.append(round(tl + tn, 2))
        else:
            t_total.append(None)

    return jsonify({
        'ok':      True,
        'history': history,
        'stats': {
            'count':          len(history),
            't_local':        safe_stats(t_local),
            't_network':      safe_stats(t_network),
            't_twin':         safe_stats(t_twin),
            't_total':        safe_stats(t_total),
            'avg_ms':         safe_stats(t_total)['avg'],
            'min_ms':         safe_stats(t_total)['min'],
            'max_ms':         safe_stats(t_total)['max'],
            'jitter_ms':      jitter_of(t_total),
            'jitter_net_ms':  jitter_of(t_network),
            'jitter_twin_ms': jitter_of(t_twin),
        },
    })


@bp.route('/sync_metrics', methods=['POST'])
def update_sync_metrics():
    """
    Receive a sync timing entry pushed by the Original.
    The Original is the source of truth — Twin mirrors its history exactly.
    If the entry has t_local_ms but no t_network_ms, it's a late update
    for an existing entry (parallel sync case).
    """
    data       = request.json
    operation  = data.get('operation', 'External Update')
    t_local    = data.get('t_local_ms')
    t_network  = data.get('t_network_ms')
    t_twin     = data.get('t_twin_ms')
    latency    = data.get('latency_ms')

    with sync_history_lock:
        # Late update: update existing entry instead of appending
        if t_local is not None and t_network is None and t_twin is None:
            for entry in reversed(sync_latency_history):
                if entry.get('operation') == operation:
                    entry['t_local_ms'] = t_local
                    if latency is not None:
                        entry['latency_ms'] = latency
                    return jsonify({'ok': True})

        # New entry
        entry = {
            'operation':    operation,
            'latency_ms':   latency,
            't_local_ms':   t_local,
            't_network_ms': t_network,
            't_twin_ms':    t_twin,
            'timestamp':    data.get('timestamp', time.time()),
        }
        sync_latency_history.append(entry)
    return jsonify({'ok': True})


@bp.route('/metrics/hosts')
def metrics_hosts():
    hosts = [name for name, props in _xarxa.nodes.items() if props['type'] == 'host']
    return jsonify({'hosts': hosts})


@bp.route('/metrics/traffic')
def metrics_traffic():
    if not _xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})

    node = request.args.get('node')
    if not node or node not in _xarxa.nodes:
        return jsonify({'ok': False, 'error': 'Node not found'})
    if _xarxa.nodes[node]['type'] == 'switch':
        return jsonify({'ok': False, 'error': 'Switches not supported'})

    mn_node = _xarxa.mininet_nodes[node]
    if mn_node.shell is None or mn_node.waiting:
        return jsonify({'ok': False, 'error': 'Node shell busy'})
    try:
        raw = mn_node.cmd('cat /proc/net/dev')
    except Exception:
        return jsonify({'ok': False, 'error': 'Node shell error'})

    interfaces = {}
    for line in raw.strip().split('\n')[2:]:
        parts = line.split(':')
        if len(parts) < 2:
            continue
        intf = parts[0].strip()
        if intf == 'lo':
            continue
        values = parts[1].split()
        interfaces[intf] = {
            'rx_bytes':   int(values[0]),
            'rx_packets': int(values[1]),
            'tx_bytes':   int(values[8]),
            'tx_packets': int(values[9]),
        }
    return jsonify({'ok': True, 'node': node, 'interfaces': interfaces})


@bp.route('/metrics/link_traffic')
def metrics_link_traffic():
    if not _xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})

    links = {}
    for name, props in _xarxa.nodes.items():
        if props['type'] != 'router':
            continue
        mn_node = _xarxa.mininet_nodes[name]
        if mn_node.shell is None or mn_node.waiting:
            continue
        try:
            raw = mn_node.cmd('cat /proc/net/dev')
        except Exception:
            continue
        for line in raw.strip().split('\n')[2:]:
            parts = line.split(':')
            if len(parts) < 2:
                continue
            intf = parts[0].strip()
            if intf == 'lo':
                continue
            values = parts[1].split()
            # Mininet names interfaces as "r1-eth0" — strip router prefix
            intf_short = intf[len(name)+1:] if intf.startswith(name + '-') else intf
            links[f'{name}-{intf_short}'] = {
                'node':     name,
                'intf':     intf_short,
                'rx_bytes': int(values[0]),
                'tx_bytes': int(values[8]),
            }
    return jsonify({'ok': True, 'links': links})


@bp.route('/ip_dashboard')
def ip_dashboard():
    flat    = []
    subnets = {}

    for name, props in _xarxa.nodes.items():
        t = props['type']
        if t == 'host':
            ip  = props.get('ip', '—')
            gw  = props.get('gw', None)
            flat.append({'node': name, 'type': t, 'intf': 'eth0', 'ip': ip, 'gw': gw})
            subnet = ip.rsplit('.', 1)[0] + '.0/' + ip.split('/')[1] if '/' in ip else ip
            subnets.setdefault(subnet, []).append(
                {'node': name, 'type': t, 'intf': 'eth0', 'ip': ip, 'gw': gw})

        elif t == 'router':
            for intf, ip in props.get('ips', {}).items():
                if intf == 'lan':
                    continue
                flat.append({'node': name, 'type': t, 'intf': intf, 'ip': ip, 'gw': None})
                subnet = ip.rsplit('.', 1)[0] + '.0/' + ip.split('/')[1] if '/' in ip else ip
                subnets.setdefault(subnet, []).append(
                    {'node': name, 'type': t, 'intf': intf, 'ip': ip, 'gw': None})

    type_order = {'router': 0, 'host': 1, 'switch': 2}
    flat.sort(key=lambda r: (type_order.get(r['type'], 9), r['node']))

    subnet_list = [
        {'subnet': s, 'members': sorted(members, key=lambda m: (m['type'], m['node']))}
        for s, members in sorted(subnets.items())
    ]
    return jsonify({'ok': True, 'flat': flat, 'subnets': subnet_list})