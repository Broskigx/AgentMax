use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use tauri::{AppHandle, Emitter, Manager, State};

struct AppState {
    process: Option<Child>,
    is_contributor: bool,
    coordinator_process: Option<Child>,
}

fn find_python(base: &Path) -> PathBuf {
    let mut search_dir = Some(base.to_path_buf());
    while let Some(dir) = search_dir {
        let candidates = [
            dir.join(".venv").join("Scripts").join("python.exe"),
            dir.join(".venv").join("bin").join("python3"),
            dir.join(".venv").join("bin").join("python"),
            dir.join("venv").join("Scripts").join("python.exe"),
            dir.join("venv").join("bin").join("python"),
        ];
        for c in &candidates {
            if c.exists() {
                return c.clone();
            }
        }
        search_dir = dir.parent().map(|p| p.to_path_buf());
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(d) = exe.parent() {
            let py = d.join("python.exe");
            if py.exists() {
                return py;
            }
        }
    }
    PathBuf::from("python")
}

fn find_training_dir() -> PathBuf {
    let exe = std::env::current_exe().unwrap_or_default();
    let mut dir = exe.parent().unwrap_or(&exe).to_path_buf();
    for _ in 0..8 {
        if dir.join("train_ddp.py").exists() {
            return dir;
        }
        if !dir.pop() { break; }
    }
    std::env::current_dir().unwrap_or_default()
}

fn find_zerotier_cli() -> Option<PathBuf> {
    let candidates = [
        r"C:\Program Files (x86)\ZeroTier\One\zerotier-cli.bat",
        r"C:\ProgramData\ZeroTier\One\zerotier-cli.bat",
        r"C:\Program Files\ZeroTier\One\zerotier-cli.bat",
        r"C:\Program Files (x86)\ZeroTier\One\zerotier-one_x64.exe",
        r"C:\ProgramData\ZeroTier\One\zerotier-one_x64.exe",
        r"C:\Program Files\ZeroTier\One\zerotier-one_x64.exe",
    ];
    for c in &candidates {
        let p = Path::new(c);
        if p.exists() {
            return Some(p.to_path_buf());
        }
    }
    None
}

fn normalize_network_id(network_id: &str) -> Result<String, String> {
    let cleaned = network_id.trim().replace(' ', "").to_lowercase();
    if cleaned.len() != 16 || !cleaned.chars().all(|c| c.is_ascii_hexdigit()) {
        return Err("Network ID ZeroTier invalido: debe tener 16 caracteres hexadecimales".into());
    }
    Ok(cleaned)
}

fn try_start_zerotier_service() {
    #[cfg(target_os = "windows")]
    {
        let _ = Command::new("sc")
            .args(["start", "ZeroTierOneService"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .output();
    }
}

fn try_open_firewall_port(port: i32) {
    #[cfg(target_os = "windows")]
    {
        let rule_name = format!("AgentMax Coordinator {}", port);
        let _ = Command::new("netsh")
            .args([
                "advfirewall", "firewall", "add", "rule",
                &format!("name={}", rule_name),
                "dir=in",
                "action=allow",
                "protocol=TCP",
                &format!("localport={}", port),
            ])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .output();
    }
}

#[allow(dead_code)]
fn emit_to(app: &AppHandle, event: &str, payload: &str) {
    let _ = app.emit(event, payload);
}

fn spawn_and_pipe(app: AppHandle, cmd: &mut Command) -> Result<Child, String> {
    let mut child = cmd.spawn().map_err(|e| format!("Error lanzando proceso: {}", e))?;

    if let Some(stdout) = child.stdout.take() {
        let ac = app.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().flatten() {
                let _ = ac.emit("training:log", &line);
                if line.contains("'loss':") {
                    if let Some(lv) = extract_loss(&line) {
                        let _ = ac.emit("training:loss", lv);
                    }
                }
            }
        });
    }

    if let Some(stderr) = child.stderr.take() {
        let ac2 = app.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().flatten() {
                let _ = ac2.emit("training:log", &line);
            }
        });
    }

    Ok(child)
}

#[allow(dead_code)]
fn auto_start_coordinator(app: &AppHandle) {
    let base = find_training_dir();
    let python = find_python(&base);

    if !base.join("coordinator.py").exists() {
        emit_to(app, "training:log", "[COORD] coordinator.py no encontrado — no se auto-inicia");
        return;
    }

    emit_to(app, "training:log", &format!("[COORD] Iniciando servidor automaticamente..."));
    emit_to(app, "training:log", &format!("[COORD] Directorio: {}", base.display()));

    let mut cmd = Command::new(&python);
    cmd.args(["coordinator.py", "--port", "12356",
              "--min_contributors", "1",
              "--max_contributors", "100",
              "--rounds", "3",
              "--epochs_per_round", "1",
              "--train_dir", &base.to_string_lossy()]);
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped()).current_dir(&base);

    match spawn_and_pipe(app.clone(), &mut cmd) {
        Ok(child) => {
            if let Some(state) = app.try_state::<Mutex<AppState>>() {
                if let Ok(mut s) = state.lock() {
                    s.coordinator_process = Some(child);
                }
            }
            // Log ZeroTier IP if available
            if let Some(zt_ip) = get_zt_ip() {
                emit_to(app, "training:log", &format!("[COORD] Servidor en puerto 12356"));
                emit_to(app, "training:log", &format!("[ZT] IP ZeroTier: {} — comparte esta IP con tus amigos", zt_ip));
            } else {
                emit_to(app, "training:log", "[COORD] Servidor iniciado en puerto 12356");
                emit_to(app, "training:log", "[ZT] ZeroTier no detectado. Usa IP local o configura ZT.");
            }
        }
        Err(e) => {
            emit_to(app, "training:log", &format!("[COORD] Error: {}", e));
        }
    }
}

fn get_zt_ip() -> Option<String> {
    let zt = find_zerotier_cli()?;
    let args: &[&str] = if zt.extension().and_then(|e| e.to_str()) == Some("exe") {
        &["-q", "listnetworks"]
    } else {
        &["listnetworks"]
    };
    let out = Command::new(&zt).args(args)
        .stdout(Stdio::piped()).stderr(Stdio::null())
        .output().ok()?;
    let s = String::from_utf8_lossy(&out.stdout);
    for line in s.lines() {
        if let Some(ip) = line.split_whitespace()
            .find(|w| w.contains('.') && w.contains('/'))
            .and_then(|w| w.split('/').next())
        {
            return Some(ip.to_string());
        }
    }
    None
}

#[tauri::command]
fn get_app_mode(state: State<Mutex<AppState>>) -> String {
    let s = state.lock().unwrap();
    if s.is_contributor { "contributor".into() } else { "master".into() }
}

#[tauri::command]
fn get_default_train_dir() -> String {
    find_training_dir().to_string_lossy().to_string()
}

#[tauri::command]
fn detect_zerotier() -> Result<String, String> {
    let zt = find_zerotier_cli().ok_or("ZeroTier no encontrado")?;

    let args: &[&str] = if zt.extension().and_then(|e| e.to_str()) == Some("exe") {
        &["-q", "listnetworks"]
    } else {
        &["listnetworks"]
    };

    let out = Command::new(&zt).args(args)
        .stdout(Stdio::piped()).stderr(Stdio::null())
        .output().map_err(|e| format!("Error ejecutando ZeroTier: {}", e))?;

    let s = String::from_utf8_lossy(&out.stdout);

    // Parse: <nwid> <name> <mac> <status> <type> <dev> <ZT assigned ips>
    let mut networks = Vec::new();
    for line in s.lines().skip(1) {
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() < 7 { continue; }
        let nwid = parts[0];
        let name = parts[1];
        let status = parts[3];
        let ip = parts[6].split('/').next().unwrap_or("?");
        networks.push(format!("{}|{}|{}|{}", nwid, name, status, ip));
    }

    if networks.is_empty() {
        return Err("ZeroTier instalado pero no unido a ninguna red".into());
    }

    Ok(networks.join("\n"))
}

#[tauri::command]
fn join_zerotier_network(network_id: String) -> Result<String, String> {
    let network_id = normalize_network_id(&network_id)?;
    let zt = find_zerotier_cli().ok_or("ZeroTier no encontrado")?;
    try_start_zerotier_service();

    let args: &[&str] = if zt.extension().and_then(|e| e.to_str()) == Some("exe") {
        &["-q", "join", network_id.as_str()]
    } else {
        &["join", network_id.as_str()]
    };

    let out = Command::new(&zt).args(args)
        .stdout(Stdio::piped()).stderr(Stdio::piped())
        .output().map_err(|e| format!("Error uniendo a red ZeroTier: {}", e))?;
    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr).trim().to_string();
        let stdout = String::from_utf8_lossy(&out.stdout).trim().to_string();
        let detail = if !stderr.is_empty() { stderr } else { stdout };
        return Err(format!("ZeroTier no pudo unirse a {}: {}", network_id, detail));
    }

    if let Some(ip) = get_zt_ip() {
        Ok(format!("Unido a red {}. IP actual: {}", network_id, ip))
    } else {
        Ok(format!("Unido a red {}. Autoriza el dispositivo y espera a que ZeroTier asigne IP.", network_id))
    }
}

#[tauri::command]
fn detect_local_gpu() -> String {
    if let Ok(out) = Command::new("nvidia-smi")
        .args(["--query-gpu=name,memory.total,driver_version",
               "--format=csv,noheader,nounits"])
        .stdout(Stdio::piped()).stderr(Stdio::null())
        .output()
    {
        let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
        if !s.is_empty() { return s; }
    }
    let py_code = "import torch; p=torch.cuda.get_device_properties(0); print(f'{p.name},{p.total_memory/1e9:.1}')";
    if let Ok(out) = Command::new("python")
        .args(["-c", py_code])
        .stdout(Stdio::piped()).stderr(Stdio::null())
        .output()
    {
        let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
        if !s.is_empty() { return s; }
    }
    "No GPU detectada".into()
}

#[tauri::command]
fn run_hardware_scan(app: AppHandle, train_dir: String) -> Result<String, String> {
    let base_path = if train_dir.is_empty() { find_training_dir() } else { PathBuf::from(&train_dir) };
    let python = find_python(&base_path);
    let script = base_path.join("hardware_detective.py");

    if !script.exists() {
        return Err(format!("hardware_detective.py no encontrado en {}", base_path.display()));
    }

    let mut cmd = Command::new(&python);
    cmd.arg(script.to_string_lossy().as_ref())
       .stdout(Stdio::piped()).stderr(Stdio::piped())
       .current_dir(&base_path);

    let _child = spawn_and_pipe(app, &mut cmd)?;
    Ok("Hardware scan iniciado".into())
}

#[tauri::command]
fn start_contributor(
    app: AppHandle, state: State<Mutex<AppState>>,
    coordinator_ip: String, coordinator_port: i32,
    train_dir: String, torchrun_path: String, max_power: bool, power_target: f64,
) -> Result<String, String> {
    let mut s = state.lock().map_err(|e| e.to_string())?;
    if s.process.is_some() {
        return Err("Ya hay un proceso activo".into());
    }

    let base = if train_dir.is_empty() { find_training_dir() } else { PathBuf::from(&train_dir) };
    if !base.exists() {
        return Err("Directorio no encontrado".into());
    }

    let python = find_python(&base);

    let torchrun = if Path::new(&torchrun_path).is_absolute() {
        torchrun_path
    } else {
        base.join(&torchrun_path).to_string_lossy().to_string()
    };
    let target = if power_target.is_finite() {
        power_target.max(10.0).min(95.0)
    } else {
        90.0
    };
    let target_arg = format!("{:.1}", target);

    let mut cmd = Command::new(&python);
    cmd.args(["contributor.py",
              "--coordinator_ip", &coordinator_ip,
              "--coordinator_port", &coordinator_port.to_string(),
              "--train_dir", &base.to_string_lossy(),
              "--torchrun", &torchrun,
              "--power_target", &target_arg]);
    if max_power {
        cmd.arg("--max_power");
    }
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped()).current_dir(&base);

    let child = spawn_and_pipe(app, &mut cmd)?;
    s.process = Some(child);
    Ok("Contribuyente iniciado".into())
}

#[tauri::command]
fn start_training(
    app: AppHandle, state: State<Mutex<AppState>>,
    mode: String, master_addr: String, master_port: String,
    venv_python: String, train_dir: String,
    max_power: bool, auto_config: bool,
    batch_size: i32, lora_rank: i32, max_seq_length: i32,
    num_workers: i32, learning_rate: f64, grad_accum: i32, epochs: f64,
) -> Result<String, String> {
    let mut s = state.lock().map_err(|e| e.to_string())?;
    if s.process.is_some() { return Err("Ya hay un proceso activo".into()); }

    let base = if train_dir.is_empty() { find_training_dir() } else { PathBuf::from(&train_dir) };
    if !base.exists() {
        return Err("Directorio no encontrado".into());
    }

    let torchrun = if Path::new(&venv_python).is_absolute() {
        PathBuf::from(&venv_python)
    } else {
        base.join(&venv_python)
    };

    let nr = if mode == "master" { "0" } else { "1" };
    let mut cmd = Command::new(&torchrun);
    cmd.args(["--nnodes","2","--nproc_per_node","1",
              "--node_rank",nr,"--master_addr",&master_addr,
              "--master_port",&master_port,"train_ddp.py"]);
    if max_power { cmd.arg("--max_power"); }
    if !auto_config {
        cmd.arg("--no-auto");
        cmd.args(["--batch_size",&batch_size.to_string()]);
        cmd.args(["--lora_rank",&lora_rank.to_string()]);
        cmd.args(["--num_workers",&num_workers.to_string()]);
    }
    cmd.args(["--max_seq_length",&max_seq_length.to_string()]);
    cmd.args(["--learning_rate",&format!("{}",learning_rate)]);
    cmd.args(["--gradient_accumulation_steps",&grad_accum.to_string()]);
    cmd.args(["--epochs",&format!("{}",epochs)]);
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped()).current_dir(&base);

    let child = spawn_and_pipe(app, &mut cmd)?;
    s.process = Some(child);
    Ok("Entrenamiento DDP iniciado".into())
}

#[tauri::command]
fn start_coordinator(
    app: AppHandle, state: State<Mutex<AppState>>,
    train_dir: String, coordinator_port: i32,
    min_contributors: i32, max_contributors: i32,
    rounds: i32, epochs_per_round: i32,
) -> Result<String, String> {
    let mut s = state.lock().map_err(|e| e.to_string())?;
    if s.process.is_some() { return Err("Ya hay un proceso activo".into()); }

    let base = if train_dir.is_empty() { find_training_dir() } else { PathBuf::from(&train_dir) };
    if !base.exists() {
        return Err("Directorio no encontrado".into());
    }

    let python = find_python(&base);
    try_open_firewall_port(coordinator_port);

    let mut cmd = Command::new(&python);
    cmd.args(["coordinator.py", "--port", &coordinator_port.to_string(),
              "--min_contributors", &min_contributors.to_string(),
              "--max_contributors", &max_contributors.to_string(),
              "--rounds", &rounds.to_string(),
              "--epochs_per_round", &epochs_per_round.to_string(),
              "--train_dir", &base.to_string_lossy()]);
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped()).current_dir(&base);

    let child = spawn_and_pipe(app, &mut cmd)?;
    s.process = Some(child);
    Ok("Coordinador federado iniciado".into())
}

#[tauri::command]
fn stop_process(state: State<Mutex<AppState>>) -> Result<String, String> {
    let mut s = state.lock().map_err(|e| e.to_string())?;
    if let Some(mut child) = s.process.take() {
        let _ = child.kill();
        let _ = child.wait();
        Ok("Proceso detenido".into())
    } else if let Some(mut child) = s.coordinator_process.take() {
        let _ = child.kill();
        let _ = child.wait();
        Ok("Coordinador detenido".into())
    } else {
        Err("No hay proceso activo".into())
    }
}

fn extract_loss(line: &str) -> Option<f64> {
    let s = line.find("'loss': ")?;
    let r = &line[s + 8..];
    r.find(',').or_else(|| r.find('}')).and_then(|e| r[..e].trim().parse().ok())
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let exe_name = std::env::current_exe().unwrap_or_default();
    let exe_name_lower = exe_name.file_stem().unwrap_or_default().to_string_lossy().to_lowercase();
    let is_contributor = args.iter().any(|a| a == "--contributor")
        || exe_name_lower.contains("contributor");

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(Mutex::new(AppState {
            process: None,
            is_contributor,
            coordinator_process: None,
        }))
        .setup(move |app| {
            if !is_contributor {
                let app2 = app.handle().clone();
                std::thread::spawn(move || {
                    // Wait for UI to load
                    std::thread::sleep(std::time::Duration::from_secs(2));

                    // Detect ZeroTier first
                    if let Some(zt) = find_zerotier_cli() {
                        let _ = app2.emit("training:log", &format!("[ZT] ZeroTier detectado: {}", zt.display()));
                        if let Some(ip) = get_zt_ip() {
                            let _ = app2.emit("training:log", &format!("[ZT] IP ZeroTier: {}", ip));
                            let _ = app2.emit("zerotier:ip", &ip);
                        } else {
                            let _ = app2.emit("training:log", "[ZT] ZeroTier instalado pero sin red activa");
                        }
                    } else {
                        let _ = app2.emit("training:log", "[ZT] ZeroTier no encontrado");
                    }

                    let _ = app2.emit("training:log", "[COORD] Listo. Ingresa Network ID/IP y pulsa Iniciar para abrir el servidor real.");
                });
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } | tauri::WindowEvent::Destroyed = event {
                if let Some(state) = window.try_state::<Mutex<AppState>>() {
                    if let Ok(mut s) = state.lock() {
                        let mut p1 = s.process.take();
                        let mut p2 = s.coordinator_process.take();
                        for child in [&mut p1, &mut p2].iter_mut() {
                            if let Some(mut c) = child.take() {
                                let _ = c.kill();
                                let _ = c.wait();
                            }
                        }
                    }
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            get_app_mode, get_default_train_dir, detect_local_gpu,
            start_training, start_coordinator, start_contributor, stop_process,
            run_hardware_scan, detect_zerotier, join_zerotier_network,
        ])
        .run(tauri::generate_context!())
        .expect("Error al iniciar Tauri");
}
