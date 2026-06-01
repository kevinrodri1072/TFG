"""
utils.py — Funcions auxiliars compartides per tots els mòduls de routes/.

Conté:
  - Ping locks per-node: evita pings concurrents al mateix shell bash
  - parse_ping: extreu min/avg/max/jitter de la sortida de ping
  - measure_bandwidth: executa iperf entre dos nodes Mininet
  - safe_stats / jitter_of: estadístiques robustes amb valors None
  - system_stats: CPU i RAM del host via psutil
"""

import re
import threading
import psutil


# ─────────────────────────────────────────────────────────────────────────────
# PING LOCKS PER NODE
#
# PROBLEMA: node.cmd('ping...') envia la comanda al shell bash del node via pipe.
# Si dos threads criden node.cmd() al mateix node simultàniament, Mininet llença
# AssertionError("shell and not self.waiting"). Per evitar-ho, cada node té el seu
# propi Lock que serialitza les crides.
#
# _ping_locks_lock protegeix el diccionari de locks (creació concurrent).
# ─────────────────────────────────────────────────────────────────────────────
_ping_locks      = {}
_ping_locks_lock = threading.Lock()

def get_ping_lock(node):
    """Retorna (creant si cal) un Lock per al node indicat."""
    with _ping_locks_lock:
        if node not in _ping_locks:
            _ping_locks[node] = threading.Lock()
        return _ping_locks[node]


# ─────────────────────────────────────────────────────────────────────────────
# PARSEIG DE PING
# ─────────────────────────────────────────────────────────────────────────────

# Regex que extreu els 4 valors de la línia de resum de ping:
# "rtt min/avg/max/mdev = 0.123/0.456/0.789/0.012 ms"
_PING_RE = re.compile(
    r'rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)'
)

def parse_ping(output):
    """
    Parseja la sortida de 'ping' i retorna (latency_dict, jitter).
    latency_dict té claus min/avg/max amb valors float en ms, o None si no hi ha resposta.
    jitter = mdev (desviació mitjana) en ms.
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


# ─────────────────────────────────────────────────────────────────────────────
# MESURA D'AMPLE DE BANDA (iperf)
# ─────────────────────────────────────────────────────────────────────────────

def measure_bandwidth(src_node, dst_node, dst_ip, iterations=3,
                      protocol='tcp', duration=1, parallel=1,
                      bandwidth_mbps=None, reverse=False):
    """
    Executa iperf entre dos nodes Mininet per mesurar l'ample de banda.

    Paràmetres:
      iterations    : nombre de runs d'iperf (els resultats es fan la mitjana)
      protocol      : 'tcp' o 'udp'
      duration      : segons per run d'iperf (per defecte 1)
      parallel      : streams paral·lels (-P)
      bandwidth_mbps: ample de banda objectiu en Mbps (UDP, -b)
      reverse       : mesura en sentit invers (-R, server→client)

    Retorna un dict amb min/avg/max en Mbps.
    """
    import time

    # Construeix els flags de iperf
    flags = []
    if protocol == 'udp':
        flags.append('-u')
        if bandwidth_mbps:
            flags.append(f'-b {bandwidth_mbps}M')
    if parallel > 1:
        flags.append(f'-P {parallel}')
    if reverse:
        flags.append('-R')

    flags_str = ' '.join(flags)
    cmd_str   = f'iperf -c {dst_ip} -t {duration} -f m {flags_str}'.strip()
    srv_flags = '-u' if protocol == 'udp' else ''

    result = {'min': None, 'avg': None, 'max': None, 'cmd': cmd_str}
    bw_values = []
    try:
        # Mata qualsevol iperf anterior al node destí i arrenca el servidor
        dst_node.cmd('pkill -f iperf 2>/dev/null; sleep 0.2')
        dst_node.sendCmd(f'iperf -s {srv_flags}')   # sendCmd no bloqueja (servidor en background)
        time.sleep(0.5)   # espera que el servidor iperf estigui llest
        for _ in range(iterations):
            out      = src_node.cmd(cmd_str)
            # Extreu el valor d'ample de banda de la sortida iperf
            bw_match = re.search(r'([\d.]+)\s+Mbits/sec', out)
            if bw_match:
                bw_values.append(float(bw_match.group(1)))
        # Para el servidor iperf enviant SIGINT
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


# ─────────────────────────────────────────────────────────────────────────────
# ESTADÍSTIQUES ROBUSTES
# ─────────────────────────────────────────────────────────────────────────────

def safe_stats(values):
    """
    Calcula min/avg/max d'una llista ignorant els valors None.
    Retorna un dict amb les tres claus; tots None si la llista és buida.
    Usada a /metrics/sync per calcular estadístiques de latència.
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
    """
    Calcula el jitter com la mitjana de les diferències absolutes consecutives.
    Ignora valors None.
    """
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return 0.0
    diffs = [abs(values[i] - values[i - 1]) for i in range(1, len(values))]
    return round(sum(diffs) / len(diffs), 2)


def system_stats():
    """
    Retorna CPU i RAM actuals del host via psutil.
    interval=0.5 mesura el CPU durant 500ms per obtenir un valor més precís.
    """
    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory()
    return {
        'cpu_percent':  cpu,
        'ram_used_mb':  round(ram.used  / 1024 / 1024, 1),
        'ram_total_mb': round(ram.total / 1024 / 1024, 1),
        'ram_percent':  ram.percent,
    }