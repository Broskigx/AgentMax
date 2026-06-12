let gpuConnected = false;

const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

function clampPowerTarget(value) {
  const n = Number.parseFloat(value);
  if (!Number.isFinite(n)) return 90;
  return Math.max(10, Math.min(95, n));
}

function addLog(text, cls = '') {
  const log = document.getElementById('contrib-log');
  const div = document.createElement('div');
  div.textContent = text;
  if (cls) div.style.color = cls;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  log.querySelector('.log-placeholder')?.remove();
}

function setStatus(text, dotClass) {
  document.getElementById('status-text').textContent = text;
  const dot = document.getElementById('status-dot');
  dot.className = `dot ${dotClass}`;
}

function updateLocalStats(stats) {
  const set = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  };
  if (stats.round != null) set('p-round', stats.round);
  if (stats.loss != null) set('p-loss', Number(stats.loss).toFixed(4));
  if (stats.gpu_util != null) set('p-gpu-util', `${stats.gpu_util}%`);
  if (stats.vram_used != null) set('p-vram', `${stats.vram_used} GB`);
  if (stats.cpu_util != null) set('p-cpu', `${stats.cpu_util}%`);
  if (stats.ram_pct != null) set('p-ram', `${stats.ram_pct}%`);
  if (stats.power_w != null) set('p-power', `${stats.power_w} W`);
  if (stats.power_target != null) set('p-target', `${stats.power_target}%`);
}

document.addEventListener('DOMContentLoaded', async () => {
  // Detect GPU on load
  if (window.__TAURI__) {
    try {
      const gpuInfo = await window.__TAURI__.core.invoke('detect_local_gpu');
      if (gpuInfo && gpuInfo !== 'No GPU detectada') {
        const parts = gpuInfo.split(',');
        document.getElementById('gpu-name').textContent = parts[0] || 'GPU';
        document.getElementById('gpu-vram').textContent = parts[1] ? `${parts[1]} GB` : '?';
        document.getElementById('gpu-info').style.display = 'none';
        document.getElementById('gpu-specs').style.display = 'flex';
      } else {
        document.getElementById('gpu-info').textContent = 'No se detecto GPU NVIDIA. Solo CPUs?';
        document.getElementById('gpu-info').style.color = '#eab308';
      }
    } catch (e) {
      document.getElementById('gpu-info').textContent = `Error detectando GPU: ${e}`;
    }

    // Detect ZeroTier
    try {
      const ztResult = await window.__TAURI__.core.invoke('detect_zerotier');
      const lines = ztResult.split('\n');
      for (const line of lines) {
        const parts = line.split('|');
        if (parts.length >= 4 && parts[2] === 'OK') {
          const ztIp = parts[3];
          document.getElementById('zt-ip-contrib').textContent = ztIp;
          document.getElementById('zt-badge-contrib').style.display = '';
          document.getElementById('zt-my-ip').textContent = ztIp;
          document.getElementById('zt-info-contrib').style.display = '';
          addLog(`[ZT] ZeroTier detectado: ${ztIp}`, '#22c55e');
          break;
        }
      }
    } catch (e) {
      // No ZeroTier - fine
    }
  } else {
    document.getElementById('gpu-info').textContent = 'Sin backend Tauri';
  }

  // Buttons
  document.getElementById('btn-connect').addEventListener('click', connect);
  document.getElementById('btn-disconnect').addEventListener('click', disconnect);

  // Auto-fill training dir
  if (window.__TAURI__) {
    const dd = await window.__TAURI__.core.invoke('get_default_train_dir');
    const trainDirEl = document.getElementById('train-dir');
    if (dd && trainDirEl) trainDirEl.value = dd;

    // Fix C3: register training event listeners
    const { listen } = window.__TAURI__.event;
    await listen('training:log', (event) => {
      const text = event.payload;
      if (text.startsWith('[LOCAL_STATS]')) {
        try {
          updateLocalStats(JSON.parse(text.slice('[LOCAL_STATS]'.length)));
        } catch (_) {}
        return;
      }
      let color = '';
      if (text.toLowerCase().includes('error') || text.toLowerCase().includes('traceback')) color = '#ef4444';
      else if (text.toLowerCase().includes('warn')) color = '#f59e0b';
      else if (text.includes("'loss':") || text.includes('[DONE]')) color = '#22c55e';
      addLog(text, color);

      // Parse loss from log
      const lossMatch = text.match(/'loss':\s*([\d.]+)/);
      if (lossMatch) {
        const lossEl = document.getElementById('p-loss');
        if (lossEl) lossEl.textContent = parseFloat(lossMatch[1]).toFixed(4);
      }
      // Parse step
      const stepMatch = text.match(/step[=\s]+(\d+)/i);
      if (stepMatch) {
        const stepEl = document.getElementById('p-step');
        if (stepEl) stepEl.textContent = stepMatch[1];
      }
    });

    await listen('training:loss', (event) => {
      const lossEl = document.getElementById('p-loss');
      if (lossEl) lossEl.textContent = parseFloat(event.payload).toFixed(4);
    });
  }
});

async function connect() {
  const ip = document.getElementById('coord-ip').value.trim();
  const port = document.getElementById('coord-port').value;
  const trainDir = document.getElementById('train-dir').value;
  const networkId = document.getElementById('zt-network-id-contrib').value.trim();
  const maxPower = document.getElementById('max-power')?.checked ?? true;
  const powerTarget = clampPowerTarget(document.getElementById('power-target')?.value ?? 90);

  if (!ip) { addLog('[ERROR] Ingresa la IP del coordinador', '#ef4444'); return; }

  if (!window.__TAURI__) {
    addLog('[ERROR] Sin backend Tauri: no puedo iniciar entrenamiento real desde el navegador.', '#ef4444');
    return;
  }

  try {
    if (networkId) {
      addLog(`[ZT] Uniendose a red ${networkId}...`);
      const ztResult = await window.__TAURI__.core.invoke('join_zerotier_network', { networkId });
      addLog(`[ZT] ${ztResult}`, '#22c55e');
      addLog('[ZT] Autoriza este dispositivo en my.zerotier.com si aun no aparece OK.', '#eab308');
      await sleep(3000);
    }

    const result = await window.__TAURI__.core.invoke('start_contributor', {
      coordinatorIp: ip,
      coordinatorPort: parseInt(port),
      trainDir: trainDir,
      torchrunPath: '.venv\\Scripts\\torchrun.exe',
      maxPower,
      powerTarget,
    });
    addLog(`[OK] ${result}`);
    setStatus('Contribuyendo', 'running');
    document.getElementById('progress-card').style.display = 'block';
    document.getElementById('btn-connect').disabled = true;
    document.getElementById('btn-disconnect').disabled = false;
    document.getElementById('gpu-state').textContent = 'Contribuyendo...';
    document.getElementById('gpu-state').className = 'training';
    gpuConnected = true;
  } catch (err) {
    addLog(`[ERROR] ${err}`, '#ef4444');
  }
}

async function disconnect() {
  if (window.__TAURI__ && gpuConnected) {
    try {
      await window.__TAURI__.core.invoke('stop_process');
      addLog('[OK] Desconectado');
    } catch (e) {
      addLog(`[ERROR] ${e}`, '#ef4444');
    }
  }
  document.getElementById('btn-connect').disabled = false;
  document.getElementById('btn-disconnect').disabled = true;
  document.getElementById('gpu-state').textContent = 'Inactivo';
  document.getElementById('gpu-state').className = 'idle';
  setStatus('Desconectado', 'stopped');
  gpuConnected = false;
}
