"""
sync.py — Synchronisation with the Digital Twin.

Strategy
--------
Instead of sending a full Mininet snapshot on every topology change (which
forces the Twin to do net.stop() + full rebuild, taking 6-10 s), we now
send *incremental events* — small JSON payloads that describe exactly what
changed.  The Twin applies each event in-place using its own Mininet
instance, keeping routing daemons alive and reducing sync latency from
seconds to ~100 ms.

The one exception is rename_node, which still uses a full snapshot because
Mininet does not support renaming nodes in-place.

Event-based sync functions
--------------------------
  sync_event(endpoint, data, t_local_ms)
      Fire-and-forget POST to the Twin's endpoint with the pre-computed
      payload.  Records latency.  Runs in a daemon thread.

Legacy snapshot function (kept for rename_node)
-----------------------------------------------
  sync_snapshot(operation, t_local_ms)
      Serialises the full matrix + nodes dict and POSTs to /load_network.
"""

import threading
import time
from collections import deque

import requests

# ── Connection settings (overridden by init_sync via CLI args) ──
# TWINS is a list of dicts {ip, port} — one per Twin PC.
# Supports any number of Twins: 1 (default), 2, 3...
TWINS       = [{'ip': '10.4.39.110', 'port': 5000}]
ORIGINAL_IP = '10.4.39.102'  # used by each Twin to ping back the Original

# ── Sync latency history ──
# Each entry: operation, t_local_ms, t_network_ms, t_twin_ms, timestamp
sync_latency_history = deque(maxlen=50)
sync_history_lock    = threading.Lock()

# Injected by app.py at startup
_xarxa = None


def init_sync(xarxa_instance, twins=None, original_ip=None, twin_port=None,
              twin_ip=None):
    """
    Give sync.py a reference to the live Xarxa object and configure peers.

    twins       : list of IP strings or {'ip':..,'port':..} dicts for all Twin PCs.
    original_ip : IP of this PC (used by Twins to know who the Original is).
    twin_port   : default port for Twins that don't specify one (default 5000).
    twin_ip     : legacy single-twin shortcut (kept for backward compatibility).
    """
    global _xarxa, TWINS, ORIGINAL_IP
    _xarxa = xarxa_instance

    port = twin_port if twin_port is not None else 5000

    if twins:
        # Parse each entry: can be a plain IP string or already a dict
        parsed = []
        for t in twins:
            if isinstance(t, dict):
                parsed.append({'ip': t['ip'], 'port': t.get('port', port)})
            else:
                # Support "IP:PORT" or plain "IP"
                if ':' in str(t):
                    ip, p = str(t).rsplit(':', 1)
                    parsed.append({'ip': ip, 'port': int(p)})
                else:
                    parsed.append({'ip': str(t), 'port': port})
        TWINS = parsed
    elif twin_ip:
        # Backward-compatible single-twin shortcut
        TWINS = [{'ip': twin_ip, 'port': port}]

    if original_ip:
        ORIGINAL_IP = original_ip

    twins_str = ', '.join(f'{t["ip"]}:{t["port"]}' for t in TWINS)
    print(f'[sync] Original={ORIGINAL_IP}  Twins=[{twins_str}]')


# ── Latency recording ──

def record_sync_latency(operation, t_local_ms, t_network_ms, t_twin_ms):
    """
    Append one timing entry to the in-memory history and push it to
    the Twin's dashboard endpoint so both sides can display sync metrics.

    When t_local_ms is provided but t_network_ms is None, this is a
    'late update' call after parallel sync — update the most recent
    matching entry instead of appending a new one.
    """
    updated_entry = None

    with sync_history_lock:
        # Late update: t_local_ms known after parallel Mininet apply
        # Find the most recent matching entry and update it
        if t_local_ms is not None and t_network_ms is None and t_twin_ms is None:
            for entry in reversed(sync_latency_history):
                if entry.get('operation') == operation:
                    entry['t_local_ms'] = round(t_local_ms, 2)
                    # t_total = max(t_local, t_network) — parallel execution
                    t_net = entry.get('t_network_ms')
                    if t_net is not None:
                        entry['latency_ms'] = round(max(t_local_ms, t_net), 2)
                    updated_entry = dict(entry)
                    break
            # If no match found, fall through and create new entry

        if updated_entry is None:
            # parallel=True means t_local and t_network ran simultaneously
            # so t_total = max(t_local, t_network), not their sum
            entry = {
                'operation':    operation,
                't_local_ms':   round(t_local_ms,   2) if t_local_ms   is not None else None,
                't_network_ms': round(t_network_ms, 2) if t_network_ms is not None else None,
                't_twin_ms':    round(t_twin_ms,    2) if t_twin_ms    is not None else None,
                'timestamp':    time.time(),
            }
            sync_latency_history.append(entry)
            updated_entry = dict(entry)

    # Push to ALL Twins so every dashboard shows identical data
    for twin in TWINS:
        try:
            requests.post(
                f'http://{twin["ip"]}:{twin["port"]}/sync_metrics',
                json=updated_entry,
                timeout=3,
            )
        except Exception as e:
            print(f'[sync_metrics] push error to {twin["ip"]}: {e}')


# ── Incremental event sync ──

def _do_sync_to_one_twin(twin, endpoint, payload, retries=3, delay=0.5):
    """
    POST an event to a single Twin. Returns (t_network_ms, t_twin_ms) or
    (None, None) on failure. Used internally by _do_sync_to_all_twins.
    """
    for attempt in range(retries):
        try:
            t_start  = time.time()
            response = requests.post(
                f'http://{twin["ip"]}:{twin["port"]}{endpoint}',
                json=payload,
                timeout=30,
            )
            t_network_ms = round((time.time() - t_start) * 1000, 2)
            if response.status_code == 200:
                t_twin_ms = response.json().get('t_local_ms', None)
                print(f'[sync] → {twin["ip"]} {endpoint}  '
                      f'net={t_network_ms}ms  twin={t_twin_ms}ms')
                return t_network_ms, t_twin_ms
        except Exception as e:
            print(f'[sync_event] {endpoint} → {twin["ip"]} '
                  f'attempt {attempt+1}/{retries}: {e}')
            if attempt < retries - 1:
                time.sleep(delay)
    print(f'[sync_event] {endpoint} → {twin["ip"]} failed after {retries} attempts')
    return None, None


def _do_sync_to_all_twins(endpoint, data, t_local_holder):
    """
    Send an event to ALL Twins in parallel (one thread per Twin).
    Records a single latency entry using:
      t_network = max(all twin round-trips)   — slowest Twin sets the pace
      t_twin    = max(all twin processing times)
    Waits for t_local_ms from the main thread before recording.
    """
    payload  = {**data, 'sync': True}
    results  = [None] * len(TWINS)          # (t_network, t_twin) per twin
    lock     = threading.Lock()

    def send_to(idx, twin):
        net_ms, twin_ms = _do_sync_to_one_twin(twin, endpoint, payload)
        with lock:
            results[idx] = (net_ms, twin_ms)

    threads = [
        threading.Thread(target=send_to, args=(i, twin), daemon=True)
        for i, twin in enumerate(TWINS)
    ]
    for t in threads: t.start()
    for t in threads: t.join()

    # Aggregate: use worst-case (max) across all twins
    valid_net   = [r[0] for r in results if r and r[0] is not None]
    valid_twin  = [r[1] for r in results if r and r[1] is not None]
    t_network_ms = round(max(valid_net),  2) if valid_net  else None
    t_twin_ms    = round(max(valid_twin), 2) if valid_twin else None

    # Wait for t_local_ms from the main thread (max 10s)
    ready = t_local_holder.get('ready')
    if ready:
        ready.wait(timeout=10)
    t_local_ms = t_local_holder.get('value')

    operation = endpoint.strip('/')
    record_sync_latency(operation, t_local_ms, t_network_ms, t_twin_ms)


def sync_event(endpoint, data, t_local_ms):
    """
    Send an incremental event to ALL Twins in a daemon thread.
    Returns a t_local_holder — call set_t_local(holder, value) once Mininet finishes.
    If t_local_ms is not None, it is set immediately (legacy/sync path).
    """
    ready  = threading.Event()
    holder = {'value': t_local_ms, 'ready': ready}
    if t_local_ms is not None:
        ready.set()
    threading.Thread(
        target=_do_sync_to_all_twins,
        args=(endpoint, data, holder),
        daemon=True,
    ).start()
    return holder


def set_t_local(holder, t_local_ms):
    """Signal that t_local_ms is now known. Called after Mininet finishes."""
    holder['value'] = round(t_local_ms, 2)
    holder['ready'].set()


# ── Full snapshot sync (kept for rename_node) ──

def _do_sync_snapshot(operation, t_local_ms):
    """
    Serialise the full network state and POST it to the Twin's
    /load_network endpoint.  The Twin will do a full Mininet rebuild.
    Only used when an in-place update is not possible (e.g. rename_node).
    """
    xarxa = _xarxa
    serializable_matrix = [
        [cell if isinstance(cell, str) else int(cell) for cell in row]
        for row in xarxa.network_matrix
    ]
    try:
        t_net_start = time.time()
        valid_net = []
        for twin in TWINS:
            try:
                requests.post(
                    f'http://{twin["ip"]}:{twin["port"]}/load_network',
                    json={
                        'matrix': serializable_matrix,
                        'nodes':  xarxa.nodes,
                        'sync':   True,
                    },
                    timeout=10,
                )
                t_network_ms = round((time.time() - t_net_start) * 1000, 2)
                valid_net.append(t_network_ms)
            except Exception as e:
                print(f'[sync_snapshot] error to {twin["ip"]}: {e}')
        t_network_ms = round(max(valid_net), 2) if valid_net else None
        record_sync_latency(operation, t_local_ms, t_network_ms, None)
    except Exception as e:
        print(f'[sync_snapshot] error: {e}')


def sync_snapshot(operation, t_local_ms):
    """Launch a full snapshot sync in a daemon thread."""
    threading.Thread(
        target=_do_sync_snapshot,
        args=(operation, t_local_ms),
        daemon=True,
    ).start()


# ── Legacy alias (used by topology.py /load_network) ──
# Keep the old name so topology.py doesn't need to change.
def sync_in_background(operation, t_local_ms):
    sync_snapshot(operation, t_local_ms)