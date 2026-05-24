"""
utils.py — Shared helper functions used across all route modules.
"""

import re
import threading
import psutil


# ── Ping locks (one per source node to avoid concurrent pings) ──

_ping_locks      = {}
_ping_locks_lock = threading.Lock()

def get_ping_lock(node):
    """Return (creating if needed) a per-node lock for ping operations."""
    with _ping_locks_lock:
        if node not in _ping_locks:
            _ping_locks[node] = threading.Lock()
        return _ping_locks[node]


# ── Measurement helpers ──

_PING_RE = re.compile(
    r'rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)'
)

def parse_ping(output):
    """
    Parse the RTT summary line from ping output.
    Returns (latency_dict, jitter) where latency_dict has min/avg/max keys.
    All values are floats in ms, or None if the line was not found.
    """
    latency = {'min': None, 'avg': None, 'max': None}
    jitter  = None
    match   = _PING_RE.search(output)
    if match:
        latency['min'] = float(match.group(1))
        latency['avg'] = float(match.group(2))
        latency['max'] = float(match.group(3))
        jitter         = float(match.group(4))
    return latency, jitter


def measure_bandwidth(src_node, dst_node, dst_ip, iterations=3):
    """
    Run iperf between src_node and dst_node for `iterations` rounds of 1 s each.
    Returns a dict with min/avg/max in Mbps, or all-None on failure.
    """
    import time
    result = {'min': None, 'avg': None, 'max': None}
    bw_values = []
    try:
        dst_node.cmd('pkill -f iperf 2>/dev/null; sleep 0.2')
        dst_node.sendCmd('iperf -s')
        time.sleep(0.5)
        for _ in range(iterations):
            out      = src_node.cmd(f'iperf -c {dst_ip} -t 1 -f m')
            bw_match = re.search(r'([\d.]+)\s+Mbits/sec', out)
            if bw_match:
                bw_values.append(float(bw_match.group(1)))
        dst_node.sendInt()
        dst_node.waitOutput()
    except Exception as e:
        print(f'iperf error: {e}')
        try:
            dst_node.sendInt()
            dst_node.waitOutput()
        except Exception:
            pass

    if bw_values:
        result['min'] = round(min(bw_values), 2)
        result['avg'] = round(sum(bw_values) / len(bw_values), 2)
        result['max'] = round(max(bw_values), 2)
    return result


def safe_stats(values):
    """
    Compute min/avg/max of a list, ignoring None entries.
    Returns a dict with all three keys; values are None when the list is empty.
    """
    values = [v for v in values if v is not None]
    if not values:
        return {'min': None, 'avg': None, 'max': None}
    return {
        'min': round(min(values), 2),
        'avg': round(sum(values) / len(values), 2),
        'max': round(max(values), 2),
    }


def jitter_of(values):
    """Average of consecutive absolute differences (ignores None entries)."""
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return 0.0
    diffs = [abs(values[i] - values[i - 1]) for i in range(1, len(values))]
    return round(sum(diffs) / len(diffs), 2)


def system_stats():
    """Return current CPU and RAM usage as a dict."""
    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory()
    return {
        'cpu_percent':  cpu,
        'ram_used_mb':  round(ram.used  / 1024 / 1024, 1),
        'ram_total_mb': round(ram.total / 1024 / 1024, 1),
        'ram_percent':  ram.percent,
    }
