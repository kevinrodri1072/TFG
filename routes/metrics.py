"""
routes/metrics.py — Network and system measurement endpoints.

Endpoints:
  GET /metrics/system       → CPU + RAM of the host machine
  GET /metrics/ping         → ping (10 pkts) between two hosts
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

# ─────────────────────────────────────────────────────────────────────────────
# metrics.py — Endpoints de mesura de la xarxa i del sistema
#
# Endpoints:
#   GET  /metrics/system      → CPU + RAM del host
#   GET  /metrics/ping        → ping configurable entre dos hosts (count, size, interval)
#   GET  /metrics/global      → Global Scan: pings entre TOTS els parells de hosts
#   GET  /metrics/sync        → historial de latències de sincronització + estadístiques
#   POST /sync_metrics        → rep mètriques de sync enviades per l'Original (al Twin)
#   GET  /metrics/hosts       → llista de noms dels nodes host
#   GET  /metrics/traffic     → comptadors rx/tx d'un node (via /proc/pid/net/dev)
#   GET  /metrics/link_traffic→ comptadors rx/tx de tots els routers
#   GET  /ip_dashboard        → IPs i subnets de tots els nodes (per "IP Dashboard")
# ─────────────────────────────────────────────────────────────────────────────
bp = Blueprint('metrics', __name__)


def init_blueprint(xarxa_instance, socketio_instance=None):
    global _xarxa, _socketio
    _xarxa    = xarxa_instance
    _socketio = socketio_instance


# ── Routes ──

@bp.route('/metrics/system')
# Retorna CPU i RAM actuals del host via psutil.
# interval=0.3: mesura el CPU durant 300ms per un valor més precís.
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
# Fa un ping entre dos hosts de la xarxa Mininet.
# Usa get_ping_lock(src) per evitar pings concurrents al mateix shell bash.
# Paràmetres via query string: src, dst, count (pkts), size (bytes), interval (s).
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
# Global Scan: fa ping (i opcionalment iperf) entre TOTS els parells de hosts.
# mode=fast → ping only (~3s)
# mode=full → ping + iperf, paral·lelitzat per grups (~15s)
# Emet events de progrés via WebSocket ('latency_matrix_progress') perquè
# el navegador pugui mostrar una barra de progrés en temps real.
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
# Retorna l'historial de latències de sincronització Digital Twin.
# op_filter: filtra per tipus d'operació (add_router, add_host, remove_node...).
# t_total = max(t_local, t_network) — execució paral·lela, no suma.
# Calcula estadístiques (min/avg/max/jitter) per a cada component de latència.
# Noves mètriques:
#   throughput_bps  → bits/s reals del link Original→Twin per operació
#   payload_bytes   → mida JSON de cada missatge de sync
#   ops_per_sec     → capacitat de CPU (màx teòric) i ops reals (últims 10s)
#   cpu_at_sync     → ús de CPU del host en el moment de cada operació
def metrics_sync():
    op_filter = request.args.get('op', '').strip()
    with sync_history_lock:
        if op_filter:
            history = [e for e in sync_latency_history if e.get('operation') == op_filter]
        else:
            history = list(sync_latency_history)
    if not history:
        return jsonify({'ok': True, 'history': [], 'stats': None})

    t_local   = [e.get('t_local_ms')   for e in history]
    t_network = [e.get('t_network_ms') for e in history]
    t_twin    = [e.get('t_twin_ms')    for e in history]

    # t_total = max(t_local, t_network) — execució paral·lela, no suma
    t_total = []
    for e in history:
        tl = e.get('t_local_ms')
        tn = e.get('t_network_ms')
        if tl is not None and tn is not None:
            t_total.append(round(max(tl, tn), 2))
        elif tn is not None:
            t_total.append(round(tn, 2))
        else:
            t_total.append(None)

    # ── Throughput i payload ──
    cpu_vals   = [e.get('cpu_percent') for e in history]

    # ── Ops/s ──
    t_local_valid = [t for t in t_local if t is not None and t > 0]
    t_total_valid = [t for t in t_total if t is not None and t > 0]
    avg_t_local   = sum(t_local_valid) / len(t_local_valid) if t_local_valid else None
    min_t_local   = min(t_local_valid) if t_local_valid else None
    avg_t_total   = sum(t_total_valid) / len(t_total_valid) if t_total_valid else None

    ops_capacity_local = round(1000 / avg_t_local, 2) if avg_t_local else None
    ops_capacity_real  = round(1000 / avg_t_total,  2) if avg_t_total else None

    sync_overhead_pct = None
    if ops_capacity_local and ops_capacity_real:
        sync_overhead_pct = round(
            (ops_capacity_local - ops_capacity_real) / ops_capacity_local * 100, 1
        )

    # ── System throughput sostenible ──
    payload_vals = [e.get('payload_bytes') for e in history]
    avg_payload  = sum(v for v in payload_vals if v) / sum(1 for v in payload_vals if v) \
                   if any(payload_vals) else None
    system_throughput_bps = round(avg_payload * 8 / (avg_t_total / 1000), 2) \
                            if avg_payload and avg_t_total else None

    return jsonify({
        'ok':      True,
        'history': history,
        'stats': {
            'count':   len(history),
            # ── Latència per component ──
            't_local':   safe_stats(t_local),    # Original Mininet
            't_network': safe_stats(t_network),  # Round-trip HTTP (context)
            't_twin':    safe_stats(t_twin),      # Twin Mininet
            't_total':   safe_stats(t_total),     # Sistema complet end-to-end
            'jitter_ms': jitter_of(t_total),      # Estabilitat del sistema
            # ── Throughput del sistema ──
            'system_throughput_bps': system_throughput_bps,  # bits/s sostinguts Original+Twin
            'payload_bytes_avg':     round(avg_payload, 1) if avg_payload else None,
            # ── CPU durant operacions ──
            'cpu_at_sync': safe_stats(cpu_vals),
            # ── Capacitat ops/s ──
            'ops_per_sec': {
                'capacity_local':    ops_capacity_local,   # ops/s sense Twin (baseline)
                'capacity_real':     ops_capacity_real,    # ops/s reals Original+Twin
                'sync_overhead_pct': sync_overhead_pct,   # % cost de tenir el Twin
            },
        },
    })


@bp.route('/sync_metrics', methods=['POST'])
# Rep una entrada de mètriques de sync enviada per l'Original.
# El Twin la guarda al seu historial per mostrar-la al seu dashboard.
# L'Original envia una entrada completa per operació → sempre s'afegeix nova.
def update_sync_metrics():
    """
    Receive a sync timing entry pushed by the Original.
    The Original is the source of truth — the Twin mirrors its history exactly,
    always appending the entry it receives. The Original sends exactly one
    complete entry per operation, so there are no late/partial updates to merge.
    """
    data = request.json or {}
    entry = {
        'operation':      data.get('operation', 'External Update'),
        'latency_ms':     data.get('latency_ms'),
        't_local_ms':     data.get('t_local_ms'),
        't_network_ms':   data.get('t_network_ms'),
        't_twin_ms':      data.get('t_twin_ms'),
        'payload_bytes':  data.get('payload_bytes'),
        'throughput_bps': data.get('throughput_bps'),
        'cpu_percent':    data.get('cpu_percent'),
        'timestamp':      data.get('timestamp', time.time()),
    }
    with sync_history_lock:
        sync_latency_history.append(entry)
    return jsonify({'ok': True})


@bp.route('/metrics/hosts')
def metrics_hosts():
    hosts = [name for name, props in _xarxa.nodes.items() if props['type'] == 'host']
    return jsonify({'hosts': hosts})


@bp.route('/metrics/traffic')
# Retorna estadístiques de tràfic d'un node (rx/tx bytes i paquets per interfície).
# Llegeix directament de /proc/{pid}/net/dev sense obrir cap shell.
# Cada node Mininet té el seu propi network namespace → /proc/{pid}/net/dev
# mostra NOMÉS les interfícies d'aquell namespace.
def metrics_traffic():
    if not _xarxa.network_ready:
        return jsonify({'ok': False, 'error': 'Network not ready'})

    node = request.args.get('node')
    if not node or node not in _xarxa.nodes:
        return jsonify({'ok': False, 'error': 'Node not found'})
    if _xarxa.nodes[node]['type'] == 'switch':
        return jsonify({'ok': False, 'error': 'Switches not supported'})

    mn_node = _xarxa.mininet_nodes[node]
    pid = getattr(mn_node, 'pid', None)
    if not pid:
        return jsonify({'ok': False, 'error': 'Node PID unavailable'})
    try:
        with open(f'/proc/{pid}/net/dev', 'r') as fh:
            raw = fh.read()
    except OSError:
        return jsonify({'ok': False, 'error': 'Node namespace unreadable'})

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
        pid = getattr(mn_node, 'pid', None)
        if not pid:
            continue
        try:
            with open(f'/proc/{pid}/net/dev', 'r') as fh:
                raw = fh.read()
        except OSError:
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
# Retorna totes les IPs de la xarxa organitzades de dues formes:
#   flat:    llista plana ordenada per tipus (routers primer, hosts després)
#   subnets: agrupada per subxarxa (útil per veure quins nodes estan a cada segment)
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