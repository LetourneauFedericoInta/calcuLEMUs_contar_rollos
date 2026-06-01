/**
 * Log Counting Visor - UI & API Integrations Controller
 * Synchronizes DOM interactions, canvas, and mathematical charts.
 */

document.addEventListener('DOMContentLoaded', () => {
    // Read workspace parameters from bulletproof HTML5 dataset attributes
    const visorEl = document.getElementById('visor-data');
    const imageId = parseInt(visorEl.dataset.imageId);
    const imageSrc = visorEl.dataset.imageUrl;
    
    let optChartInstance = null;
    let signalChartInstance = null;
    let editor = null;

    // Loading overlay helper
    const showLoading = (show, text = 'Loading...') => {
        const alpineState = Alpine.$data(visorEl);
        alpineState.isLoading = show;
        alpineState.loadingText = text;
    };

    // Callback when line coordinates are added, dragged, or edited
    const handleLinesChanged = (lines) => {
        updateLinesTable(lines);
        updateSignalLinesDropdown(lines);
        
        // Auto-save coordinate lines to SQLite to maintain session persistence
        saveLinesData(lines, false);
    };

    // Instantiate HTML5 Canvas Editor
    editor = new CanvasEditor('canvas-editor', 'canvas-container', imageSrc, handleLinesChanged);

    // Initial Load - Fetch lines from SQLite database
    const loadLinesFromDb = async () => {
        showLoading(true, "Restoring workspace session...");
        try {
            const res = await fetch(`/api/image/${imageId}/lines`);
            const data = await res.json();
            
            // Map lines to editor state
            editor.lines = data.map(l => ({
                id: l.id,
                p1_x: l.p1_x,
                p1_y: l.p1_y,
                p2_x: l.p2_x,
                p2_y: l.p2_y,
                ground_truth: l.ground_truth,
                detected_count: l.detected_count,
                peaks: []
            }));
            
            // Run a soft initial peak detection if lines already exist and image is optimized
            if (editor.lines.length > 0) {
                updateLinesTable(editor.lines);
                updateSignalLinesDropdown(editor.lines);
                await runDetection(true);
            } else {
                updateLinesTable([]);
            }
        } catch (err) {
            console.error("Failed to load session lines:", err);
        } finally {
            showLoading(false);
        }
    };

    // Table rendering pipeline
    const updateLinesTable = (lines) => {
        const tbody = document.getElementById('lines-table-body');
        const counter = document.getElementById('txt-line-counter');
        
        counter.textContent = `${lines.length} Line${lines.length !== 1 ? 's' : ''}`;
        
        if (lines.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="5" class="p-6 text-center text-slate-500">No reference lines drawn yet</td>
                </tr>
            `;
            return;
        }

        tbody.innerHTML = lines.map((line, idx) => {
            const len = Math.round(Math.sqrt((line.p2_x - line.p1_x)**2 + (line.p2_y - line.p1_y)**2));
            const isSelected = (idx === editor.selectedLineIdx);
            
            // Calculate error percentage if Ground Truth is provided and non-zero
            let errorHtml = '<span class="text-slate-600 font-semibold font-mono text-[10px]">-</span>';
            if (line.ground_truth > 0 && line.detected_count !== undefined) {
                const err = Math.round((Math.abs(line.detected_count - line.ground_truth) / line.ground_truth) * 100);
                const colorClass = err === 0 ? 'text-brand-accent font-extrabold' : (err <= 10 ? 'text-emerald-400 font-bold' : 'text-amber-400 font-bold');
                errorHtml = `<span class="${colorClass} text-xs font-mono">${err}%</span>`;
            }

            return `
                <tr class="hover:bg-brand-dark/35 cursor-pointer border-b border-brand-border/20 transition-all ${isSelected ? 'bg-brand-accent/15 border-brand-accent/40 text-white font-semibold shadow-inner' : ''}" 
                    onclick="window.selectLineFromTable(${idx})">
                    <td class="p-2.5 font-bold ${isSelected ? 'text-brand-accent' : 'text-white'}">Line #${idx + 1}</td>
                    <td class="p-2.5 text-slate-400 font-mono text-[10px] leading-tight">
                        P1: [${Math.round(line.p1_x)}, ${Math.round(line.p1_y)}]<br>
                        P2: [${Math.round(line.p2_x)}, ${Math.round(line.p2_y)}] (${len}px)
                    </td>
                    <td class="p-2.5 text-center" onclick="event.stopPropagation()">
                        <div class="flex flex-col items-center gap-1">
                            <span class="text-[10px] font-extrabold text-brand-accent tracking-wide">Est: ${line.detected_count || 0}</span>
                            <input type="number" value="${line.ground_truth}" min="0" max="999" 
                                   class="w-14 bg-brand-dark border border-brand-border rounded px-1.5 py-0.5 text-center font-bold text-white outline-none focus:border-brand-accent text-[11px]"
                                   onchange="window.updateLineGT(${idx}, this.value)" />
                        </div>
                    </td>
                    <td class="p-2.5 text-center align-middle font-mono">${errorHtml}</td>
                    <td class="p-2.5 text-right" onclick="event.stopPropagation()">
                        <button class="w-7 h-7 rounded bg-red-500/10 hover:bg-red-500 text-red-400 hover:text-white transition-colors"
                                onclick="window.deleteLineClick(${idx})">
                            <i class="fa-solid fa-trash-can text-[10px]"></i>
                        </button>
                    </td>
                </tr>
            `;
        }).join('');
    };

    // Highlight row click selects canvas line
    window.selectLineFromTable = (idx) => {
        editor.selectedLineIdx = idx;
        editor.redraw();
        updateLinesTable(editor.lines);
        
        // Load the matching signal analysis profile immediately!
        const select = document.getElementById('select-chart-line');
        select.value = String(idx);
        loadSignalProfile(idx);
    };

    // Synchronize global click deletes and GT edits
    window.deleteLineClick = (idx) => {
        editor.deleteLine(idx);
        if (editor.selectedLineIdx === idx) {
            editor.selectedLineIdx = -1;
        }
    };

    window.updateLineGT = (idx, value) => {
        if (idx >= 0 && idx < editor.lines.length) {
            editor.lines[idx].ground_truth = parseInt(value) || 0;
            saveLinesData(editor.lines, false);
        }
    };

    // Update signal line dropdown list
    const updateSignalLinesDropdown = (lines) => {
        const select = document.getElementById('select-chart-line');
        const activeVal = select.value;
        
        select.innerHTML = '<option value="">-- Choose a Line --</option>' + 
            lines.map((_, idx) => `<option value="${idx}">Line #${idx + 1}</option>`).join('');
            
        if (activeVal !== "" && parseInt(activeVal) < lines.length) {
            select.value = activeVal;
        } else {
            select.value = "";
            document.getElementById('empty-sig-chart').style.display = 'flex';
            if (signalChartInstance) {
                signalChartInstance.destroy();
                signalChartInstance = null;
            }
        }
    };

    // Network request: Save drawn lines to SQLite
    const saveLinesData = async (lines, triggerNotification = true) => {
        if (triggerNotification) showLoading(true, "Saving lines...");
        try {
            const response = await fetch(`/api/image/${imageId}/lines`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ lines })
            });
            const result = await response.json();
            if (result.success) {
                // Instantly bind database primary key IDs to in-memory editor lines
                if (result.lines) {
                    result.lines.forEach((savedLine, idx) => {
                        if (editor.lines[idx]) {
                            editor.lines[idx].id = savedLine.id;
                        }
                    });
                }
                if (triggerNotification) {
                    alert("Reference lines persisted successfully!");
                }
            }
        } catch (err) {
            console.error("Failed to save lines to DB:", err);
        } finally {
            if (triggerNotification) showLoading(false);
        }
    };

    document.getElementById('btn-save-lines').addEventListener('click', () => {
        saveLinesData(editor.lines, true);
    });

    // Tool selectors events
    document.getElementById('ctrl-tool-draw').addEventListener('click', () => editor.setTool('draw'));
    document.getElementById('ctrl-tool-pan').addEventListener('click', () => editor.setTool('pan'));
    
    // Zoom control actions
    document.getElementById('ctrl-zoom-in').addEventListener('click', () => editor.zoom(1.2, editor.canvas.width/2, editor.canvas.height/2));
    document.getElementById('ctrl-zoom-out').addEventListener('click', () => editor.zoom(0.8, editor.canvas.width/2, editor.canvas.height/2));
    document.getElementById('ctrl-zoom-reset').addEventListener('click', () => {
        editor.resetView();
        editor.redraw();
    });
    
    document.getElementById('ctrl-delete-selected').addEventListener('click', () => {
        if (editor.selectedLineIdx !== -1) {
            editor.deleteLine(editor.selectedLineIdx);
            editor.selectedLineIdx = -1;
        } else {
            alert("No line selected! Click on a line segment in the canvas or a row in the table first.");
        }
    });

    document.getElementById('ctrl-clear-lines').addEventListener('click', () => {
        if (confirm("Are you sure you want to delete all drawn lines?")) {
            editor.clearAllLines();
        }
    });

    // Network request: Run active Peak Detection pipeline
    const runDetection = async (isInitial = false) => {
        if (!isInitial) showLoading(true, "Analyzing intensity signals...");
        try {
            const params = {
                gray_conversion: document.getElementById('param-gray-conv').value,
                pre_filter: document.getElementById('param-pre-filter').value,
                profile_type: document.getElementById('param-profile-type').value,
                band_width: parseInt(document.getElementById('param-band-width').value),
                distance_mode: document.getElementById('param-distance-mode').value,
                detection_method: document.getElementById('param-peak-method').value
            };

            const response = await fetch(`/api/image/${imageId}/run_detection`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(params)
            });
            const result = await response.json();
            
            if (result.success && result.detected_lines) {
                // Update editor peaks data
                result.detected_lines.forEach(detLine => {
                    const line = editor.lines.find(l => l.id === detLine.line_id || editor.lines.indexOf(l) === result.detected_lines.indexOf(detLine));
                    if (line) {
                        line.detected_count = detLine.detected_count;
                        line.peaks = detLine.peaks;
                    }
                });
                editor.redraw();
                updateLinesTable(editor.lines);
                
                // Refresh active profile chart if displayed
                const activeLineIdx = document.getElementById('select-chart-line').value;
                if (activeLineIdx !== "") {
                    loadSignalProfile(parseInt(activeLineIdx));
                }
            }
        } catch (err) {
            console.error("Detection execution failed:", err);
        } finally {
            if (!isInitial) showLoading(false);
        }
    };

    document.getElementById('btn-run-detection').addEventListener('click', () => runDetection(false));

    // Network request: Run combinatoric Grid Search Optimization
    document.getElementById('btn-grid-search').addEventListener('click', async () => {
        if (editor.lines.length === 0) {
            alert("Please draw at least one reference line and fill in Ground Truth counts before optimizing!");
            return;
        }

        // Verify if all lines have non-zero Ground Truths to prevent flat comparisons
        const missingGt = editor.lines.some(l => l.ground_truth === 0);
        if (missingGt && !confirm("Some drawn lines have a Ground Truth of 0. Optimize anyway?")) {
            return;
        }

        showLoading(true, "Evaluating 128 mathematical permutations...");
        try {
            const response = await fetch(`/api/image/${imageId}/optimize`, { method: 'POST' });
            const result = await response.json();
            
            if (result.success && result.top_configs) {
                // Update workspace status state
                const alpineState = Alpine.$data(visorEl);
                alpineState.status = 'Optimized';
                
                // Load best configurations directly into active parameter selectors
                const best = result.best_config;
                document.getElementById('param-gray-conv').value = best.gray_conversion;
                document.getElementById('param-pre-filter').value = best.pre_filter;
                document.getElementById('param-profile-type').value = best.profile_type;
                document.getElementById('param-band-width').value = String(best.band_width);
                document.getElementById('param-distance-mode').value = best.distance_mode;
                document.getElementById('param-peak-method').value = best.detection_method;
                
                // Render chart
                renderOptimizationChart(result.top_configs);
                
                // Immediately run detection with the newly loaded optimal parameters
                await runDetection(false);
                
                alert(`Optimization complete!\nOptimal Setup: ${best.gray_conversion} | ${best.profile_type} h=${best.band_width} | ${best.detection_method}\nWAPE Accuracy: ${(1.0 - best.wape)*100.0}%`);
            }
        } catch (err) {
            console.error("Optimization failed:", err);
            alert("Optimization failed: Check server console.");
        } finally {
            showLoading(false);
        }
    });

    // Render horizontal top 15 configs ranking bar chart
    const renderOptimizationChart = (configs) => {
        document.getElementById('empty-opt-chart').style.display = 'none';
        
        const categories = configs.map((c, i) => `#${i+1}: ${c.gray_conversion} | h=${c.band_width} | ${c.detection_method}`);
        const errorsData = configs.map(c => Math.round(c.wape * 1000) / 10); // WAPE error %
        const stdDevData = configs.map(c => Math.round(c.std_err * 10) / 10); // Std dev of line errors
        
        const options = {
            series: [{
                name: 'WAPE Error %',
                data: errorsData
            }],
            chart: {
                type: 'bar',
                height: 320,
                toolbar: { show: false },
                background: 'transparent',
                foreColor: '#94a3b8'
            },
            theme: { mode: 'dark' },
            colors: ['#10b981'], // Accent emerald green
            plotOptions: {
                bar: {
                    horizontal: true,
                    barHeight: '65%',
                    borderRadius: 4
                }
            },
            dataLabels: {
                enabled: true,
                formatter: (val) => `${val}%`,
                style: { colors: ['#ffffff'], fontSize: '10px' }
            },
            xaxis: {
                categories: categories,
                labels: { style: { fontFamily: 'Outfit' } },
                title: { text: 'Relative Error (WAPE %)', style: { fontFamily: 'Outfit' } }
            },
            yaxis: {
                labels: { 
                    maxWidth: 180,
                    style: { fontFamily: 'Outfit', fontSize: '9px' } 
                }
            },
            grid: {
                borderColor: '#1f294d',
                strokeDashArray: 4
            },
            tooltip: {
                shared: true,
                theme: 'dark',
                y: {
                    formatter: (val, opts) => {
                        const index = opts.dataPointIndex;
                        const cfg = configs[index];
                        return `${val}% (MAE: ${cfg.mae.toFixed(1)} | Std Dev: ${stdDevData[index]}px)`;
                    }
                }
            }
        };

        if (optChartInstance) {
            optChartInstance.destroy();
        }
        
        optChartInstance = new ApexCharts(document.getElementById('chart-opt-ranking'), options);
        optChartInstance.render();
    };

    // Network request: Fetch line profile and plot it
    const loadSignalProfile = async (lineIdx) => {
        const line = editor.lines[lineIdx];
        if (!line || !line.id) return;
        
        document.getElementById('empty-sig-chart').style.display = 'none';

        const paramsObj = {
            gray_conversion: document.getElementById('param-gray-conv').value,
            pre_filter: document.getElementById('param-pre-filter').value,
            profile_type: document.getElementById('param-profile-type').value,
            band_width: parseInt(document.getElementById('param-band-width').value),
            distance_mode: document.getElementById('param-distance-mode').value,
            detection_method: document.getElementById('param-peak-method').value
        };

        // Convert parameters to URL search queries
        const queryParams = new URLSearchParams(paramsObj).toString();
        
        try {
            const res = await fetch(`/api/image/${imageId}/line/${line.id}/profile?${queryParams}`);
            const data = await res.json();
            
            renderSignalChart(data);
        } catch (err) {
            console.error("Failed to load line profile:", err);
        }
    };

    // Render Dual-Axis Profile Signal Chart in ApexCharts
    const renderSignalChart = (data) => {
        const xCoords = data.x;
        const rawProfile = data.raw_profile;
        const normProfile = data.normalized_profile;
        const peaks = data.peaks; // Indices along raw data

        // Compile peaks markers series as a flat array aligned with normProfile
        const peaksScatterData = normProfile.map((val, idx) => {
            const isPeak = peaks.some(p => Math.round(p) === idx);
            return isPeak ? val : null;
        });

        const options = {
            series: [
                {
                    name: 'Raw Profile (std grayscale)',
                    type: 'line',
                    data: rawProfile
                },
                {
                    name: 'Normalized Filtered Profile',
                    type: 'line',
                    data: normProfile
                },
                {
                    name: 'Detected Center Peaks',
                    type: 'line', // Use line type with 0 width to act as a bulletproof scatter overlay
                    data: peaksScatterData
                }
            ],
            chart: {
                height: 380,
                type: 'line',
                toolbar: { show: false },
                background: 'transparent',
                foreColor: '#94a3b8',
                events: {
                    mouseLeave: function(event, chartContext, config) {
                        if (editor && editor.hoveredChartPoint) {
                            editor.hoveredChartPoint = null;
                            editor.redraw();
                        }
                    }
                }
            },
            theme: { mode: 'dark' },
            colors: ['#3b82f6', '#10b981', '#ef4444'], // Raw is blue, normalized is green, peaks are red
            stroke: {
                width: [1.5, 2.5, 0],
                curve: 'smooth'
            },
            markers: {
                size: [0, 0, 7], // Scatter peaks are large, line series are hidden markers
                shape: 'square'
            },
            xaxis: {
                type: 'numeric',
                title: { text: 'Pixel Offset Along Reference Line', style: { fontFamily: 'Outfit' } },
                labels: { style: { fontFamily: 'Outfit' } }
            },
            yaxis: [
                {
                    seriesName: 'Raw Profile (std grayscale)',
                    title: { text: 'Raw Grayscale Intensity', style: { fontFamily: 'Outfit', color: '#3b82f6' } },
                    labels: { 
                        style: { colors: '#3b82f6', fontFamily: 'Outfit' },
                        formatter: (val) => val !== undefined && val !== null ? val.toFixed(0) : ''
                    }
                },
                {
                    seriesName: 'Normalized Filtered Profile',
                    opposite: true,
                    title: { text: 'Normalized Intensity', style: { fontFamily: 'Outfit', color: '#10b981' } },
                    labels: { 
                        style: { colors: '#10b981', fontFamily: 'Outfit' },
                        formatter: (val) => val !== undefined && val !== null ? val.toFixed(1) : ''
                    }
                },
                {
                    seriesName: 'Normalized Filtered Profile', // Scale peaks Scatter to match Normalized line scale
                    show: false // Hide duplicate Y-axis scale
                }
            ],
            grid: {
                borderColor: '#1f294d',
                strokeDashArray: 4
            },
            tooltip: {
                theme: 'dark',
                shared: true,
                custom: function({ series, seriesIndex, dataPointIndex, w }) {
                    // Update canvas hover coordinate in real-time
                    if (dataPointIndex !== undefined && dataPointIndex !== -1 && dataPointIndex >= 0 && editor && editor.selectedLineIdx !== -1) {
                        const line = editor.lines[editor.selectedLineIdx];
                        if (line) {
                            const N = data.x.length;
                            if (N > 1) {
                                const ratio = dataPointIndex / (N - 1);
                                const px = line.p1_x + ratio * (line.p2_x - line.p1_x);
                                const py = line.p1_y + ratio * (line.p2_y - line.p1_y);
                                editor.hoveredChartPoint = { x: px, y: py };
                                editor.redraw();
                            }
                        }
                    }
                    
                    // Return an empty, invisible div to prevent the large popup tooltip from rendering
                    return '<div style="display: none; height: 0; width: 0; padding: 0; border: none; background: transparent;"></div>';
                }
            }
        };

        if (signalChartInstance) {
            signalChartInstance.destroy();
        }
        
        signalChartInstance = new ApexCharts(document.getElementById('chart-signal-analysis'), options);
        signalChartInstance.render();
    };

    // Chart toggle tabs behavior
    const tabBtnOpt = document.getElementById('tab-btn-opt');
    const tabBtnSig = document.getElementById('tab-btn-sig');
    const tabContentOpt = document.getElementById('tab-content-opt');
    const tabContentSig = document.getElementById('tab-content-sig');

    tabBtnOpt.addEventListener('click', () => {
        tabBtnOpt.className = "text-xs font-bold uppercase tracking-wider pb-2 border-b-2 border-brand-accent text-white outline-none";
        tabBtnSig.className = "text-xs font-bold uppercase tracking-wider pb-2 border-b-2 border-transparent text-slate-400 hover:text-white outline-none";
        tabContentOpt.classList.remove('hidden');
        tabContentSig.classList.add('hidden');
    });

    tabBtnSig.addEventListener('click', () => {
        tabBtnSig.className = "text-xs font-bold uppercase tracking-wider pb-2 border-b-2 border-brand-accent text-white outline-none";
        tabBtnOpt.className = "text-xs font-bold uppercase tracking-wider pb-2 border-b-2 border-transparent text-slate-400 hover:text-white outline-none";
        tabContentSig.classList.remove('hidden');
        tabContentOpt.classList.add('hidden');
    });

    // Dropdown signal line select event
    document.getElementById('select-chart-line').addEventListener('change', (e) => {
        const val = e.target.value;
        if (val !== "") {
            const idx = parseInt(val);
            // Highlight the line on the canvas and table automatically!
            editor.selectedLineIdx = idx;
            editor.redraw();
            updateLinesTable(editor.lines);
            
            loadSignalProfile(idx);
        } else {
            editor.selectedLineIdx = -1;
            editor.redraw();
            updateLinesTable(editor.lines);
            
            document.getElementById('empty-sig-chart').style.display = 'flex';
            if (signalChartInstance) {
                signalChartInstance.destroy();
                signalChartInstance = null;
            }
        }
    });

    // Export Session JSON Trigger
    document.getElementById('btn-export-session').addEventListener('click', async () => {
        try {
            const res = await fetch(`/api/image/${imageId}/export_json`);
            const data = await res.json();
            
            // Trigger browser download dialog
            const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(data, null, 2));
            const downloadAnchor = document.createElement('a');
            downloadAnchor.setAttribute("href", dataStr);
            downloadAnchor.setAttribute("download", `conteo_session_${data.image.filename}.json`);
            document.body.appendChild(downloadAnchor);
            downloadAnchor.click();
            downloadAnchor.remove();
        } catch (err) {
            console.error("Export session failed:", err);
        }
    });

    // Core Init Launcher
    loadLinesFromDb();
});
