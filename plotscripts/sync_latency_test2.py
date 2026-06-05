"""
sync_latency_test.py — Digital Twin Sync Latency Study
======================================================
Progressively scales the network using the same FIXED_SEQUENCE
as scalability_test.py, recording individual sync latency
measurements for every operation.

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
  - 8 plots: latency, ops/s capacity, throughput, component breakdown,
             payload size, CPU overhead, memory delta, FRR memory

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

# ── FIXED_SEQUENCE (same as scalability_test.py) ──
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
    fig, axes = plt.subplots(4, 2, figsize=(16, 22))
    mode_label = routing_mode.upper().replace('_', '+')
    fig.suptitle(
        f'Digital Twin Network — System Capacity & Sync Latency Study\n'
        f'Routing mode: {mode_label}  ·  How much can the Original+Twin system handle?',
        fontsize=14, fontweight='bold'
    )

    hosts_data   = sorted([r for r in rows if r['op_type'] == 'host'],   key=lambda x: x['n_nodes'])
    routers_data = sorted([r for r in rows if r['op_type'] == 'router'], key=lambda x: x['n_nodes'])
    has_twin     = any(r['t_twin_ms'] is not None for r in hosts_data + routers_data)

    # ── G1: Latència total ──────────────────────────────────────────────────
    ax = axes[0][0]
    if hosts_data:
        ax.plot([r['n_nodes'] for r in hosts_data],
                [r['t_total_ms'] for r in hosts_data],
                'o-', color='#27ae60', linewidth=2, label='add_host')
    if routers_data:
        ax.plot([r['n_nodes'] for r in routers_data],
                [r['t_total_ms'] for r in routers_data],
                's-', color='#e74c3c', linewidth=2, label='add_router')
    ax.set_title('End-to-End Sync Latency vs Network Size', fontweight='bold', fontsize=12)
    ax.set_xlabel('Network size (total nodes)')
    ax.set_ylabel('t_total  ms  [max(t_local, t_network)]')
    ax.legend(loc='upper left')
    ax.grid(True, alpha=0.3, linestyle='--')

    # ── G2: ops/s ───────────────────────────────────────────────────────────
    ax = axes[0][1]
    h_cap = [r for r in hosts_data   if r.get('capacity_ops_s') is not None]
    r_cap = [r for r in routers_data if r.get('capacity_ops_s') is not None]
    if h_cap:
        ax.plot([r['n_nodes'] for r in h_cap],
                [r['capacity_ops_s'] for r in h_cap],
                'o-', color='#27ae60', linewidth=2, label='add_host')
    if r_cap:
        ax.plot([r['n_nodes'] for r in r_cap],
                [r['capacity_ops_s'] for r in r_cap],
                's-', color='#e74c3c', linewidth=2, label='add_router')
    ax.set_title('System Capacity (ops/s) vs Network Size', fontweight='bold', fontsize=12)
    ax.set_xlabel('Network size (total nodes)')
    ax.set_ylabel('ops/s  [= 1000 / t_total]')
    ax.legend(loc='upper right')
    ax.grid(True, alpha=0.3, linestyle='--')

    # ── G3: Throughput ──────────────────────────────────────────────────────
    ax = axes[1][0]
    h_thr = [r for r in hosts_data   if r.get('throughput_bps') is not None]
    r_thr = [r for r in routers_data if r.get('throughput_bps') is not None]
    if h_thr:
        ax.plot([r['n_nodes'] for r in h_thr],
                [r['throughput_bps'] / 1000 for r in h_thr],
                'o-', color='#27ae60', linewidth=2, label='add_host')
    if r_thr:
        ax.plot([r['n_nodes'] for r in r_thr],
                [r['throughput_bps'] / 1000 for r in r_thr],
                's-', color='#e74c3c', linewidth=2, label='add_router')
    ax.set_title('System Throughput (Kbps) vs Network Size', fontweight='bold', fontsize=12)
    ax.set_xlabel('Network size (total nodes)')
    ax.set_ylabel('Kbps  [= payload×8 / t_total]')
    ax.legend(loc='upper left')
    ax.grid(True, alpha=0.3, linestyle='--')

    # ── G4: Twin overhead — barres horitzontals apilades ────────────────────
    # Cada barra = t_total desglossat en: treball local (verd) + overhead Twin (roig)
    # Overhead = temps que el sistema espera el Twin després que l'Original ha acabat
    # 0% = l'Original és el coll d'ampolla (Twin acaba abans, overhead absorbit)
    # >0% = la xarxa+Twin és el coll d'ampolla (el sistema espera el ACK)
    ax = axes[1][1]

    bar_labels   = []
    local_vals   = []
    overhead_vals= []
    pct_vals     = []
    bar_colors_l = []
    bar_colors_o = []

    for data_list, label, col_l, col_o in [
        (hosts_data,   'add_host',   '#1D9E75', '#D85A30'),
        (routers_data, 'add_router', '#0F6E56', '#993C1D'),
    ]:
        valid = [r for r in data_list
                 if r.get('t_local_ms') is not None and r.get('t_total_ms') is not None]
        if not valid:
            continue
        avg_local    = sum(r['t_local_ms'] for r in valid) / len(valid)
        avg_total    = sum(r['t_total_ms'] for r in valid) / len(valid)
        avg_overhead = max(0.0, avg_total - avg_local)
        ovh_vals     = [max(0.0, (1 - r['t_local_ms'] / r['t_total_ms']) * 100)
                        for r in valid if r['t_total_ms'] > 0]
        avg_pct      = sum(ovh_vals) / len(ovh_vals) if ovh_vals else 0.0

        bar_labels.append(label)
        local_vals.append(round(avg_local, 1))
        overhead_vals.append(round(avg_overhead, 1))
        pct_vals.append(round(avg_pct, 1))
        bar_colors_l.append(col_l)
        bar_colors_o.append(col_o)

    if bar_labels:
        y_pos = range(len(bar_labels))
        bars_l = ax.barh(y_pos, local_vals, color=bar_colors_l,
                         alpha=0.85, height=0.55, label='t_local (local work)')
        bars_o = ax.barh(y_pos, overhead_vals, left=local_vals, color=bar_colors_o,
                         alpha=0.85, height=0.55, label='Twin overhead (t_total − t_local)')

        # Etiquetes dins cada segment
        for i, (lv, ov, pct, col) in enumerate(zip(
                local_vals, overhead_vals, pct_vals, bar_colors_l)):
            # Temps local al centre del segment verd
            ax.text(lv / 2, i, f'{lv:.0f}ms',
                    ha='center', va='center', fontsize=10,
                    color='white', fontweight='bold')
            # % overhead a la dreta del segment roig (si hi ha espai)
            if ov > 3:
                ax.text(lv + ov / 2, i, f'{pct}%',
                        ha='center', va='center', fontsize=11,
                        color='white', fontweight='bold')
            else:
                # Si el segment és molt petit, l'etiqueta va fora
                ax.text(lv + ov + 2, i, f'{pct}%',
                        ha='left', va='center', fontsize=11,
                        color=bar_colors_o[i], fontweight='bold')

        ax.set_yticks(list(y_pos))
        ax.set_yticklabels(bar_labels, fontsize=12)
        ax.set_xlabel('Time (ms)')
        ax.axvline(x=0, color='grey', linewidth=0.8, alpha=0.4)

        # Llegenda manual
        from matplotlib.patches import Patch
        legend_elements = [
            Patch(facecolor='#1D9E75', alpha=0.85, label='t_local  (local Mininet work)'),
            Patch(facecolor='#D85A30', alpha=0.85, label='Twin overhead  (t_total − t_local)'),
        ]
        ax.legend(handles=legend_elements, loc='lower right', fontsize=9)
    else:
        ax.text(0.5, 0.5, 'No data', ha='center', va='center',
                transform=ax.transAxes, color='grey')

    ax.set_title('Twin Sync Overhead — time breakdown', fontweight='bold', fontsize=12)
    ax.text(0.98, 0.98,
            '0% → Original is bottleneck, Twin cost absorbed\n'
            '>0% → network+Twin is bottleneck, system waits',
            ha='right', va='top', transform=ax.transAxes,
            fontsize=8, color='grey', style='italic')
    ax.grid(True, alpha=0.3, linestyle='--', axis='x')
    ax.set_axisbelow(True)

    # ── G5: Component breakdown — add_host ──────────────────────────────────
    ax = axes[2][0]
    if hosts_data:
        h_x = [r['n_nodes'] for r in hosts_data]
        ax.plot(h_x, [r['t_local_ms']   for r in hosts_data],
                '-',  color='#3498db', linewidth=2, label='t_local (Original)')
        ax.plot(h_x, [r['t_network_ms'] for r in hosts_data],
                '--', color='#e67e22', linewidth=2, label='t_network (RTT)')
        if has_twin:
            ax.plot(h_x, [r['t_twin_ms'] for r in hosts_data],
                    ':',  color='#27ae60', linewidth=2, label='t_twin (Twin Mininet)')
    else:
        ax.text(0.5, 0.5, 'No host data', ha='center', va='center',
                transform=ax.transAxes, color='grey')
    if not has_twin:
        ax.text(0.5, 0.04, f't_twin not available (mode: {routing_mode})',
                ha='center', transform=ax.transAxes, fontsize=9, color='grey', style='italic')
    ax.set_title('Latency Breakdown — add_host', fontweight='bold', fontsize=12,
                 color='#27ae60')
    ax.set_xlabel('Network size (total nodes)')
    ax.set_ylabel('Time (ms)')
    ax.legend(loc='upper left')
    ax.grid(True, alpha=0.3, linestyle='--')

    # ── G6: Component breakdown — add_router ────────────────────────────────
    ax = axes[2][1]
    if routers_data:
        r_x = [r['n_nodes'] for r in routers_data]
        ax.plot(r_x, [r['t_local_ms']   for r in routers_data],
                '-',  color='#c0392b', linewidth=2, label='t_local (Original)')
        ax.plot(r_x, [r['t_network_ms'] for r in routers_data],
                '--', color='#d35400', linewidth=2, label='t_network (RTT)')
        if has_twin:
            ax.plot(r_x, [r['t_twin_ms'] for r in routers_data],
                    ':',  color='#e74c3c', linewidth=2, label='t_twin (Twin Mininet)')
    else:
        ax.text(0.5, 0.5, 'No router data', ha='center', va='center',
                transform=ax.transAxes, color='grey')
    if not has_twin:
        ax.text(0.5, 0.04, f't_twin not available (mode: {routing_mode})',
                ha='center', transform=ax.transAxes, fontsize=9, color='grey', style='italic')
    ax.set_title('Latency Breakdown — add_router', fontweight='bold', fontsize=12,
                 color='#e74c3c')
    ax.set_xlabel('Network size (total nodes)')
    ax.set_ylabel('Time (ms)')
    ax.legend(loc='upper left')
    ax.grid(True, alpha=0.3, linestyle='--')

    # ── G7: RAM del sistema acumulada ───────────────────────────────────────
    # Valor absolut de virtual_memory().used després de cada operació.
    # Mostra com creix la RAM total conforme escala la xarxa, capturant tant
    # el kernel (namespaces, veth, OVS) com els processos (Python, FRR).
    ax = axes[3][0]
    all_mem = sorted([r for r in rows if r.get('system_mem_mb') is not None],
                     key=lambda x: x['n_nodes'])
    if all_mem:
        mem_vals = [r['system_mem_mb'] for r in all_mem]
        ax.plot([r['n_nodes'] for r in all_mem], mem_vals,
                'o-', color='#2980b9', linewidth=1.5, markersize=4,
                label='System RAM used')
        # Referència: RAM total del hardware (~3700 MB en els lab PCs)
        ax.axhline(y=3700, color='#c0392b', linestyle='-', alpha=0.6,
                   label='Total RAM (~3700 MB)')
        # Creixement observat durant el test
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
    ax.set_title('System RAM Usage vs Network Size', fontweight='bold', fontsize=12)
    ax.set_xlabel('Network size (total nodes)')
    ax.set_ylabel('RAM used  MB  (system-wide)')
    ax.grid(True, alpha=0.3, linestyle='--')

    # ── G8: Memòria FRR acumulada ───────────────────────────────────────────
    ax = axes[3][1]
    all_frr = sorted([r for r in rows if r.get('frr_total_mb') is not None],
                     key=lambda x: x['n_nodes'])
    if all_frr:
        frr_vals = [r['frr_total_mb'] for r in all_frr]
        max_frr  = max(frr_vals)
        ax.plot([r['n_nodes'] for r in all_frr], frr_vals,
                'D-', color='#8e44ad', linewidth=2, label='FRR total (zebra+ospfd)')
        ax.axhline(y=1500, color='#e74c3c', linestyle='--', alpha=0.7,
                   label='Free RAM at start (~1500 MB)')
        ax.set_ylim(0, max(max_frr * 2, 300))
        if max_frr < 50:
            ax.text(0.5, 0.6,
                    f'Flat ~{round(max_frr)}MB: initial router daemons only\n'
                    f'(mode: {routing_mode} — new routers have no FRR daemons)',
                    ha='center', transform=ax.transAxes,
                    fontsize=9, color='grey', style='italic')
        ax.legend(loc='upper left')
    else:
        ax.text(0.5, 0.5, 'FRR data not available',
                ha='center', va='center', transform=ax.transAxes, color='grey')
    ax.set_title('FRR Daemons Total Memory vs Network Size', fontweight='bold', fontsize=12)
    ax.set_xlabel('Network size (total nodes)')
    ax.set_ylabel('FRR total RAM  MB')
    ax.grid(True, alpha=0.3, linestyle='--')

    plt.tight_layout()
    plt.subplots_adjust(top=0.95)
    plt.savefig(PLOT_FILE, dpi=150, bbox_inches='tight')
    print(f' Plot saved to {PLOT_FILE}')



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