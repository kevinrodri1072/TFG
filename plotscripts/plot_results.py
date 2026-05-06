"""
Plot scalability test results from CSV.
Usage: python3 plot_results.py scalability_XXXXXXXX.csv
"""

import csv
import sys
import matplotlib.pyplot as plt

if len(sys.argv) < 2:
    print('Usage: python3 plot_results.py <csv_file>')
    sys.exit(1)

csv_file = sys.argv[1]

checkpoints = []
op_avg, op_min, op_max           = [], [], []
local_avg, local_min, local_max  = [], [], []
net_avg, net_min, net_max        = [], [], []
jitter                           = []

def f(row, key):
    v = row.get(key, '')
    return float(v) if v else None

with open(csv_file) as file:
    reader = csv.DictReader(file)
    for row in reader:
        checkpoints.append(int(row['checkpoint']))
        op_avg.append(f(row, 'op_avg_ms'))
        op_min.append(f(row, 'op_min_ms'))
        op_max.append(f(row, 'op_max_ms'))
        local_avg.append(f(row, 'local_avg_ms'))
        local_min.append(f(row, 'local_min_ms'))
        local_max.append(f(row, 'local_max_ms'))
        net_avg.append(f(row, 'network_avg_ms'))
        net_min.append(f(row, 'network_min_ms'))
        net_max.append(f(row, 'network_max_ms'))
        jitter.append(f(row, 'network_jitter_ms'))

fig, axes = plt.subplots(2, 2, figsize=(14, 10))
fig.suptitle('Digital Twin Network — Scalability Test', fontsize=15, fontweight='bold')

# ── Plot 1: Local Mininet time ──
ax1 = axes[0][0]
ax1.plot(checkpoints, local_avg, 'b-o', label='Avg', linewidth=2)
ax1.fill_between(checkpoints, local_min, local_max, alpha=0.2, color='blue', label='Min/Max range')
ax1.set_xlabel('Number of nodes')
ax1.set_ylabel('Time (ms)')
ax1.set_title('🖥 Local Mininet Time (Original)')
ax1.legend()
ax1.grid(True, alpha=0.3)
ax1.set_xticks(checkpoints)

# ── Plot 2: Network round-trip ──
ax2 = axes[0][1]
ax2.plot(checkpoints, net_avg, 'g-o', label='Avg', linewidth=2)
ax2.fill_between(checkpoints, net_min, net_max, alpha=0.2, color='green', label='Min/Max range')
ax2.set_xlabel('Number of nodes')
ax2.set_ylabel('Time (ms)')
ax2.set_title('🌐 Network Round-trip (HTTP)')
ax2.legend()
ax2.grid(True, alpha=0.3)
ax2.set_xticks(checkpoints)

# ── Plot 3: Network jitter ──
ax3 = axes[1][0]
ax3.plot(checkpoints, jitter, 'r-o', label='Jitter', linewidth=2)
ax3.set_xlabel('Number of nodes')
ax3.set_ylabel('Jitter (ms)')
ax3.set_title('📶 Network Jitter')
ax3.legend()
ax3.grid(True, alpha=0.3)
ax3.set_xticks(checkpoints)

# ── Plot 4: Operation time (total measured by script) ──
ax4 = axes[1][1]
ax4.plot(checkpoints, op_avg, 'm-o', label='Avg', linewidth=2)
ax4.fill_between(checkpoints, op_min, op_max, alpha=0.2, color='purple', label='Min/Max range')
ax4.set_xlabel('Number of nodes')
ax4.set_ylabel('Time (ms)')
ax4.set_title('⏱ Total Operation Time (script)')
ax4.legend()
ax4.grid(True, alpha=0.3)
ax4.set_xticks(checkpoints)

plt.tight_layout()
output = csv_file.replace('.csv', '.png')
plt.savefig(output, dpi=150, bbox_inches='tight')
print(f'✅ Graph saved to {output}')
plt.show()