"""
sync.py — Synchronisation with the Digital Twin.

The Original sends full state snapshots (matrix + nodes) to the Twin
after every topology change. The Twin rebuilds its Mininet network from
the snapshot so both sides stay consistent without replicating every
individual Mininet call.
"""

import threading
import time
from collections import deque

import requests

# Filled in by app.py after parsing CLI arguments
DIGITAL_TWIN_IP   = '10.4.39.103'
DIGITAL_TWIN_PORT = 5000

# Sync latency history (only the Original writes here).
# Each entry stores three decomposed timings:
#   t_local_ms   — time Mininet spent on the Original side
#   t_network_ms — HTTP round-trip to the Twin (pure network overhead)
#   t_twin_ms    — time Mininet spent on the Twin side (returned in response)
sync_latency_history = deque(maxlen=50)
sync_history_lock    = threading.Lock()

# Set by app.py so sync functions can read the live network state
_xarxa_ref = None

def init_sync(xarxa_instance):
    """Call once at startup to give sync.py access to the Xarxa object."""
    global _xarxa_ref
    _xarxa_ref = xarxa_instance


# ── Core sync functions ──

def record_sync_latency(operation, t_local_ms, t_network_ms, t_twin_ms):
    """Append a timing entry to the history and push it to the Twin dashboard."""
    entry = {
        'operation':    operation,
        't_local_ms':   round(t_local_ms,   2) if t_local_ms   is not None else None,
        't_network_ms': round(t_network_ms, 2) if t_network_ms is not None else None,
        't_twin_ms':    round(t_twin_ms,    2) if t_twin_ms    is not None else None,
        # Kept for backwards compatibility with the dashboard history list
        'latency_ms':   round(t_network_ms, 2) if t_network_ms is not None else None,
        'timestamp':    time.time(),
    }
    with sync_history_lock:
        sync_latency_history.append(entry)

    # Push to Twin dashboard too so it can show sync metrics
    try:
        requests.post(
            f'http://{DIGITAL_TWIN_IP}:{DIGITAL_TWIN_PORT}/sync_metrics',
            json=entry,
            timeout=1,
        )
    except Exception as e:
        print(f'[sync_metrics] push error: {e}')


def synchronize(route, data, t_local_ms, retries=3, delay=0.5):
    """
    POST `data` to the Twin at `route`, with automatic retries.
    Records the decomposed latency on success.
    Returns the Twin's JSON response or None on failure.
    """
    for attempt in range(retries):
        try:
            data['sync'] = True
            t_net_start  = time.time()
            response     = requests.post(
                f'http://{DIGITAL_TWIN_IP}:{DIGITAL_TWIN_PORT}{route}',
                json=data,
                timeout=15,
            )
            t_network_ms = round((time.time() - t_net_start) * 1000, 2)
            if response.status_code == 200:
                resp_json = response.json()
                t_twin_ms = resp_json.get('t_local_ms', None)
                record_sync_latency(route.strip('/'), t_local_ms, t_network_ms, t_twin_ms)
                return resp_json
        except Exception as e:
            print(f'Sync error (attempt {attempt + 1}/{retries}): {e}')
            if attempt < retries - 1:
                time.sleep(delay)
    return None


def synchronize_snapshot(operation, t_local_ms):
    """
    Send a full state snapshot (matrix + nodes) to the Twin's /load_network.
    The Twin rebuilds its network from scratch — no per-operation replication.

    t_twin_ms is recorded as None because the Twin rebuilds asynchronously
    and we do not wait for it to finish.
    """
    xarxa = _xarxa_ref
    serializable_matrix = [
        [cell if isinstance(cell, str) else int(cell) for cell in row]
        for row in xarxa.network_matrix
    ]
    try:
        t_net_start = time.time()
        requests.post(
            f'http://{DIGITAL_TWIN_IP}:{DIGITAL_TWIN_PORT}/load_network',
            json={'matrix': serializable_matrix, 'nodes': xarxa.nodes, 'sync': True},
            timeout=10,
        )
        t_network_ms = round((time.time() - t_net_start) * 1000, 2)
        record_sync_latency(operation, t_local_ms, t_network_ms, None)
    except Exception as e:
        print(f'Snapshot sync error: {e}')


def sync_in_background(operation, t_local_ms):
    """Launch synchronize_snapshot in a daemon thread so the browser is never blocked."""
    threading.Thread(
        target=synchronize_snapshot,
        args=(operation, t_local_ms),
        daemon=True,
    ).start()
