"""
structure_benchmark.py
======================
Benchmarks three graph data structures used to represent network topologies:

  1. Adjacency Matrix  — N×N matrix (current implementation in xarxa.py)
  2. Adjacency List    — dict of sets (alternative)
  3. Incidence Matrix  — N×E matrix where E = number of edges (alternative)

Network size N counts only hosts and routers (not switches).
Switches are layer-2 transparent elements and are not considered
network nodes in the Digital Twin project.

Operations measured:
  - add_node        : add a new host node connected to a router
  - remove_node     : remove an existing host node
  - get_neighbors   : find all direct neighbours of a router
  - change_property : update a node attribute (e.g. IP address)
  - serialize       : save to .mat file
  - deserialize     : load from .mat file

Each operation is repeated REPS times and the median is reported.

Network sizes: powers of 2 from 2 to 128 (hosts + routers only).
"""

import copy
import os
import tempfile
import time
from statistics import median

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import scipy.io as sio
import csv
from datetime import datetime

# ── Configuration ──────────────────────────────────────────────────────────────
# SIZES defined in fixed topology section above
REPS  = 1000
OUT_DIR  = os.path.dirname(os.path.abspath(__file__))
TS       = datetime.now().strftime('%Y%m%d_%H%M%S')
CSV_FILE  = os.path.join(OUT_DIR, f'structure_benchmark_{TS}.csv')
PLOT_FILE = os.path.join(OUT_DIR, f'structure_benchmark_{TS}.png')


# ── Synthetic network generator ────────────────────────────────────────────────

# ── Fixed network topology (same as scalability_test.py) ──────────────────────
# This is the exact same topology used in the scalability test, ensuring
# that both studies are directly comparable.
# Starts from the default network (h1-h5, r1, r2) and grows to 128 nodes.

DEFAULT_NODES = ['h1','h2','h3','h4','h5','r1','r2']

FIXED_SEQUENCE = [
    # ── 7 → 8 ────────────────────────────────────────────────────────────────
    {'type': 'host',   'name': 'h6',   'router': 'r1'},
    # ── 8 → 16 ───────────────────────────────────────────────────────────────
    {'type': 'router', 'name': 'r3',   'connected_routers': ['r1']},
    {'type': 'host',   'name': 'h7',   'router': 'r2'},
    {'type': 'host',   'name': 'h8',   'router': 'r2'},
    {'type': 'router', 'name': 'r4',   'connected_routers': ['r2']},
    {'type': 'host',   'name': 'h9',   'router': 'r3'},
    {'type': 'host',   'name': 'h10',  'router': 'r3'},
    {'type': 'host',   'name': 'h11',  'router': 'r4'},
    {'type': 'host',   'name': 'h12',  'router': 'r4'},
    # ── 16 → 32 ──────────────────────────────────────────────────────────────
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
    # ── 32 → 64 ──────────────────────────────────────────────────────────────
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
    # ── 64 → 128 ─────────────────────────────────────────────────────────────
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
    # ── 128 → 256 ────────────────────────────────────────────────────────────────
    {'type': 'router', 'name': 'r33',  'connected_routers': ['r2', 'r18']},
    {'type': 'router', 'name': 'r34',  'connected_routers': ['r9']},
    {'type': 'host',   'name': 'h122', 'router': 'r7'},
    {'type': 'host',   'name': 'h123', 'router': 'r6'},
    {'type': 'host',   'name': 'h124', 'router': 'r28'},
    {'type': 'host',   'name': 'h125', 'router': 'r3'},
    {'type': 'host',   'name': 'h126', 'router': 'r2'},
    {'type': 'host',   'name': 'h127', 'router': 'r6'},
    {'type': 'host',   'name': 'h128', 'router': 'r14'},
    {'type': 'host',   'name': 'h129', 'router': 'r15'},
    {'type': 'host',   'name': 'h130', 'router': 'r33'},
    {'type': 'host',   'name': 'h131', 'router': 'r2'},
    {'type': 'host',   'name': 'h132', 'router': 'r13'},
    {'type': 'host',   'name': 'h133', 'router': 'r27'},
    {'type': 'host',   'name': 'h134', 'router': 'r15'},
    {'type': 'host',   'name': 'h135', 'router': 'r29'},
    {'type': 'host',   'name': 'h136', 'router': 'r18'},
    {'type': 'host',   'name': 'h137', 'router': 'r1'},
    {'type': 'host',   'name': 'h138', 'router': 'r11'},
    {'type': 'host',   'name': 'h139', 'router': 'r28'},
    {'type': 'host',   'name': 'h140', 'router': 'r22'},
    {'type': 'host',   'name': 'h141', 'router': 'r18'},
    {'type': 'host',   'name': 'h142', 'router': 'r10'},
    {'type': 'host',   'name': 'h143', 'router': 'r14'},
    {'type': 'host',   'name': 'h144', 'router': 'r22'},
    {'type': 'host',   'name': 'h145', 'router': 'r7'},
    {'type': 'host',   'name': 'h146', 'router': 'r6'},
    {'type': 'host',   'name': 'h147', 'router': 'r25'},
    {'type': 'host',   'name': 'h148', 'router': 'r7'},
    {'type': 'host',   'name': 'h149', 'router': 'r23'},
    {'type': 'host',   'name': 'h150', 'router': 'r23'},
    {'type': 'host',   'name': 'h151', 'router': 'r17'},
    {'type': 'host',   'name': 'h152', 'router': 'r3'},
    {'type': 'host',   'name': 'h153', 'router': 'r30'},
    {'type': 'host',   'name': 'h154', 'router': 'r8'},
    {'type': 'host',   'name': 'h155', 'router': 'r25'},
    {'type': 'host',   'name': 'h156', 'router': 'r6'},
    {'type': 'host',   'name': 'h157', 'router': 'r19'},
    {'type': 'host',   'name': 'h158', 'router': 'r24'},
    {'type': 'host',   'name': 'h159', 'router': 'r13'},
    {'type': 'host',   'name': 'h160', 'router': 'r5'},
    {'type': 'host',   'name': 'h161', 'router': 'r3'},
    {'type': 'router', 'name': 'r35',  'connected_routers': ['r19', 'r6']},
    {'type': 'host',   'name': 'h162', 'router': 'r15'},
    {'type': 'host',   'name': 'h163', 'router': 'r7'},
    {'type': 'host',   'name': 'h164', 'router': 'r25'},
    {'type': 'host',   'name': 'h165', 'router': 'r18'},
    {'type': 'router', 'name': 'r36',  'connected_routers': ['r24', 'r11']},
    {'type': 'host',   'name': 'h166', 'router': 'r24'},
    {'type': 'host',   'name': 'h167', 'router': 'r23'},
    {'type': 'host',   'name': 'h168', 'router': 'r14'},
    {'type': 'host',   'name': 'h169', 'router': 'r18'},
    {'type': 'router', 'name': 'r37',  'connected_routers': ['r5', 'r11']},
    {'type': 'host',   'name': 'h170', 'router': 'r35'},
    {'type': 'host',   'name': 'h171', 'router': 'r16'},
    {'type': 'host',   'name': 'h172', 'router': 'r11'},
    {'type': 'host',   'name': 'h173', 'router': 'r30'},
    {'type': 'router', 'name': 'r38',  'connected_routers': ['r36']},
    {'type': 'host',   'name': 'h174', 'router': 'r15'},
    {'type': 'host',   'name': 'h175', 'router': 'r21'},
    {'type': 'host',   'name': 'h176', 'router': 'r4'},
    {'type': 'host',   'name': 'h177', 'router': 'r15'},
    {'type': 'router', 'name': 'r39',  'connected_routers': ['r21', 'r26']},
    {'type': 'host',   'name': 'h178', 'router': 'r18'},
    {'type': 'host',   'name': 'h179', 'router': 'r5'},
    {'type': 'host',   'name': 'h180', 'router': 'r14'},
    {'type': 'host',   'name': 'h181', 'router': 'r37'},
    {'type': 'router', 'name': 'r40',  'connected_routers': ['r21', 'r14']},
    {'type': 'host',   'name': 'h182', 'router': 'r32'},
    {'type': 'host',   'name': 'h183', 'router': 'r26'},
    {'type': 'host',   'name': 'h184', 'router': 'r30'},
    {'type': 'host',   'name': 'h185', 'router': 'r10'},
    {'type': 'router', 'name': 'r41',  'connected_routers': ['r16']},
    {'type': 'host',   'name': 'h186', 'router': 'r36'},
    {'type': 'host',   'name': 'h187', 'router': 'r35'},
    {'type': 'host',   'name': 'h188', 'router': 'r17'},
    {'type': 'host',   'name': 'h189', 'router': 'r38'},
    {'type': 'router', 'name': 'r42',  'connected_routers': ['r38', 'r26']},
    {'type': 'host',   'name': 'h190', 'router': 'r24'},
    {'type': 'host',   'name': 'h191', 'router': 'r15'},
    {'type': 'host',   'name': 'h192', 'router': 'r9'},
    {'type': 'host',   'name': 'h193', 'router': 'r33'},
    {'type': 'router', 'name': 'r43',  'connected_routers': ['r4', 'r8']},
    {'type': 'host',   'name': 'h194', 'router': 'r10'},
    {'type': 'host',   'name': 'h195', 'router': 'r41'},
    {'type': 'host',   'name': 'h196', 'router': 'r11'},
    {'type': 'host',   'name': 'h197', 'router': 'r28'},
    {'type': 'router', 'name': 'r44',  'connected_routers': ['r25', 'r39']},
    {'type': 'host',   'name': 'h198', 'router': 'r30'},
    {'type': 'host',   'name': 'h199', 'router': 'r34'},
    {'type': 'host',   'name': 'h200', 'router': 'r17'},
    {'type': 'host',   'name': 'h201', 'router': 'r36'},
    {'type': 'router', 'name': 'r45',  'connected_routers': ['r1', 'r44']},
    {'type': 'host',   'name': 'h202', 'router': 'r8'},
    {'type': 'host',   'name': 'h203', 'router': 'r44'},
    {'type': 'host',   'name': 'h204', 'router': 'r35'},
    {'type': 'host',   'name': 'h205', 'router': 'r18'},
    {'type': 'router', 'name': 'r46',  'connected_routers': ['r22', 'r8']},
    {'type': 'host',   'name': 'h206', 'router': 'r19'},
    {'type': 'host',   'name': 'h207', 'router': 'r28'},
    {'type': 'host',   'name': 'h208', 'router': 'r11'},
    {'type': 'host',   'name': 'h209', 'router': 'r30'},
    {'type': 'router', 'name': 'r47',  'connected_routers': ['r17']},
    {'type': 'host',   'name': 'h210', 'router': 'r33'},
    {'type': 'host',   'name': 'h211', 'router': 'r12'},
    {'type': 'host',   'name': 'h212', 'router': 'r33'},
    {'type': 'host',   'name': 'h213', 'router': 'r7'},
    {'type': 'router', 'name': 'r48',  'connected_routers': ['r20', 'r41']},
    {'type': 'host',   'name': 'h214', 'router': 'r33'},
    {'type': 'host',   'name': 'h215', 'router': 'r39'},
    {'type': 'host',   'name': 'h216', 'router': 'r13'},
    {'type': 'host',   'name': 'h217', 'router': 'r10'},
    {'type': 'router', 'name': 'r49',  'connected_routers': ['r11']},
    {'type': 'host',   'name': 'h218', 'router': 'r35'},
    {'type': 'host',   'name': 'h219', 'router': 'r34'},
    {'type': 'host',   'name': 'h220', 'router': 'r1'},
    {'type': 'host',   'name': 'h221', 'router': 'r39'},
    {'type': 'router', 'name': 'r50',  'connected_routers': ['r2']},
    {'type': 'host',   'name': 'h222', 'router': 'r8'},
    {'type': 'host',   'name': 'h223', 'router': 'r24'},
    {'type': 'host',   'name': 'h224', 'router': 'r20'},
    {'type': 'host',   'name': 'h225', 'router': 'r16'},
    {'type': 'router', 'name': 'r51',  'connected_routers': ['r37']},
    {'type': 'host',   'name': 'h226', 'router': 'r6'},
    {'type': 'host',   'name': 'h227', 'router': 'r6'},
    {'type': 'host',   'name': 'h228', 'router': 'r47'},
    {'type': 'host',   'name': 'h229', 'router': 'r32'},
    {'type': 'router', 'name': 'r52',  'connected_routers': ['r49', 'r35']},
]

# Checkpoints: N = hosts + routers (switches excluded)
SIZES = [2, 4, 8, 16, 32, 64, 128, 256]

def make_network(target_size):
    """
    Build a network snapshot of exactly target_size significant nodes
    (hosts + routers, switches excluded) using the same FIXED_SEQUENCE
    as scalability_test.py. This guarantees both studies use identical
    topologies and results are directly comparable.

    The default network starts with 7 nodes (h1-h5, r1, r2).
    Nodes are added one by one until target_size is reached.
    """
    # Default nodes
    nodes = {
        'h1': {'type': 'host',   'ip': '10.1.0.2/24', 'gw': '10.1.0.1'},
        'h2': {'type': 'host',   'ip': '10.1.0.3/24', 'gw': '10.1.0.1'},
        'h3': {'type': 'host',   'ip': '10.2.0.2/24', 'gw': '10.2.0.1'},
        'h4': {'type': 'host',   'ip': '10.2.0.3/24', 'gw': '10.2.0.1'},
        'h5': {'type': 'host',   'ip': '10.2.0.4/24', 'gw': '10.2.0.1'},
        'r1': {'type': 'router', 'ip': '10.1.0.1/24'},
        'r2': {'type': 'router', 'ip': '10.2.0.1/24'},
    }
    edges = [
        ('h1', 'r1'), ('h2', 'r1'),
        ('h3', 'r2'), ('h4', 'r2'), ('h5', 'r2'),
        ('r1', 'r2'),
    ]

    if target_size <= 1:
        # N=1: just one router
        return {'r1': {'type': 'router', 'ip': '10.1.0.1/24'}}, []

    if target_size == 2:
        # N=2: 1 router + 1 host
        n = {
            'r1': {'type': 'router', 'ip': '10.1.0.1/24'},
            'h1': {'type': 'host',   'ip': '10.1.0.2/24', 'gw': '10.1.0.1'},
        }
        return n, [('h1', 'r1')]

    if target_size <= 4:
        # N=3,4: 1 router + N-1 hosts
        n = {'r1': {'type': 'router', 'ip': '10.1.0.1/24'}}
        e = []
        for i in range(target_size - 1):
            hname = f'h{i+1}'
            n[hname] = {'type': 'host', 'ip': f'10.1.0.{i+2}/24', 'gw': '10.1.0.1'}
            e.append((hname, 'r1'))
        return n, e

    # Add nodes from FIXED_SEQUENCE until we reach target_size
    for op in FIXED_SEQUENCE:
        current = len(nodes)
        if current >= target_size:
            break
        if op['type'] == 'host':
            router = op['router']
            nodes[op['name']] = {
                'type': 'host',
                'ip':   '10.0.0.1/24',
                'gw':   '10.0.0.1',
            }
            edges.append((op['name'], router))
        else:
            nodes[op['name']] = {'type': 'router', 'ip': '10.0.0.1/24'}
            for cr in op['connected_routers']:
                edges.append((op['name'], cr))

    return nodes, edges

# ── Adjacency Matrix ───────────────────────────────────────────────────────────

class AdjacencyMatrix:
    """
    N×N matrix where entry (i,j) = 1 if nodes i and j are connected, 0 otherwise.
    Node properties stored separately in self.props.

    This mirrors the network_matrix used in xarxa.py, which stores the
    connection type string ('host', 'router') instead of 1.

    Memory: O(N²). Adding/removing a node requires copying the full matrix.
    """

    def __init__(self, nodes, edges):
        self.names  = list(nodes.keys())
        self.props  = copy.deepcopy(nodes)
        n           = len(self.names)
        self.matrix = [[0] * n for _ in range(n)]
        idx = {name: i for i, name in enumerate(self.names)}
        for a, b in edges:
            self.matrix[idx[a]][idx[b]] = 1
            self.matrix[idx[b]][idx[a]] = 1

    def _idx(self, name):
        return self.names.index(name)

    def add_node(self, name, props, connect_to):
        """
        Add a node and connect it to connect_to neighbours.
        Requires expanding the matrix: add one row and one column.
        Cost: O(N) — must touch every existing row to append a column.
        """
        self.names.append(name)
        self.props[name] = props
        n = len(self.names)
        for row in self.matrix:
            row.append(0)
        new_row = [0] * n
        self.matrix.append(new_row)
        new_idx = n - 1
        for neighbor in connect_to:
            ni = self._idx(neighbor)
            self.matrix[new_idx][ni] = 1
            self.matrix[ni][new_idx] = 1

    def remove_node(self, name):
        """
        Remove a node and all its edges.
        Requires shrinking the matrix: remove one row and one column.
        Cost: O(N) — must touch every row to remove the column.
        """
        idx = self._idx(name)
        self.matrix.pop(idx)
        for row in self.matrix:
            row.pop(idx)
        self.names.pop(idx)
        del self.props[name]

    def get_neighbors(self, name):
        """
        Find all direct neighbours of a node.
        Cost: O(N) — must scan the full row.
        """
        idx = self._idx(name)
        return [self.names[j] for j, v in enumerate(self.matrix[idx]) if v != 0]

    def change_property(self, name, key, value):
        """Update a node property. Cost: O(1)."""
        self.props[name][key] = value

    def serialize(self, path):
        """
        Save to .mat file.
        Converts the N×N list-of-lists to a numpy array first.
        Cost: O(N²) — must copy the full matrix to numpy.
        """
        mat = np.array(self.matrix, dtype=np.float64)
        sio.savemat(path, {'matrix': mat, 'names': self.names})

    def deserialize(self, path):
        """Load from .mat file. Cost: O(N²)."""
        return sio.loadmat(path)['matrix']


# ── Adjacency List ─────────────────────────────────────────────────────────────

class AdjacencyList:
    """
    Dict of sets: {node_name: set(neighbour_names)}.
    Node properties stored separately in self.props.

    Memory: O(N + E) where E = number of edges.
    Adding/removing a node only touches the affected entries.
    """

    def __init__(self, nodes, edges):
        self.props = copy.deepcopy(nodes)
        self.adj   = {name: set() for name in nodes}
        for a, b in edges:
            self.adj[a].add(b)
            self.adj[b].add(a)

    def add_node(self, name, props, connect_to):
        """
        Add a node and connect it to neighbours.
        Cost: O(degree) — only touches the new node and its neighbours.
        """
        self.props[name] = props
        self.adj[name]   = set()
        for neighbor in connect_to:
            self.adj[name].add(neighbor)
            self.adj[neighbor].add(name)

    def remove_node(self, name):
        """
        Remove a node and all its edges.
        Cost: O(degree) — only touches the node's neighbours.
        """
        for neighbor in list(self.adj[name]):
            self.adj[neighbor].discard(name)
        del self.adj[name]
        del self.props[name]

    def get_neighbors(self, name):
        """
        Find all direct neighbours.
        Cost: O(1) — direct dict lookup.
        """
        return list(self.adj[name])

    def change_property(self, name, key, value):
        """Update a node property. Cost: O(1)."""
        self.props[name][key] = value

    def serialize(self, path):
        """
        Save to .mat file.
        Converts adjacency list to a dense matrix for .mat compatibility.
        Cost: O(N + E).
        """
        names = list(self.adj.keys())
        idx   = {n: i for i, n in enumerate(names)}
        n     = len(names)
        mat   = np.zeros((n, n), dtype=np.float64)
        for name, neighbors in self.adj.items():
            for nb in neighbors:
                mat[idx[name]][idx[nb]] = 1.0
        sio.savemat(path, {'adj': mat, 'names': names})

    def deserialize(self, path):
        """Load from .mat file. Cost: O(N + E)."""
        return sio.loadmat(path)['adj']


# ── Incidence Matrix ───────────────────────────────────────────────────────────

class IncidenceMatrix:
    """
    N×E matrix where rows = nodes, columns = edges.
    Entry (i,e) = 1 if node i is an endpoint of edge e, else 0.
    Each column has exactly two 1s (the two endpoints of the edge).

    Memory: O(N·E). Efficient for sparse graphs where E << N².
    Adding a node adds one row and one column per new edge.
    """

    def __init__(self, nodes, edges):
        self.names  = list(nodes.keys())
        self.props  = copy.deepcopy(nodes)
        self.edges  = list(edges)
        n = len(self.names)
        e = len(self.edges)
        idx = {name: i for i, name in enumerate(self.names)}
        self.matrix = np.zeros((n, max(e, 1)), dtype=np.int8)
        for j, (a, b) in enumerate(self.edges):
            self.matrix[idx[a], j] = 1
            self.matrix[idx[b], j] = 1

    def _idx(self, name):
        return self.names.index(name)

    def add_node(self, name, props, connect_to):
        """
        Add a node and connect it to neighbours.
        Adds one row (the new node) and one column per new edge.
        Cost: O(N·degree) — matrix must be extended.
        """
        self.names.append(name)
        self.props[name] = props
        new_row = np.zeros((1, self.matrix.shape[1]), dtype=np.int8)
        self.matrix = np.vstack([self.matrix, new_row])
        new_idx = len(self.names) - 1
        for neighbor in connect_to:
            ni  = self._idx(neighbor)
            col = np.zeros((len(self.names), 1), dtype=np.int8)
            col[new_idx, 0] = 1
            col[ni,      0] = 1
            self.matrix = np.hstack([self.matrix, col])
            self.edges.append((name, neighbor))

    def remove_node(self, name):
        """
        Remove a node and all its edges (columns where node appears).
        Cost: O(N·E) — must delete rows and columns from the matrix.
        """
        idx = self._idx(name)
        cols_to_remove = [j for j, (a, b) in enumerate(self.edges)
                          if a == name or b == name]
        self.matrix = np.delete(self.matrix, cols_to_remove, axis=1)
        self.matrix = np.delete(self.matrix, idx, axis=0)
        self.edges  = [(a, b) for a, b in self.edges
                       if a != name and b != name]
        self.names.pop(idx)
        del self.props[name]

    def get_neighbors(self, name):
        """
        Find all direct neighbours.
        Scans columns where this node appears and finds the other endpoint.
        Cost: O(E).
        """
        idx  = self._idx(name)
        cols = np.where(self.matrix[idx] == 1)[0]
        neighbors = set()
        for c in cols:
            a, b = self.edges[c]
            neighbors.add(b if a == name else a)
        return list(neighbors)

    def change_property(self, name, key, value):
        """Update a node property. Cost: O(1)."""
        self.props[name][key] = value

    def serialize(self, path):
        """
        Save to .mat file.
        The N×E matrix is already numpy — direct save.
        Cost: O(N·E).
        """
        sio.savemat(path, {
            'inc': self.matrix.astype(np.float64),
            'names': self.names,
        })

    def deserialize(self, path):
        """Load from .mat file. Cost: O(N·E)."""
        return sio.loadmat(path)['inc']


# ── Benchmark engine ───────────────────────────────────────────────────────────

def measure(fn, reps=REPS):
    """Run fn() reps times, return median time in microseconds."""
    times = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1e6)
    return round(median(times), 3)


def benchmark_structure(StructClass, nodes, edges):
    """Benchmark all operations for a given structure."""
    results = {}

    # Pick stable targets for repeated operations
    hosts   = [n for n, p in nodes.items() if p['type'] == 'host']
    routers = [n for n, p in nodes.items() if p['type'] == 'router']
    target_router = routers[0]
    target_host   = hosts[-1]  # we'll remove the last host

    # ── add_node ──────────────────────────────────────────────────────────────
    # Each repetition rebuilds the structure and adds a new host to r1
    def bench_add():
        s = StructClass(nodes, edges)
        s.add_node('_new_host',
                   {'type': 'host', 'ip': '99.0.0.2/24', 'gw': '99.0.0.1'},
                   [routers[0]])

    results['add_node'] = measure(bench_add)

    # ── remove_node ───────────────────────────────────────────────────────────
    # Each repetition rebuilds and removes the last host
    def bench_remove():
        s = StructClass(nodes, edges)
        s.remove_node(target_host)

    results['remove_node'] = measure(bench_remove)

    # ── get_neighbors ─────────────────────────────────────────────────────────
    # Use a cached structure — no rebuild needed
    s_cached = StructClass(nodes, edges)

    def bench_neighbors():
        s_cached.get_neighbors(target_router)

    results['get_neighbors'] = measure(bench_neighbors)

    # ── change_property ───────────────────────────────────────────────────────
    def bench_change():
        s_cached.change_property(hosts[0], 'ip', '10.99.0.1/24')

    results['change_property'] = measure(bench_change)

    # ── serialize ─────────────────────────────────────────────────────────────
    tmp = tempfile.mktemp(suffix='.mat')

    def bench_serialize():
        s_cached.serialize(tmp)

    results['serialize'] = measure(bench_serialize)

    # ── deserialize ───────────────────────────────────────────────────────────
    s_cached.serialize(tmp)

    def bench_deserialize():
        s_cached.deserialize(tmp)

    results['deserialize'] = measure(bench_deserialize)

    try:
        os.remove(tmp)
    except Exception:
        pass

    return results


# ── Main ───────────────────────────────────────────────────────────────────────

STRUCTURES = {
    'Adjacency Matrix': AdjacencyMatrix,
    'Adjacency List':   AdjacencyList,
    'Incidence Matrix': IncidenceMatrix,
}

OPERATIONS = ['add_node', 'remove_node', 'get_neighbors',
              'change_property', 'serialize', 'deserialize']

COLORS = {
    'Adjacency Matrix': '#e74c3c',
    'Adjacency List':   '#27ae60',
    'Incidence Matrix': '#3498db',
}

OP_LABELS = {
    'add_node':        'Add Node',
    'remove_node':     'Remove Node',
    'get_neighbors':   'Get Neighbors',
    'change_property': 'Change Property',
    'serialize':       'Serialize (.mat)',
    'deserialize':     'Deserialize (.mat)',
}


def main():
    print('=' * 62)
    print('  Data Structure Benchmark — Digital Twin Network')
    print(f'  Sizes (hosts + routers): {SIZES}')
    print(f'  Repetitions per measurement: {REPS}')
    print('  Note: switches are NOT counted in network size')
    print('=' * 62)

    all_results = {s: {op: [] for op in OPERATIONS} for s in STRUCTURES}
    csv_rows    = []

    for size in SIZES:
        print(f'\n▶ N = {size} nodes (hosts + routers)', flush=True)
        nodes, edges = make_network(size)
        n_routers = sum(1 for p in nodes.values() if p['type'] == 'router')
        n_hosts   = sum(1 for p in nodes.values() if p['type'] == 'host')
        print(f'  ({n_routers} routers, {n_hosts} hosts, {len(edges)} edges)')

        for sname, StructClass in STRUCTURES.items():
            print(f'  [{sname:20s}] ', end='', flush=True)
            try:
                res = benchmark_structure(StructClass, nodes, edges)
                for op in OPERATIONS:
                    all_results[sname][op].append(res[op])
                print(' | '.join(f'{OP_LABELS[op]}={res[op]:.1f}µs'
                                 for op in OPERATIONS))
                csv_rows.append({
                    'size':      size,
                    'structure': sname,
                    **res,
                })
            except Exception as ex:
                print(f'ERROR: {ex}')
                for op in OPERATIONS:
                    all_results[sname][op].append(None)

    # ── Save CSV ──────────────────────────────────────────────────────────────
    with open(CSV_FILE, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['size', 'structure'] + OPERATIONS)
        writer.writeheader()
        writer.writerows(csv_rows)
    print(f'\n✅ CSV saved → {CSV_FILE}')

    # ── Plot ──────────────────────────────────────────────────────────────────
    fig, axes = plt.subplots(2, 3, figsize=(15, 9))
    axes = axes.flatten()

    for ax, op in zip(axes, OPERATIONS):
        for sname, color in COLORS.items():
            times = all_results[sname][op]
            valid = [(SIZES[i], t) for i, t in enumerate(times) if t is not None]
            if valid:
                xs, ys = zip(*valid)
                ax.plot(xs, ys, 'o-', label=sname, color=color,
                        linewidth=2, markersize=5)

        ax.set_title(OP_LABELS[op], fontsize=12, fontweight='bold')
        ax.set_xlabel('Network size N (hosts + routers)', fontsize=10)
        ax.set_ylabel('Time (µs) — median of 1000 runs', fontsize=10)
        ax.set_xscale('log', base=2)
        ax.set_xticks(SIZES)
        ax.set_xticklabels(SIZES)
        ax.legend(fontsize=9)
        ax.grid(True, alpha=0.3)
        ax.set_ylim(bottom=0)

    fig.suptitle(
        'Data Structure Benchmark — Digital Twin Network\n'
        'Adjacency Matrix  vs  Adjacency List  vs  Incidence Matrix\n'
        '(network size = hosts + routers, switches excluded)',
        fontsize=13, fontweight='bold'
    )
    plt.tight_layout()
    plt.savefig(PLOT_FILE, dpi=150, bbox_inches='tight')
    print(f'✅ Plot saved  → {PLOT_FILE}')
    print('\n🏁 Benchmark complete!')


if __name__ == '__main__':
    main()