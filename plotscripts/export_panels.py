"""
export_panels.py — Regenera els panels individuals a partir d'un CSV ja existent.

Ús:
    python3 export_panels.py merged_ospf.csv
    python3 export_panels.py merged_manual.csv
    python3 export_panels.py merged_ospf.csv merged_manual.csv  (els dos a la vegada)
"""

import csv
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')

# Importa generate_plots del script principal
import importlib.util
spec = importlib.util.spec_from_file_location(
    'slt', Path(__file__).parent / 'sync_latency_test.py'
)
slt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(slt)


def load_csv(path):
    rows = []
    with open(path, newline='') as f:
        for r in csv.DictReader(f):
            row = {}
            for k, v in r.items():
                try:
                    row[k] = float(v) if v not in ('', None) else None
                except (ValueError, TypeError):
                    row[k] = v if v not in ('', None) else None
            rows.append(row)
    return rows


for csv_path in sys.argv[1:]:
    p = Path(csv_path)
    rows = load_csv(p)
    valid = [r for r in rows if r.get('t_total_ms') is not None]
    if not valid:
        print(f'No valid rows in {p.name}, skipping.')
        continue
    routing_mode = valid[0].get('routing_mode') or 'unknown'
    slt.PLOT_FILE = str(p.with_suffix('.png'))
    print(f'\nGenerating panels for {p.name} (mode={routing_mode})...')
    slt.generate_plots(valid, routing_mode)