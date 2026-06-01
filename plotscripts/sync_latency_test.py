"""
sync_latency_test.py — Digital Twin Sync Latency Study
======================================================
Progressively scales the network using the same FIXED_SEQUENCE
as scalability_test.py, recording individual sync latency
measurements for every operation.

Metrics recorded per operation:
  - op_type       : 'add_host' or 'add_router'
  - n_nodes       : network size (hosts + routers) at time of operation
  - t_local_ms    : time to apply change in Original Mininet
  - t_network_ms  : HTTP round-trip Original → Twin
  - t_twin_ms     : time to apply change in Twin Mininet
  - t_total_ms    : t_local + t_network (real end-to-end sync latency)

Generates:
  - CSV with all individual measurements
  - 4 plots: total latency, component breakdown, host vs router, jitter

Usage:
    sudo python3 sync_latency_test.py
"""

import requests
import time
import csv
import statistics
from datetime import datetime
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

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


def get_last_sync_entry():
    """Get the most recent sync entry from /metrics/sync history."""
    try:
        data = requests.get(f'{ORIGINAL_URL}/metrics/sync', timeout=5).json()
        history = data.get('history', [])
        return history[-1] if history else None
    except Exception:
        return None


def do_operation(op):
    """Execute one operation and return the sync metrics for it.
    Returns a dict with the sync entry, or a dict with 'error' key on failure.
    Never returns None — always returns something so the caller can continue.
    """
    # Snapshot history length before operation
    try:
        before = requests.get(f'{ORIGINAL_URL}/metrics/sync', timeout=5).json()
        before_count = len(before.get('history', []))
    except Exception as e:
        return {'error': f'metrics/sync unreachable: {e}'}

    # Execute operation
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

    # Wait for sync entry to appear (max 10s — increased from 5s)
    for attempt in range(20):
        time.sleep(0.5)
        try:
            after = requests.get(f'{ORIGINAL_URL}/metrics/sync', timeout=5).json()
            history = after.get('history', [])
            if len(history) > before_count:
                return history[-1]  # Most recent entry = our operation
        except Exception:
            pass
    return {'error': 'Sync entry did not appear within 10s (t_local may be pending)'}


def safe_stats(values):
    values = [v for v in values if v is not None]
    if not values:
        return None, None, None
    return round(min(values), 2), round(statistics.mean(values), 2), round(max(values), 2)


def jitter_of(values):
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return 0.0
    diffs = [abs(values[i] - values[i-1]) for i in range(1, len(values))]
    return round(sum(diffs) / len(diffs), 2)


# ── Plotting ─────────────────────────────────────────────────────────────────

def generate_plots(rows):
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle(
        'Digital Twin Network — Sync Latency Study\n'
        '(t_total = t_local + t_network)',
        fontsize=14, fontweight='bold'
    )

    checkpoints = sorted(set(r['n_nodes'] for r in rows))

    def vals_at(cp, key, op_type=None):
        return [r[key] for r in rows
                if r['n_nodes'] == cp
                and r[key] is not None
                and (op_type is None or r['op_type'] == op_type)]

    # ── Plot 1: t_total min/avg/max per checkpoint ──
    ax = axes[0][0]
    avgs = [safe_stats(vals_at(cp, 't_total_ms'))[1] for cp in checkpoints]
    mins = [safe_stats(vals_at(cp, 't_total_ms'))[0] for cp in checkpoints]
    maxs = [safe_stats(vals_at(cp, 't_total_ms'))[2] for cp in checkpoints]
    ax.plot(checkpoints, avgs, 'o-', color='#2980b9', linewidth=2, label='avg')
    ax.fill_between(checkpoints, mins, maxs, alpha=0.2, color='#2980b9', label='min–max')
    ax.set_title('Total sync latency (t_total)', fontweight='bold')
    ax.set_xlabel('Network size (hosts + routers)')
    ax.set_ylabel('t_total (ms)')
    ax.set_xticks(checkpoints)
    ax.legend()
    ax.grid(True, alpha=0.3)

    # ── Plot 2: stacked bars — component breakdown ──
    ax = axes[0][1]
    w = 3
    x = np.array(checkpoints)
    local_avgs   = [safe_stats(vals_at(cp, 't_local_ms'))[1]   or 0 for cp in checkpoints]
    network_avgs = [safe_stats(vals_at(cp, 't_network_ms'))[1] or 0 for cp in checkpoints]
    twin_avgs    = [safe_stats(vals_at(cp, 't_twin_ms'))[1]    or 0 for cp in checkpoints]
    # overhead = t_network - t_twin (network transport time)
    overhead_avgs = [max(0, n - t) for n, t in zip(network_avgs, twin_avgs)]

    ax.bar(x, local_avgs,    width=w, label='t_local (Original Mininet)', color='#3498db')
    ax.bar(x, overhead_avgs, width=w, bottom=local_avgs, label='t_network overhead', color='#e67e22')
    ax.bar(x, twin_avgs,     width=w,
           bottom=[l + o for l, o in zip(local_avgs, overhead_avgs)],
           label='t_twin (Twin Mininet)', color='#27ae60')
    ax.set_title('Latency component breakdown (avg)', fontweight='bold')
    ax.set_xlabel('Network size (hosts + routers)')
    ax.set_ylabel('Time (ms)')
    ax.set_xticks(checkpoints)
    ax.legend()
    ax.grid(True, alpha=0.3, axis='y')

    # ── Plot 3: add_host vs add_router ──
    ax = axes[1][0]
    for op_type, color, label in [
        ('add_host',   '#27ae60', 'add_host'),
        ('add_router', '#e74c3c', 'add_router'),
    ]:
        avgs = [safe_stats(vals_at(cp, 't_total_ms', op_type))[1] for cp in checkpoints]
        valid_cp = [cp for cp, a in zip(checkpoints, avgs) if a is not None]
        valid_avg = [a for a in avgs if a is not None]
        if valid_avg:
            ax.plot(valid_cp, valid_avg, 'o-', color=color, linewidth=2, label=label)
    ax.set_title('add_host vs add_router latency', fontweight='bold')
    ax.set_xlabel('Network size (hosts + routers)')
    ax.set_ylabel('t_total (ms)')
    ax.set_xticks(checkpoints)
    ax.legend()
    ax.grid(True, alpha=0.3)

    # ── Plot 4: jitter per component ──
    ax = axes[1][1]
    jitter_total   = [jitter_of(vals_at(cp, 't_total_ms'))   for cp in checkpoints]
    jitter_network = [jitter_of(vals_at(cp, 't_network_ms')) for cp in checkpoints]
    jitter_twin    = [jitter_of(vals_at(cp, 't_twin_ms'))    for cp in checkpoints]
    ax.plot(checkpoints, jitter_total,   'o-', color='#2980b9', linewidth=2, label='jitter t_total')
    ax.plot(checkpoints, jitter_network, 's--', color='#e67e22', linewidth=2, label='jitter t_network')
    ax.plot(checkpoints, jitter_twin,    '^--', color='#27ae60', linewidth=2, label='jitter t_twin')
    ax.set_title('Sync latency jitter per component', fontweight='bold')
    ax.set_xlabel('Network size (hosts + routers)')
    ax.set_ylabel('Jitter (ms)')
    ax.set_xticks(checkpoints)
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(PLOT_FILE, dpi=150, bbox_inches='tight')
    print(f'✅ Plot saved to {PLOT_FILE}')


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print('=' * 60)
    print('  Digital Twin Network — Sync Latency Study')
    print(f'  Checkpoints: {CHECKPOINTS}')
    print(f'  CSV:  {CSV_FILE}')
    print(f'  Plot: {PLOT_FILE}')
    print('=' * 60)

    print('\n⏳ Waiting for network...')
    if not wait_for_network():
        print('❌ Network not available.')
        return
    print('✅ Network ready!')

    current = count_nodes()
    print(f'\n📊 Initial nodes: {current}')
    if current != 7:
        print(f'⚠️  Expected 7 nodes (h1-h5, r1, r2) but found {current}.')
        print('   Reset the network and retry.')
        return

    rows = []
    failures = 0
    checkpoints_iter = iter(CHECKPOINTS)
    next_checkpoint  = next(checkpoints_iter)
    print(f'🎯 First checkpoint: {next_checkpoint} nodes\n')

    MAX_NODES = 128  # stop as soon as we reach this many non-switch nodes

    for op in FIXED_SEQUENCE:
        # Hard stop: never execute an operation that would push us past the limit
        if count_nodes() >= MAX_NODES:
            print(f'\n  🛑 Reached {MAX_NODES} nodes — stopping sequence.')
            break

        name = op['name']
        if op['type'] == 'router':
            print(f'  ➕ add_router {name} → {op["connected_routers"]}...', end='', flush=True)
        else:
            print(f'  ➕ add_host   {name} → {op["router"]}...', end='', flush=True)

        entry = do_operation(op)

        # Check for error
        if 'error' in entry:
            failures += 1
            print(f' ❌ {entry["error"]}')
            # Record failure in CSV with null latencies
            current = count_nodes()
            rows.append({
                'op_type':      op['type'],
                'op_name':      name,
                'n_nodes':      current,
                't_local_ms':   None,
                't_network_ms': None,
                't_twin_ms':    None,
                't_total_ms':   None,
                'error':        entry['error'],
            })
            # Skip OSPF wait — node likely wasn't added
            time.sleep(0.5)
            # Update checkpoint based on actual node count
            current = count_nodes()
            while next_checkpoint is not None and current >= next_checkpoint:
                print(f'\n  📏 Checkpoint {next_checkpoint} reached ({failures} failures so far)')
                try:
                    next_checkpoint = next(checkpoints_iter)
                    print(f'  🎯 Next checkpoint: {next_checkpoint} nodes\n')
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
            'op_type':      op['type'],
            'op_name':      name,
            'n_nodes':      current,
            't_local_ms':   t_local,
            't_network_ms': t_network,
            't_twin_ms':    t_twin,
            't_total_ms':   t_total,
            'error':        '',
        }
        rows.append(row)
        print(f' ✅  total={t_total}ms  local={t_local}ms  net={t_network}ms  twin={t_twin}ms')

        # Wait for OSPF convergence after router operations
        if op['type'] == 'router':
            print(f'     ⏳ Waiting for OSPF convergence (5s)...')
            time.sleep(5)
        else:
            time.sleep(0.5)

        if next_checkpoint is not None and current >= next_checkpoint:
            print(f'\n  📏 Checkpoint {next_checkpoint} reached ({failures} failures so far)')
            try:
                next_checkpoint = next(checkpoints_iter)
                print(f'  🎯 Next checkpoint: {next_checkpoint} nodes\n')
            except StopIteration:
                next_checkpoint = None
                break

    # Save CSV
    if rows:
        with open(CSV_FILE, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print(f'\n✅ CSV saved to {CSV_FILE}')

        # Generate plots (only rows with valid data)
        valid_rows = [r for r in rows if r['t_total_ms'] is not None]
        if valid_rows:
            generate_plots(valid_rows)
        else:
            print('⚠️  No valid rows to plot.')

    total_ops = len(rows)
    ok_ops    = sum(1 for r in rows if not r.get('error'))
    print(f'\n🏁 Sync latency study complete!')
    print(f'   {ok_ops}/{total_ops} operations succeeded  |  {failures} failures')


if __name__ == '__main__':
    main()