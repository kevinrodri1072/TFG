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

# ── Connection settings ──
DIGITAL_TWIN_IP   = '10.4.39.202'   # Twin PC IP
ORIGINAL_IP       = '10.4.39.205'   # Original PC IP
DIGITAL_TWIN_PORT = 5000

# ── Sync latency history ──
# Each entry: operation, t_local_ms, t_network_ms, t_twin_ms, timestamp
sync_latency_history = deque(maxlen=50)
sync_history_lock    = threading.Lock()

# Injected by app.py at startup
_xarxa = None


def init_sync(xarxa_instance):
    """Give sync.py a reference to the live Xarxa object."""
    global _xarxa
    _xarxa = xarxa_instance


# ── Latency recording ──

def record_sync_latency(operation, t_local_ms, t_network_ms, t_twin_ms):
    """
    Append one timing entry to the in-memory history and push it to
    the Twin's dashboard endpoint so both sides can display sync metrics.
    """
    entry = {
        'operation':    operation,
        't_local_ms':   round(t_local_ms,   2) if t_local_ms   is not None else None,
        't_network_ms': round(t_network_ms, 2) if t_network_ms is not None else None,
        't_twin_ms':    round(t_twin_ms,    2) if t_twin_ms    is not None else None,
        'latency_ms':   round(t_network_ms, 2) if t_network_ms is not None else None,
        'timestamp':    time.time(),
    }
    with sync_history_lock:
        sync_latency_history.append(entry)

    try:
        requests.post(
            f'http://{DIGITAL_TWIN_IP}:{DIGITAL_TWIN_PORT}/sync_metrics',
            json=entry,
            timeout=3,
        )
    except Exception as e:
        print(f'[sync_metrics] push error: {e}')


# ── Incremental event sync ──

def _do_sync_event(endpoint, data, t_local_ms, retries=3, delay=0.5):
    """
    POST a pre-computed event payload to the Twin's endpoint.
    The payload always includes 'sync': True so the Twin knows not to
    re-synchronise back (avoids infinite loops).
    Records t_local_ms and the measured HTTP round-trip as t_network_ms.
    t_twin_ms is returned by the Twin inside its JSON response.
    """
    payload = {**data, 'sync': True}
    for attempt in range(retries):
        try:
            t_net_start  = time.time()
            response     = requests.post(
                f'http://{DIGITAL_TWIN_IP}:{DIGITAL_TWIN_PORT}{endpoint}',
                json=payload,
                timeout=30,
            )
            t_network_ms = round((time.time() - t_net_start) * 1000, 2)
            if response.status_code == 200:
                resp_json = response.json()
                t_twin_ms = resp_json.get('t_local_ms', None)
                operation = endpoint.strip('/')
                record_sync_latency(operation, t_local_ms, t_network_ms, t_twin_ms)
                return
        except Exception as e:
            print(f'[sync_event] {endpoint} attempt {attempt+1}/{retries}: {e}')
            if attempt < retries - 1:
                time.sleep(delay)
    print(f'[sync_event] {endpoint} failed after {retries} attempts')


def sync_event(endpoint, data, t_local_ms):
    """
    Send an incremental event to the Twin in a daemon thread.
    The caller provides the *already-computed* payload so both sides
    apply exactly the same values (avoids divergence from independent
    recalculations).
    """
    threading.Thread(
        target=_do_sync_event,
        args=(endpoint, data, t_local_ms),
        daemon=True,
    ).start()


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
        requests.post(
            f'http://{DIGITAL_TWIN_IP}:{DIGITAL_TWIN_PORT}/load_network',
            json={
                'matrix': serializable_matrix,
                'nodes':  xarxa.nodes,
                'sync':   True,
            },
            timeout=10,
        )
        t_network_ms = round((time.time() - t_net_start) * 1000, 2)
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