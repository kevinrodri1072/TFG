"""
sync_latency_test.py — Digital Twin Sync Latency Study
======================================================
Progressively scales the network using a FIXED_SEQUENCE, 
recording individual sync latency measurements for every operation.

Metrics recorded per operation:
  - op_type        : 'add_host' or 'add_router'
  - n_nodes        : network size (hosts + routers) at time of operation
  - t_local_ms     : time to apply change in Original Mininet
  - t_network_ms   : HTTP round-trip Original → Twin
  - t_twin_ms      : time to apply change in Twin Mininet
  - t_total_ms     : max(t_local, t_network) — real end-to-end sync latency (parallel execution)
  - payload_bytes  : real size of the JSON sync payload sent to the Twin
  - throughput_bps : real transmission rate of the sync event link
  - cpu_percent    : original host CPU utilization at registration time

Generates:
  - CSV with all individual measurements (including original-twin sync metrics)
  - 9 plots (3x3 grid): end-to-end latency, latency breakdown (add_host,
    add_router), ops/s capacity, Twin sync overhead, sync payload size,
    system RAM, host CPU usage, jitter per operation type

Usage:
    sudo python3 sync_latency_test.py
"""

import requests
import time
import csv
import statistics
import psutil
from datetime import datetime
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# ── Configuration ──
ORIGINAL_URL = 'http://localhost:5000'
TIMESTAMP    = datetime.now().strftime('%Y%m%d_%H%M%S')
CSV_FILE     = f'sync_latency_{TIMESTAMP}.csv'
PLOT_FILE    = f'sync_latency_{TIMESTAMP}.png'
CHECKPOINTS  = [8, 16, 32, 64, 128]

# ── FIXED_SEQUENCE ──
FIXED_SEQUENCE = [
    {'type': 'host',   'name': 'h6',   'router': 'r1'},
    {'type': 'router', 'name': 'r3',   'connected_routers': ['r1']},
    {'type': 'host',   'name': 'h7',   'router': 'r2'},
    {'type': 'host',   'name': 'h8',   'router': 'r2'},
    {'type': 'router', 'name': 'r4',   'connected_routers': ['r2']},
    {'type': 'host',   'name': 'h9',   'router': 'r3'},
    {'type': 'host',   'name': 'h10',  'router': 'r3'},
    {'type': 'host',   'name': 'h11',  'router': 'r4'},
    {'type': 'host',   'name': 'h12',  'router': 'r4'},
    {'type': 'router', 'name': 'r5',   'connected_routers': ['r1', 'r3']},
    {'type': 'host',   'name': 'h13',  'router': 'r5'},
    {'type': 'host',   'name': 'h14',  'router': 'r5'},
    {'type': 'router', 'name': 'r6',   'connected_routers': ['r2', 'r4']},
    {'type': 'host',   'name': 'h15',  'router': 'r6'},
    {'type': 'host',   'name': 'h16',  'router': 'r6'},
    {'type': 'router', 'name': 'r7',   'connected_routers': ['r3']},
    {'type': 'host',   'name': 'h17',  'router': 'r7'},
    {'type': 'host',   'name': 'h18',  'router': 'r7'},
    {'type': 'router', 'name': 'r8',   'connected_routers': ['r4']},
    {'type': 'host',   'name': 'h19',  'router': 'r8'},
    {'type': 'host',   'name': 'h20',  'router': 'r8'},
    {'type': 'host',   'name': 'h21',  'router': 'r1'},
    {'type': 'host',   'name': 'h22',  'router': 'r2'},
    {'type': 'host',   'name': 'h23',  'router': 'r3'},
    {'type': 'host',   'name': 'h24',  'router': 'r4'},
    {'type': 'host',   'name': 'h25',  'router': 'r5'},
    {'type': 'host',   'name': 'h26',  'router': 'r6'},
    {'type': 'host',   'name': 'h27',  'router': 'r7'},
    {'type': 'host',   'name': 'h28',  'router': 'r8'},
    {'type': 'host',   'name': 'h29',  'router': 'r1'},
    {'type': 'host',   'name': 'h30',  'router': 'r2'},
    {'type': 'host',   'name': 'h31',  'router': 'r3'},
    {'type': 'router', 'name': 'r9',   'connected_routers': ['r5']},
    {'type': 'host',   'name': 'h32',  'router': 'r9'},
    {'type': 'host',   'name': 'h33',  'router': 'r9'},
    {'type': 'router', 'name': 'r10',  'connected_routers': ['r6']},
    {'type': 'host',   'name': 'h34',  'router': 'r10'},
    {'type': 'host',   'name': 'h35',  'router': 'r10'},
    {'type': 'router', 'name': 'r11',  'connected_routers': ['r7', 'r5']},
    {'type': 'host',   'name': 'h36',  'router': 'r11'},
    {'type': 'host',   'name': 'h37',  'router': 'r11'},
    {'type': 'router', 'name': 'r12',  'connected_routers': ['r8', 'r6']},
    {'type': 'host',   'name': 'h38',  'router': 'r12'},
    {'type': 'host',   'name': 'h39',  'router': 'r12'},
    {'type': 'host',   'name': 'h40',  'router': 'r4'},
    {'type': 'host',   'name': 'h41',  'router': 'r5'},
    {'type': 'host',   'name': 'h42',  'router': 'r6'},
    {'type': 'host',   'name': 'h43',  'router': 'r7'},
    {'type': 'host',   'name': 'h44',  'router': 'r8'},
    {'type': 'host',   'name': 'h45',  'router': 'r9'},
    {'type': 'host',   'name': 'h46',  'router': 'r10'},
    {'type': 'host',   'name': 'h47',  'router': 'r11'},
    {'type': 'host',   'name': 'h48',  'router': 'r12'},
    {'type': 'router', 'name': 'r13',  'connected_routers': ['r9']},
    {'type': 'host',   'name': 'h49',  'router': 'r13'},
    {'type': 'host',   'name': 'h50',  'router': 'r13'},
    {'type': 'router', 'name': 'r14',  'connected_routers': ['r10']},
    {'type': 'host',   'name': 'h51',  'router': 'r14'},
    {'type': 'host',   'name': 'h52',  'router': 'r14'},
    {'type': 'router', 'name': 'r15',  'connected_routers': ['r11']},
    {'type': 'host',   'name': 'h53',  'router': 'r15'},
    {'type': 'host',   'name': 'h54',  'router': 'r15'},
    {'type': 'router', 'name': 'r16',  'connected_routers': ['r12']},
    {'type': 'host',   'name': 'h55',  'router': 'r16'},
    {'type': 'host',   'name': 'h56',  'router': 'r16'},
    {'type': 'router', 'name': 'r17',  'connected_routers': ['r13']},
    {'type': 'host',   'name': 'h57',  'router': 'r17'},
    {'type': 'host',   'name': 'h58',  'router': 'r17'},
    {'type': 'router', 'name': 'r18',  'connected_routers': ['r14']},
    {'type': 'host',   'name': 'h59',  'router': 'r18'},
    {'type': 'host',   'name': 'h60',  'router': 'r18'},
    {'type': 'router', 'name': 'r19',  'connected_routers': ['r15', 'r13']},
    {'type': 'host',   'name': 'h61',  'router': 'r19'},
    {'type': 'host',   'name': 'h62',  'router': 'r19'},
    {'type': 'router', 'name': 'r20',  'connected_routers': ['r16', 'r14']},
    {'type': 'host',   'name': 'h63',  'router': 'r20'},
    {'type': 'host',   'name': 'h64',  'router': 'r20'},
    {'type': 'host',   'name': 'h65',  'router': 'r13'},
    {'type': 'host',   'name': 'h66',  'router': 'r14'},
    {'type': 'host',   'name': 'h67',  'router': 'r15'},
    {'type': 'host',   'name': 'h68',  'router': 'r16'},
    {'type': 'host',   'name': 'h69',  'router': 'r17'},
    {'type': 'host',   'name': 'h70',  'router': 'r18'},
    {'type': 'host',   'name': 'h71',  'router': 'r19'},
    {'type': 'host',   'name': 'h72',  'router': 'r20'},
    {'type': 'router', 'name': 'r21',  'connected_routers': ['r17']},
    {'type': 'host',   'name': 'h73',  'router': 'r21'},
    {'type': 'host',   'name': 'h74',  'router': 'r21'},
    {'type': 'router', 'name': 'r22',  'connected_routers': ['r18']},
    {'type': 'host',   'name': 'h75',  'router': 'r22'},
    {'type': 'host',   'name': 'h76',  'router': 'r22'},
    {'type': 'router', 'name': 'r23',  'connected_routers': ['r19']},
    {'type': 'host',   'name': 'h77',  'router': 'r23'},
    {'type': 'host',   'name': 'h78',  'router': 'r23'},
    {'type': 'router', 'name': 'r24',  'connected_routers': ['r20']},
    {'type': 'host',   'name': 'h79',  'router': 'r24'},
    {'type': 'host',   'name': 'h80',  'router': 'r24'},
    {'type': 'host',   'name': 'h81',  'router': 'r21'},
    {'type': 'host',   'name': 'h82',  'router': 'r22'},
    {'type': 'host',   'name': 'h83',  'router': 'r23'},
    {'type': 'host',   'name': 'h84',  'router': 'r24'},
    {'type': 'router', 'name': 'r25',  'connected_routers': ['r21', 'r23']},
    {'type': 'host',   'name': 'h85',  'router': 'r25'},
    {'type': 'host',   'name': 'h86',  'router': 'r25'},
    {'type': 'router', 'name': 'r26',  'connected_routers': ['r22', 'r24']},
    {'type': 'host',   'name': 'h87',  'router': 'r26'},
    {'type': 'host',   'name': 'h88',  'router': 'r26'},
    {'type': 'host',   'name': 'h89',  'router': 'r25'},
    {'type': 'host',   'name': 'h90',  'router': 'r26'},
    {'type': 'router', 'name': 'r27',  'connected_routers': ['r25']},
    {'type': 'host',   'name': 'h91',  'router': 'r27'},
    {'type': 'host',   'name': 'h92',  'router': 'r27'},
    {'type': 'router', 'name': 'r28',  'connected_routers': ['r26']},
    {'type': 'host',   'name': 'h93',  'router': 'r28'},
    {'type': 'host',   'name': 'h94',  'router': 'r28'},
    {'type': 'host',   'name': 'h95',  'router': 'r27'},
    {'type': 'host',   'name': 'h96',  'router': 'r28'},
    {'type': 'router', 'name': 'r29',  'connected_routers': ['r27']},
    {'type': 'host',   'name': 'h97',  'router': 'r29'},
    {'type': 'host',   'name': 'h98',  'router': 'r29'},
    {'type': 'router', 'name': 'r30',  'connected_routers': ['r28']},
    {'type': 'host',   'name': 'h99',  'router': 'r30'},
    {'type': 'host',   'name': 'h100', 'router': 'r30'},
    {'type': 'host',   'name': 'h101', 'router': 'r29'},
    {'type': 'host',   'name': 'h102', 'router': 'r30'},
    {'type': 'router', 'name': 'r31',  'connected_routers': ['r29', 'r27']},
    {'type': 'host',   'name': 'h103', 'router': 'r31'},
    {'type': 'host',   'name': 'h104', 'router': 'r31'},
    {'type': 'router', 'name': 'r32',  'connected_routers': ['r30', 'r28']},
    {'type': 'host',   'name': 'h105', 'router': 'r32'},
]

# ── Helpers ──────────────────────────────────────────────────────────────────

def wait_for_network():
    for _ in range(30):
        try:
            if requests.get(f'{ORIGINAL_URL}/topology', timeout=5).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False

def count_nodes():
    r = requests.get(f'{ORIGINAL_URL}/topology', timeout=10).json()
    return sum(1 for n, p in r['nodes'].items() if p['type'] != 'switch')

def get_routing_mode():
    try:
        r = requests.get(f'{ORIGINAL_URL}/get_routing_mode', timeout=5)
        if r.status_code == 200:
            data = r.json()
            if data.get('ok'):
                return str(data.get('mode')).lower()
    except Exception as e:
        print(f'  [!] Error al consultar el modo de enrutamiento: {e}')
    return 'manual'

def safe_stats(values):
    values = [v for v in values if v is not None]
    if not values:
        return None, None, None
    return round(min(values), 2), round(statistics.mean(values), 2), round(max(values), 2)


def get_system_memory_mb():
    """
    RAM del sistema en ús en MB (virtual_memory().used).
    Captura tant la memòria del kernel (namespaces de xarxa, veth, OVS)
    com la del procés Python — és la mètrica correcta per mesurar el cost
    real de cada operació Mininet, que viu majoritàriament al kernel.
    """
    return round(psutil.virtual_memory().used / 1024 / 1024, 1)


def get_frr_memory_mb():
    """
    Suma del RSS de tots els daemons FRR actius (zebra, ospfd, ldpd) en MB.
    En mode manual retorna 0.0 perquè no hi ha daemons en execució.
    """
    total = 0
    count = 0
    for proc in psutil.process_iter(['name', 'memory_info']):
        try:
            if proc.info['name'] in ('zebra', 'ospfd', 'ldpd', 'bgpd'):
                total += proc.info['memory_info'].rss
                count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return round(total / 1024 / 1024, 1) if count > 0 else 0.0

def do_operation(op):
    # Snapshot de memòria del sistema ABANS de l'operació
    mem_before = get_system_memory_mb()

    try:
        before = requests.get(f'{ORIGINAL_URL}/metrics/sync', timeout=5).json()
        before_count = len(before.get('history', []))
    except Exception as e:
        return {'error': f'metrics/sync unreachable: {e}'}

    try:
        if op['type'] == 'router':
            r = requests.post(f'{ORIGINAL_URL}/add_router',
                              json={'name': op['name'],
                                    'connected_routers': op['connected_routers']},
                              timeout=30)
        else:
            r = requests.post(f'{ORIGINAL_URL}/add_host',
                              json={'name': op['name'], 'router': op['router']},
                              timeout=30)
    except requests.exceptions.Timeout:
        return {'error': 'Request timed out (>30s)'}
    except Exception as e:
        return {'error': f'Request failed: {e}'}

    resp = r.json()
    if not resp.get('ok'):
        return {'error': resp.get('error', 'Server returned ok=False')}

    for attempt in range(20):
        time.sleep(0.5)
        try:
            after = requests.get(f'{ORIGINAL_URL}/metrics/sync', timeout=5).json()
            history = after.get('history', [])
            if len(history) > before_count:
                entry = history[-1]
                # RAM del sistema DESPRÉS — valor absolut (no delta)
                # Guardem el valor absolut per construir la corba acumulada
                mem_after = get_system_memory_mb()
                frr_total = get_frr_memory_mb()
                entry['_system_mem_mb'] = mem_after
                entry['_frr_total_mb']  = frr_total
                return entry
        except Exception:
            pass
    return {'error': 'Sync entry did not appear within 10s (t_local may be pending)'}

# ── Plotting ─────────────────────────────────────────────────────────────────

def generate_plots(rows, routing_mode='unknown'):
    """
    9 plots in a 3x3 grid, ordered as a coherent narrative:
      Row 1 — TIME:      G1 end-to-end latency · G2 breakdown add_host · G3 breakdown add_router
      Row 2 — CAPACITY:  G4 ops/s capacity     · G5 Twin sync overhead · G6 sync payload size
      Row 3 — RESOURCES: G7 system RAM         · G8 CPU usage          · G9 jitter per op type
    """
    fig, axes = plt.subplots(3, 3, figsize=(22, 16))
    mode_label = routing_mode.upper().replace('_', '+')
    fig.suptitle(
        f'Digital Twin Network — System Capacity & Sync Latency Study\n'
        f'Routing mode: {mode_label}  ·  How much can the Original+Twin system handle?',
        fontsize=15, fontweight='bold'
    )

    hosts_data   = sorted([r for r in rows if r['op_type'] == 'host'],   key=lambda x: x['n_nodes'])
    routers_data = sorted([r for r in rows if r['op_type'] == 'router'], key=lambda x: x['n_nodes'])
    has_twin     = any(r['t_twin_ms'] is not None for r in hosts_data + routers_data)

    # ── G1: End-to-end sync latency ─────────────────────────────────────────
    ax = axes[0][0]
    if hosts_data:
        ax.plot([r['n_nodes'] for r in hosts_data],
                [r['t_total_ms'] for r in hosts_data],
                'o-', color='#27ae60', linewidth=2, markersize=4, label='add_host')
    if routers_data:
        ax.plot([r['n_nodes'] for r in routers_data],
                [r['t_total_ms'] for r in routers_data],
                's-', color='#e74c3c', linewidth=2, markersize=4, label='add_router')
    ax.set_title('G1 — End-to-End Sync Latency vs Network Size',
                 fontweight='bold', fontsize=12)
    ax.set_xlabel('Network size (total nodes)')
    ax.set_ylabel('t_total (ms)  [max(t_local, t_network)]')
    ax.legend(loc='upper left')
    ax.grid(True, alpha=0.3, linestyle='--')

    # ── Helper: latency breakdown as overlapping areas ──────────────────────
    # t_local and t_network run IN PARALLEL (not stacked), so the two areas
    # overlap and the upper envelope of the two equals t_total.
    def breakdown_areas(ax, data, op_label, color_title):
        if not data:
            ax.text(0.5, 0.5, f'No {op_label} data', ha='center', va='center',
                    transform=ax.transAxes, color='grey')
        else:
            # Filter per series: fill_between cannot handle None values
            loc = [(r['n_nodes'], r['t_local_ms'])   for r in data if r['t_local_ms']   is not None]
            net = [(r['n_nodes'], r['t_network_ms']) for r in data if r['t_network_ms'] is not None]
            twn = [(r['n_nodes'], r['t_twin_ms'])    for r in data if r['t_twin_ms']    is not None]
            if loc:
                x, y = zip(*loc)
                ax.fill_between(x, 0, y, color='#3498db', alpha=0.35, zorder=2)
                ax.plot(x, y, '-', color='#3498db', linewidth=1.8, zorder=4,
                        label='t_local (Original Mininet)')
            if net:
                x, y = zip(*net)
                ax.fill_between(x, 0, y, color='#e67e22', alpha=0.35, zorder=3)
                ax.plot(x, y, '-', color='#e67e22', linewidth=1.8, zorder=5,
                        label='t_network (HTTP round-trip)')
            if twn:
                x, y = zip(*twn)
                ax.plot(x, y, ':', color='#27ae60', linewidth=2, zorder=6,
                        label='t_twin (Twin Mininet)')
            if loc and net:
                ax.text(0.98, 0.04, 'Areas overlap (parallel execution) — upper envelope = t_total',
                        ha='right', va='bottom', transform=ax.transAxes,
                        fontsize=8.5, color='#5F5E5A', style='italic')
        if not has_twin:
            ax.text(0.5, 0.04, f't_twin not available (mode: {routing_mode})',
                    ha='center', transform=ax.transAxes,
                    fontsize=9, color='grey', style='italic')
        ax.set_title(f'{op_label} — Latency Breakdown by Component',
                     fontweight='bold', fontsize=12, color=color_title)
        ax.set_xlabel('Network size (total nodes)')
        ax.set_ylabel('Time (ms)')
        ax.legend(loc='upper left', fontsize=9)
        ax.grid(True, alpha=0.3, linestyle='--')

    # ── G2: Breakdown — add_host ────────────────────────────────────────────
    breakdown_areas(axes[0][1], hosts_data,   'G2 — add_host',   '#27ae60')

    # ── G3: Breakdown — add_router ──────────────────────────────────────────
    breakdown_areas(axes[0][2], routers_data, 'G3 — add_router', '#e74c3c')

    # ── G4: System capacity (ops/s) ─────────────────────────────────────────
    ax = axes[1][0]
    h_cap = [r for r in hosts_data   if r.get('capacity_ops_s') is not None]
    r_cap = [r for r in routers_data if r.get('capacity_ops_s') is not None]
    if h_cap:
        ax.plot([r['n_nodes'] for r in h_cap],
                [r['capacity_ops_s'] for r in h_cap],
                'o-', color='#27ae60', linewidth=2, markersize=4, label='add_host')
    if r_cap:
        ax.plot([r['n_nodes'] for r in r_cap],
                [r['capacity_ops_s'] for r in r_cap],
                's-', color='#e74c3c', linewidth=2, markersize=4, label='add_router')
    ax.set_title('G4 — System Capacity (ops/s) vs Network Size',
                 fontweight='bold', fontsize=12)
    ax.set_xlabel('Network size (total nodes)')
    ax.set_ylabel('Capacity (ops/s)  [= 1000 / t_total]')
    ax.legend(loc='upper right')
    ax.grid(True, alpha=0.3, linestyle='--')

    # ── G5: Twin sync overhead — visual rings ───────────────────────────────
    ax = axes[1][1]
    ax.set_aspect('equal')
    ax.axis('off')
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 7.5)

    from matplotlib.patches import Wedge

    def compute_ring_stats(data_list):
        valid = [r for r in data_list
                 if r.get('t_local_ms') and r.get('t_total_ms')
                 and r['t_total_ms'] > 0]
        if not valid:
            return None
        avg_local = sum(r['t_local_ms'] for r in valid) / len(valid)
        avg_total = sum(r['t_total_ms'] for r in valid) / len(valid)
        pct = round(max(0.0, (1 - avg_local / avg_total) * 100), 1)
        ops_real  = round(1000 / avg_total, 1)
        ops_local = round(1000 / avg_local, 1)
        return pct, ops_real, ops_local

    def draw_ring(ax, cx, cy, pct, col_fg, col_bg, label, ops_real, ops_local):
        R, w = 1.35, 0.38
        # Background full ring
        ax.add_patch(Wedge((cx, cy), R, 0, 360, width=w,
                           facecolor=col_bg, zorder=2))
        # Foreground arc — clockwise from top
        angle = pct / 100 * 360
        ax.add_patch(Wedge((cx, cy), R, 90 - angle, 90, width=w,
                           facecolor=col_fg, zorder=3))
        # Percentage
        ax.text(cx, cy + 0.18, f'{pct:.1f}', ha='center', va='center',
                fontsize=24, fontweight='bold', color=col_fg, zorder=4)
        ax.text(cx, cy - 0.32, '%', ha='center', va='center',
                fontsize=14, color=col_fg, zorder=4)
        # Label above
        ax.text(cx, cy + R + 0.28, label, ha='center', va='bottom',
                fontsize=11, fontweight='bold',
                color='#2C2C2A' if routing_mode != 'unknown' else '#444')
        # Stats below
        ax.text(cx, cy - R - 0.12, f'{ops_real} ops/s  with Twin',
                ha='center', va='top', fontsize=9, color='#5F5E5A')
        ax.text(cx, cy - R - 0.42, f'{ops_local} ops/s  without Twin',
                ha='center', va='top', fontsize=9, color='#B4B2A9')

    h_stats = compute_ring_stats(hosts_data)
    r_stats = compute_ring_stats(routers_data)

    if h_stats:
        draw_ring(ax, 2.5, 4.2, h_stats[0],
                  '#1D9E75', '#E1F5EE', 'add_host',
                  h_stats[1], h_stats[2])
    if r_stats:
        draw_ring(ax, 7.5, 4.2, r_stats[0],
                  '#1D9E75', '#E1F5EE', 'add_router',
                  r_stats[1], r_stats[2])

    # Summary message
    all_pcts = [s[0] for s in [h_stats, r_stats] if s]
    max_pct  = max(all_pcts) if all_pcts else 0
    ax.text(5, 1.15,
            'The Digital Twin synchronized in real time',
            ha='center', va='center', fontsize=11,
            color='#5F5E5A')
    ax.text(5, 0.45,
            f'costs less than {int(max_pct) + 1}% of system capacity',
            ha='center', va='center', fontsize=14,
            fontweight='bold', color='#1D9E75')

    ax.set_title('G5 — Twin Sync Overhead (cost of a live Digital Twin)',
                 fontweight='bold', fontsize=12, pad=10)

    # ── G6: Sync payload size ───────────────────────────────────────────────
    ax = axes[1][2]
    h_pay = [r for r in hosts_data   if r.get('payload_bytes') is not None]
    r_pay = [r for r in routers_data if r.get('payload_bytes') is not None]
    if h_pay:
        ax.plot([r['n_nodes'] for r in h_pay],
                [r['payload_bytes'] for r in h_pay],
                'o-', color='#27ae60', linewidth=2, markersize=4, label='add_host')
    if r_pay:
        ax.plot([r['n_nodes'] for r in r_pay],
                [r['payload_bytes'] for r in r_pay],
                's-', color='#e74c3c', linewidth=2, markersize=4, label='add_router')
    if not h_pay and not r_pay:
        ax.text(0.5, 0.5, 'No payload data', ha='center', va='center',
                transform=ax.transAxes, color='grey')
    ax.set_title('G6 — Sync Payload Size vs Network Size',
                 fontweight='bold', fontsize=12)
    ax.set_xlabel('Network size (total nodes)')
    ax.set_ylabel('Payload size (bytes per sync event)')
    ax.legend(loc='upper left')
    ax.grid(True, alpha=0.3, linestyle='--')

    # ── G7: System RAM usage ────────────────────────────────────────────────
    # Absolute value of virtual_memory().used after each operation.
    # Captures both kernel memory (namespaces, veth, OVS) and processes
    # (Python, FRR) as the network scales.
    ax = axes[2][0]
    all_mem = sorted([r for r in rows if r.get('system_mem_mb') is not None],
                     key=lambda x: x['n_nodes'])
    if all_mem:
        mem_vals = [r['system_mem_mb'] for r in all_mem]
        ax.plot([r['n_nodes'] for r in all_mem], mem_vals,
                'o-', color='#2980b9', linewidth=1.5, markersize=4,
                label='System RAM used')
        # Reference: total hardware RAM (~3700 MB on the lab PCs)
        ax.axhline(y=3700, color='#c0392b', linestyle='-', alpha=0.6,
                   label='Total RAM (~3700 MB)')
        growth = round(mem_vals[-1] - mem_vals[0], 0)
        ax.annotate(f'Growth: +{growth:.0f} MB\n({mem_vals[0]:.0f}→{mem_vals[-1]:.0f} MB)',
                    xy=(all_mem[-1]['n_nodes'], mem_vals[-1]),
                    xytext=(-80, -40), textcoords='offset points',
                    fontsize=9, color='#2980b9',
                    arrowprops=dict(arrowstyle='->', color='#2980b9', lw=1.2))
        ax.set_ylim(min(mem_vals) * 0.95, 3800)
        ax.legend(loc='upper left', fontsize=8)
    else:
        ax.text(0.5, 0.5, 'No memory data', ha='center', va='center',
                transform=ax.transAxes, color='grey')
    ax.set_title('G7 — System RAM Usage vs Network Size',
                 fontweight='bold', fontsize=12)
    ax.set_xlabel('Network size (total nodes)')
    ax.set_ylabel('RAM used (MB, system-wide)')
    ax.grid(True, alpha=0.3, linestyle='--')

    # ── G8: CPU usage at sync time ──────────────────────────────────────────
    # cpu_percent is host-wide (psutil), measured when each operation is
    # registered — plotted as a single series across all operation types.
    ax = axes[2][1]
    all_cpu = sorted([r for r in rows if r.get('cpu_percent') is not None],
                     key=lambda x: x['n_nodes'])
    if all_cpu:
        cpu_x = [r['n_nodes'] for r in all_cpu]
        cpu_y = [r['cpu_percent'] for r in all_cpu]
        ax.plot(cpu_x, cpu_y, 'o-', color='#8e44ad', linewidth=1.5,
                markersize=4, label='CPU at sync registration')
        cpu_avg = sum(cpu_y) / len(cpu_y)
        ax.axhline(y=cpu_avg, color='#8e44ad', linestyle='--', alpha=0.6,
                   label=f'Average ({cpu_avg:.1f}%)')
        ax.set_ylim(0, max(100, max(cpu_y) * 1.1))
        ax.legend(loc='upper left', fontsize=9)
    else:
        ax.text(0.5, 0.5, 'No CPU data', ha='center', va='center',
                transform=ax.transAxes, color='grey')
    ax.set_title('G8 — Host CPU Usage vs Network Size',
                 fontweight='bold', fontsize=12)
    ax.set_xlabel('Network size (total nodes)')
    ax.set_ylabel('CPU usage (%)')
    ax.grid(True, alpha=0.3, linestyle='--')

    # ── G9: Jitter per operation type ───────────────────────────────────────
    # Jitter = |Δt_total| between consecutive operations of the SAME type.
    # Computed per type to avoid the artificial inflation caused by mixing
    # fast add_host and slow add_router operations in one series.
    ax = axes[2][2]

    def jitter_series(data):
        vals = [(r['n_nodes'], r['t_total_ms']) for r in data
                if r.get('t_total_ms') is not None]
        if len(vals) < 2:
            return [], [], None
        xs = [vals[i][0] for i in range(1, len(vals))]
        ys = [round(abs(vals[i][1] - vals[i - 1][1]), 2) for i in range(1, len(vals))]
        avg = round(sum(ys) / len(ys), 2)
        return xs, ys, avg

    h_jx, h_jy, h_javg = jitter_series(hosts_data)
    r_jx, r_jy, r_javg = jitter_series(routers_data)
    if h_jy:
        ax.plot(h_jx, h_jy, 'o-', color='#27ae60', linewidth=1.5, markersize=4,
                label=f'add_host (avg {h_javg} ms)')
    if r_jy:
        ax.plot(r_jx, r_jy, 's-', color='#e74c3c', linewidth=1.5, markersize=4,
                label=f'add_router (avg {r_javg} ms)')
    if not h_jy and not r_jy:
        ax.text(0.5, 0.5, 'Not enough data for jitter', ha='center', va='center',
                transform=ax.transAxes, color='grey')
    ax.set_title('G9 — Sync Jitter per Operation Type',
                 fontweight='bold', fontsize=12)
    ax.set_xlabel('Network size (total nodes)')
    ax.set_ylabel('Jitter (ms)  [|Δt_total| between consecutive ops]')
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(True, alpha=0.3, linestyle='--')

    plt.tight_layout()
    plt.subplots_adjust(top=0.93)
    plt.savefig(PLOT_FILE, dpi=150, bbox_inches='tight')
    print(f' Plot saved to {PLOT_FILE}')

    # ── Export each panel as an individual figure ────────────────────────────
    # Naming: replace .png suffix with _G1.png, _G2.png, … _G9.png
    import copy
    import matplotlib.collections as mcoll
    from matplotlib.patches import Wedge

    base = PLOT_FILE[:-4] if PLOT_FILE.endswith('.png') else PLOT_FILE
    panel_titles = ['G1', 'G2', 'G3', 'G4', 'G5', 'G6', 'G7', 'G8', 'G9']

    for idx, (panel_ax, panel_name) in enumerate(zip(axes.flat, panel_titles)):

        # ── G5: re-render from scratch (Wedge patches can't be copied) ───────
        if panel_name == 'G5':
            fig_single, ax_single = plt.subplots(figsize=(8, 6))
            ax_single.set_aspect('equal')
            ax_single.axis('off')
            ax_single.set_xlim(0, 10)
            ax_single.set_ylim(0, 7.5)

            h_stats = compute_ring_stats(hosts_data)
            r_stats = compute_ring_stats(routers_data)

            if h_stats:
                draw_ring(ax_single, 2.5, 4.2, h_stats[0],
                          '#1D9E75', '#E1F5EE', 'add_host',
                          h_stats[1], h_stats[2])
            if r_stats:
                draw_ring(ax_single, 7.5, 4.2, r_stats[0],
                          '#1D9E75', '#E1F5EE', 'add_router',
                          r_stats[1], r_stats[2])

            all_pcts = [s[0] for s in [h_stats, r_stats] if s]
            max_pct  = max(all_pcts) if all_pcts else 0
            ax_single.text(5, 1.15,
                           'The Digital Twin synchronized in real time',
                           ha='center', va='center', fontsize=11, color='#5F5E5A')
            ax_single.text(5, 0.45,
                           f'costs less than {int(max_pct) + 1}% of system capacity',
                           ha='center', va='center', fontsize=14,
                           fontweight='bold', color='#1D9E75')
            ax_single.set_title('G5 — Twin Sync Overhead (cost of a live Digital Twin)',
                                 fontweight='bold', fontsize=11, pad=10)
            out_path = f'{base}_{panel_name}.png'
            fig_single.tight_layout()
            fig_single.savefig(out_path, dpi=150, bbox_inches='tight')
            plt.close(fig_single)
            print(f' Panel saved to {out_path}')
            continue

        # ── All other panels: copy lines, fills and texts ─────────────────────
        fig_single, ax_single = plt.subplots(figsize=(8, 5))
        for line in panel_ax.get_lines():
            ax_single.plot(
                line.get_xdata(), line.get_ydata(),
                color=line.get_color(), linewidth=line.get_linewidth(),
                linestyle=line.get_linestyle(), marker=line.get_marker(),
                markersize=line.get_markersize(), label=line.get_label(),
                zorder=line.get_zorder(),
            )
        for coll in panel_ax.collections:
            if isinstance(coll, mcoll.PolyCollection):
                for path in coll.get_paths():
                    verts = path.vertices
                    ax_single.fill(verts[:, 0], verts[:, 1],
                                   color=coll.get_facecolor()[0],
                                   alpha=coll.get_alpha() or 0.35,
                                   zorder=coll.get_zorder())
        for txt in panel_ax.texts:
            ax_single.text(
                txt.get_position()[0], txt.get_position()[1],
                txt.get_text(),
                transform=ax_single.transAxes
                    if txt.get_transform() == panel_ax.transAxes
                    else ax_single.transData,
                fontsize=txt.get_fontsize(), color=txt.get_color(),
                ha=txt.get_ha(), va=txt.get_va(), style=txt.get_style(),
            )
        ax_single.set_title(panel_ax.get_title(), fontweight='bold', fontsize=11)
        ax_single.set_xlabel(panel_ax.get_xlabel())
        ax_single.set_ylabel(panel_ax.get_ylabel())
        ax_single.set_xlim(panel_ax.get_xlim())
        ax_single.set_ylim(panel_ax.get_ylim())
        ax_single.grid(True, alpha=0.3, linestyle='--')
        if panel_ax.get_legend() is not None:
            handles, labels = panel_ax.get_legend_handles_labels()
            if handles:
                ax_single.legend(handles=handles, labels=labels,
                                 loc=panel_ax.get_legend()._loc, fontsize=9)
        out_path = f'{base}_{panel_name}.png'
        fig_single.tight_layout()
        fig_single.savefig(out_path, dpi=150, bbox_inches='tight')
        plt.close(fig_single)
        print(f' Panel saved to {out_path}')




# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print('=' * 60)
    print('  Digital Twin Network — Sync Latency Study')
    print(f'  Checkpoints: {CHECKPOINTS}')
    print(f'  CSV:  {CSV_FILE}')
    print(f'  Plot: {PLOT_FILE}')
    print('=' * 60)

    print('\n Waiting for network...')
    if not wait_for_network():
        print(' Network not available.')
        return
    print(' Network ready!')

    # --- NUEVA DETECCIÓN AUTOMÁTICA ---
    ROUTING_MODE = get_routing_mode()
    print(f' [I] Modo de enrutamiento detectado: {ROUTING_MODE.upper()}')
    # ----------------------------------

    current = count_nodes()
    print(f'\n Initial nodes: {current}')
    if current != 7:
        print(f'  Expected 7 nodes (h1-h5, r1, r2) but found {current}.')
        print('   Reset the network and retry.')
        return

    rows = []
    failures = 0
    checkpoints_iter = iter(CHECKPOINTS)
    next_checkpoint  = next(checkpoints_iter)
    print(f' First checkpoint: {next_checkpoint} nodes\n')

    MAX_NODES = 128

    for op in FIXED_SEQUENCE:
        if count_nodes() >= MAX_NODES:
            print(f'\n   Reached {MAX_NODES} nodes — stopping sequence.')
            break

        name = op['name']
        if op['type'] == 'router':
            print(f'   add_router {name} -> {op["connected_routers"]}...', end='', flush=True)
        else:
            print(f'   add_host   {name} -> {op["router"]}...', end='', flush=True)

        entry = do_operation(op)

        if 'error' in entry:
            failures += 1
            print(f'  {entry["error"]}')
            current = count_nodes()
            rows.append({
                'op_type':        op['type'],
                'op_name':        name,
                'routing_mode':   ROUTING_MODE,
                'n_nodes':        current,
                't_local_ms':     None,
                't_network_ms':   None,
                't_twin_ms':      None,
                't_total_ms':     None,
                'capacity_ops_s': None,
                'payload_bytes':  None,
                'throughput_bps': None,
                'cpu_percent':    None,
                'system_mem_mb':  None,
                'frr_total_mb':   None,
                'error':          entry['error'],
            })
            time.sleep(0.5)
            current = count_nodes()
            while next_checkpoint is not None and current >= next_checkpoint:
                print(f'\n   Checkpoint {next_checkpoint} reached ({failures} failures so far)')
                try:
                    next_checkpoint = next(checkpoints_iter)
                    print(f'   Next checkpoint: {next_checkpoint} nodes\n')
                except StopIteration:
                    next_checkpoint = None
            continue

        t_local   = entry.get('t_local_ms')
        t_network = entry.get('t_network_ms')
        t_twin    = entry.get('t_twin_ms')
        t_total   = round(max(t_local, t_network), 2) if t_local and t_network else (
                    round(t_network, 2) if t_network else None)

        current = count_nodes()
        row = {
            'op_type':        op['type'],
            'op_name':        name,
            'routing_mode':   ROUTING_MODE,
            'n_nodes':        current,
            't_local_ms':     t_local,
            't_network_ms':   t_network,
            't_twin_ms':      t_twin,
            't_total_ms':     t_total,
            'capacity_ops_s': round(1000 / t_total, 2) if t_total and t_total > 0 else None,
            'payload_bytes':  entry.get('payload_bytes'),
            'throughput_bps': entry.get('throughput_bps'),
            'cpu_percent':    entry.get('cpu_percent'),
            'system_mem_mb':  entry.get('_system_mem_mb'),
            'frr_total_mb':   entry.get('_frr_total_mb'),
            'error':          '',
        }
        rows.append(row)

        cap = row['capacity_ops_s']
        mem = row['system_mem_mb']
        frr = row['frr_total_mb']
        print(f'   total={t_total}ms  local={t_local}ms  net={t_network}ms  twin={t_twin}ms  '
              f'cap={cap}ops/s  sysram={mem}MB  frr={frr}MB')

        if op['type'] == 'router':
            # 1. Modo Manual
            if ROUTING_MODE == 'manual':
                print('      [MANUAL] Configuración estática instantánea. Saltando espera larga.')
                time.sleep(0.5)
            
            # 2. Modo OSPF estándar
            elif ROUTING_MODE == 'ospf':
                print('      [OSPF] Esperando 5s para la convergencia de rutas...')
                time.sleep(5)
            
            # 3. Modo OSPF con BFD
            elif ROUTING_MODE == 'ospf_bfd':
                print('      [OSPF_BFD] Esperando 5s para la convergencia (BFD activo)...')
                time.sleep(5)
            
            # 4. Modo OSPF con MPLS (nombrado 'mpls' en tu backend)
            elif ROUTING_MODE == 'mpls':
                print('      [MPLS] Esperando 6s para OSPF y distribución de etiquetas LDP...')
                time.sleep(6)
            
            # 5. Modo OSPF con MPLS y BFD (nombrado 'mpls_bfd' en tu backend)
            elif ROUTING_MODE == 'mpls_bfd':
                print('      [MPLS_BFD] Esperando 6s para convergencia completa, LDP y sesiones BFD...')
                time.sleep(6)
            
            # Caso de seguridad por si acaso
            else:
                print(f'      [{ROUTING_MODE.upper()}] Modo desconocido. Aplicando espera preventiva de 5s...')
                time.sleep(5)
        else:
            time.sleep(0.5)

        if next_checkpoint is not None and current >= next_checkpoint:
            print(f'\n   Checkpoint {next_checkpoint} reached ({failures} failures so far)')
            try:
                next_checkpoint = next(checkpoints_iter)
                print(f'   Next checkpoint: {next_checkpoint} nodes\n')
            except StopIteration:
                next_checkpoint = None
                break

    if rows:
        with open(CSV_FILE, 'w', newline='') as f:
            # DictWriter infiere las columnas dinamicamente a partir de las llaves de la fila 0
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print(f'\n CSV saved to {CSV_FILE}')

        valid_rows = [r for r in rows if r['t_total_ms'] is not None]
        if valid_rows:
            generate_plots(valid_rows, ROUTING_MODE)
        else:
            print('  No valid rows to plot.')

    total_ops = len(rows)
    ok_ops    = sum(1 for r in rows if not r.get('error'))
    print(f'\n Sync latency study complete!')
    print(f'   {ok_ops}/{total_ops} operations succeeded  |  {failures} failures')

if __name__ == '__main__':
    main()