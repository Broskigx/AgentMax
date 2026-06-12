'use strict';

// ── State ────────────────────────────────────────────────────────────────────
let lossData    = [];
let lossChart   = null;
let isMaxPower  = false;
let activeProcess = null; // 'coordinator' | 'contributor' | 'training'

// Fix H7: track known devices by ID to avoid duplicates
const knownDevices = new Map(); // id -> row element (main coordinator panel)
const allDevices   = new Map(); // id -> data (devices tab)
const lastLossKeyByDevice = new Map(); // id -> round:step:loss

function clampPowerTarget(value) {
  const n = Number.parseFloat(value);
  if (!Number.isFinite(n)) return 90;
  return Math.max(10, Math.min(95, n));
}

// ── Tabs ─────────────────────────────────────────────────────────────────────
function switchTab(name) {
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  const content = document.getElementById(`tab-${name}`);
  const btn = document.querySelector(`[data-tab="${name}"]`);
  if (content) content.classList.add('active');
  if (btn) btn.classList.add('active');
}

// ── Chart ─────────────────────────────────────────────────────────────────────
function initChart() {
  if (typeof Chart === 'undefined') {
    addLog('[WARN] Chart.js no cargado; la tabla de dispositivos sigue activa.');
    return;
  }
  const ctx = document.getElementById('loss-chart').getContext('2d');
  lossChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [{
        label: 'Loss',
        data: [],
        borderColor: '#22c55e',
        backgroundColor: 'rgba(34,197,94,0.04)',
        fill: true,
        tension: 0.4,
        pointRadius: 0,
        borderWidth: 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: {
        x: { display: false },
        y: {
          beginAtZero: false,
          grid: { color: 'rgba(255,255,255,0.04)' },
          ticks: { color: '#52525b', font: { size: 10 } },
        },
      },
      plugins: { legend: { display: false } },
    },
  });
}

function addLossPoint(value) {
  const v = parseFloat(value);
  if (isNaN(v)) return;
  lossData.push(v);
  if (lossChart) {
    lossChart.data.labels.push(lossData.length);
    lossChart.data.datasets[0].data.push(v);
    if (lossChart.data.labels.length > 300) {
      lossChart.data.labels.shift();
      lossChart.data.datasets[0].data.shift();
    }
    lossChart.update('none');
  }
  document.getElementById('chart-panel').style.display = '';
  document.getElementById('stats-row').style.display = 'grid';
  const el = document.getElementById('current-loss');
  if (el) { el.style.display = ''; el.textContent = v.toFixed(4); }
  const statEl = document.getElementById('stat-loss');
  if (statEl) statEl.textContent = v.toFixed(4);
}

// ── Logs ─────────────────────────────────────────────────────────────────────
function addLog(text) {
  if (typeof text !== 'string') text = JSON.stringify(text);
  const container = document.getElementById('log-container');
  if (!container) return;

  const div = document.createElement('div');
  div.textContent = text;

  const t = text.toLowerCase();
  if (text.includes("'loss':") || text.includes('"loss":')) {
    div.className = 'log-loss';
    const m = text.match(/'loss':\s*([\d.]+)/);
    if (m) addLossPoint(m[1]);
  } else if (t.includes('error') || t.includes('traceback') || t.includes('exception')) {
    div.className = 'log-error';
  } else if (t.includes('warn')) {
    div.className = 'log-warn';
  } else if (text.startsWith('[') && (t.includes('ok') || t.includes('done') || t.includes('save'))) {
    div.className = 'log-sys';
  } else if (text.startsWith('[')) {
    div.className = 'log-info';
  } else {
    div.className = 'log-plain';
  }

  // Parse hardware JSON
  if (text.startsWith('[HARDWARE_JSON]')) {
    try {
      const hw = JSON.parse(text.slice(15).trim());
      renderHardware(hw);
    } catch (_) {}
    div.style.display = 'none'; // hide raw JSON from log
  }

  // Parse device list from coordinator
  if (text.startsWith('[DEVICE_LIST]')) {
    try {
      const devices = JSON.parse(text.slice(13).trim());
      updateDeviceTable(devices);
    } catch (_) {}
  }

  // Coordinator connection count
  const connMatch = text.match(/Total contributors:\s*(\d+)/);
  if (connMatch) updateContribCount(parseInt(connMatch[1]));

  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  if (container.children.length > 1000) container.removeChild(container.firstChild);

  updateStatus(text);
}

function clearLogs() {
  const c = document.getElementById('log-container');
  if (c) c.innerHTML = '';
}

function updateStatus(text) {
  const dot  = document.getElementById('status-dot');
  const stxt = document.getElementById('status-text');
  if (!dot) return;
  const t = text.toLowerCase();
  if (text.includes("'loss':") || t.includes('[train]') || t.includes('training')) {
    dot.className  = 'sdot running';
    stxt.textContent = 'Entrenando...';
  } else if (t.includes('[coord]') || t.includes('coordinator')) {
    dot.className  = 'sdot running';
    stxt.textContent = 'Coordinando...';
  } else if (t.includes('error') || t.includes('traceback')) {
    dot.className  = 'sdot error';
    stxt.textContent = 'Error';
  }
}

// ── Hardware rendering ────────────────────────────────────────────────────────
function renderHardware(hw) {
  const gpu   = hw.gpu   || {};
  const cpu   = hw.cpu   || {};
  const ram   = hw.ram   || {};
  const bench = hw.benchmark || {};

  // GPU card
  const gpuContent = document.getElementById('hw-gpu-content');
  if (gpuContent && gpu.available) {
    const usedPct = gpu.vram_total_gb > 0
      ? Math.round((1 - gpu.vram_free_gb / gpu.vram_total_gb) * 100) : 0;
    const barClass = usedPct > 85 ? 'red' : usedPct > 65 ? 'amber' : 'green';

    gpuContent.innerHTML = `
      <div class="hw-row"><span class="hw-key">GPU</span><span class="hw-val green">${gpu.gpu}</span></div>
      <div class="hw-row"><span class="hw-key">VRAM Total</span><span class="hw-val">${gpu.vram_total_gb} GB</span></div>
      <div class="hw-row"><span class="hw-key">VRAM Libre</span><span class="hw-val green">${gpu.vram_free_gb} GB</span></div>
      <div class="hw-row"><span class="hw-key">Temperatura</span><span class="hw-val ${gpu.temp_c > 80 ? 'amber' : ''}">${gpu.temp_c}°C</span></div>
      <div class="hw-row"><span class="hw-key">Power</span><span class="hw-val">${gpu.power_draw_w}W / ${gpu.power_limit_w}W</span></div>
      <div class="hw-row"><span class="hw-key">Driver</span><span class="hw-val">${gpu.driver_version}</span></div>
      <div class="hw-row"><span class="hw-key">CUDA</span><span class="hw-val">${gpu.cuda_version}</span></div>
      <div class="vram-bar-wrap">
        <div class="vram-bar-label"><span>VRAM utilizada</span><span>${usedPct}%</span></div>
        <div class="vram-bar-bg"><div class="vram-bar-fill ${barClass}" style="width:${usedPct}%"></div></div>
      </div>`;
    const badge = document.getElementById('hw-gpu-badge');
    if (badge) { badge.style.display = ''; badge.textContent = 'NVIDIA'; }
  } else if (gpuContent) {
    gpuContent.innerHTML = `<div class="hw-row"><span class="hw-key">GPU</span><span class="hw-val" style="color:#ef4444">No GPU NVIDIA detectada</span></div>`;
  }

  // CPU + RAM card
  const cpuContent = document.getElementById('hw-cpu-content');
  if (cpuContent) {
    const ramUsedPct = ram.used_pct || 0;
    cpuContent.innerHTML = `
      <div class="hw-row"><span class="hw-key">CPU</span><span class="hw-val">${cpu.name || '?'}</span></div>
      <div class="hw-row"><span class="hw-key">Núcleos</span><span class="hw-val">${cpu.cores_physical}P / ${cpu.cores_logical}L</span></div>
      <div class="hw-row"><span class="hw-key">Freq. Máx</span><span class="hw-val green">${cpu.freq_max_ghz} GHz</span></div>
      <div class="hw-row"><span class="hw-key">RAM Total</span><span class="hw-val">${ram.total_gb} GB</span></div>
      <div class="hw-row"><span class="hw-key">RAM Libre</span><span class="hw-val green">${ram.free_gb} GB</span></div>
      <div class="vram-bar-wrap">
        <div class="vram-bar-label"><span>RAM utilizada</span><span>${ramUsedPct}%</span></div>
        <div class="vram-bar-bg"><div class="vram-bar-fill ${ramUsedPct > 85 ? 'red' : 'green'}" style="width:${ramUsedPct}%"></div></div>
      </div>`;
  }

  // Benchmark
  const benchContent = document.getElementById('hw-bench-content');
  if (benchContent) {
    if (bench.available) {
      benchContent.innerHTML = `
        <div class="hw-row"><span class="hw-key">TFLOPS FP16</span><span class="hw-val green">${bench.tflops_fp16} TFLOPS</span></div>
        <div class="hw-row"><span class="hw-key">VRAM Pico</span><span class="hw-val">${bench.vram_peak_gb} GB</span></div>`;
    } else {
      benchContent.innerHTML = `<div class="hw-placeholder">Benchmark no disponible (sin GPU CUDA).</div>`;
    }
  }

  // Auto config
  const vram     = gpu.vram_free_gb || gpu.vram_total_gb || 0;
  const ramTotal = ram.total_gb || 0;
  const cores    = cpu.cores_logical || 8;
  const tflops   = bench.tflops_fp16 || 0;
  renderOptimalConfig(vram, ramTotal, cores, tflops, gpu.gpu || '');
}

function renderOptimalConfig(vram, ram, cores, tflops, gpuName) {
  // Simple table lookup (mirrors auto_config_engine.py)
  const table = [
    [0,4,  1,8,  2,2048,8],
    [4,6,  1,16, 4,2048,6],
    [6,8,  1,16, 4,4096,4],
    [8,10, 2,32, 8,4096,4],
    [10,12,4,64, 8,4096,2],
    [12,16,4,64,12,6144,2],
    [16,24,8,128,16,8192,1],
    [24,999,8,128,16,8192,1],
  ];
  let bs=1, rank=8, workers=2, seq=2048, ga=8;
  for (const [vmin,vmax,b,r,w,s,g] of table) {
    if (vram >= vmin && vram < vmax) { bs=b; rank=r; workers=w; seq=s; ga=g; break; }
  }
  workers = Math.min(workers, Math.max(1, cores - 1));

  const eff = bs * ga;
  const t   = tflops || 8;
  const eta = Math.round(400 * (seq/2) * 3 / (t * 800000) * 10) / 10;

  const cfg = { batch_size: bs, lora_rank: rank, num_workers: workers,
                max_seq_length: seq, gradient_accumulation_steps: ga,
                effective_batch: eff, eta_hours: eta };

  const el = document.getElementById('hw-config-content');
  const btn = document.getElementById('btn-apply-config');
  if (!el) return;

  const warnings = [];
  if (vram < 4) warnings.push('VRAM muy baja (<4 GB) — puede haber OOM');
  if (ram < 8)  warnings.push('RAM baja (<8 GB) — posible swapping lento');

  el.innerHTML = `
    <div class="config-chips">
      <div class="config-chip">Batch/GPU <span>${bs}</span></div>
      <div class="config-chip">LoRA rank <span>${rank}</span></div>
      <div class="config-chip">Workers <span>${workers}</span></div>
      <div class="config-chip">Max seq <span>${seq}</span></div>
      <div class="config-chip">Grad accum <span>${ga}</span></div>
      <div class="config-chip">Batch efectivo <span>${eff}</span></div>
      <div class="config-chip">ETA estimada <span>~${eta}h</span></div>
    </div>
    ${warnings.length ? `<div class="warning-list">${warnings.map(w=>`<div class="warning-item">⚠️ ${w}</div>`).join('')}</div>` : ''}`;

  if (btn) {
    btn.style.display = '';
    btn._cfg = cfg;
    btn.onclick = () => applyRecommendedConfig(cfg);
  }
}

function applyRecommendedConfig(cfg) {
  const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };
  set('hp-batch',     cfg.batch_size);
  set('hp-lora',      cfg.lora_rank);
  set('hp-workers',   cfg.num_workers);
  set('hp-seq',       cfg.max_seq_length);
  set('hp-grad-accum',cfg.gradient_accumulation_steps);
  addLog('[OK] Config recomendada aplicada a hiperparámetros DDP.');
  switchTab('ddp');
}

// ── Device table (Fix H7: dedup by ID) ───────────────────────────────────────
function updateDeviceTable(devices) {
  if (!Array.isArray(devices)) return;

  const tbody     = document.getElementById('device-tbody');
  const allTbody  = document.getElementById('all-devices-tbody');
  const badge     = document.getElementById('contrib-count');
  const devBadge  = document.getElementById('device-count-badge');
  const nsCount   = document.getElementById('ns-count');
  const nsVram    = document.getElementById('ns-vram');
  const nsLoss    = document.getElementById('ns-loss');
  const nsCpu     = document.getElementById('ns-cpu');
  const nsRam     = document.getElementById('ns-ram');
  const nsPower   = document.getElementById('ns-power');
  const fmtNum = (value, digits = 1) => Number(value || 0).toFixed(digits);
  const fmtPct = (value) => `${fmtNum(value, 1)}%`;

  // Remove placeholder
  tbody?.querySelector('.placeholder-row')?.remove();
  allTbody?.querySelector('.placeholder-row')?.remove();

  const activeIds = new Set(devices.map(d => d.id));

  // Remove stale rows
  for (const [id] of knownDevices) {
    if (!activeIds.has(id)) {
      knownDevices.get(id)?.remove();
      knownDevices.delete(id);
    }
  }
  for (const [id] of allDevices) {
    if (!activeIds.has(id)) {
      allDevices.get(id)?.remove();
      allDevices.delete(id);
    }
  }

  let totalVram = 0, usedVram = 0, lossSum = 0, lossCount = 0;
  let cpuSum = 0, ramSum = 0, gpuSum = 0, powerSum = 0;
  let cpuCount = 0, ramCount = 0, gpuCount = 0, powerCount = 0;

  devices.forEach(d => {
    totalVram += d.vram_gb || 0;
    usedVram += d.vram_used || 0;
    if (d.cpu_util != null) { cpuSum += Number(d.cpu_util) || 0; cpuCount++; }
    if (d.ram_pct != null) { ramSum += Number(d.ram_pct) || 0; ramCount++; }
    if (d.gpu_util != null) { gpuSum += Number(d.gpu_util) || 0; gpuCount++; }
    if (d.power_w != null) { powerSum += Number(d.power_w) || 0; powerCount++; }

    if (d.loss != null) {
      lossSum += d.loss;
      lossCount++;
      const key = `${d.round || 0}:${d.step || 0}:${d.loss}`;
      if (lastLossKeyByDevice.get(d.id) !== key) {
        lastLossKeyByDevice.set(d.id, key);
        addLossPoint(d.loss);
      }
    }

    const statusHtml = `<span class="dev-status ${d.status === 'training' ? 'training' : d.status === 'idle' ? 'idle' : 'waiting'}">${d.status || 'idle'}</span>`;

    // Mini table (coordinator tab)
    if (tbody) {
      if (knownDevices.has(d.id)) {
        const row = knownDevices.get(d.id);
        row.cells[4].innerHTML = statusHtml;
        row.cells[5].textContent = d.loss != null ? d.loss.toFixed(4) : '-';
      } else {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${d.id}</td><td>${d.hostname || `node-${d.id}`}</td>
          <td style="color:#22c55e;font-weight:600">${(d.gpu||'?').replace('NVIDIA ','')}</td>
          <td>${d.vram_gb || 0} GB</td><td>${statusHtml}</td>
          <td>${d.loss != null ? d.loss.toFixed(4) : '-'}</td>`;
        tbody.appendChild(tr);
        knownDevices.set(d.id, tr);
      }
    }

    // Full table (devices tab)
    if (allTbody) {
      if (allDevices.has(d.id)) {
        const row = allDevices.get(d.id);
        row.cells[5].innerHTML = statusHtml;
        row.cells[6].textContent = d.round || '-';
        row.cells[7].textContent = d.step  || '-';
        row.cells[8].textContent = d.loss != null ? d.loss.toFixed(4) : '-';
        row.cells[9].textContent = fmtPct(d.cpu_util);
        row.cells[10].textContent = fmtPct(d.ram_pct);
        row.cells[11].textContent = fmtPct(d.gpu_util);
        row.cells[12].textContent = `${fmtNum(d.vram_used)} GB`;
        row.cells[13].textContent = `${fmtNum(d.power_w)} W`;
        row.cells[14].textContent = `${fmtNum(d.temp_c)} C`;
        row.cells[15].textContent = fmtPct(d.power_target);
      } else {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${d.id}</td>
          <td>${d.hostname || `node-${d.id}`}</td>
          <td style="color:#22c55e;font-weight:600">${d.gpu || '?'}</td>
          <td>${d.vram_gb || 0}</td>
          <td>${d.cores || '-'}</td>
          <td>${statusHtml}</td>
          <td>${d.round || '-'}</td>
          <td>${d.step  || '-'}</td>
          <td>${d.loss  != null ? d.loss.toFixed(4) : '-'}</td>
          <td>${fmtPct(d.cpu_util)}</td>
          <td>${fmtPct(d.ram_pct)}</td>
          <td>${fmtPct(d.gpu_util)}</td>
          <td>${fmtNum(d.vram_used)} GB</td>
          <td>${fmtNum(d.power_w)} W</td>
          <td>${fmtNum(d.temp_c)} C</td>
          <td>${fmtPct(d.power_target)}</td>`;
        allTbody.appendChild(tr);
        allDevices.set(d.id, tr);
      }
    }
  });

  const n = devices.length;
  const avgCpu = cpuCount > 0 ? cpuSum / cpuCount : null;
  const avgRam = ramCount > 0 ? ramSum / ramCount : null;
  const avgGpu = gpuCount > 0 ? gpuSum / gpuCount : null;
  const totalPower = powerCount > 0 ? powerSum : null;
  const gpuStat = document.getElementById('stat-gpu');
  const vramStat = document.getElementById('stat-vram');
  const cpuStat = document.getElementById('stat-cpu');
  const ramStat = document.getElementById('stat-ram');
  const powerStat = document.getElementById('stat-power');
  if (gpuStat) gpuStat.textContent = avgGpu != null ? fmtPct(avgGpu) : '-';
  if (vramStat) vramStat.textContent = usedVram > 0 ? `${fmtNum(usedVram)} GB` : '-';
  if (cpuStat) cpuStat.textContent = avgCpu != null ? fmtPct(avgCpu) : '-';
  if (ramStat) ramStat.textContent = avgRam != null ? fmtPct(avgRam) : '-';
  if (powerStat) powerStat.textContent = totalPower != null ? `${fmtNum(totalPower)} W` : '-';
  if (badge)    badge.textContent = n;
  if (devBadge) devBadge.textContent = `${n} conectados`;
  if (nsCount)  nsCount.textContent = n;
  if (nsVram)   nsVram.textContent  = totalVram.toFixed(1) + ' GB';
  if (nsLoss)   nsLoss.textContent  = lossCount > 0 ? (lossSum / lossCount).toFixed(4) : '-';
  if (nsCpu)    nsCpu.textContent   = avgCpu != null ? fmtPct(avgCpu) : '-';
  if (nsRam)    nsRam.textContent   = avgRam != null ? fmtPct(avgRam) : '-';
  if (nsPower)  nsPower.textContent = totalPower != null ? `${fmtNum(totalPower)} W` : '-';
}

function updateContribCount(n) {
  const badge = document.getElementById('contrib-count');
  if (badge) badge.textContent = n;
}

// ── Training controls ─────────────────────────────────────────────────────────
async function startTraining() {
  if (!window.__TAURI__) { addLog('[WARN] No hay backend Tauri'); return; }
  const cfg = {
    mode:          document.getElementById('mode-select').value,
    masterAddr:    document.getElementById('master-addr').value,
    masterPort:    document.getElementById('master-port').value,
    venvPython:    document.getElementById('venv-path').value,
    trainDir:      document.getElementById('train-dir-ddp').value,
    maxPower:      isMaxPower,
    autoConfig:    document.getElementById('hp-auto').checked,
    batchSize:     parseInt(document.getElementById('hp-batch').value)     || 2,
    loraRank:      parseInt(document.getElementById('hp-lora').value)      || 32,
    maxSeqLength:  parseInt(document.getElementById('hp-seq').value)       || 4096,
    numWorkers:    parseInt(document.getElementById('hp-workers').value)   || 8,
    learningRate:  parseFloat(document.getElementById('hp-lr').value)      || 2e-4,
    gradAccum:     parseInt(document.getElementById('hp-grad-accum').value)|| 4,
    epochs:        parseFloat(document.getElementById('hp-epochs').value)  || 3,
  };
  if (!cfg.trainDir) { addLog('[ERROR] Configura el Directorio training'); return; }
  try {
    const result = await window.__TAURI__.core.invoke('start_training', cfg);
    addLog(`[OK] ${result}`);
    setProcessActive('training');
  } catch (err) { addLog(`[ERROR] ${err}`); }
}

async function startCoordinator() {
  if (!window.__TAURI__) { addLog('[WARN] No hay backend Tauri'); return; }
  const networkId = document.getElementById('zt-network-id-main')?.value.trim();
  const cfg = {
    trainDir:        document.getElementById('train-dir-coord').value,
    coordinatorPort: parseInt(document.getElementById('coord-port').value)   || 12356,
    minContributors: parseInt(document.getElementById('coord-min').value)    || 1,
    maxContributors: parseInt(document.getElementById('coord-max').value)    || 100,
    rounds:          parseInt(document.getElementById('coord-rounds').value) || 3,
    epochsPerRound:  parseInt(document.getElementById('coord-epochs').value) || 1,
  };
  if (!cfg.trainDir) { addLog('[ERROR] Configura el Directorio training'); return; }
  try {
    if (networkId) {
      addLog(`[ZT] Uniendose a red ${networkId}...`);
      const ztResult = await window.__TAURI__.core.invoke('join_zerotier_network', { networkId });
      addLog(`[ZT] ${ztResult}`);
      addLog('[ZT] Autoriza este Master en my.zerotier.com si todavia no aparece OK.');
    }
    const result = await window.__TAURI__.core.invoke('start_coordinator', cfg);
    addLog(`[OK] ${result}`);
    addLog(`[COORD] Puerto ${cfg.coordinatorPort} — esperando ${cfg.minContributors}+ contribuyentes...`);
    setProcessActive('coordinator');
  } catch (err) { addLog(`[ERROR] ${err}`); }
}

// Fix M12: actually invoke start_contributor instead of just showing text
async function connectContributor() {
  if (!window.__TAURI__) {
    addLog('[WARN] No hay backend Tauri');
    return;
  }
  const ip       = document.getElementById('contrib-ip').value.trim();
  const port     = parseInt(document.getElementById('contrib-port').value) || 12356;
  const trainDir = document.getElementById('train-dir-contrib').value;
  const torchrun = document.getElementById('contrib-torchrun').value;
  const powerTarget = clampPowerTarget(document.getElementById('contrib-power-target')?.value ?? 90);

  if (!ip)       { addLog('[ERROR] Ingresa la IP del coordinador'); return; }
  if (!trainDir) { addLog('[ERROR] Configura el Directorio training'); return; }

  try {
    const result = await window.__TAURI__.core.invoke('start_contributor', {
      coordinatorIp:   ip,
      coordinatorPort: port,
      trainDir:        trainDir,
      torchrunPath:    torchrun,
      maxPower:        true,
      powerTarget,
    });
    addLog(`[OK] ${result}`);
    document.getElementById('btn-connect').disabled    = true;
    document.getElementById('btn-disconnect').disabled = false;
    const statusEl = document.getElementById('contrib-status');
    if (statusEl) statusEl.style.display = '';
    const myStatus = document.getElementById('my-status');
    if (myStatus) { myStatus.textContent = 'Conectando...'; myStatus.className = 'stat-val connected'; }
    setProcessActive('contributor');
  } catch (err) { addLog(`[ERROR] ${err}`); }
}

async function disconnectContributor() {
  if (window.__TAURI__ && activeProcess === 'contributor') {
    try { await window.__TAURI__.core.invoke('stop_process'); } catch (_) {}
  }
  document.getElementById('btn-connect').disabled    = false;
  document.getElementById('btn-disconnect').disabled = true;
  const statusEl = document.getElementById('contrib-status');
  if (statusEl) statusEl.style.display = 'none';
  const myStatus = document.getElementById('my-status');
  if (myStatus) { myStatus.textContent = 'Desconectado'; myStatus.className = 'stat-val disconnected'; }
  clearActiveProcess();
  addLog('[CONTRIB] Desconectado');
}

// Fix M13: only reset buttons for the active process
async function stopProcess() {
  if (!window.__TAURI__) { addLog('[WARN] No hay backend'); return; }
  try {
    const result = await window.__TAURI__.core.invoke('stop_process');
    addLog(`[OK] ${result}`);
  } catch (err) {
    addLog(`[WARN] ${err}`);
  }
  clearActiveProcess();
}

function setProcessActive(type) {
  activeProcess = type;
  const dot  = document.getElementById('status-dot');
  const stxt = document.getElementById('status-text');
  if (dot)  dot.className  = 'sdot running';
  if (stxt) stxt.textContent = type === 'coordinator' ? 'Coordinando...' :
                                type === 'contributor' ? 'Contribuyendo...' : 'Entrenando...';

  if (type === 'coordinator') {
    document.getElementById('btn-start-coord').disabled = true;
    document.getElementById('btn-stop-coord').disabled  = false;
  } else if (type === 'training') {
    document.getElementById('btn-start-train').disabled = true;
    document.getElementById('btn-stop-train').disabled  = false;
  }
}

function clearActiveProcess() {
  const prev = activeProcess;
  activeProcess = null;

  // Fix M13: only reset the buttons that were active
  if (prev === 'coordinator' || prev === null) {
    const ss = document.getElementById('btn-start-coord');
    const st = document.getElementById('btn-stop-coord');
    if (ss) ss.disabled = false;
    if (st) st.disabled = true;
  }
  if (prev === 'training' || prev === null) {
    const ss = document.getElementById('btn-start-train');
    const st = document.getElementById('btn-stop-train');
    if (ss) ss.disabled = false;
    if (st) st.disabled = true;
  }
  if (prev === 'contributor' || prev === null) {
    document.getElementById('btn-connect').disabled    = false;
    document.getElementById('btn-disconnect').disabled = true;
  }

  const dot  = document.getElementById('status-dot');
  const stxt = document.getElementById('status-text');
  if (dot)  dot.className  = 'sdot stopped';
  if (stxt) stxt.textContent = 'Detenido';
}

// ── Hardware scan ─────────────────────────────────────────────────────────────
async function runHardwareScan() {
  if (!window.__TAURI__) {
    addLog('[WARN] Sin backend Tauri — hardware scan no disponible');
    return;
  }
  addLog('[HW] Iniciando escaneo de hardware (con benchmark GPU)...');
  const trainDir = document.getElementById('train-dir-coord')?.value || '';
  try {
    await window.__TAURI__.core.invoke('run_hardware_scan', { trainDir });
    addLog('[HW] Escaneo en progreso — los resultados aparecerán en la pestaña Hardware.');
    switchTab('hardware');
  } catch (err) {
    addLog(`[ERROR] Hardware scan: ${err}`);
  }
}

// ── Max power / reset ─────────────────────────────────────────────────────────
function setMaxPower(enabled) {
  isMaxPower = enabled;
  const btn = document.getElementById('btn-max-power');
  if (!btn) return;
  if (enabled) {
    btn.textContent = '⚡ Max Power ON';
    btn.style.background = '#22c55e';
    btn.style.color = '#000';
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
    set('hp-batch', 4); set('hp-lora', 64); set('hp-seq', 4096);
    set('hp-workers', 12); set('hp-lr', '3e-4'); set('hp-grad-accum', 2); set('hp-epochs', 5);
    const auto = document.getElementById('hp-auto');
    if (auto) auto.checked = true;
  } else {
    btn.textContent = '⚡ Max Power';
    btn.style.background = '';
    btn.style.color = '';
  }
}

function resetHP() {
  isMaxPower = false;
  setMaxPower(false);
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
  set('hp-batch', 2); set('hp-lora', 32); set('hp-seq', 4096);
  set('hp-workers', 8); set('hp-lr', '2e-4'); set('hp-grad-accum', 4); set('hp-epochs', 3);
  const auto = document.getElementById('hp-auto');
  if (auto) auto.checked = true;
}

// ── Demo mode (no Tauri) ──────────────────────────────────────────────────────
function backendUnavailable() {
  addLog('[ERROR] Demo deshabilitado: este build solo inicia entrenamiento real via Tauri.');
  return;
}

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  initChart();

  // Tab switching
  document.querySelectorAll('.tab').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });

  // Coordinator
  document.getElementById('btn-start-coord')?.addEventListener('click', startCoordinator);
  document.getElementById('btn-stop-coord')?.addEventListener('click', stopProcess);

  // Contributor
  document.getElementById('btn-connect')?.addEventListener('click', connectContributor);
  document.getElementById('btn-disconnect')?.addEventListener('click', disconnectContributor);

  // DDP
  document.getElementById('btn-start-train')?.addEventListener('click', startTraining);
  document.getElementById('btn-stop-train')?.addEventListener('click', stopProcess);
  document.getElementById('btn-max-power')?.addEventListener('click', () => setMaxPower(!isMaxPower));
  document.getElementById('btn-reset-hp')?.addEventListener('click', resetHP);

  // Logs
  document.getElementById('btn-clear-logs')?.addEventListener('click', clearLogs);
  document.getElementById('btn-clear-chart')?.addEventListener('click', () => {
    lossData = [];
    if (lossChart) { lossChart.data.labels = []; lossChart.data.datasets[0].data = []; lossChart.update(); }
  });

  // Hardware
  document.getElementById('btn-hw-scan')?.addEventListener('click', runHardwareScan);
  document.getElementById('btn-optimize')?.addEventListener('click', () => {
    addLog('[SYSTEM] Ejecuta: powershell -NoProfile -ExecutionPolicy Bypass -File "optimize.ps1"');
  });

  // ZeroTier
  document.getElementById('btn-zt-join')?.addEventListener('click', async () => {
    const nwid = document.getElementById('zt-network-id')?.value.trim().toUpperCase();
    if (!nwid || nwid.length < 10) {
      addLog('[ZT] Network ID invalido (debe tener 16 caracteres)');
      return;
    }
    addLog(`[ZT] Uniendo a red ${nwid}...`);
    try {
      const result = await window.__TAURI__.core.invoke('join_zerotier_network', { networkId: nwid });
      addLog(`[ZT] ${result}`);
      addLog('[ZT] IMPORTANTE: autoriza el dispositivo en https://my.zerotier.com');
      document.getElementById('zt-status-msg').textContent = 'Unido. Autoriza el dispositivo en my.zerotier.com y espera 15s.';
    } catch (e) {
      addLog(`[ZT] Error: ${e}`);
    }
  });

  if (window.__TAURI__) {
    const { listen } = window.__TAURI__.event;
    await listen('training:log',  (ev) => addLog(ev.payload));
    await listen('training:loss', (ev) => addLossPoint(ev.payload));

    // ZeroTier event: show ZT IP in header and cards
    await listen('zerotier:ip', (ev) => {
      const ip = ev.payload;
      const els = ['zt-ip','zt-indicator-ip','zt-card-ip'];
      els.forEach(id => { const el = document.getElementById(id); if (el) el.textContent = ip; });
      const el = document.getElementById('zt-status');
      if (el) el.style.display = '';
      const card = document.getElementById('zt-card');
      if (card) card.style.display = '';
    });

    // Auto-fill train dirs
    const dd = await window.__TAURI__.core.invoke('get_default_train_dir');
    if (dd) {
      ['train-dir-ddp', 'train-dir-coord', 'train-dir-contrib'].forEach(id => {
        const el = document.getElementById(id);
        if (el && !el.value) el.value = dd;
      });
    }

    addLog('[OK] Backend Tauri conectado');

    // Auto hardware scan on startup (non-blocking)
    setTimeout(() => runHardwareScan(), 1500);

  } else {
    backendUnavailable();
  }
});
