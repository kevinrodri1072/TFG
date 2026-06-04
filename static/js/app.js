// El servidor WebSocket de mètriques corre al port 5001 del MATEIX host que
// serveix aquesta pàgina (port 5000). Usem location.hostname en lloc de
// 'localhost' perquè el dashboard funcioni també quan s'accedeix en remot
// (ex. des d'un portàtil apuntant a http://10.4.39.102:5000).
var socket = io(location.protocol + '//' + location.hostname + ':5001');

        // ── WebSocket: System metrics (CPU/RAM) ──
        socket.on('metrics_system', function(sys) {
            var cpuEl  = document.getElementById('cpu-val');
            var cpuBar = document.getElementById('cpu-bar');
            var ramEl  = document.getElementById('ram-val');
            var ramBar = document.getElementById('ram-bar');
            var ramDet = document.getElementById('ram-detail');
            if (!cpuEl) return;
            cpuEl.textContent  = sys.cpu_percent + '%';
            cpuBar.style.width = sys.cpu_percent + '%';
            cpuBar.style.background = sys.cpu_percent < 60 ? '#3498db' : sys.cpu_percent < 85 ? '#f39c12' : '#e74c3c';
            ramEl.textContent  = sys.ram_percent + '%';
            ramBar.style.width = sys.ram_percent + '%';
            if (ramDet) ramDet.textContent = sys.ram_used_mb.toFixed(0) + ' MB / ' + sys.ram_total_mb.toFixed(0) + ' MB';
        });

        // Link traffic WS listener registered inside topology closure (see below)

        // ── WebSocket: Twin physical channel ping ──
        var _channelHistory = [];
        var _channelMaxPoints = 20;

        socket.on('twin_channel_ping', function(d) {
            var section = document.getElementById('twin-channel-section');
            if (section) section.style.display = 'block';

            // Update title with target IP
            var title = document.getElementById('channel-title');
            if (title && d.target) title.textContent = 'Physical Channel → ' + d.target;

            var dot    = document.getElementById('channel-dot');
            var avg    = document.getElementById('channel-avg');
            var avg2   = document.getElementById('channel-avg2');
            var minEl  = document.getElementById('channel-min');
            var maxEl  = document.getElementById('channel-max');
            var jitter = document.getElementById('channel-jitter');
            var cmd    = document.getElementById('channel-cmd');

            if (cmd && d.target) cmd.textContent = 'ping -c 3 -i 0.2 ' + d.target + ' · every 5s';

            if (!d.reachable) {
                if (dot) dot.style.background = '#e74c3c';
                if (avg) avg.textContent = 'Unreachable';
                return;
            }

            var color = d.latency_avg < 1 ? '#2ecc71' : d.latency_avg < 5 ? '#f39c12' : '#e74c3c';
            if (dot)    dot.style.background = color;
            if (avg)    avg.textContent    = d.latency_avg !== null ? d.latency_avg.toFixed(3) + ' ms' : '—';
            if (avg2)   avg2.textContent   = d.latency_avg !== null ? d.latency_avg.toFixed(3) : '—';
            if (minEl)  minEl.textContent  = d.latency_min !== null ? d.latency_min.toFixed(3) : '—';
            if (maxEl)  maxEl.textContent  = d.latency_max !== null ? d.latency_max.toFixed(3) : '—';
            if (jitter) jitter.textContent = d.jitter !== null ? d.jitter.toFixed(3) + ' ms' : '—';

            if (d.latency_avg !== null) {
                _channelHistory.push(d.latency_avg);
                if (_channelHistory.length > _channelMaxPoints) _channelHistory.shift();
                _drawChannelChart();
            }
        });

        function _drawChannelChart() {
            var canvas = document.getElementById('channel-chart');
            if (!canvas || _channelHistory.length < 2) return;
            var ctx = canvas.getContext('2d');
            canvas.width = canvas.offsetWidth;
            var w = canvas.width, h = canvas.height, pad = 4;
            ctx.clearRect(0, 0, w, h);
            var maxV = Math.max.apply(null, _channelHistory) || 1;
            var minV = Math.min.apply(null, _channelHistory);
            var stepX = (w - pad * 2) / Math.max(_channelMaxPoints - 1, 1);

            // Area fill
            ctx.beginPath();
            ctx.moveTo(pad, h - pad);
            _channelHistory.forEach(function(v, i) {
                var x = pad + i * stepX;
                var y = h - pad - ((v - minV) / (maxV - minV + 0.001)) * (h - pad * 2);
                ctx.lineTo(x, y);
            });
            ctx.lineTo(pad + (_channelHistory.length - 1) * stepX, h - pad);
            ctx.closePath();
            ctx.fillStyle = 'rgba(46,204,113,0.2)';
            ctx.fill();

            // Line
            ctx.beginPath();
            _channelHistory.forEach(function(v, i) {
                var x = pad + i * stepX;
                var y = h - pad - ((v - minV) / (maxV - minV + 0.001)) * (h - pad * 2);
                if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
            });
            ctx.strokeStyle = '#2ecc71';
            ctx.lineWidth = 1.5;
            ctx.stroke();

            // Last point dot
            var last = _channelHistory[_channelHistory.length - 1];
            var lx = pad + (_channelHistory.length - 1) * stepX;
            var ly = h - pad - ((last - minV) / (maxV - minV + 0.001)) * (h - pad * 2);
            ctx.beginPath();
            ctx.arc(lx, ly, 3, 0, Math.PI * 2);
            ctx.fillStyle = '#2ecc71';
            ctx.fill();
        }

        var selectedNode = null;
        var topologyData = null;

        // ── Load topology and build vis.js network ──
        fetch('/topology')
            .then(response => response.json())
            .then(data => {
                topologyData = data;
                var nodes = new vis.DataSet(
                    Object.keys(data.nodes)
                        .filter(name => data.nodes[name].type !== 'switch')
                        .map(name => {
                            var type = data.nodes[name].type;
                            var image = type === 'host' ? '/static/host.png' : '/static/router.png';
                            return { id: name, label: name, shape: 'image', image: image, size: 30 };
                        })
                );
                var edgesData = data.links.filter(link =>
                    data.nodes[link.from].type !== 'switch' &&
                    data.nodes[link.to].type !== 'switch'
                ).map(link => ({
                    id: link.from + '___' + link.to,
                    from: link.from,
                    to: link.to,
                    color: { color: '#848484', highlight: '#848484', hover: '#848484' },
                    width: 2
                }));
                var edges = new vis.DataSet(edgesData);
                var container = document.getElementById('network');
                var network = new vis.Network(container, {nodes: nodes, edges: edges}, {
                    interaction: { navigationButtons: false, keyboard: false, hover: true }
                });

                // ── Link traffic coloring via WebSocket ──
                // Registered HERE so applyLinkColors has closure access to edges
                var _prevLinkBytes = {};
                function applyLinkColors(d) {
                    if (!d || !d.links || !topologyData) return;

                    // ── Router-router links (p2p) ──
                    var routers = Object.keys(topologyData.nodes).filter(function(n) {
                        return topologyData.nodes[n].type === 'router';
                    });
                    routers.forEach(function(rname) {
                        var props = topologyData.nodes[rname];
                        if (!props.p2p_links) return;
                        props.p2p_links.forEach(function(link) {
                            var entry = d.links[rname + '-' + link.local_intf];
                            if (!entry) return;
                            var fullKey = rname + '_' + link.local_intf;
                            var prev = _prevLinkBytes[fullKey];
                            var curr = entry.rx_bytes + entry.tx_bytes;
                            _prevLinkBytes[fullKey] = curr;
                            if (prev === undefined) return;
                            var bps = curr - prev;
                            var color = bps < 1000 ? '#27ae60' : bps < 50000 ? '#f39c12' : '#e74c3c';
                            var width = bps < 1000 ? 2 : bps < 50000 ? 3 : 5;
                            var update = {color: {color: color, highlight: color, hover: color}, width: width};
                            var edgeId1 = rname + '___' + link.peer;
                            var edgeId2 = link.peer + '___' + rname;
                            if (edges.get(edgeId1)) edges.update([Object.assign({id: edgeId1}, update)]);
                            else if (edges.get(edgeId2)) edges.update([Object.assign({id: edgeId2}, update)]);
                        });
                    });

                    // ── Host-router links ──
                    // Each host has eth0 — color its edge based on its own traffic
                    var hosts = Object.keys(topologyData.nodes).filter(function(n) {
                        return topologyData.nodes[n].type === 'host';
                    });
                    hosts.forEach(function(hname) {
                        var entry = d.links[hname + '-' + 'eth0'];
                        if (!entry) return;
                        var fullKey = hname + '_eth0';
                        var prev = _prevLinkBytes[fullKey];
                        var curr = entry.rx_bytes + entry.tx_bytes;
                        _prevLinkBytes[fullKey] = curr;
                        if (prev === undefined) return;
                        var bps = curr - prev;
                        var color = bps < 1000 ? '#27ae60' : bps < 50000 ? '#f39c12' : '#e74c3c';
                        var width = bps < 1000 ? 2 : bps < 50000 ? 3 : 5;
                        var update = {color: {color: color, highlight: color, hover: color}, width: width};
                        // Find the edge connecting this host to its router
                        var allEdges = edges.get();
                        allEdges.forEach(function(e) {
                            if (e.from === hname || e.to === hname) {
                                edges.update([Object.assign({id: e.id}, update)]);
                            }
                        });
                    });
                }
                socket.on('metrics_link_traffic', applyLinkColors);

                network.on('hoverNode', function(params) {
                    var popup = document.getElementById('node-popup');
                    selectedNode = params.node;
                    var type = topologyData.nodes[selectedNode].type;
                    popup.style.left = (params.event.clientX + 10) + 'px';
                    popup.style.top  = (params.event.clientY - 10) + 'px';
                    document.getElementById('popup-title').textContent = selectedNode + ' (' + type + ')';
                    document.getElementById('btn-add-host').style.display = type === 'router' ? 'block' : 'none';
                    document.getElementById('btn-view-routes').style.display = type === 'router' ? 'block' : 'none';
                    document.getElementById('btn-wireshark').style.display = type !== 'switch' ? 'block' : 'none';
                    document.getElementById('wireshark-selector').style.display = 'none';
                    var infoDiv = document.getElementById('popup-info');
                    var nodeProps = topologyData.nodes[selectedNode];
                    if (type === 'host') {
                        infoDiv.innerHTML = 'IP: ' + nodeProps.ip + '<br>GW: ' + nodeProps.gw;
                    } else if (type === 'router') {
                        infoDiv.innerHTML = Object.entries(nodeProps.ips)
                            .filter(([k]) => k !== 'lan')
                            .map(([k, v]) => `${k}: ${v}`)
                            .join('<br>');
                    }
                    document.getElementById('popup-buttons').style.display = 'block';
                    document.getElementById('popup-add-host').style.display = 'none';
                    popup.style.display = 'block';
                });

                network.on('blurNode', function() {
                    setTimeout(function() {
                        var popup = document.getElementById('node-popup');
                        if (!popup.matches(':hover')) {
                            popup.style.display = 'none';
                            selectedNode = null;
                        }
                    }, 100);
                });

                // Populate host selectors
                loadHostSelectors();
            });

        document.getElementById('node-popup').addEventListener('mouseleave', function() {
            this.style.display = 'none';
            selectedNode = null;
        });

        // ── Node popup actions ──
        function removeSelectedNode() {
            document.getElementById('node-popup').style.display = 'none';
            if (selectedNode && confirm('Do you want to remove ' + selectedNode + '?')) {
                fetch('/remove_node', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({name: selectedNode})
                })
                .then(r => r.json())
                .then(data => { if (data.ok) location.reload(); });
            }
        }

        function showAddHostInput() {
            document.getElementById('host-name').value = '';
            document.getElementById('host-router').value = selectedNode;
            document.getElementById('popup-buttons').style.display = 'none';
            document.getElementById('popup-add-host').style.display = 'block';
        }

        function closeAddHostInput() {
            document.getElementById('popup-add-host').style.display = 'none';
            document.getElementById('popup-buttons').style.display = 'block';
        }

        function addHost() {
            var name = document.getElementById('host-name').value;
            var router = document.getElementById('host-router').value;
            fetch('/add_host', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name: name, router: router})
            })
            .then(r => r.json())
            .then(data => {
                if (data.ok) location.reload();
                else alert(data.error);
            });
        }

        // ── Router form ──
        function showRouterForm() {
            fetch('/topology')
                .then(r => r.json())
                .then(data => {
                    var select = document.getElementById('connected-router');
                    select.innerHTML = '';
                    Object.keys(data.nodes).forEach(name => {
                        if (data.nodes[name].type === 'router') {
                            var opt = document.createElement('option');
                            opt.value = name; opt.textContent = name;
                            select.appendChild(opt);
                        }
                    });
                });
            document.getElementById('router-form').style.display = 'block';
        }

        function closeRouterForm() {
            document.getElementById('router-form').style.display = 'none';
        }

        function addRouter() {
            var name = document.getElementById('router-name').value;
            var select = document.getElementById('connected-router');
            var connectedRouters = Array.from(select.selectedOptions).map(o => o.value);
            fetch('/add_router', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name: name, connected_routers: connectedRouters})
            })
            .then(r => r.json())
            .then(data => {
                if (data.ok) { closeRouterForm(); location.reload(); }
                else alert(data.error);
            });
        }

        // ── Matrix modal ──
        var matrixData = null;

        function showMatrix() {
            fetch('/matrix')
                .then(r => r.json())
                .then(data => {
                    matrixData = data;
                    renderMatrix();
                    document.getElementById('matrix-modal').style.display = 'block';
                });
        }

        function renderMatrix() {
            if (!matrixData) return;
            var showSwitches = document.getElementById('show-switches').checked;
            var names = matrixData.names;
            var matrix = matrixData.matrix;
            var indices, filteredNames;
            if (showSwitches) {
                indices = names.map((_, i) => i);
                filteredNames = names;
            } else {
                indices = names.map((n, i) => ({ n, i }))
                               .filter(x => !x.n.startsWith('sw'))
                               .map(x => x.i);
                filteredNames = indices.map(i => names[i]);
            }
            var table = document.getElementById('matrix-table');
            table.innerHTML = '';
            var header = '<tr><th></th>';
            filteredNames.forEach(name => header += `<th>${name}</th>`);
            header += '</tr>';
            table.innerHTML += header;
            indices.forEach(i => {
                var tr = `<tr><th>${names[i]}</th>`;
                indices.forEach(j => {
                    var cell;
                    if (!showSwitches) {
                        var typeI = names[i].startsWith('r') ? 'router' : 'host';
                        if (i === j) cell = 0;
                        else if (matrix[i][j] !== 0) cell = matrix[i][j];
                        else {
                            var commonSwitch = names.findIndex((n, k) =>
                                n.startsWith('sw') && matrix[i][k] !== 0 && matrix[j][k] !== 0
                            );
                            cell = commonSwitch !== -1 ? typeI : 0;
                        }
                    } else {
                        cell = matrix[i][j];
                    }
                    if (cell === 0)             tr += `<td class="cell-empty">0</td>`;
                    else if (cell === 'host')   tr += `<td class="cell-host">host</td>`;
                    else if (cell === 'router') tr += `<td class="cell-router">router</td>`;
                    else if (cell === 'switch') tr += `<td class="cell-switch">switch</td>`;
                    else                        tr += `<td>${cell}</td>`;
                });
                tr += '</tr>';
                table.innerHTML += tr;
            });
        }

        function closeMatrix() {
            document.getElementById('matrix-modal').style.display = 'none';
        }

        function saveNetwork() {
            var a = document.createElement('a');
            a.href = '/export';
            a.download = 'network.mat';
            a.click();
        }

        function loadNetwork(input) {
            var file = input.files[0];
            if (!file) return;
            
            var formData = new FormData();
            formData.append('file', file);
            
            // Mostrem un missatge o canviem el punt a groc per indicar procés
            document.querySelector('.status-dot').className = 'status-dot orange';

            fetch('/load_network', { method: 'POST', body: formData })
                .then(r => r.json())
                .then(data => {
                    if (data.ok) {
                        // En lloc de fer un reload immediat, actualitzem les mètriques i la topologia
                        setTimeout(() => {
                            refreshSyncMetrics(); // Demanem les noves dades de latència al servidor
                            updateTopology();     // Dibuixem la nova xarxa al vis.js
                            
                            // Si realment necessites el reload, fes-lo més tard
                            // setTimeout(() => location.reload(), 2000); 
                        }, 1000);
                    } else {
                        alert('Error loading network: ' + data.error);
                        document.querySelector('.status-dot').className = 'status-dot red';
                    }
                });
            input.value = '';
        }

        // ── Metrics ──

        // ── Ping chart ──
        var pingHistory = [];  // [{label, avg}]

        function drawPingChart() {
            var canvas = document.getElementById('ping-chart');
            var ctx = canvas.getContext('2d');
            canvas.width = canvas.offsetWidth;
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            if (pingHistory.length === 0) return;

            var vals = pingHistory.map(p => p.avg);
            var maxV = Math.max(...vals) || 1;
            var minV = Math.min(...vals);
            var w = canvas.width, h = canvas.height;
            var pad = 4, barW = Math.max(4, (w - pad * (pingHistory.length + 1)) / pingHistory.length);

            pingHistory.forEach((p, i) => {
                var barH = Math.max(4, ((p.avg - minV) / (maxV - minV + 0.001)) * (h - 20) + 8);
                var x = pad + i * (barW + pad);
                var color = p.avg < 5 ? '#2ecc71' : p.avg < 20 ? '#f39c12' : '#e74c3c';
                ctx.fillStyle = color;
                ctx.fillRect(x, h - barH - 14, barW, barH);
                // label
                ctx.fillStyle = '#7f8c8d';
                ctx.font = '9px Arial';
                ctx.textAlign = 'center';
                ctx.fillText(p.avg.toFixed(1), x + barW / 2, h - 2);
            });
        }

        // Update ping cmd preview on input change
        ['ping-count','ping-size'].forEach(function(id) {
            var el = document.getElementById(id);
            if (el) el.addEventListener('input', function() {
                var c = document.getElementById('ping-count').value || 10;
                var s = document.getElementById('ping-size').value || 64;
                var prev = document.getElementById('ping-cmd-preview');
                if (prev) {
                    var sFlag = parseInt(s) !== 64 ? ' -s ' + s : '';
                    prev.textContent = 'ping -c ' + c + ' -i 0.2' + sFlag + ' <dst>';
                }
            });
        });

        function loadHostSelectors() {
            fetch('/metrics/hosts')
                .then(r => r.json())
                .then(data => {
                    ['ping-src', 'ping-dst'].forEach(id => {
                        var sel = document.getElementById(id);
                        sel.innerHTML = '<option value="">Source</option>';
                        if (id === 'ping-dst') sel.innerHTML = '<option value="">Destination</option>';
                        data.hosts.forEach(h => { sel.innerHTML += `<option value="${h}">${h}</option>`; });
                    });
                    if (data.hosts.length >= 2) {
                        document.getElementById('ping-src').value = data.hosts[0];
                        document.getElementById('ping-dst').value = data.hosts[1];
                    }
                });
        }

        function measurePing() {
            var src = document.getElementById('ping-src').value;
            var dst = document.getElementById('ping-dst').value;
            if (!src || !dst) { alert('Select source and destination hosts'); return; }
            if (src === dst) { alert('Source and destination must be different'); return; }

            var btn = document.getElementById('btn-ping');
            btn.disabled = true;
            document.getElementById('ping-spinner').style.display = 'block';

            var pingCount = document.getElementById('ping-count') ? document.getElementById('ping-count').value : 10;
            var pingSize  = document.getElementById('ping-size')  ? document.getElementById('ping-size').value  : 64;
            // Show command being executed
            var cmdPrev = document.getElementById('ping-cmd-preview');
            if (cmdPrev) {
                var sizeFlag = parseInt(pingSize) !== 64 ? ' -s ' + pingSize : '';
                cmdPrev.textContent = 'ping -c ' + pingCount + ' -i 0.2' + sizeFlag + ' <dst>';
                cmdPrev.style.color = '#3498db';
            }
            fetch(`/metrics/ping?src=${src}&dst=${dst}&count=${pingCount}&size=${pingSize}`)
                .then(r => r.json())
                .then(data => {
                    btn.disabled = false;
                    document.getElementById('ping-spinner').style.display = 'none';
                    if (!data.ok) { alert('Error: ' + data.error); return; }

                    var lat = data.latency_ms;
                    document.getElementById('ping-lat-avg').textContent = lat.avg !== null ? lat.avg.toFixed(2) + ' ms' : '—';
                    document.getElementById('ping-lat-avg2').textContent = lat.avg !== null ? lat.avg.toFixed(2) : '—';
                    document.getElementById('ping-lat-min').textContent  = lat.min !== null ? lat.min.toFixed(2) : '—';
                    document.getElementById('ping-lat-max').textContent  = lat.max !== null ? lat.max.toFixed(2) : '—';

                    var latEl = document.getElementById('ping-lat-avg');
                    if (lat.avg !== null)
                        latEl.className = 'metric-value ' + (lat.avg < 5 ? 'good' : lat.avg < 20 ? 'warn' : 'bad');

                    document.getElementById('ping-jitter').textContent =
                        data.jitter_ms !== null ? data.jitter_ms.toFixed(2) + ' ms' : '—';

                    // Add to history and redraw chart
                    if (lat.avg !== null) {
                        pingHistory.push({ label: src + '→' + dst, avg: lat.avg });
                        if (pingHistory.length > 10) pingHistory.shift();
                        drawPingChart();
                    }
                })
                .catch(() => {
                    btn.disabled = false;
                    document.getElementById('ping-spinner').style.display = 'none';
                    alert('Connection error');
                });
        }

        
        function refreshSyncMetrics() {
            var filter = document.getElementById('sync-op-filter');
            var op = filter ? filter.value : '';
            var url = '/metrics/sync' + (op ? '?op=' + encodeURIComponent(op) : '');
            fetch(url)
                .then(r => r.json())
                .then(data => updateSyncDashboard(data));
        }

        function updateSyncDashboard(data) {
            if (!data.ok) return;
            var stats = data.stats;
            var dot = document.getElementById('sync-dot');
            if (!stats) {
                document.getElementById('sync-count').textContent = '0';
                dot.className = 'status-dot grey';
                return;
            }
            dot.className = 'status-dot green';
            document.getElementById('sync-count').textContent = stats.count;

            // ── Fill min/avg/max latency rows ──
            function fill(prefix, obj) {
                document.getElementById(prefix + '-min').textContent = obj && obj.min !== null ? obj.min + ' ms' : '—';
                document.getElementById(prefix + '-avg').textContent = obj && obj.avg !== null ? obj.avg + ' ms' : '—';
                document.getElementById(prefix + '-max').textContent = obj && obj.max !== null ? obj.max + ' ms' : '—';
            }
            fill('sync-local', stats.t_local);
            fill('sync-net',   stats.t_network);
            fill('sync-twin',  stats.t_twin);
            fill('sync-total', stats.t_total);

            document.getElementById('sync-jitter').textContent =
                stats.jitter_ms !== null && stats.jitter_ms !== undefined ? stats.jitter_ms + ' ms' : '—';

            // ── Format helpers ──
            function fmtBytes(b) {
                if (b === null || b === undefined) return '—';
                return b < 1024 ? b + ' B' : (b / 1024).toFixed(1) + ' KB';
            }
            function fmtBps(bps) {
                if (bps === null || bps === undefined) return '—';
                if (bps >= 1e6) return (bps / 1e6).toFixed(2) + ' Mbps';
                if (bps >= 1e3) return (bps / 1e3).toFixed(1) + ' Kbps';
                return bps.toFixed(0) + ' bps';
            }

            // ── Throughput ──
            document.getElementById('sync-payload-avg').textContent = fmtBytes(stats.payload_bytes_avg);
            document.getElementById('sync-sys-throughput').textContent = fmtBps(stats.system_throughput_bps);

            // ── CPU ──
            var cpu = stats.cpu_at_sync;
            document.getElementById('sync-cpu-avg').textContent = cpu && cpu.avg !== null ? cpu.avg + '%' : '—';

            // ── Ops per second ──
            var ops = stats.ops_per_sec;
            document.getElementById('sync-ops-capacity-local').textContent =
                ops && ops.capacity_local !== null ? ops.capacity_local + ' ops/s' : '—';
            document.getElementById('sync-ops-capacity-real').textContent =
                ops && ops.capacity_real !== null ? ops.capacity_real + ' ops/s' : '—';
            document.getElementById('sync-overhead-pct').textContent =
                ops && ops.sync_overhead_pct !== null ? ops.sync_overhead_pct + '%' : '—';

            // ── Recent operations list ──
            var list   = document.getElementById('sync-history-list');
            list.innerHTML = '';
            var recent = data.history.slice(-8).reverse();
            recent.forEach(entry => {
                var d     = new Date(entry.timestamp * 1000);
                var time  = d.toLocaleTimeString('ca-ES', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
                var net   = entry.t_network_ms !== null && entry.t_network_ms !== undefined ? entry.t_network_ms : null;
                var local = entry.t_local_ms   !== null && entry.t_local_ms   !== undefined ? entry.t_local_ms   : null;
                var twin  = entry.t_twin_ms    !== null && entry.t_twin_ms    !== undefined ? entry.t_twin_ms    : null;
                var total = (local !== null && net !== null) ? Math.round(Math.max(local, net) * 100) / 100 : null;
                var color = total === null ? '#aaa' : total < 150 ? '#2ecc71' : total < 500 ? '#f39c12' : '#e74c3c';
                var pb    = entry.payload_bytes ? fmtBytes(entry.payload_bytes) : null;
                var thr   = entry.throughput_bps ? fmtBps(entry.throughput_bps) : null;
                var detail = `local:${local ?? '?'}ms  net:${net ?? '?'}ms  twin:${twin ?? '?'}ms  total:${total ?? '?'}ms` +
                             (pb  ? `  payload:${pb}`  : '') +
                             (thr ? `  thr:${thr}`     : '') +
                             (entry.cpu_percent !== null && entry.cpu_percent !== undefined ? `  cpu:${entry.cpu_percent}%` : '');
                var display = total !== null ? total + ' ms' : (net !== null ? net + ' ms' : '?');
                list.innerHTML += `<div class="sync-entry"><span class="sync-op">${time} · ${entry.operation}</span><span class="sync-ms" style="color:${color}" title="${detail}">${display}</span></div>`;
            });
        }

        function refreshSystemResources() {
            // Fallback polling — only runs if WebSocket is not connected
            if (socket && socket.connected) return;
            fetch('/metrics/system')
                .then(r => r.json())
                .then(sys => {
                    if (!sys.ok) return;
                    document.getElementById('cpu-val').textContent = sys.cpu_percent + '%';
                    document.getElementById('cpu-bar').style.width = sys.cpu_percent + '%';
                    document.getElementById('cpu-bar').style.background =
                        sys.cpu_percent < 60 ? '#3498db' : sys.cpu_percent < 85 ? '#f39c12' : '#e74c3c';
                    document.getElementById('ram-val').textContent = sys.ram_percent + '%';
                    document.getElementById('ram-bar').style.width = sys.ram_percent + '%';
                    document.getElementById('ram-detail').textContent =
                        sys.ram_used_mb.toFixed(0) + ' MB / ' + sys.ram_total_mb.toFixed(0) + ' MB';
                })
                .catch(() => {});
        }

        // ── IP Dashboard ──
        var ipView = 'flat';

        function showIPDashboard() {
            document.getElementById('ip-modal').style.display = 'block';
            setIPView('flat');
            loadIPDashboard();
        }

        function closeIPDashboard() {
            document.getElementById('ip-modal').style.display = 'none';
        }

        function setIPView(view) {
            ipView = view;
            document.getElementById('ip-view-flat').style.display    = view === 'flat'    ? 'block' : 'none';
            document.getElementById('ip-view-grouped').style.display = view === 'grouped' ? 'block' : 'none';
            document.getElementById('ip-tab-flat').style.background    = view === 'flat'    ? '#2c3e50' : 'white';
            document.getElementById('ip-tab-flat').style.color         = view === 'flat'    ? 'white'   : '#333';
            document.getElementById('ip-tab-flat').style.borderColor   = view === 'flat'    ? '#2c3e50' : '#ccc';
            document.getElementById('ip-tab-grouped').style.background = view === 'grouped' ? '#2c3e50' : 'white';
            document.getElementById('ip-tab-grouped').style.color      = view === 'grouped' ? 'white'   : '#333';
            document.getElementById('ip-tab-grouped').style.borderColor= view === 'grouped' ? '#2c3e50' : '#ccc';
        }

        function loadIPDashboard() {
            fetch('/ip_dashboard')
                .then(r => r.json())
                .then(data => {
                    if (!data.ok) return;
                    renderFlatView(data.flat);
                    renderGroupedView(data.subnets);
                });
        }

        function renderFlatView(rows) {
            var typeColors = { host: '#d4edda', router: '#cce5ff', switch: '#fff3cd' };
            var tbody = document.getElementById('ip-flat-tbody');
            tbody.innerHTML = '';
            rows.forEach(r => {
                var bg = typeColors[r.type] || 'white';
                tbody.innerHTML += `
                    <tr style="background:${bg};">
                        <td style="padding:6px 10px; border:1px solid #eee; font-weight:bold;">${r.node}</td>
                        <td style="padding:6px 10px; border:1px solid #eee;">${r.type}</td>
                        <td style="padding:6px 10px; border:1px solid #eee; font-family:monospace;">${r.intf}</td>
                        <td style="padding:6px 10px; border:1px solid #eee; font-family:monospace;">${r.ip}</td>
                        <td style="padding:6px 10px; border:1px solid #eee; font-family:monospace;">${r.gw || '—'}</td>
                    </tr>`;
            });
        }

        function renderGroupedView(subnets) {
            var div = document.getElementById('ip-view-grouped');
            div.innerHTML = '';
            subnets.forEach(s => {
                var rows = s.members.map(m => `
                    <tr>
                        <td style="padding:5px 10px; border:1px solid #eee; font-weight:bold;">${m.node}</td>
                        <td style="padding:5px 10px; border:1px solid #eee;">${m.type}</td>
                        <td style="padding:5px 10px; border:1px solid #eee; font-family:monospace;">${m.intf}</td>
                        <td style="padding:5px 10px; border:1px solid #eee; font-family:monospace;">${m.ip}</td>
                        <td style="padding:5px 10px; border:1px solid #eee; font-family:monospace;">${m.gw || '—'}</td>
                    </tr>`).join('');
                div.innerHTML += `
                    <div style="margin-bottom:18px;">
                        <div style="background:#2c3e50; color:white; padding:7px 12px; border-radius:4px 4px 0 0; font-size:13px; font-weight:bold; display:flex; justify-content:space-between;">
                            <span>🌐 ${s.subnet}</span>
                            <span style="font-weight:normal; font-size:12px;">${s.members.length} node${s.members.length !== 1 ? 's' : ''}</span>
                        </div>
                        <table style="width:100%; border-collapse:collapse; font-size:13px;">
                            <thead><tr style="background:#f8f9fa;">
                                <th style="padding:5px 10px; text-align:left; border:1px solid #ccc;">Node</th>
                                <th style="padding:5px 10px; text-align:left; border:1px solid #ccc;">Type</th>
                                <th style="padding:5px 10px; text-align:left; border:1px solid #ccc;">Interface</th>
                                <th style="padding:5px 10px; text-align:left; border:1px solid #ccc;">IP / Mask</th>
                                <th style="padding:5px 10px; text-align:left; border:1px solid #ccc;">Gateway</th>
                            </tr></thead>
                            <tbody>${rows}</tbody>
                        </table>
                    </div>`;
            });
        }

        // ── Routes modal ──
        var routesRouter = null;

        function showRoutesModal() {
            routesRouter = selectedNode;
            document.getElementById('node-popup').style.display = 'none';
            document.getElementById('routes-modal-title').textContent = routesRouter + ' — Routes';
            var props = topologyData.nodes[routesRouter];
            var ipsText = Object.entries(props.ips)
                .filter(([k]) => k !== 'lan')
                .map(([k, v]) => k + ': ' + v).join('  |  ');
            document.getElementById('routes-modal-ips').textContent = ipsText;
            document.getElementById('routes-modal').style.display = 'block';
            refreshRoutes();
        }

        function closeRoutesModal() {
            document.getElementById('routes-modal').style.display = 'none';
            document.getElementById('routes-error').style.display = 'none';
            routesRouter = null;
        }

        function refreshRoutes() {
            if (!routesRouter) return;
            fetch('/router_routes?router=' + routesRouter)
                .then(r => r.json())
                .then(data => {
                    if (!data.ok) { showRoutesError(data.error); return; }
                    document.getElementById('routes-source-label').textContent =
                        '(live from Mininet)';
                    var tbody = document.getElementById('routes-tbody');
                    tbody.innerHTML = '';
                    if (data.routes.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="3" style="padding:8px; color:#aaa; text-align:center;">No routes</td></tr>';
                        return;
                    }
                    data.routes.forEach(route => {
                        var dst = route.dst || '—';
                        var via = route.via || '(direct)';
                        var canDelete = route.via ? true : false;
                        tbody.innerHTML += `
                            <tr>
                                <td style="padding:6px 10px; border:1px solid #eee;">${dst}</td>
                                <td style="padding:6px 10px; border:1px solid #eee;">${via}</td>
                                <td style="padding:6px 10px; border:1px solid #eee; text-align:center;">
                                    ${canDelete
                                        ? `<button onclick="deleteRoute('${dst}')" style="padding:2px 8px; background:#e74c3c; color:white; border:none; border-radius:3px; cursor:pointer; font-size:12px;">Delete</button>`
                                        : '<span style="color:#aaa; font-size:11px;">kernel</span>'}
                                </td>
                            </tr>`;
                    });
                })
                .catch(() => showRoutesError('Connection error'));
        }

        function addManualRoute() {
            var dst = document.getElementById('new-route-dst').value.trim();
            var via = document.getElementById('new-route-via').value.trim();
            if (!dst || !via) { showRoutesError('Both destination and via are required'); return; }
            document.getElementById('routes-error').style.display = 'none';
            fetch('/router_routes', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({router: routesRouter, action: 'add', dst: dst, via: via})
            })
            .then(r => r.json())
            .then(data => {
                if (!data.ok) { showRoutesError(data.error); return; }
                document.getElementById('new-route-dst').value = '';
                document.getElementById('new-route-via').value = '';
                refreshRoutes();
            })
            .catch(() => showRoutesError('Connection error'));
        }

        function deleteRoute(dst) {
            fetch('/router_routes', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({router: routesRouter, action: 'delete', dst: dst})
            })
            .then(r => r.json())
            .then(data => {
                if (!data.ok) { showRoutesError(data.error); return; }
                refreshRoutes();
            })
            .catch(() => showRoutesError('Connection error'));
        }

        function showRoutesError(msg) {
            var el = document.getElementById('routes-error');
            el.textContent = msg;
            el.style.display = 'block';
        }

        // ── Wireshark ──
        function showWiresharkSelector() {
            var node  = selectedNode;
            var props = topologyData.nodes[node];
            var type  = props.type;
            var sel   = document.getElementById('wireshark-intf-select');
            sel.innerHTML = '';

            if (type === 'host') {
                sel.innerHTML = '<option value="eth0">eth0</option>';
            } else if (type === 'router') {
                Object.keys(props.ips)
                    .filter(k => k !== 'lan')
                    .forEach(intf => {
                        sel.innerHTML += `<option value="${intf}">${intf} (${props.ips[intf]})</option>`;
                    });
            }
            document.getElementById('wireshark-selector').style.display = 'block';
        }

        function openWireshark() {
            var node = selectedNode;
            var intf = document.getElementById('wireshark-intf-select').value;
            fetch('/open_wireshark', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({node: node, intf: intf})
            })
            .then(r => r.json())
            .then(data => {
                if (!data.ok) {
                    alert('Error: ' + data.error);
                } else {
                    document.getElementById('wireshark-selector').style.display = 'none';
                    document.getElementById('node-popup').style.display = 'none';
                }
            });
        }

        // ── Routing mode ──
        var _currentRoutingMode = 'ospf';

        function toggleRoutingDD() {
            var menu = document.getElementById('routing-dd-menu');
            menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
        }

        function setRoutingDDLabel(mode) {
            var labels = {ospf:'OSPF', ospf_bfd:'OSPF + BFD', mpls:'OSPF + MPLS', mpls_bfd:'OSPF + MPLS + BFD', manual:'Manual'};
            var sel = document.getElementById('routing-dd-selected');
            sel.innerHTML = (labels[mode] || mode) + ' <span style="position:absolute; right:8px; top:50%; transform:translateY(-50%); font-size:10px;">&#9660;</span>';
            document.querySelectorAll('.routing-dd-item').forEach(function(i) {
                i.classList.toggle('active', i.dataset.value === mode);
            });
            _currentRoutingMode = mode;
        }

        function setRoutingMode(mode) {
            var labels = {ospf:'OSPF', ospf_bfd:'OSPF + BFD', mpls:'OSPF + MPLS', mpls_bfd:'OSPF + MPLS + BFD', manual:'Manual'};
            var label = labels[mode] || mode;
            if (!confirm('Switch routing mode to ' + label + '? This will restart routing on all routers.')) {
                setRoutingDDLabel(_currentRoutingMode);
                return;
            }
            fetch('/set_routing_mode', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({mode: mode})
            })
            .then(r => r.json())
            .then(data => {
                if (!data.ok) { alert('Error: ' + data.error); return; }
                setRoutingDDLabel(mode);
            });
        }

        document.querySelectorAll('.routing-dd-item').forEach(function(item) {
            item.addEventListener('click', function() {
                document.getElementById('routing-dd-menu').style.display = 'none';
                document.getElementById('routing-dd-tooltip').style.display = 'none';
                setRoutingMode(this.dataset.value);
            });
            item.addEventListener('mouseenter', function(e) {
                var tip = document.getElementById('routing-dd-tooltip');
                tip.textContent = this.dataset.tip;
                tip.style.display = 'block';
                var rect = this.getBoundingClientRect();
                tip.style.top = rect.top + 'px';
                tip.style.left = (rect.right + 10) + 'px';
            });
            item.addEventListener('mouseleave', function() {
                document.getElementById('routing-dd-tooltip').style.display = 'none';
            });
        });

        document.addEventListener('click', function(e) {
            if (!document.getElementById('routing-dd').contains(e.target)) {
                document.getElementById('routing-dd-menu').style.display = 'none';
            }
        });

        // Load current routing mode on startup
        fetch('/get_routing_mode')
            .then(r => r.json())
            .then(d => { setRoutingDDLabel(d.mode); })
            .catch(() => {});

        // Refresh sync metrics every 3 seconds
        refreshSyncMetrics();
        setInterval(refreshSyncMetrics, 3000);

        // Refresh system resources 
        refreshSystemResources();
        setInterval(refreshSystemResources, 500);

        // ── XRFs ──
        var xrfData = {};

        function initXRFPanel() {
            fetch('/is_twin')
                .then(r => r.json())
                .then(data => {
                    if (data.is_twin) {
                        document.getElementById('btn-xrfs').style.display = 'inline-block';
                        loadXRFStatus();
                        setInterval(loadXRFStatus, 10000);
                    }
                });
        }

        function showXRFModal() {
            document.getElementById('xrf-modal').style.display = 'block';
            loadXRFStatus();
        }

        function closeXRFModal() {
            document.getElementById('xrf-modal').style.display = 'none';
            document.getElementById('xrf-modal-result').style.display = 'none';
        }

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
            var list = document.getElementById('xrf-modal-list');
            if (!list) return;
            list.innerHTML = '';
            for (var id in xrfData) {
                var xrf     = xrfData[id];
                var running = xrf.status === 'running';
                var dot     = running ? '🟢' : '🔴';
                var btns    = running
                    ? `<button onclick="undeployXRF('${id}')" style="padding:5px 12px; background:#e74c3c; color:white; border:none; border-radius:4px; cursor:pointer; font-size:12px;">Undeploy</button>
                       <button onclick="showXRFResult('${id}')" style="padding:5px 12px; background:#2c3e50; color:white; border:none; border-radius:4px; cursor:pointer; font-size:12px;">Query</button>`
                    : `<button onclick="deployXRF('${id}')" style="padding:5px 12px; background:#27ae60; color:white; border:none; border-radius:4px; cursor:pointer; font-size:12px;">Deploy</button>`;
                list.innerHTML += `
                    <div style="display:flex; justify-content:space-between; align-items:center; padding:12px; background:#f8f9fa; border-radius:6px; margin-bottom:10px;">
                        <div>
                            <div style="font-weight:bold; font-size:14px;">${dot} ${xrf.name}</div>
                            <div style="font-size:12px; color:#7f8c8d; margin-top:2px;">${xrf.description}</div>
                        </div>
                        <div style="display:flex; gap:6px;">${btns}</div>
                    </div>`;
            }
        }

        function deployXRF(id) {
            fetch('/xrf/deploy', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({xrf: id})})
            .then(r => r.json())
            .then(data => { if (!data.ok) { alert('Error: ' + data.error); return; } setTimeout(loadXRFStatus, 3000); });
        }

        function undeployXRF(id) {
            if (!confirm('Undeploy ' + xrfData[id].name + '?')) return;
            fetch('/xrf/undeploy', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({xrf: id})})
            .then(r => r.json())
            .then(data => { if (!data.ok) { alert('Error: ' + data.error); return; } document.getElementById('xrf-modal-result').style.display = 'none'; setTimeout(loadXRFStatus, 2000); });
        }

        function toggleMaxHops() {
            var dst = document.getElementById('hops-dst').value;
            document.getElementById('hops-max-group').style.display = dst ? 'none' : 'flex';
        }

        function showXRFResult(id) {
            document.getElementById('xrf-modal-result').style.display = 'block';
            document.getElementById('xrf-modal-result-title').textContent = xrfData[id].name + ' — Results';
            document.getElementById('xrf-neighbors-params').style.display = id === 'neighbors' ? 'block' : 'none';
            document.getElementById('xrf-hops-params').style.display      = id === 'hops'      ? 'block' : 'none';
            document.getElementById('xrf-traffic-params').style.display   = id === 'traffic'   ? 'block' : 'none';
            document.getElementById('xrf-modal-result-content').innerHTML = '';

            if (topologyData) {
                var all     = Object.keys(topologyData.nodes).filter(n => topologyData.nodes[n].type !== 'switch');
                var routers = Object.keys(topologyData.nodes).filter(n => topologyData.nodes[n].type === 'router');
                var hosts   = Object.keys(topologyData.nodes).filter(n => topologyData.nodes[n].type === 'host');

                var nsel = document.getElementById('neighbors-node');
                nsel.innerHTML = '<option value="">All nodes</option>';
                all.forEach(n => { nsel.innerHTML += `<option value="${n}">${n}</option>`; });

                ['hops-src','hops-dst'].forEach((selId,i) => {
                    var sel = document.getElementById(selId);
                    var opts = i===1 ? '<option value="">— Any (use max hops) —</option>' : '';
                    all.forEach(n => { opts += `<option value="${n}">${n}</option>`; });
                    sel.innerHTML = opts;
                });

                var tsel = document.getElementById('traffic-node');
                tsel.innerHTML = '<option value="">All nodes</option>';
                all.forEach(n => { tsel.innerHTML += `<option value="${n}">${n}</option>`; });
            }
            queryXRF(id);
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
                    ? neighbors[node].map(n => `<span style="background:#e8f4fd; color:#2980b9; padding:2px 8px; border-radius:12px; margin-right:4px; font-size:12px;">${n}</span>`).join('')
                    : '<span style="color:#aaa; font-size:12px;">none</span>';
                html += `<tr style="background:${bg};"><td style="padding:8px 12px; font-weight:bold;">${node}</td><td style="padding:8px 12px;">${conns}</td></tr>`;
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
                var pathHtml = path.map((n,i) => {
                    var arrow = i < path.length-1 ? ' <span style="color:#aaa;">→</span> ' : '';
                    var color = (i===0||i===path.length-1) ? '#2c3e50' : '#2980b9';
                    return `<span style="background:#e8f4fd; color:${color}; padding:4px 10px; border-radius:12px; font-weight:bold;">${n}</span>${arrow}`;
                }).join('');
                return `<div style="background:#f8f9fa; padding:14px; border-radius:6px;"><div style="font-size:13px; color:#7f8c8d; margin-bottom:8px;">Path (${result.hops} hops):</div><div style="display:flex; flex-wrap:wrap; align-items:center; gap:4px;">${pathHtml}</div></div>`;
            }
            var html = `<div style="font-size:13px; color:#7f8c8d; margin-bottom:8px;">Nodes reachable from <strong>${src}</strong>:</div>`;
            html += '<table style="width:100%; border-collapse:collapse; font-size:13px;"><tr style="background:#2c3e50; color:white;"><th style="padding:8px 12px; text-align:left;">Node</th><th style="padding:8px 12px; text-align:center;">Hops</th></tr>';
            Object.entries(result).sort((a,b)=>a[1]-b[1]).forEach(([node,hops],i) => {
                var bg = i%2===0 ? '#f8f9fa' : 'white';
                html += `<tr style="background:${bg};"><td style="padding:8px 12px; font-weight:bold;">${node}</td><td style="padding:8px 12px; text-align:center; color:#2980b9;">${hops}</td></tr>`;
            });
            return html + '</table>';
        }

        function renderTraffic(data) {
            var traffic = data.result ? data.result.traffic : data.traffic;
            if (!traffic) return '<p style="color:#e74c3c;">No data</p>';
            var html = '';
            for (var node in traffic) {
                html += `<div style="margin-bottom:14px;"><div style="font-weight:bold; font-size:13px; margin-bottom:6px; color:#2c3e50;">📡 ${node}</div><table style="width:100%; border-collapse:collapse; font-size:12px;"><tr style="background:#f0f0f0;"><th style="padding:6px 10px; text-align:left;">Interface</th><th style="padding:6px 10px; text-align:right;">RX bytes</th><th style="padding:6px 10px; text-align:right;">RX pkts</th><th style="padding:6px 10px; text-align:right;">TX bytes</th><th style="padding:6px 10px; text-align:right;">TX pkts</th></tr>`;
                for (var intf in traffic[node]) {
                    var d = traffic[node][intf];
                    // La consulta global (sense node) només retorna bytes, no packets.
                    var rxp = (d.rx_packets !== undefined && d.rx_packets !== null) ? d.rx_packets.toLocaleString() : '—';
                    var txp = (d.tx_packets !== undefined && d.tx_packets !== null) ? d.tx_packets.toLocaleString() : '—';
                    html += `<tr style="border-bottom:1px solid #eee;"><td style="padding:6px 10px; font-family:monospace;">${intf}</td><td style="padding:6px 10px; text-align:right;">${d.rx_bytes.toLocaleString()}</td><td style="padding:6px 10px; text-align:right;">${rxp}</td><td style="padding:6px 10px; text-align:right;">${d.tx_bytes.toLocaleString()}</td><td style="padding:6px 10px; text-align:right;">${txp}</td></tr>`;
                }
                html += '</table></div>';
            }
            return html;
        }

        function renderLatencyMatrix(data) {
            var r = data.result ? data.result : data;
            if (!r.ok) return `<p style="color:#e74c3c;">Error: ${r.error}</p>`;
            var ping = r.ping || {};
            var bw   = r.bandwidth || {};
            var hosts = r.hosts || [];

            // Build matrix
            var html = '<div style="font-size:12px; color:#7f8c8d; margin-bottom:10px;">Latency (avg ms) between all host pairs</div>';
            html += '<div style="overflow-x:auto;"><table style="border-collapse:collapse; font-size:12px; width:100%;">';
            html += '<tr><th style="padding:6px 10px; background:#2c3e50; color:white;"></th>';
            hosts.forEach(h => {
                html += `<th style="padding:6px 10px; background:#2c3e50; color:white; text-align:center;">${h}</th>`;
            });
            html += '</tr>';
            hosts.forEach(src => {
                html += `<tr><td style="padding:6px 10px; font-weight:bold; background:#f8f9fa;">${src}</td>`;
                hosts.forEach(dst => {
                    if (src === dst) {
                        html += `<td style="padding:6px 10px; text-align:center; color:#ccc; background:#f8f9fa;">—</td>`;
                    } else {
                        var key1 = src + '->' + dst;
                        var key2 = dst + '->' + src;
                        var val = ping[key1] ? ping[key1].avg : (ping[key2] ? ping[key2].avg : null);
                        var color = val === null ? '#aaa' : val < 0.5 ? '#27ae60' : val < 2 ? '#f39c12' : '#e74c3c';
                        var bg = val === null ? '#f8f9fa' : val < 0.5 ? '#f0fdf4' : val < 2 ? '#fffbeb' : '#fdf2f2';
                        html += `<td style="padding:6px 10px; text-align:center; color:${color}; background:${bg}; font-weight:bold;">${val !== null ? val + ' ms' : '—'}</td>`;
                    }
                });
                html += '</tr>';
            });
            html += '</table></div>';

            // Bandwidth table
            if (Object.keys(bw).length > 0) {
                html += '<div style="font-size:12px; color:#7f8c8d; margin:14px 0 8px;">Bandwidth (Mbits/sec)</div>';
                html += '<table style="border-collapse:collapse; font-size:12px; width:100%;">';
                html += '<tr style="background:#2c3e50; color:white;"><th style="padding:6px 10px; text-align:left;">Pair</th><th style="padding:6px 10px; text-align:right;">Min</th><th style="padding:6px 10px; text-align:right;">Avg</th><th style="padding:6px 10px; text-align:right;">Max</th></tr>';
                var i = 0;
                for (var pair in bw) {
                    var bg = i%2===0 ? '#f8f9fa' : 'white';
                    html += `<tr style="background:${bg};"><td style="padding:6px 10px;">${pair}</td><td style="padding:6px 10px; text-align:right;">${bw[pair].min}</td><td style="padding:6px 10px; text-align:right; font-weight:bold;">${bw[pair].avg}</td><td style="padding:6px 10px; text-align:right;">${bw[pair].max}</td></tr>`;
                    i++;
                }
                html += '</table>';
            }
            return html;
        }

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
            }
            document.getElementById('xrf-modal-result-content').innerHTML = '<div style="color:#7f8c8d; font-size:13px;">⏳ Loading...</div>';
            fetch('/xrf/query', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({xrf: id, params: params})})
            .then(r => r.json())
            .then(data => {
                var html = '';
                if (!data.ok) html = `<p style="color:#e74c3c;">Error: ${data.error}</p>`;
                else if (id === 'neighbors') html = renderNeighbors(data);
                else if (id === 'hops')      html = renderHops(data);
                else if (id === 'traffic')   html = renderTraffic(data);
                else if (id === 'latency_matrix') html = renderLatencyMatrix(data);
                document.getElementById('xrf-modal-result-content').innerHTML = html;
            });
        }

        initXRFPanel();

        // ═══════════════════════════════════════════════════════════════
        // PROPOSALS + TWINS — init (runs on both Original and Twin)
        // ═══════════════════════════════════════════════════════════════

        var isTwin = false;
        var _lastProposalCount = 0;  // track new arrivals for notifications

        fetch('/is_twin')
            .then(r => r.json())
            .then(data => {
                isTwin = data.is_twin;
                if (isTwin) {
                    // Twin: show single Propose dropdown
                    var wrap = document.getElementById('btn-propose-wrap');
                    if (wrap) wrap.style.display = 'inline-block';
                } else {
                    // Original: show Proposals btn + Twins toolbar indicator
                    var btnP = document.getElementById('btn-proposals');
                    if (btnP) btnP.style.display = 'inline-block';
                    var tw = document.getElementById('twins-toolbar-wrap');
                    if (tw) tw.style.display = 'inline-block';
                    refreshProposals();
                    setInterval(refreshProposals, 3000);
                    refreshTwinStatus();
                    setInterval(refreshTwinStatus, 5000);
                }
            });

        // ── Twins dropdown (Original toolbar) ──
        function toggleTwinsDD() {
            var dd = document.getElementById('twins-dd');
            if (!dd) return;
            dd.style.display = dd.style.display === 'block' ? 'none' : 'block';
        }
        document.addEventListener('click', function(e) {
            var wrap = document.getElementById('twins-toolbar-wrap');
            if (wrap && !wrap.contains(e.target)) {
                var dd = document.getElementById('twins-dd');
                if (dd) dd.style.display = 'none';
            }
        });

        // ── Propose dropdown (Twin) ──
        function toggleProposeDD() {
            var dd = document.getElementById('propose-dd');
            dd.style.display = dd.style.display === 'block' ? 'none' : 'block';
        }
        function closeProposeDD() {
            var dd = document.getElementById('propose-dd');
            if (dd) dd.style.display = 'none';
        }
        document.addEventListener('click', function(e) {
            var wrap = document.getElementById('btn-propose-wrap');
            if (wrap && !wrap.contains(e.target)) closeProposeDD();
        });

        // ═══════════════════════════════════════════════════════════════
        // PROPOSALS — Original side
        // ═══════════════════════════════════════════════════════════════

        function showProposalsModal() {
            document.getElementById('proposals-modal').style.display = 'flex';
            renderProposalsFull();
        }
        function closeProposalsModal() {
            document.getElementById('proposals-modal').style.display = 'none';
        }

        function refreshProposals() {
            fetch('/proposals')
                .then(r => r.json())
                .then(data => {
                    if (!data.ok) return;
                    var pending = data.proposals.filter(p => p.status === 'pending');
                    var count = pending.length;

                    // Toolbar badge
                    var badge = document.getElementById('proposals-toolbar-badge');
                    var btn   = document.getElementById('btn-proposals');
                    if (badge) {
                        badge.style.display = count > 0 ? 'inline' : 'none';
                        badge.textContent = count;
                    }
                    if (btn) {
                        btn.style.background = count > 0 ? '#8e44ad' : '#2c3e50';
                        btn.style.borderColor = count > 0 ? '#9b59b6' : '#34495e';
                    }

                    // Browser notification if new proposals arrived
                    if (count > _lastProposalCount && _lastProposalCount >= 0) {
                        var newCount = count - _lastProposalCount;
                        showProposalNotification(newCount, pending);
                    }
                    _lastProposalCount = count;

                    // If modal is open, refresh it too
                    var modal = document.getElementById('proposals-modal');
                    if (modal && modal.style.display === 'flex') {
                        renderProposalsFull(data.proposals);
                    }
                })
                .catch(() => {});
        }

        function showProposalNotification(n, pending) {
            // In-page toast notification
            var existing = document.getElementById('proposal-toast');
            if (existing) existing.remove();
            var toast = document.createElement('div');
            toast.id = 'proposal-toast';
            var latest = pending[pending.length - 1];
            var detail = latest
                ? (latest.op_type + ': ' + (latest.payload.name || '?') + ' from ' + latest.twin_ip)
                : '';
            toast.innerHTML = `<div style="display:flex;align-items:center;gap:10px;">
                <span style="font-size:18px;">📥</span>
                <div>
                    <div style="font-weight:bold;">New proposal${n > 1 ? 's' : ''} (${n})</div>
                    <div style="font-size:11px;color:#bdc3c7;">${detail}</div>
                </div>
                <button onclick="document.getElementById('proposal-toast').remove();showProposalsModal();"
                        style="margin-left:auto;background:#8e44ad;color:#fff;border:none;
                               padding:4px 10px;border-radius:4px;cursor:pointer;font-size:11px;">
                    Review
                </button>
            </div>`;
            toast.style.cssText = `position:fixed;bottom:24px;right:24px;z-index:9999;
                background:#1a2a3a;border:2px solid #8e44ad;border-radius:8px;
                padding:14px 16px;color:#ecf0f1;font-size:13px;min-width:280px;
                box-shadow:0 4px 20px rgba(0,0,0,0.6);animation:slideIn 0.3s ease;`;
            document.body.appendChild(toast);
            // Add animation
            if (!document.getElementById('toast-style')) {
                var s = document.createElement('style');
                s.id = 'toast-style';
                s.textContent = '@keyframes slideIn{from{transform:translateX(120%);opacity:0}to{transform:translateX(0);opacity:1}}';
                document.head.appendChild(s);
            }
            setTimeout(() => { if (toast.parentNode) toast.remove(); }, 8000);
            // Also try browser notification
            if ('Notification' in window && Notification.permission === 'granted') {
                new Notification('Digital Twin — New Proposal', {body: detail, icon: '/static/router.png'});
            } else if ('Notification' in window && Notification.permission !== 'denied') {
                Notification.requestPermission();
            }
        }

        function renderProposalsFull(proposals) {
            if (!proposals) {
                fetch('/proposals').then(r => r.json()).then(d => { if (d.ok) renderProposalsFull(d.proposals); });
                return;
            }
            var pending = proposals.filter(p => p.status === 'pending');
            var mBadge = document.getElementById('proposals-modal-badge');
            var list   = document.getElementById('proposals-modal-list');
            var empty  = document.getElementById('proposals-modal-empty');
            if (!list) return;
            if (mBadge) { mBadge.style.display = pending.length > 0 ? 'inline' : 'none'; mBadge.textContent = pending.length; }
            if (pending.length === 0) { list.innerHTML = ''; empty.style.display = 'block'; return; }
            empty.style.display = 'none';
            list.innerHTML = pending.map(function(p) {
                var ts = new Date(p.timestamp * 1000).toLocaleTimeString();
                var icon = p.op_type === 'add_router' ? '🔀' : p.op_type === 'remove_node' ? '🗑' : '💻';
                var detail = p.op_type === 'add_host'   ? (p.payload.name + ' → ' + p.payload.router)
                           : p.op_type === 'add_router' ? (p.payload.name + ' → [' + (p.payload.connected_routers||[]).join(', ') + ']')
                           : p.payload.name;
                return `<div style="background:#0d1f2d;border:1px solid #1a3a5c;border-radius:6px;
                                    padding:10px 12px;margin-bottom:8px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <div>
                            <span style="color:#3498db;font-size:12px;">${icon} ${p.op_type}</span>
                            <span style="color:#ecf0f1;font-size:12px;margin-left:6px;font-weight:bold;">${detail}</span>
                            <div style="color:#4a6278;font-size:11px;margin-top:3px;">
                                from <span style="color:#95a5a6;">${p.twin_ip}</span> · ${ts}
                            </div>
                        </div>
                        <div style="display:flex;gap:6px;">
                            <button onclick="approveProposal('${p.id}')"
                                    style="background:#27ae60;color:#fff;border:none;padding:5px 14px;
                                           border-radius:4px;cursor:pointer;font-size:12px;">✓ Approve</button>
                            <button onclick="rejectProposal('${p.id}')"
                                    style="background:#e74c3c;color:#fff;border:none;padding:5px 14px;
                                           border-radius:4px;cursor:pointer;font-size:12px;">✗ Reject</button>
                        </div>
                    </div>
                </div>`;
            }).join('');
        }

        function approveProposal(id) {
            fetch('/proposals/approve/' + id, {method:'POST'})
                .then(r => r.json())
                .then(data => {
                    if (data.ok) { refreshProposals(); setTimeout(() => location.reload(), 600); }
                    else { alert('Approve failed: ' + (data.error || '')); refreshProposals(); }
                });
        }

        function rejectProposal(id) {
            fetch('/proposals/reject/' + id, {method:'POST'})
                .then(r => r.json())
                .then(() => refreshProposals());
        }

        // ── Twin Status panel (Original sidebar) ──

        function refreshTwinStatus() {
            fetch('/proposals/twin_status')
                .then(r => r.json())
                .then(data => { if (data.ok) renderTwinStatus(data.twin_status); })
                .catch(() => {});
        }

        function renderTwinStatus(statuses) {
            var list  = document.getElementById('twin-status-list');
            var badge = document.getElementById('twins-count-badge');
            if (!list) return;
            var ips = Object.keys(statuses);
            // Badge: count only connected/diverged (not offline/disconnected)
            var activeCount = ips.filter(ip => ['connected','diverged'].includes(statuses[ip].status)).length;
            if (badge) badge.textContent = ips.length + ' (' + activeCount + ' active)';
            if (ips.length === 0) {
                list.innerHTML = '<span style="color:#4a6278;font-size:11px;">No twins registered yet.</span>';
                return;
            }
            list.innerHTML = ips.map(function(ip) {
                var s = statuses[ip];
                // Color by status
                var color = s.status === 'connected'    ? '#27ae60'
                          : s.status === 'diverged'     ? '#f39c12'
                          : s.status === 'offline'      ? '#95a5a6'
                          :                               '#e74c3c';  // disconnected
                var policyLabel = s.policy === 'disconnect' ? 'on error: disconnect' : 'on error: resync';

                // Resync button: shown when diverged, offline or disconnected
                var resyncBtn = (s.status !== 'connected')
                    ? `<button onclick="twinAction('${ip}','resync')"
                               title="Send full snapshot to restore Original state"
                               style="font-size:10px;background:#f39c12;color:#fff;border:none;
                                      padding:2px 6px;border-radius:3px;cursor:pointer;margin-right:3px;">
                          🔄 Resync</button>`
                    : '';

                // Main action button
                var mainBtn = s.status === 'disconnected'
                    ? `<button onclick="twinAction('${ip}','reconnect')"
                               title="Re-enable sync to this Twin"
                               style="font-size:10px;background:#27ae60;color:#fff;border:none;
                                      padding:2px 6px;border-radius:3px;cursor:pointer;">
                          Reconnect</button>`
                    : s.status === 'offline'
                    ? `<span style="font-size:10px;color:#95a5a6;font-style:italic;">no heartbeat</span>`
                    : `<button onclick="twinAction('${ip}','disconnect')"
                               title="Stop sending changes to this Twin"
                               style="font-size:10px;background:#e74c3c;color:#fff;border:none;
                                      padding:2px 6px;border-radius:3px;cursor:pointer;">
                          Disconnect</button>`;

                // Last seen
                var lastSeen = s.last_seen
                    ? new Date(s.last_seen * 1000).toLocaleTimeString()
                    : 'never';

                return `<div style="padding:5px 0;border-bottom:1px solid #1a2a3a;">
                    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:3px;">
                        <div>
                            <span style="color:${color};font-size:13px;">●</span>
                            <span style="color:#ecf0f1;font-size:11px;margin-left:3px;">${ip}</span>
                            <span style="color:${color};font-size:10px;margin-left:4px;">${s.status}</span>
                            <span style="color:#4a6278;font-size:10px;margin-left:4px;">· ${lastSeen}</span>
                        </div>
                        <div style="display:flex;gap:3px;align-items:center;">${resyncBtn}${mainBtn}</div>
                    </div>
                    <div style="color:#4a6278;font-size:10px;cursor:pointer;"
                         onclick="togglePolicy('${ip}','${s.policy}')"
                         title="Click to toggle divergence policy">⚙ ${policyLabel}</div>
                </div>`;
            }).join('');
        }

        function twinAction(ip, action) {
            fetch('/proposals/twin_action', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({twin_ip:ip, action:action})
            }).then(() => { refreshTwinStatus(); if (action==='resync') alert('Resync started for ' + ip); });
        }

        function togglePolicy(ip, currentPolicy) {
            var newPolicy = currentPolicy === 'resync' ? 'disconnect' : 'resync';
            fetch('/proposals/twin_action', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({twin_ip:ip, action:'set_policy', policy:newPolicy})
            }).then(() => refreshTwinStatus());
        }

        // ═══════════════════════════════════════════════════════════════
        // PROPOSALS — Twin side
        // ═══════════════════════════════════════════════════════════════

        var _proposeOpType = 'add_host';

        function showProposeModal(opType) {
            _proposeOpType = opType || 'add_host';
            document.getElementById('propose-modal').style.display = 'flex';
            document.getElementById('propose-form-host').style.display   = _proposeOpType === 'add_host'    ? 'block' : 'none';
            document.getElementById('propose-form-router').style.display = _proposeOpType === 'add_router'  ? 'block' : 'none';
            document.getElementById('propose-form-remove').style.display = _proposeOpType === 'remove_node' ? 'block' : 'none';
            document.getElementById('propose-status').style.display = 'none';
            // Populate dropdowns from current topology
            fetch('/topology').then(r => r.json()).then(data => {
                var routers = Object.keys(data.nodes).filter(n => data.nodes[n].type === 'router');
                var allNodes = Object.keys(data.nodes).filter(n => data.nodes[n].type !== 'switch');
                var rOpts  = routers.map(r  => `<option value="${r}">${r}</option>`).join('');
                var nOpts  = allNodes.map(n => `<option value="${n}">${n} (${data.nodes[n].type})</option>`).join('');
                var el1 = document.getElementById('propose-host-router');
                var el2 = document.getElementById('propose-router-connected');
                var el3 = document.getElementById('propose-remove-node');
                if (el1) el1.innerHTML = rOpts;
                if (el2) el2.innerHTML = rOpts;
                if (el3) el3.innerHTML = nOpts;
            });
        }

        function closeProposeModal() {
            document.getElementById('propose-modal').style.display = 'none';
        }

        function submitProposal() {
            var status = document.getElementById('propose-status');
            status.style.display = 'block'; status.style.color = '#f39c12';
            status.textContent = 'Sending proposal to Original...';
            var payload = {};
            if (_proposeOpType === 'add_host') {
                payload.name   = document.getElementById('propose-host-name').value.trim();
                payload.router = document.getElementById('propose-host-router').value;
                if (!payload.name) { status.textContent = 'Host name is required.'; return; }
            } else if (_proposeOpType === 'add_router') {
                payload.name = document.getElementById('propose-router-name').value.trim();
                var sel = document.getElementById('propose-router-connected');
                payload.connected_routers = Array.from(sel.selectedOptions).map(o => o.value);
                if (!payload.name) { status.textContent = 'Router name is required.'; return; }
            } else {
                payload.name = document.getElementById('propose-remove-node').value;
                if (!payload.name) { status.textContent = 'Select a node to remove.'; return; }
            }
            fetch('/propose_to_original', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({op_type: _proposeOpType, payload: payload})
            })
            .then(r => r.json())
            .then(data => {
                if (data.ok) {
                    status.style.color = '#27ae60';
                    status.textContent = '✓ Proposal sent! id: ' + data.proposal_id;
                    setTimeout(closeProposeModal, 2200);
                } else {
                    status.style.color = '#e74c3c';
                    status.textContent = '✗ ' + (data.error || 'Error');
                }
            })
            .catch(() => { status.style.color = '#e74c3c'; status.textContent = '✗ Network error'; });
        }