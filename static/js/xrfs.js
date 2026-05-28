var socket = io('http://localhost:5001');

// ── WebSocket: latency matrix progress ──
socket.on('chaos_progress', function(d) {
    var wrap = document.getElementById('chaos-progress-wrap');
    var bar  = document.getElementById('chaos-progress-bar');
    var msg  = document.getElementById('chaos-progress-msg');
    var pct  = document.getElementById('chaos-progress-pct');
    if (!wrap) return;
    wrap.style.display = 'block';
    if (bar) { bar.style.width = d.percent + '%'; bar.style.background = d.percent < 100 ? '#e74c3c' : '#27ae60'; }
    if (msg) msg.textContent = d.msg;
    if (pct) pct.textContent = d.percent + '%';
});

socket.on('latency_matrix_progress', function(d) {
    var bar  = document.getElementById('latency-progress-bar');
    var msg  = document.getElementById('latency-progress-msg');
    var pct  = document.getElementById('latency-progress-pct');
    var wrap = document.getElementById('latency-progress-wrap');
    if (!bar) return;
    if (wrap) wrap.style.display = 'block';
    if (bar)  bar.style.width   = d.percent + '%';
    if (bar)  bar.style.background = d.percent < 100 ? '#3498db' : '#27ae60';
    if (msg)  msg.textContent   = d.msg;
    if (pct)  pct.textContent   = d.percent + '%';
});

/**
 * xrfs.js — Standalone XRF page logic.
 *
 * Manages the XRF list (deploy / undeploy / query) and renders
 * results for each XRF type (neighbors, hops, traffic, chaos, latency_matrix).
 */

var xrfData      = {};
var topologyData = null;

// ── Initialisation ──

fetch('/topology')
    .then(r => r.json())
    .then(data => { topologyData = data; });

loadXRFStatus();
setInterval(loadXRFStatus, 10000);


// ── XRF list management ──

function loadXRFStatus() {
    fetch('/xrf/status')
        .then(r => r.json())
        .then(data => {
            if (!data.ok) return;
            xrfData = data.xrfs;
            renderXRFList();
        });
}

function renderXRFList() {
    var list = document.getElementById('xrf-list');
    if (!list) return;
    list.innerHTML = '';
    for (var id in xrfData) {
        var xrf     = xrfData[id];
        var running = xrf.status === 'running';
        var dot     = running ? '🟢' : '🔴';
        var btns    = running
            ? '<button onclick="undeployXRF(\'' + id + '\')" class="btn btn-undeploy">Undeploy</button> ' +
              '<button onclick="showXRFResult(\'' + id + '\')" class="btn btn-query">Query</button>'
            : '<button onclick="deployXRF(\'' + id + '\')" class="btn btn-deploy">Deploy</button>';
        list.innerHTML +=
            '<div class="xrf-item">' +
            '  <div>' +
            '    <div class="xrf-name">' + dot + ' ' + xrf.name + '</div>' +
            '    <div class="xrf-desc">' + xrf.description + '</div>' +
            '  </div>' +
            '  <div style="display:flex; gap:6px;">' + btns + '</div>' +
            '</div>';
    }
}

function deployXRF(id) {
    fetch('/xrf/deploy', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({xrf: id})})
    .then(r => r.json())
    .then(data => {
        if (!data.ok) { alert('Error: ' + data.error); return; }
        setTimeout(loadXRFStatus, 3000);
    });
}

function undeployXRF(id) {
    if (!confirm('Undeploy ' + xrfData[id].name + '?')) return;
    fetch('/xrf/undeploy', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({xrf: id})})
    .then(r => r.json())
    .then(data => {
        if (!data.ok) { alert('Error: ' + data.error); return; }
        document.getElementById('xrf-result').style.display = 'none';
        setTimeout(loadXRFStatus, 2000);
    });
}


// ── Show result panel ──

function toggleMaxHops() {
    var dst = document.getElementById('hops-dst').value;
    document.getElementById('hops-max-group').style.display = dst ? 'none' : 'flex';
}

function showXRFResult(id) {
    // Stop live traffic if switching away
    if (id !== 'traffic') stopTrafficLive();
    document.getElementById('xrf-result').style.display = 'block';
    document.getElementById('xrf-result-title').textContent = xrfData[id].name + ' — Results';
    document.getElementById('xrf-neighbors-params').style.display = id === 'neighbors' ? 'block' : 'none';
    document.getElementById('xrf-hops-params').style.display      = id === 'hops'      ? 'block' : 'none';
    document.getElementById('xrf-traffic-params').style.display   = id === 'traffic'   ? 'block' : 'none';
    if (id === 'traffic') { setTimeout(startTrafficLive, 100); }
    document.getElementById('xrf-chaos-params').style.display          = id === 'chaos'          ? 'block' : 'none';
    document.getElementById('xrf-latency-matrix-params').style.display = id === 'latency_matrix' ? 'block' : 'none';
    document.getElementById('xrf-result-content').innerHTML = '';

    if (topologyData) {
        var all     = Object.keys(topologyData.nodes).filter(function(n) { return topologyData.nodes[n].type !== 'switch'; });
        var routers = Object.keys(topologyData.nodes).filter(function(n) { return topologyData.nodes[n].type === 'router'; });
        var hosts   = Object.keys(topologyData.nodes).filter(function(n) { return topologyData.nodes[n].type === 'host'; });

        var nsel = document.getElementById('neighbors-node');
        nsel.innerHTML = '<option value="">All nodes</option>';
        all.forEach(function(n) { nsel.innerHTML += '<option value="' + n + '">' + n + '</option>'; });

        ['hops-src','hops-dst'].forEach(function(selId, i) {
            var sel = document.getElementById(selId);
            var opts = i === 1 ? '<option value="">— Any (use max hops) —</option>' : '';
            all.forEach(function(n) { opts += '<option value="' + n + '">' + n + '</option>'; });
            sel.innerHTML = opts;
        });

        var tsel = document.getElementById('traffic-node');
        tsel.innerHTML = '<option value="">-- Select a node --</option>';
        all.forEach(function(n) { tsel.innerHTML += '<option value="' + n + '">' + n + '</option>'; });
        // Auto-select first node
        if (all.length > 0) tsel.value = all[0];

        var cnode = document.getElementById('chaos-node');
        cnode.innerHTML = '';
        routers.forEach(function(n) { cnode.innerHTML += '<option value="' + n + '">' + n + '</option>'; });

        var csrc = document.getElementById('chaos-src');
        csrc.innerHTML = '';
        hosts.forEach(function(n) { csrc.innerHTML += '<option value="' + n + '">' + n + '</option>'; });

        var cdst = document.getElementById('chaos-dst');
        cdst.innerHTML = '';
        hosts.forEach(function(n, i) { cdst.innerHTML += '<option value="' + n + '"' + (i===1?' selected':'') + '>' + n + '</option>'; });
    }
    // User must click Run/Query manually — no auto-trigger
}


// ── Render functions ──

function renderChaos(data) {
    var r = data.result ? data.result : data;
    if (!r.ok) return '<p style="color:#e74c3c;">Error: ' + r.error + '</p>';
    var recoveryColor = r.t_recovery_s < 10 ? '#27ae60' : r.t_recovery_s < 20 ? '#f39c12' : '#e74c3c';
    var tooltipStyle = 'position:relative; display:inline-block; cursor:help; margin-left:4px; color:#95a5a6; font-size:11px;';
    var tipStyle = 'visibility:hidden; opacity:0; background:#2c3e50; color:white; text-align:left; border-radius:6px; padding:8px 10px; position:absolute; z-index:9999; bottom:130%; left:50%; transform:translateX(-50%); width:200px; font-size:11px; line-height:1.5; transition:opacity 0.2s; pointer-events:none;';
    return '' +
        '<div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:14px;">' +
        '  <div style="background:#f8f9fa; padding:12px; border-radius:6px; text-align:center;">' +
        '    <div style="font-size:11px; color:#7f8c8d; margin-bottom:4px;">Baseline latency<span class="chaos-tip" style="' + tooltipStyle + '">ℹ️<span class="chaos-tiptext" style="' + tipStyle + '">Normal network latency before any failure. It is the benchmark.</span></span></div>' +
        '    <div style="font-size:22px; font-weight:bold; color:#2c3e50;">' + r.baseline_avg_ms + ' ms</div>' +
        '  </div>' +
        '  <div style="background:#f8f9fa; padding:12px; border-radius:6px; text-align:center;">' +
        '    <div style="font-size:11px; color:#7f8c8d; margin-bottom:4px;">Recovery latency<span class="chaos-tip" style="' + tooltipStyle + '">ℹ️<span class="chaos-tiptext" style="' + tipStyle + '">Latency of first successful ping after recovery. May be slightly higher than baseline while OSPF stabilizes.</span></span></div>' +
        '    <div style="font-size:22px; font-weight:bold; color:#2c3e50;">' + (r.recovery_avg_ms != null ? r.recovery_avg_ms : '—') + ' ms</div>' +
        '  </div>' +
        '  <div style="background:#fdf2f2; padding:12px; border-radius:6px; text-align:center;">' +
        '    <div style="font-size:11px; color:#7f8c8d; margin-bottom:4px;">Packet loss<span class="chaos-tip" style="' + tooltipStyle + '">ℹ️<span class="chaos-tiptext" style="' + tipStyle + '">Percentage of packets lost while the router was down. 100% indicates total loss of connectivity.</span></span></div>' +
        '    <div style="font-size:22px; font-weight:bold; color:#e74c3c;">' + r.loss_pct + '%</div>' +
        '    <div style="font-size:11px; color:#aaa;">' + r.lost_packets + ' / ' + r.total_packets + ' packets</div>' +
        '  </div>' +
        '  <div style="background:#f0fdf4; padding:12px; border-radius:6px; text-align:center;">' +
        '    <div style="font-size:11px; color:#7f8c8d; margin-bottom:4px;">Recovery time<span class="chaos-tip" style="' + tooltipStyle + '">ℹ️<span class="chaos-tiptext" style="' + tipStyle + '">Time from when the router is back up until the first ping works. Includes OSPF reconvergence (dead-interval + renegotiation + SPF).</span></span></div>' +
        '    <div style="font-size:22px; font-weight:bold; color:' + recoveryColor + ';">' + (r.t_recovery_s != null ? r.t_recovery_s : '—') + ' s</div>' +
        '    <div style="font-size:11px; color:#aaa;">Router: ' + r.node + ' | ' + r.duration_s + 's down</div>' +
        '  </div>' +
        '</div>' +
        '<div style="font-size:12px; color:#7f8c8d; text-align:center;">Ping: ' + r.src + ' → ' + r.dst + '</div>';
}

function renderNeighbors(data) {
    var neighbors = data.result ? data.result.neighbors : data.neighbors;
    if (!neighbors) return '<p style="color:#e74c3c;">No data</p>';
    var selectedNode = document.getElementById('neighbors-node').value;
    if (selectedNode) {
        var filtered = {};
        filtered[selectedNode] = neighbors[selectedNode] || [];
        neighbors = filtered;
    }
    var html = '<table style="width:100%; border-collapse:collapse; font-size:13px;">';
    html += '<tr style="background:#2c3e50; color:white;"><th style="padding:8px 12px; text-align:left;">Node</th><th style="padding:8px 12px; text-align:left;">Connected to</th></tr>';
    var i = 0;
    for (var node in neighbors) {
        var bg = i%2===0 ? '#f8f9fa' : 'white';
        var conns = neighbors[node].length > 0
            ? neighbors[node].map(function(n) { return '<span style="background:#e8f4fd; color:#2980b9; padding:2px 8px; border-radius:12px; margin-right:4px; font-size:12px;">' + n + '</span>'; }).join('')
            : '<span style="color:#aaa; font-size:12px;">none</span>';
        html += '<tr style="background:' + bg + ';"><td style="padding:8px 12px; font-weight:bold;">' + node + '</td><td style="padding:8px 12px;">' + conns + '</td></tr>';
        i++;
    }
    return html + '</table>';
}

function renderHops(data) {
    var result = data.result ? data.result.result : null;
    var src    = data.result ? data.result.src : '?';
    if (!result) return '<p style="color:#e74c3c;">No data</p>';
    if (result.path) {
        var path = result.path;
        var pathHtml = path.map(function(n, i) {
            var arrow = i < path.length-1 ? ' <span style="color:#aaa;">→</span> ' : '';
            var color = (i===0||i===path.length-1) ? '#2c3e50' : '#2980b9';
            return '<span style="background:#e8f4fd; color:' + color + '; padding:4px 10px; border-radius:12px; font-weight:bold;">' + n + '</span>' + arrow;
        }).join('');
        return '<div style="background:#f8f9fa; padding:14px; border-radius:6px;"><div style="font-size:13px; color:#7f8c8d; margin-bottom:8px;">Path (' + result.hops + ' hops):</div><div style="display:flex; flex-wrap:wrap; align-items:center; gap:4px;">' + pathHtml + '</div></div>';
    }
    var html = '<div style="font-size:13px; color:#7f8c8d; margin-bottom:8px;">Nodes reachable from <strong>' + src + '</strong>:</div>';
    html += '<table style="width:100%; border-collapse:collapse; font-size:13px;"><tr style="background:#2c3e50; color:white;"><th style="padding:8px 12px; text-align:left;">Node</th><th style="padding:8px 12px; text-align:center;">Hops</th></tr>';
    Object.entries(result).sort(function(a,b) { return a[1]-b[1]; }).forEach(function(entry, i) {
        var node = entry[0], hops = entry[1];
        var bg = i%2===0 ? '#f8f9fa' : 'white';
        html += '<tr style="background:' + bg + ';"><td style="padding:8px 12px; font-weight:bold;">' + node + '</td><td style="padding:8px 12px; text-align:center; color:#2980b9;">' + hops + '</td></tr>';
    });
    return html + '</table>';
}

function renderTraffic(data) {
    var traffic = data.result ? data.result.traffic : data.traffic;
    if (!traffic) return '<p style="color:#e74c3c;">No data</p>';
    var html = '';
    for (var node in traffic) {
        html += '<div style="margin-bottom:14px;"><div style="font-weight:bold; font-size:13px; margin-bottom:6px; color:#2c3e50;">📡 ' + node + '</div>';
        html += '<table style="width:100%; border-collapse:collapse; font-size:12px;"><tr style="background:#f0f0f0;"><th style="padding:6px 10px; text-align:left;">Interface</th><th style="padding:6px 10px; text-align:right;">RX bytes</th><th style="padding:6px 10px; text-align:right;">RX pkts</th><th style="padding:6px 10px; text-align:right;">TX bytes</th><th style="padding:6px 10px; text-align:right;">TX pkts</th></tr>';
        for (var intf in traffic[node]) {
            var d = traffic[node][intf];
            html += '<tr style="border-bottom:1px solid #eee;"><td style="padding:6px 10px; font-family:monospace;">' + intf + '</td><td style="padding:6px 10px; text-align:right;">' + d.rx_bytes.toLocaleString() + '</td><td style="padding:6px 10px; text-align:right;">' + d.rx_packets.toLocaleString() + '</td><td style="padding:6px 10px; text-align:right;">' + d.tx_bytes.toLocaleString() + '</td><td style="padding:6px 10px; text-align:right;">' + d.tx_packets.toLocaleString() + '</td></tr>';
        }
        html += '</table></div>';
    }
    return html;
}

function renderLatencyMatrix(data) {
    var r = data.result ? data.result : data;
    if (!r.ok) return '<p style="color:#e74c3c;">Error: ' + r.error + '</p>';
    var ping  = r.ping  || {};
    var bw    = r.bandwidth || {};
    var hosts = r.hosts || [];

    // ── Global stats ──
    var allAvg = [], allMin = [], allMax = [], allJitter = [];
    for (var k in ping) {
        if (ping[k].avg    !== null) allAvg.push(ping[k].avg);
        if (ping[k].min    !== null) allMin.push(ping[k].min);
        if (ping[k].max    !== null) allMax.push(ping[k].max);
        if (ping[k].jitter !== null) allJitter.push(ping[k].jitter);
    }
    var gAvg    = allAvg.length    ? (allAvg.reduce(function(a,b){return a+b;},0)/allAvg.length).toFixed(3) : null;
    var gMin    = allMin.length    ? Math.min.apply(null, allMin).toFixed(3) : null;
    var gMax    = allMax.length    ? Math.max.apply(null, allMax).toFixed(3) : null;
    var gJitter = allJitter.length ? (allJitter.reduce(function(a,b){return a+b;},0)/allJitter.length).toFixed(3) : null;

    var bwCmd  = r.iperf_cmd || '';
    var pingCmd = r.ping_cmd || '';
    var hasBw  = Object.keys(bw).length > 0;

    // ── Summary cards ──
    var html = '<div style="display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin-bottom:14px;">';
    [{label:'Avg latency', val: gAvg    !== null ? gAvg    + ' ms' : '—', color:'#2980b9'},
     {label:'Min latency', val: gMin    !== null ? gMin    + ' ms' : '—', color:'#27ae60'},
     {label:'Max latency', val: gMax    !== null ? gMax    + ' ms' : '—', color:'#e74c3c'},
     {label:'Avg jitter',  val: gJitter !== null ? gJitter + ' ms' : '—', color:'#f39c12'},
    ].forEach(function(c) {
        html += '<div style="background:#f8f9fa; border-radius:6px; padding:10px; text-align:center; border-top:3px solid ' + c.color + ';">';
        html += '<div style="font-size:10px; color:#7f8c8d; margin-bottom:4px;">' + c.label + '</div>';
        html += '<div style="font-size:18px; font-weight:bold; color:' + c.color + ';">' + c.val + '</div>';
        html += '</div>';
    });
    html += '</div>';

    // ── Tabs ──
    var tabs = [
        {id:'tab-latency', label:'🌡️ Latency'},
        {id:'tab-jitter',  label:'📈 Jitter'},
    ];
    if (hasBw) tabs.push({id:'tab-bw-avg', label:'📊 BW avg'}, {id:'tab-bw-min', label:'📊 BW min'}, {id:'tab-bw-max', label:'📊 BW max'});

    html += '<div style="display:flex; gap:4px; margin-bottom:10px; flex-wrap:wrap;">';
    tabs.forEach(function(t, i) {
        var active = i === 0 ? 'background:#2c3e50; color:white;' : 'background:#ecf0f1; color:#7f8c8d;';
        html += '<button id="' + t.id + '" onclick="lmShowTab(\'' + t.id + '\')" style="padding:5px 10px; font-size:11px; border:none; border-radius:4px; cursor:pointer; ' + active + '">' + t.label + '</button>';
    });
    html += '</div>';

    // ── Commands used ──
    if (pingCmd) html += '<div style="font-family:monospace; font-size:10px; color:#7f8c8d; background:#f8f9fa; padding:4px 8px; border-radius:4px; margin-bottom:8px;">$ ' + pingCmd + '</div>';

    // ── Tab content ──
    // Helper: heatmap for a given value extractor
    function heatmap(valFn, unit) {
        var vals = [];
        for (var k in ping) { var v = valFn(ping[k]); if (v !== null) vals.push(v); }
        var maxVal = vals.length ? Math.max.apply(null, vals) : 1;
        var t = '<div style="overflow-x:auto;"><table style="border-collapse:collapse; font-size:11px;">';
        t += '<tr><th style="padding:5px 8px; background:#2c3e50; color:white;"></th>';
        hosts.forEach(function(h) { t += '<th style="padding:5px 8px; background:#2c3e50; color:white; text-align:center; min-width:50px;">' + h + '</th>'; });
        t += '</tr>';
        hosts.forEach(function(src) {
            t += '<tr><td style="padding:5px 8px; font-weight:bold; background:#f8f9fa; white-space:nowrap;">' + src + '</td>';
            hosts.forEach(function(dst) {
                if (src === dst) { t += '<td style="padding:5px 8px; text-align:center; background:#ecf0f1; color:#bdc3c7;">—</td>'; return; }
                var key1 = src+'->'+dst, key2 = dst+'->'+src;
                var entry = ping[key1] || ping[key2];
                var val   = entry ? valFn(entry) : null;
                var bg = '#f8f9fa', color = '#aaa';
                if (val !== null) {
                    // Absolute scale: <1ms green, 1-5ms orange, >5ms red
                    if      (val < 0.5)  { bg = '#1a7a4a'; color = 'white'; }
                    else if (val < 1)    { bg = '#27ae60'; color = 'white'; }
                    else if (val < 2)    { bg = '#f39c12'; color = 'white'; }
                    else if (val < 5)    { bg = '#e67e22'; color = 'white'; }
                    else                 { bg = '#e74c3c'; color = 'white'; }
                }
                t += '<td style="padding:5px 8px; text-align:center; background:'+bg+'; color:'+color+'; font-weight:bold; font-size:11px;">'+(val !== null ? val+' '+unit : '—')+'</td>';
            });
            t += '</tr>';
        });
        return t + '</table></div>';
    }

    // Helper: SVG line chart for bandwidth metric
    function bwChart(metric) {
        var pairs  = Object.keys(bw);
        if (!pairs.length) return '<p style="color:#7f8c8d; font-size:12px;">No bandwidth data.</p>';
        var vals   = pairs.map(function(p) { return bw[p][metric]; });
        var maxVal = Math.max.apply(null, vals) || 1;
        var W = 340, H = Math.max(80, pairs.length * 22 + 20), pad = 40, rPad = 60;
        var svg = '<svg width="'+W+'" height="'+H+'" style="display:block; width:100%; overflow:visible;">';
        // Y axis labels and grid
        pairs.forEach(function(pair, i) {
            var y = pad/2 + i * ((H - pad) / pairs.length) + (H - pad) / pairs.length / 2;
            svg += '<text x="'+(pad-4)+'" y="'+(y+4)+'" font-size="10" fill="#7f8c8d" text-anchor="end">'+pair+'</text>';
            svg += '<line x1="'+pad+'" y1="'+y+'" x2="'+(W-rPad)+'" y2="'+y+'" stroke="#ecf0f1" stroke-width="1"/>';
        });
        // Bars
        var barH = Math.max(10, (H - pad) / pairs.length - 4);
        pairs.forEach(function(pair, i) {
            var val  = bw[pair][metric];
            var pct  = val / maxVal;
            var barW = Math.max(2, pct * (W - pad - rPad));
            var y    = pad/2 + i * ((H - pad) / pairs.length) + (H - pad) / pairs.length / 2 - barH/2;
            var color = pct > 0.7 ? '#27ae60' : pct > 0.4 ? '#3498db' : '#e74c3c';
            svg += '<rect x="'+pad+'" y="'+y+'" width="'+barW.toFixed(1)+'" height="'+barH+'" fill="'+color+'" rx="2"/>';
            svg += '<text x="'+(pad + barW + 4)+'" y="'+(y + barH/2 + 4)+'" font-size="10" fill="#2c3e50">'+val+' Mbps</text>';
        });
        return '<div style="background:#f8f9fa; border-radius:6px; padding:8px; border:1px solid #ecf0f1;">'+svg+'</svg></div>';
    }

    // Show ping command used
    var pingCmdHtml = '';
    if (r.ping_cmd) {
        pingCmdHtml = '<div style="font-family:monospace; font-size:10px; color:#7f8c8d; background:#f8f9fa; padding:4px 8px; border-radius:4px; margin-top:8px;">$ ' + r.ping_cmd + '</div>';
    }
    html += '<div id="lm-tab-latency">' + heatmap(function(e) { return e.avg; }, 'ms') + pingCmdHtml + '</div>';
    html += '<div id="lm-tab-jitter"  style="display:none;">' + heatmap(function(e) { return e.jitter; }, 'ms') + '</div>';
    if (hasBw) {
        if (bwCmd) html += '<div id="lm-bw-cmd" style="display:none; font-family:monospace; font-size:10px; color:#7f8c8d; background:#f8f9fa; padding:4px 8px; border-radius:4px; margin-bottom:8px;">$ ' + bwCmd + '</div>';
        html += '<div id="lm-tab-bw-avg" style="display:none;">' + bwChart('avg') + '</div>';
        html += '<div id="lm-tab-bw-min" style="display:none;">' + bwChart('min') + '</div>';
        html += '<div id="lm-tab-bw-max" style="display:none;">' + bwChart('max') + '</div>';
    }

    return html;
}

function lmShowTab(btnId) {
    // btnId is the button id e.g. 'tab-latency', 'tab-jitter', 'tab-bw-avg'
    // corresponding tab content id is 'lm-tab-latency', etc.
    var tabId = 'lm-' + btnId;

    // Show/hide tab content
    var allTabs = ['lm-tab-latency','lm-tab-jitter','lm-tab-bw-avg','lm-tab-bw-min','lm-tab-bw-max'];
    allTabs.forEach(function(t) {
        var el = document.getElementById(t);
        if (el) el.style.display = t === tabId ? 'block' : 'none';
    });

    // Show iperf cmd only for BW tabs
    var bwCmd = document.getElementById('lm-bw-cmd');
    if (bwCmd) bwCmd.style.display = tabId.indexOf('bw') !== -1 ? 'block' : 'none';

    // Update button active styles
    var allBtns = ['tab-latency','tab-jitter','tab-bw-avg','tab-bw-min','tab-bw-max'];
    allBtns.forEach(function(b) {
        var btn = document.getElementById(b);
        if (!btn) return;
        var isActive = b === btnId;
        btn.style.background = isActive ? '#2c3e50' : '#ecf0f1';
        btn.style.color      = isActive ? 'white'   : '#7f8c8d';
        btn.style.fontWeight = isActive ? 'bold'    : 'normal';
    });
}


// ── Latency Matrix mode helpers ──

function onLatencyModeChange() {
    var mode    = document.getElementById('latency-mode').value;
    var warning = document.getElementById('latency-mode-warning');
    if (warning) warning.style.display = mode === 'full' ? 'block' : 'none';
    updateLmPingPreview();
}

function onIperfProtocolChange() {
    var proto   = document.getElementById('iperf-protocol').value;
    var bwGroup = document.getElementById('iperf-bw-group');
    if (bwGroup) bwGroup.style.display = proto === 'udp' ? 'flex' : 'none';
    updateIperfPreview();
}

function updateIperfPreview() {
    var proto = document.getElementById('iperf-protocol');
    var dur   = document.getElementById('iperf-duration');
    var par   = document.getElementById('iperf-parallel');
    var bw    = document.getElementById('iperf-bandwidth');
    var rev   = document.getElementById('iperf-reverse');
    var prev  = document.getElementById('iperf-cmd-preview');
    if (!proto || !prev) return;
    var cmd = 'iperf -c &lt;dst_ip&gt; -t ' + (dur ? dur.value : 1) + ' -f m';
    if (proto.value === 'udp') {
        cmd += ' -u';
        if (bw && bw.value) cmd += ' -b ' + bw.value + 'M';
    }
    if (par && parseInt(par.value) > 1) cmd += ' -P ' + par.value;
    if (rev && rev.checked) cmd += ' -R';
    prev.innerHTML = cmd;
    updateLmPingPreview();
}

function updateLmPingPreview() {
    var c    = document.getElementById('lm-ping-count');
    var s    = document.getElementById('lm-ping-size');
    var prev = document.getElementById('iperf-cmd-preview');
    if (!c || !prev) return;
    var count = c.value || 5;
    var size  = s ? s.value : 64;
    var sFlag = parseInt(size) !== 64 ? ' -s ' + size : '';
    // Only update if iperf cmd is shown
    var mode = document.getElementById('latency-mode');
    if (!mode) return;
    var pingCmd = '<span style="color:#27ae60;">ping -c ' + count + ' -i 0.2' + sFlag + ' &lt;dst_ip&gt;</span>';
    if (mode.value === 'fast') {
        prev.innerHTML = pingCmd;
    } else {
        // In full mode, show both iperf and ping commands
        updateIperfPreview();
        prev.innerHTML += '<br>' + pingCmd;
    }
}

// ── Live Traffic Monitor ──

var _trafficInterval  = null;
var _trafficPrevBytes = {};
var _trafficNode      = null;
var _trafficHistory   = {};   // {intf: [{t, bps}]}
var _trafficMaxPoints = 30;   // last 30 samples = 60s

function startTrafficLive() {
    var node = document.getElementById('traffic-node').value;
    if (!node) {
        stopTrafficLive();
        document.getElementById('xrf-result-content').innerHTML = '';
        return;
    }
    // Stop existing interval first (without resetting _trafficNode)
    if (_trafficInterval) { clearInterval(_trafficInterval); _trafficInterval = null; }
    document.getElementById('traffic-stop-btn').style.display = 'none';

    // Now set up new monitoring
    _trafficNode = node;
    _trafficPrevBytes = {};
    _trafficHistory = {};
    document.getElementById('traffic-stop-btn').style.display = 'inline-block';
    document.getElementById('xrf-result-content').innerHTML =
        '<div style="color:#7f8c8d; font-size:13px;">⏳ Loading...</div>';
    _fetchTrafficOnce();
    _trafficInterval = setInterval(_fetchTrafficOnce, 2000);
}

function stopTrafficLive() {
    if (_trafficInterval) { clearInterval(_trafficInterval); _trafficInterval = null; }
    document.getElementById('traffic-stop-btn').style.display = 'none';
    _trafficNode = null;
}

function _fetchTrafficOnce() {
    if (!_trafficNode) return;
    var node = _trafficNode;

    // Try /metrics/traffic?node= which works for both routers and hosts
    fetch('/metrics/traffic?node=' + node)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (!data.ok || !data.interfaces) return;
            var links = {};
            for (var intf in data.interfaces) {
                links[node + '-' + intf] = {
                    node:     node,
                    intf:     intf,
                    rx_bytes: data.interfaces[intf].rx_bytes,
                    tx_bytes: data.interfaces[intf].tx_bytes,
                };
            }
            _renderTrafficLive(links);
        })
        .catch(function() {});
}

function _bpsLabel(bps) {
    if (bps < 1000)    return bps.toFixed(0) + ' B/s';
    if (bps < 1000000) return (bps/1000).toFixed(1) + ' KB/s';
    return (bps/1000000).toFixed(2) + ' MB/s';
}

function _renderTrafficLive(links) {
    var node = _trafficNode;
    if (!node) return;

    var now = Date.now();
    var bpsData = {};

    for (var key in links) {
        if (links[key].node !== node) continue;
        var d    = links[key];
        var prev = _trafficPrevBytes[key];
        var curr = d.rx_bytes + d.tx_bytes;
        var bps  = 0;
        if (prev !== undefined) {
            bps = Math.max(0, (curr - prev.bytes) / ((now - prev.time) / 1000));
        }
        _trafficPrevBytes[key] = {bytes: curr, time: now};

        if (!_trafficHistory[d.intf]) _trafficHistory[d.intf] = [];
        _trafficHistory[d.intf].push({t: now, bps: bps});
        if (_trafficHistory[d.intf].length > _trafficMaxPoints)
            _trafficHistory[d.intf].shift();

        bpsData[d.intf] = {bps: bps, rx_bytes: d.rx_bytes, tx_bytes: d.tx_bytes};
    }

    if (Object.keys(bpsData).length === 0) {
        document.getElementById('xrf-result-content').innerHTML =
            '<p style="color:#7f8c8d;">No interfaces found for ' + node + '</p>';
        return;
    }

    // Build chart HTML per interface using SVG sparklines
    var COLORS = ['#3498db','#27ae60','#e74c3c','#f39c12','#9b59b6','#1abc9c'];
    var html = '<div style="font-weight:bold; font-size:13px; margin-bottom:10px; color:#2c3e50;">📡 ' + node + ' — Live Traffic</div>';

    var intfs = Object.keys(bpsData);
    intfs.forEach(function(intf, idx) {
        var info    = bpsData[intf];
        var color   = COLORS[idx % COLORS.length];
        var history = _trafficHistory[intf] || [];
        var W = 320, H = 60, pad = 4;

        // Build SVG sparkline
        var maxBps = Math.max.apply(null, history.map(function(p) { return p.bps; })) || 1;
        var points = history.map(function(p, i) {
            var x = pad + (i / (_trafficMaxPoints - 1)) * (W - 2*pad);
            var y = H - pad - ((p.bps / maxBps) * (H - 2*pad));
            return x.toFixed(1) + ',' + y.toFixed(1);
        }).join(' ');

        // Fill area under line
        var fillPoints = '';
        if (history.length > 0) {
            fillPoints = (pad) + ',' + (H - pad) + ' ' + points + ' ' +
                (pad + ((history.length-1) / (_trafficMaxPoints-1)) * (W-2*pad)).toFixed(1) + ',' + (H-pad);
        }

        html += '<div style="margin-bottom:16px;">';
        html += '<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">';
        html += '<span style="font-family:monospace; font-size:12px; color:#2c3e50; font-weight:bold;">' + intf + '</span>';
        html += '<span style="font-size:13px; font-weight:bold; color:' + color + ';">' + _bpsLabel(info.bps) + '</span>';
        html += '</div>';

        // SVG chart
        html += '<div style="background:#f8f9fa; border-radius:6px; padding:4px; border:1px solid #ecf0f1;">';
        html += '<svg width="' + W + '" height="' + H + '" style="display:block; width:100%;">';
        // Grid lines
        for (var g = 0; g <= 3; g++) {
            var gy = pad + (g/3) * (H - 2*pad);
            html += '<line x1="' + pad + '" y1="' + gy.toFixed(0) + '" x2="' + (W-pad) + '" y2="' + gy.toFixed(0) + '" stroke="#ecf0f1" stroke-width="1"/>';
        }
        if (fillPoints) {
            html += '<polygon points="' + fillPoints + '" fill="' + color + '" fill-opacity="0.15"/>';
            html += '<polyline points="' + points + '" fill="none" stroke="' + color + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>';
            // Current value dot
            var lastP = history[history.length-1];
            var lx = pad + ((history.length-1) / (_trafficMaxPoints-1)) * (W-2*pad);
            var ly = H - pad - ((lastP.bps / maxBps) * (H - 2*pad));
            html += '<circle cx="' + lx.toFixed(1) + '" cy="' + ly.toFixed(1) + '" r="3" fill="' + color + '"/>';
        }
        html += '</svg></div>';

        // Scale label + totals
        html += '<div style="display:flex; justify-content:space-between; font-size:10px; color:#95a5a6; margin-top:3px;">';
        html += '<span>max: ' + _bpsLabel(maxBps) + '</span>';
        html += '<span>RX: ' + (info.rx_bytes/1024).toFixed(1) + ' KB &nbsp;|&nbsp; TX: ' + (info.tx_bytes/1024).toFixed(1) + ' KB total</span>';
        html += '</div>';
        html += '</div>';
    });

    html += '<div style="font-size:10px; color:#bdc3c7; margin-top:4px;">↻ Auto-refresh every 2s &nbsp;|&nbsp; last ' + _trafficMaxPoints + ' samples</div>';
    document.getElementById('xrf-result-content').innerHTML = html;
}

// ── Query XRF ──

function queryXRF(id) {
    var params = {};
    if (id === 'neighbors') {
        var node = document.getElementById('neighbors-node').value;
        if (node) params.node = node;
    } else if (id === 'hops') {
        params.src = document.getElementById('hops-src').value;
        var dst = document.getElementById('hops-dst').value;
        var max_hops = document.getElementById('hops-max').value;
        if (dst) params.dst = dst;
        else if (max_hops) params.max_hops = parseInt(max_hops);
    } else if (id === 'chaos') {
        params.node     = document.getElementById('chaos-node').value;
        params.src      = document.getElementById('chaos-src').value;
        params.dst      = document.getElementById('chaos-dst').value;
        params.duration = parseInt(document.getElementById('chaos-duration').value);
        // Reset progress bar
        var cwrap = document.getElementById('chaos-progress-wrap');
        var cbar  = document.getElementById('chaos-progress-bar');
        var cmsg  = document.getElementById('chaos-progress-msg');
        var cpct  = document.getElementById('chaos-progress-pct');
        if (cwrap) cwrap.style.display = 'block';
        if (cbar)  { cbar.style.width = '0%'; cbar.style.background = '#e74c3c'; }
        if (cmsg)  cmsg.textContent = 'Starting...';
        if (cpct)  cpct.textContent = '0%';

        // Validate src and dst are on different subnets
        if (topologyData) {
            var srcProps = topologyData.nodes[params.src];
            var dstProps = topologyData.nodes[params.dst];
            if (srcProps && dstProps && srcProps.gw === dstProps.gw) {
                document.getElementById('xrf-result-content').innerHTML =
                    '<p style="color:#e74c3c;">⚠️ Source and destination are on the same subnet. Traffic does not pass through the router, so the experiment would not be meaningful. Choose hosts from different subnets (e.g. h1 → h3).</p>';
                return;
            }
        }
    } else if (id === 'latency_matrix') {
        params.mode       = document.getElementById('latency-mode').value;
        params.ping_count = document.getElementById('lm-ping-count') ? document.getElementById('lm-ping-count').value : 5;
        params.ping_size  = document.getElementById('lm-ping-size')  ? document.getElementById('lm-ping-size').value  : 64;
        if (params.mode === 'full') {
            params.protocol   = document.getElementById('iperf-protocol').value;
            params.duration   = parseInt(document.getElementById('iperf-duration').value);
            params.parallel   = parseInt(document.getElementById('iperf-parallel').value);
            params.iterations = parseInt(document.getElementById('iperf-iterations').value);
            params.reverse    = document.getElementById('iperf-reverse') ? document.getElementById('iperf-reverse').checked : false;
            var bwEl = document.getElementById('iperf-bandwidth');
            if (bwEl && bwEl.value) params.bandwidth = parseInt(bwEl.value);
        }
        // Reset and show progress bar
        var wrap = document.getElementById('latency-progress-wrap');
        var bar  = document.getElementById('latency-progress-bar');
        var msg  = document.getElementById('latency-progress-msg');
        var pct  = document.getElementById('latency-progress-pct');
        if (wrap) wrap.style.display = 'block';
        if (bar)  bar.style.width   = '0%';
        if (bar)  bar.style.background = '#3498db';
        if (msg)  msg.textContent   = 'Starting...';
        if (pct)  pct.textContent   = '0%';
    }
    document.getElementById('xrf-result-content').innerHTML = '<div style="color:#7f8c8d; font-size:13px;">⏳ Loading...</div>';
    fetch('/xrf/query', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({xrf: id, params: params})})
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (!data.ok) {
            document.getElementById('xrf-result-content').innerHTML =
                '<p style="color:#e74c3c;">Error: ' + data.error + '</p>';
            return;
        }
        if (data.async && data.job_id) {
            // Long-running XRF: poll for result every 2s
            pollXRFResult(id, data.job_id);
            return;
        }
        // Fast XRF: result is already here
        var html = '';
        if (id === 'neighbors') html = renderNeighbors(data);
        else if (id === 'hops')      html = renderHops(data);
        else if (id === 'traffic')   html = renderTraffic(data);
        document.getElementById('xrf-result-content').innerHTML = html;
    });
}

function pollXRFResult(xrf_id, job_id) {
    fetch('/xrf/result/' + job_id)
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (!data.ok) {
            var wrap = document.getElementById('latency-progress-wrap');
            if (wrap) wrap.style.display = 'none';
            document.getElementById('xrf-result-content').innerHTML =
                '<p style="color:#e74c3c;">Error: ' + data.error + '</p>';
            return;
        }
        if (!data.ready) {
            // Not ready yet, poll again in 2s
            setTimeout(function() { pollXRFResult(xrf_id, job_id); }, 2000);
            return;
        }
        // Result ready!
        var wrap = document.getElementById('latency-progress-wrap');
        if (wrap) wrap.style.display = 'none';
        var html = '';
        if (xrf_id === 'chaos') {
            html = renderChaos(data);
            var cwrap = document.getElementById('chaos-progress-wrap');
            if (cwrap) cwrap.style.display = 'none';
        }
        else if (xrf_id === 'latency_matrix') html = renderLatencyMatrix(data);
        document.getElementById('xrf-result-content').innerHTML = html;
    })
    .catch(function() {
        setTimeout(function() { pollXRFResult(xrf_id, job_id); }, 2000);
    });
}