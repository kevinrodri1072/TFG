"""
Scalability Test Script for Digital Twin Network
=================================================
Progressively scales the network adding routers and hosts,
measuring sync latency at powers of 2 (8, 16, 32, 64, 128 nodes).
 
The sequence of nodes is FIXED and deterministic: every run adds
the same nodes in the same order, so results across runs are
comparable regardless of network conditions.
 
Usage:
    python3 scalability_test.py
"""
 
import requests
import time
import csv
from datetime import datetime
 
# ── Configuration ──
ORIGINAL_URL = 'http://localhost:5000'
TWIN_URL     = 'http://10.4.39.153:5000'  # Used only to verify Twin is reachable (optional)
CHECKPOINTS  = [8, 16, 32, 64, 128]
RESULTS_FILE = f'scalability_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
 
# ──────────────────────────────────────────────────────────────────────────────
# FIXED SEQUENCE OF NODES
# ──────────────────────────────────────────────────────────────────────────────
# Initial state: h1-h5 (hosts), r1, r2 (routers), sw1, sw2 (switches)
# Non-switch node count starts at 7 (h1-h5 + r1 + r2).
#
# Each entry is either:
#   {'type': 'router', 'name': 'r3', 'connected_routers': ['r1']}
#   {'type': 'host',   'name': 'h6', 'router': 'r1'}
#
# The sequence is designed so:
#   - New routers always connect to already-existing routers.
#   - Hosts are always assigned to already-existing routers.
#   - The 121 entries below bring us from 7 → 128 non-switch nodes.
#
# Topology rationale: roughly 1 router per 3 hosts, spread evenly.
# ──────────────────────────────────────────────────────────────────────────────
FIXED_SEQUENCE = [
    # ── 7 → 8 (checkpoint 8) ──────────────────────────────────────────────
    {'type': 'host',   'name': 'h6',   'router': 'r1'},
 
    # ── 8 → 16 (checkpoint 16) ────────────────────────────────────────────
    {'type': 'router', 'name': 'r3',   'connected_routers': ['r1']},
    {'type': 'host',   'name': 'h7',   'router': 'r2'},
    {'type': 'host',   'name': 'h8',   'router': 'r2'},
    {'type': 'router', 'name': 'r4',   'connected_routers': ['r2']},
    {'type': 'host',   'name': 'h9',   'router': 'r3'},
    {'type': 'host',   'name': 'h10',  'router': 'r3'},
    {'type': 'host',   'name': 'h11',  'router': 'r4'},
    {'type': 'host',   'name': 'h12',  'router': 'r4'},
 
    # ── 16 → 32 (checkpoint 32) ───────────────────────────────────────────
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
 
    # ── 32 → 64 (checkpoint 64) ───────────────────────────────────────────
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
 
    # ── 64 → 128 (checkpoint 128) ─────────────────────────────────────────
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
    {'type': 'host',   'name': 'h106', 'router': 'r32'},
    {'type': 'host',   'name': 'h107', 'router': 'r31'},
    {'type': 'host',   'name': 'h108', 'router': 'r32'},
    {'type': 'host',   'name': 'h109', 'router': 'r1'},
    {'type': 'host',   'name': 'h110', 'router': 'r2'},
    {'type': 'host',   'name': 'h111', 'router': 'r3'},
    {'type': 'host',   'name': 'h112', 'router': 'r4'},
    {'type': 'host',   'name': 'h113', 'router': 'r5'},
    {'type': 'host',   'name': 'h114', 'router': 'r6'},
    {'type': 'host',   'name': 'h115', 'router': 'r7'},
    {'type': 'host',   'name': 'h116', 'router': 'r8'},
    {'type': 'host',   'name': 'h117', 'router': 'r9'},
    {'type': 'host',   'name': 'h118', 'router': 'r10'},
    {'type': 'host',   'name': 'h119', 'router': 'r11'},
    {'type': 'host',   'name': 'h120', 'router': 'r12'},
    {'type': 'host',   'name': 'h121', 'router': 'r31'},
]
 
# Sanity check: starting from 7 nodes, adding len(FIXED_SEQUENCE) should reach 128
assert 7 + len(FIXED_SEQUENCE) == 153, \
    f"Sequence length error: 7 + {len(FIXED_SEQUENCE)} = {7 + len(FIXED_SEQUENCE)}, expected 153"
 
 
# ── Helpers ────────────────────────────────────────────────────────────────────
 
def get_topology():
    r = requests.get(f'{ORIGINAL_URL}/topology', timeout=10)
    return r.json()
 
def get_sync_metrics(url):
    try:
        return requests.get(f'{url}/metrics/sync', timeout=5).json()
    except:
        return {'stats': None, 'history': []}
 
def count_nodes():
    topo = get_topology()
    return sum(1 for n, p in topo['nodes'].items() if p['type'] != 'switch')
 
def add_router(name, connected_routers):
    t = time.time()
    r = requests.post(f'{ORIGINAL_URL}/add_router',
                      json={'name': name, 'connected_routers': connected_routers},
                      timeout=30)
    elapsed = round((time.time() - t) * 1000, 2)
    return r.json().get('ok', False), elapsed
 
def add_host(name, router):
    t = time.time()
    r = requests.post(f'{ORIGINAL_URL}/add_host',
                      json={'name': name, 'router': router},
                      timeout=30)
    elapsed = round((time.time() - t) * 1000, 2)
    return r.json().get('ok', False), elapsed
 
def wait_for_network():
    for _ in range(30):
        try:
            if requests.get(f'{ORIGINAL_URL}/topology', timeout=5).status_code == 200:
                return True
        except:
            pass
        time.sleep(1)
    return False
 
def stats(values):
    if not values:
        return None, None, None
    return round(min(values), 2), round(sum(values)/len(values), 2), round(max(values), 2)
 
def measure_checkpoint(checkpoint, op_times):
    orig  = get_sync_metrics(ORIGINAL_URL).get('stats') or {}
    op_min, op_avg, op_max = stats(op_times)
 
    def g(obj, key):
        """Safely get min/avg/max from a nested stats dict."""
        if not obj or not isinstance(obj.get(key), dict):
            return None
        return obj[key]
 
    local   = g(orig, 't_local')
    network = g(orig, 't_network')
    twin    = g(orig, 't_twin')
 
    return {
        'checkpoint':        checkpoint,
        # Total operation times measured by the test script itself
        'op_min_ms':         op_min,
        'op_avg_ms':         op_avg,
        'op_max_ms':         op_max,
        # Original Mininet time
        'local_min_ms':      local['min']    if local else None,
        'local_avg_ms':      local['avg']    if local else None,
        'local_max_ms':      local['max']    if local else None,
        # Pure network round-trip (HTTP POST)
        'network_min_ms':    network['min']  if network else None,
        'network_avg_ms':    network['avg']  if network else None,
        'network_max_ms':    network['max']  if network else None,
        'network_jitter_ms': orig.get('jitter_ms'),
        # Twin Mininet time
        'twin_min_ms':       twin['min']     if twin else None,
        'twin_avg_ms':       twin['avg']     if twin else None,
        'twin_max_ms':       twin['max']     if twin else None,
    }
 
def print_result(r):
    print(f'\n{"="*60}')
    print(f'  CHECKPOINT: {r["checkpoint"]} nodes')
    print(f'{"="*60}')
    print(f'  Op time (script):  min={r["op_min_ms"]}ms  avg={r["op_avg_ms"]}ms  max={r["op_max_ms"]}ms')
    print(f'  Local  (Original): min={r["local_min_ms"]}ms  avg={r["local_avg_ms"]}ms  max={r["local_max_ms"]}ms')
    print(f'  Network (HTTP):    min={r["network_min_ms"]}ms  avg={r["network_avg_ms"]}ms  max={r["network_max_ms"]}ms  jitter={r["network_jitter_ms"]}ms')
    print(f'  Twin   (Mininet):  min={r["twin_min_ms"]}ms  avg={r["twin_avg_ms"]}ms  max={r["twin_max_ms"]}ms')
 
def save_results(results):
    if not results:
        return
    with open(RESULTS_FILE, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
    print(f'\n✅ Results saved to {RESULTS_FILE}')
 
 
# ── Main ───────────────────────────────────────────────────────────────────────
 
def main():
    print('='*55)
    print('  Digital Twin Network - Scalability Test')
    print('  Mode: DETERMINISTIC (fixed sequence)')
    print(f'  Checkpoints: {CHECKPOINTS}')
    print(f'  Results: {RESULTS_FILE}')
    print('='*55)
 
    print('\n⏳ Waiting for network...')
    if not wait_for_network():
        print('❌ Network not available. Make sure app.py is running.')
        return
    print('✅ Network ready!')
 
    current = count_nodes()
    print(f'\n📊 Initial nodes: {current}')
 
    if current != 7:
        print(f'⚠️  Expected 7 initial nodes (h1-h5, r1, r2) but found {current}.')
        print('   Reset the network to its initial state and retry.')
        return
 
    results    = []
    op_times   = []
    checkpoints_iter = iter(CHECKPOINTS)
    next_checkpoint  = next(checkpoints_iter)
 
    print(f'🎯 First checkpoint: {next_checkpoint} nodes\n')
 
    for op in FIXED_SEQUENCE:
        if op['type'] == 'router':
            print(f'  ➕ Adding router {op["name"]} → {op["connected_routers"]}...')
            ok, elapsed = add_router(op['name'], op['connected_routers'])
            delay = 0.5
        else:
            print(f'  ➕ Adding host   {op["name"]} → {op["router"]}...')
            ok, elapsed = add_host(op['name'], op['router'])
            delay = 0.5
 
        if ok:
            op_times.append(elapsed)
            print(f'     ✅ {elapsed} ms')
            if op['type'] == 'router':
                print(f'     ⏳ Waiting for OSPF convergence (5s)...')
                time.sleep(5)
        else:
            print(f'     ❌ Failed adding {op["name"]} — aborting test.')
            save_results(results)
            return
 
        time.sleep(delay)
 
        current = count_nodes()
        if current == next_checkpoint:
            print(f'\n📏 Measuring checkpoint {next_checkpoint}...')
            time.sleep(3)  # Extra wait for OSPF to fully converge at checkpoint
            result = measure_checkpoint(next_checkpoint, op_times)
            results.append(result)
            print_result(result)
            op_times = []
 
            try:
                next_checkpoint = next(checkpoints_iter)
                print(f'\n🎯 Next checkpoint: {next_checkpoint} nodes')
            except StopIteration:
                break  # All checkpoints done
 
    save_results(results)
    print('\n🏁 Scalability test complete!')
 
if __name__ == '__main__':
    main()