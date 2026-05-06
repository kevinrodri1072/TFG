"""
Combine multiple scalability test runs into a single chart with error bars.
Usage: python3 plot_combined.py run1.csv run2.csv [run3.csv ...]
"""

import csv
import sys
import os
import numpy as np
import matplotlib.pyplot as plt

if len(sys.argv) < 2:
    print('Usage: python3 plot_combined.py <folder/> OR run1.csv run2.csv ...')
    sys.exit(1)

# If single argument and it's a folder, load all CSVs inside
if len(sys.argv) == 2 and os.path.isdir(sys.argv[1]):
    folder = sys.argv[1]
    csv_files = sorted([
        os.path.join(folder, f)
        for f in os.listdir(folder)
        if f.endswith('.csv')
    ])
    if not csv_files:
        print(f'❌ No CSV files found in {folder}')
        sys.exit(1)
    print(f'📂 Found {len(csv_files)} CSV files in {folder}')
else:
    csv_files = sys.argv[1:]

def f(row, key):
    v = row.get(key, '')
    return float(v) if v else None

# Load all runs
all_runs = []
for csv_file in csv_files:
    run = {}
    with open(csv_file) as file:
        reader = csv.DictReader(file)
        for row in reader:
            cp = int(row['checkpoint'])
            run[cp] = {
                'op_avg':    f(row, 'op_avg_ms'),
                'local_avg': f(row, 'local_avg_ms'),
                'net_avg':   f(row, 'network_avg_ms'),
                'net_min':   f(row, 'network_min_ms'),
                'net_max':   f(row, 'network_max_ms'),
                'jitter':    f(row, 'network_jitter_ms'),
            }
    all_runs.append(run)
    print(f'✅ Loaded {csv_file}')

# Get common checkpoints
checkpoints = sorted(set.intersection(*[set(r.keys()) for r in all_runs]))
print(f'📊 Checkpoints: {checkpoints}')

def combined(key):
    """For each checkpoint, get mean and std across runs."""
    means, stds, mins, maxs = [], [], [], []
    for cp in checkpoints:
        vals = [r[cp][key] for r in all_runs if r[cp][key] is not None]
        means.append(np.mean(vals))
        stds.append(np.std(vals))
        mins.append(np.min(vals))
        maxs.append(np.max(vals))
    return np.array(means), np.array(stds), np.array(mins), np.array(maxs)

local_avg, local_std, _, _       = combined('local_avg')
net_avg,   net_std,   net_min, net_max = combined('net_avg')
jitter,    jitter_std, _, _       = combined('jitter')
op_avg,    op_std,    _, _        = combined('op_avg')

n_runs = len(all_runs)
fig, axes = plt.subplots(2, 2, figsize=(14, 10))
fig.suptitle(
    f'Digital Twin Network — Scalability Test ({n_runs} runs)',
    fontsize=15, fontweight='bold'
)

def plot_with_errorbars(ax, y, yerr, color, title, ylabel, label='Avg'):
    ax.errorbar(checkpoints, y, yerr=yerr, fmt='-o', color=color,
                linewidth=2, capsize=5, label=f'{label} ± std ({n_runs} runs)')
    ax.fill_between(checkpoints, y - yerr, y + yerr, alpha=0.2, color=color)
    ax.set_xlabel('Number of nodes')
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend()
    ax.grid(True, alpha=0.3)
    ax.set_xticks(checkpoints)

plot_with_errorbars(axes[0][0], local_avg, local_std, 'blue',
    '🖥 Local Mininet Time (Original)', 'Time (ms)')

plot_with_errorbars(axes[0][1], net_avg, net_std, 'green',
    '🌐 Network Round-trip (HTTP)', 'Time (ms)')

plot_with_errorbars(axes[1][0], jitter, jitter_std, 'red',
    '📶 Network Jitter', 'Jitter (ms)')

plot_with_errorbars(axes[1][1], op_avg, op_std, 'purple',
    '⏱ Total Operation Time (script)', 'Time (ms)')

plt.tight_layout()

output = os.path.join(sys.argv[1], 'scalability_combined.png') \
    if os.path.isdir(sys.argv[1]) else 'scalability_combined.png'
plt.savefig(output, dpi=150, bbox_inches='tight')
print(f'\n✅ Combined graph saved to {output}')
plt.show()