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

                // ── Link traffic coloring ──
                var _prevLinkBytes = {};
                function updateLinkColors() {
                    fetch('/metrics/link_traffic')
                    .then(r => r.json())
                    .then(d => {
                        if (!d.ok) return;
                        var routers = Object.keys(topologyData.nodes).filter(n => topologyData.nodes[n].type === 'router');
                        routers.forEach(function(rname) {
                            var props = topologyData.nodes[rname];
                            if (!props.p2p_links) return;
                            props.p2p_links.forEach(function(link) {
                                var key = rname + '-' + rname + '-' + link.local_intf;
                                var trafficKey = rname + '-' + rname + '-' + link.local_intf;
                                var entry = d.links[rname + '-' + rname + '-' + link.local_intf];
                                if (!entry) {
                                    entry = d.links[rname + '-' + link.local_intf];
                                }
                                if (!entry) return;
                                var fullKey = rname + '_' + link.local_intf;
                                var prev = _prevLinkBytes[fullKey];
                                var curr = entry.rx_bytes + entry.tx_bytes;
                                _prevLinkBytes[fullKey] = curr;
                                if (prev === undefined) return;
                                var bps = (curr - prev) / 3;
                                var color;
                                if (bps < 1000) color = '#27ae60';
                                else if (bps < 50000) color = '#f39c12';
                                else color = '#e74c3c';
                                var edgeId1 = rname + '___' + link.peer;
                                var edgeId2 = link.peer + '___' + rname;
                                if (edges.get(edgeId1)) {
                                    edges.update([{id: edgeId1, color: {color: color, highlight: color, hover: color}, width: bps < 1000 ? 2 : bps < 50000 ? 3 : 5}]);
                                } else if (edges.get(edgeId2)) {
                                    edges.update([{id: edgeId2, color: {color: color, highlight: color, hover: color}, width: bps < 1000 ? 2 : bps < 50000 ? 3 : 5}]);
                                }
                            });
                        });
                    })
                    .catch(() => {});
                }
                setInterval(updateLinkColors, 1000);
                updateLinkColors();

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
                    document.getElementById('popup-rename').style.display = 'none';
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

        function showRenameInput() {
            document.getElementById('rename-input').value = selectedNode;
            document.getElementById('popup-buttons').style.display = 'none';
            document.getElementById('popup-rename').style.display = 'block';
        }

        function closeRenameInput() {
            document.getElementById('popup-rename').style.display = 'none';
            document.getElementById('popup-buttons').style.display = 'block';
        }

        function renameNode() {
            var oldName = selectedNode;
            var newName = document.getElementById('rename-input').value.trim();
            if (!newName || newName === oldName) { closeRenameInput(); return; }
            document.getElementById('node-popup').style.display = 'none';
            fetch('/rename_node', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({old_name: oldName, new_name: newName})
            })
            .then(r => r.json())
            .then(data => {
                if (data.ok) setTimeout(() => location.reload(), 4000);
                else alert(data.error);
            });
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

            fetch(`/metrics/ping?src=${src}&dst=${dst}`)
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
            fetch('/metrics/sync')
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

            function fill(prefix, obj) {
                document.getElementById(prefix + '-min').textContent = obj && obj.min !== null ? obj.min + ' ms' : '—';
                document.getElementById(prefix + '-avg').textContent = obj && obj.avg !== null ? obj.avg + ' ms' : '—';
                document.getElementById(prefix + '-max').textContent = obj && obj.max !== null ? obj.max + ' ms' : '—';
            }
            fill('sync-local', stats.t_local);
            fill('sync-net',   stats.t_network);
            fill('sync-twin',  stats.t_twin);
            document.getElementById('sync-jitter').textContent =
                stats.jitter_ms !== null ? stats.jitter_ms + ' ms' : '—';

            var list   = document.getElementById('sync-history-list');
            list.innerHTML = '';
            var recent = data.history.slice(-8).reverse();
            recent.forEach(entry => {
                var d    = new Date(entry.timestamp * 1000);
                var time = d.toLocaleTimeString('ca-ES', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
                var net  = entry.t_network_ms !== null && entry.t_network_ms !== undefined ? entry.t_network_ms : '?';
                var color = net < 100 ? '#2ecc71' : net < 300 ? '#f39c12' : '#e74c3c';
                var detail = `local:${entry.t_local_ms ?? '?'}ms net:${net}ms twin:${entry.t_twin_ms ?? '?'}ms`;
                list.innerHTML += `<div class="sync-entry"><span class="sync-op">${time} · ${entry.operation}</span><span class="sync-ms" style="color:${color}" title="${detail}">${net} ms</span></div>`;
            });
        }

        function refreshSystemResources() {
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
                    }
                });
        }

        initXRFPanel();