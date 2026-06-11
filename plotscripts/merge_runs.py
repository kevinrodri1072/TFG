"""
merge_runs.py — Fusió de múltiples runs del sync_latency_test2
==============================================================
Agrupa per (op_type, op_name) i calcula la mitjana de totes les
columnes numèriques. El CSV resultant té el mateix format que els
originals i és directament compatible amb generate_plots.

Ús:
    python3 merge_runs.py run1.csv run2.csv run3.csv
    python3 merge_runs.py run1.csv run2.csv run3.csv -o merged.csv
"""

import argparse
import csv
import importlib.util
import statistics
import sys
from collections import defaultdict
from pathlib import Path

NUMERIC_COLS = [
    'n_nodes',
    't_local_ms',
    't_network_ms',
    't_twin_ms',
    't_total_ms',
    'capacity_ops_s',
    'payload_bytes',
    'throughput_bps',
    'cpu_percent',
    'system_mem_mb',
    'frr_total_mb',
]

STRING_COLS = [
    'op_type',
    'op_name',
    'routing_mode',
    'error',
]


def load_csv(path):
    rows = []
    with open(path, newline='') as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def parse_float(val):
    """Return float or None for empty / non-numeric values."""
    if val is None or str(val).strip() == '':
        return None
    try:
        return float(val)
    except ValueError:
        return None


def merge(csv_paths):
    # groups[(op_type, op_name)] = list of rows (one per run)
    groups   = defaultdict(list)
    # Preserve insertion order for final output
    order    = []
    seen_key = set()
    # routing_mode is the same across all runs — take from first file
    routing_mode_global = 'unknown'

    for path in csv_paths:
        rows = load_csv(path)
        for row in rows:
            key = (row['op_type'], row['op_name'])
            if key not in seen_key:
                seen_key.add(key)
                order.append(key)
            groups[key].append(row)
            if row.get('routing_mode'):
                routing_mode_global = row['routing_mode']

    merged_rows = []
    for key in order:
        run_rows = groups[key]
        op_type, op_name = key

        merged = {
            'op_type':      op_type,
            'op_name':      op_name,
            'routing_mode': routing_mode_global,
            'error':        '',
        }

        for col in NUMERIC_COLS:
            vals = [parse_float(r.get(col)) for r in run_rows]
            vals = [v for v in vals if v is not None]
            if vals:
                merged[col] = round(statistics.mean(vals), 2)
            else:
                merged[col] = ''

        # Flag rows where at least one run had an error
        errors = [r.get('error', '') for r in run_rows if r.get('error', '')]
        if errors:
            merged['error'] = f'partial ({len(errors)}/{len(run_rows)} runs failed)'

        merged_rows.append(merged)

    return merged_rows, routing_mode_global


def write_csv(rows, path):
    if not rows:
        print('No rows to write.')
        return
    fieldnames = STRING_COLS + NUMERIC_COLS
    # Keep only fieldnames that actually exist in rows
    fieldnames = [f for f in fieldnames if f in rows[0]]
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(
        description='Merge multiple sync_latency_test2 CSV runs into one averaged CSV.'
    )
    parser.add_argument('csvfiles', nargs='+', metavar='CSV',
                        help='Input CSV files (at least 2)')
    parser.add_argument('-o', '--output', default=None,
                        help='Output CSV path (default: merged_<routing_mode>.csv)')
    args = parser.parse_args()

    if len(args.csvfiles) < 2:
        print('Error: provide at least 2 CSV files to merge.')
        sys.exit(1)

    missing = [p for p in args.csvfiles if not Path(p).exists()]
    if missing:
        print(f'Error: file(s) not found: {", ".join(missing)}')
        sys.exit(1)

    print(f'Merging {len(args.csvfiles)} runs...')
    merged_rows, routing_mode = merge(args.csvfiles)

    out_path = args.output or f'merged_{routing_mode}.csv'
    write_csv(merged_rows, out_path)

    ok    = sum(1 for r in merged_rows if not r.get('error'))
    total = len(merged_rows)
    print(f'  {total} operations merged  ({ok} clean, {total - ok} with partial failures)')
    print(f'  Routing mode : {routing_mode}')
    print(f'  Output       : {out_path}')

    # ── Generate plot ────────────────────────────────────────────────────────
    # Load generate_plots from sync_latency_test2.py (same directory)
    script_dir = Path(__file__).parent
    slt_path   = script_dir / 'sync_latency_test2.py'
    if not slt_path.exists():
        print(f'  Warning: sync_latency_test2.py not found at {slt_path} — skipping plot.')
        return

    import importlib.util
    spec = importlib.util.spec_from_file_location('slt', slt_path)
    slt  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(slt)

    # Convert string values back to float/None for generate_plots
    plot_rows = []
    for r in merged_rows:
        pr = dict(r)
        for col in NUMERIC_COLS:
            v = pr.get(col)
            pr[col] = float(v) if v not in (None, '') else None
        plot_rows.append(pr)

    valid_rows = [r for r in plot_rows if r.get('t_total_ms') is not None]
    if not valid_rows:
        print('  Warning: no valid rows to plot.')
        return

    plot_path     = str(out_path).replace('.csv', '.png')
    slt.PLOT_FILE = plot_path
    slt.generate_plots(valid_rows, routing_mode)
    print(f'  Plot         : {plot_path}')


if __name__ == '__main__':
    main()