var socket = io('http://localhost:5001');

// ── WebSocket: latency matrix progress ──
socket.on('xrf_result', function(data) {
    // Result from async XRF query (chaos, latency_matrix)
    var wrap = document.getElementById('latency-progress-wrap');
    if (wrap) wrap.style.display = 'none';

    var html = '';
    if (!data.ok) {
        html = '<p style="color:#e74c3c;">Error: ' + data.error + '</p>';
    } else {
        var id = data.xrf;
        if (id === 'chaos')          html = renderChaos(data);
        else if (id === 'latency_matrix') html = renderLatencyMatrix(data);
        else html = '<pre>' + JSON.stringify(data.result, null, 2) + '</pre>';
    }
    var el = document.getElementById('xrf-result-content');
    if (el) el.innerHTML = html;
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
    document.getElementById('xrf-result').style.display = 'block';
    document.getElementById('xrf-result-title').textContent = xrfData[id].name + ' — Results';
    document.getElementById('xrf-neighbors-params').style.display = id === 'neighbors' ? 'block' : 'none';
    document.getElementById('xrf-hops-params').style.display      = id === 'hops'      ? 'block' : 'none';
    document.getElementById('xrf-traffic-params').style.display   = id === 'traffic'   ? 'block' : 'none';
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
        tsel.innerHTML = '<option value="">All nodes</option>';
        all.forEach(function(n) { tsel.innerHTML += '<option value="' + n + '">' + n + '</option>'; });

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
    if (id !== 'chaos') queryXRF(id);
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
    var ping = r.ping || {};
    var bw   = r.bandwidth || {};
    var hosts = r.hosts || [];

    var html = '<div style="font-size:12px; color:#7f8c8d; margin-bottom:10px;">Latency (avg ms) between all host pairs</div>';
    html += '<div style="overflow-x:auto;"><table style="border-collapse:collapse; font-size:12px; width:100%;">';
    html += '<tr><th style="padding:6px 10px; background:#2c3e50; color:white;"></th>';
    hosts.forEach(function(h) {
        html += '<th style="padding:6px 10px; background:#2c3e50; color:white; text-align:center;">' + h + '</th>';
    });
    html += '</tr>';
    hosts.forEach(function(src) {
        html += '<tr><td style="padding:6px 10px; font-weight:bold; background:#f8f9fa;">' + src + '</td>';
        hosts.forEach(function(dst) {
            if (src === dst) {
                html += '<td style="padding:6px 10px; text-align:center; color:#ccc; background:#f8f9fa;">—</td>';
            } else {
                var key1 = src + '->' + dst;
                var key2 = dst + '->' + src;
                var val = ping[key1] ? ping[key1].avg : (ping[key2] ? ping[key2].avg : null);
                var color = val === null ? '#aaa' : val < 0.5 ? '#27ae60' : val < 2 ? '#f39c12' : '#e74c3c';
                var bg = val === null ? '#f8f9fa' : val < 0.5 ? '#f0fdf4' : val < 2 ? '#fffbeb' : '#fdf2f2';
                html += '<td style="padding:6px 10px; text-align:center; color:' + color + '; background:' + bg + '; font-weight:bold;">' + (val !== null ? val + ' ms' : '—') + '</td>';
            }
        });
        html += '</tr>';
    });
    html += '</table></div>';

    if (Object.keys(bw).length > 0) {
        html += '<div style="font-size:12px; color:#7f8c8d; margin:14px 0 8px;">Bandwidth (Mbits/sec)</div>';
        html += '<table style="border-collapse:collapse; font-size:12px; width:100%;">';
        html += '<tr style="background:#2c3e50; color:white;"><th style="padding:6px 10px; text-align:left;">Pair</th><th style="padding:6px 10px; text-align:right;">Min</th><th style="padding:6px 10px; text-align:right;">Avg</th><th style="padding:6px 10px; text-align:right;">Max</th></tr>';
        var i = 0;
        for (var pair in bw) {
            var bg = i%2===0 ? '#f8f9fa' : 'white';
            html += '<tr style="background:' + bg + ';"><td style="padding:6px 10px;">' + pair + '</td><td style="padding:6px 10px; text-align:right;">' + bw[pair].min + '</td><td style="padding:6px 10px; text-align:right; font-weight:bold;">' + bw[pair].avg + '</td><td style="padding:6px 10px; text-align:right;">' + bw[pair].max + '</td></tr>';
            i++;
        }
        html += '</table>';
    }
    return html;
}


// ── Latency Matrix mode helpers ──

function onLatencyModeChange() {
    var mode    = document.getElementById('latency-mode').value;
    var warning = document.getElementById('latency-mode-warning');
    if (warning) warning.style.display = mode === 'full' ? 'block' : 'none';
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
    } else if (id === 'traffic') {
        var node = document.getElementById('traffic-node').value;
        if (node) params.node = node;
    } else if (id === 'chaos') {
        params.node     = document.getElementById('chaos-node').value;
        params.src      = document.getElementById('chaos-src').value;
        params.dst      = document.getElementById('chaos-dst').value;
        params.duration = parseInt(document.getElementById('chaos-duration').value);

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
        params.mode = document.getElementById('latency-mode').value;
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
        if (xrf_id === 'chaos')          html = renderChaos(data);
        else if (xrf_id === 'latency_matrix') html = renderLatencyMatrix(data);
        document.getElementById('xrf-result-content').innerHTML = html;
    })
    .catch(function() {
        setTimeout(function() { pollXRFResult(xrf_id, job_id); }, 2000);
    });
}