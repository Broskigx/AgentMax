use hmac::{Hmac, Mac};
use sha2::{Digest, Sha256};
use std::sync::atomic::{AtomicBool, Ordering};

type HmacSha256 = Hmac<Sha256>;

// Embedded key — split across segments to frustrate naive string search
const KEY_A: &[u8] = b"AgentMax\x2b\x7f\x11\xc3";
const KEY_B: &[u8] = b"\xa9\x55\xe2\x30\xd4\x88\x1b\x69";
const KEY_C: &[u8] = b"\x3a\xf0\x4e\x82\x91\xcc\x07\xde";

static SECURITY_OK: AtomicBool = AtomicBool::new(true);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SecurityMode {
    Off,
    Audit,
    Enforce,
}

impl SecurityMode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Off => "off",
            Self::Audit => "audit",
            Self::Enforce => "enforce",
        }
    }
}

// "DE TODO" anti-RE / anti-debug / anti-tamper list for closed-source protection.
// Cobertura exhaustiva ("de todo") contra:
//   - IDEs profesionales (Visual Studio Pro, sus debuggers remotos, JIT, hosting, etc.)
//   - Binary Ninja en TODAS sus formas (GUI, headless, plugins, versiones 3/4, renames comunes, etc.)
//   - Prácticamente todas las herramientas de reverse engineering, depuración, memory editing,
//     unpacking, monitoring, hooking e inyección usadas por profesionales y crackers.
//
// Esta lista alimenta cracker_running(), que se combina con is_debugger_present (NtQuery + APIs),
// rdtsc timing anomaly, VM detection y el watchdog agresivo.
//
// En release (closed source): cualquier detección → log forense en Recovery + exit(1) inmediato
// tanto en el chequeo temprano de main() como cada 5s en el watchdog.
//
// El scanner hace to_lowercase() + contains() sobre el full path del proceso.
// Añadir muchas variantes complica el simple rename por parte del atacante.
const CRACKER_NAMES: &[&str] = &[
    // === Debuggers (user + kernel + pro) ===
    "x64dbg",
    "x32dbg",
    "x96dbg",
    "ollydbg",
    "ollyice",
    "immunitydebugger",
    "immunity",
    "windbg",
    "kd",
    "cdb",
    "ntsd",
    "livekd",
    "dbgview",
    "gdb",
    "lldb",
    "rr",
    // === Professional IDEs & Debug Hosts (VS Pro, JetBrains, etc.) ===
    "devenv",
    "devenv.exe",
    "msvsmon",
    "vsjitdebugger",
    "vsdebug",
    "vshost",
    "vsd",
    "msbuild",
    "vsdebugeng",
    "vsdebugconsole",
    "rider",
    "clion",
    "intellij",
    "visualstudio",
    "visual studio",
    "jetbrains",
    "resharper",
    // === Disassemblers / Full RE Suites - Anti IDA Pro "mas duro" ===
    // IDA Pro (todas las versiones profesionales, 32/64, launchers, plugins, Python/IDC)
    "ida64",
    "idaq64",
    "idaq",
    "idaw",
    "ida",
    "ida.exe",
    "ida64.exe",
    "idaw.exe",
    "idapro",
    "ida pro",
    "idapro.exe",
    "ida pro 7",
    "ida pro 8",
    "ida pro 9",
    "ida pro 10",
    "ida70",
    "ida71",
    "ida72",
    "ida73",
    "ida74",
    "ida75",
    "ida76",
    "ida77",
    "ida80",
    "ida90",
    "idapython",
    "idc",
    "ida plugin",
    "ida plugins",
    "ida64 plugin",
    "ida pro plugin",
    "idat.exe",
    "idat64.exe", // headless / batch modes used by pros
    "ida -",      // common command line patterns for IDA Pro launches
    "idag.exe",
    "idag64.exe", // GUI variants in some installs
    "ida pro 64",
    "ida64 pro",
    "ghidra",
    "ghidrarun",
    "ghidra.exe",
    "cutter",
    "iaito",
    "radare2",
    "r2",
    "rizin",
    "rizin.exe",
    "hiew",
    "hiew32",
    "hiew64",
    "010editor",
    "010 editor",
    "010editor",
    "petools",
    "stud_pe",
    "dependencywalker",
    "depends",
    "dumpbin",
    "capa",
    "floss",
    // === Binary Ninja (ALL variants + headless + plugins) - Maximum coverage ===
    "binaryninja",
    "binja",
    "binary_ninja",
    "binaryninja3",
    "bn3",
    "binaryninja4",
    "bn4",
    "binaryninja.exe",
    "binja.exe",
    "binary_ninja.exe",
    "binaryninja64",
    "binaryninja32",
    "binaryninja-gui",
    "binaryninja-headless",
    "bn",
    "bn-plugin",
    "binaryninja-plugin",
    "binary_ninja64",
    "bn64",
    "binaryninja3.exe",
    "binaryninja4.exe",
    // === Decompilers / .NET / Specialized RE ===
    "dnspy",
    "de4dot",
    "ilspy",
    "dotpeek",
    "justdecompile",
    "retdec",
    "snowman",
    "hex-rays",
    "decompiler",
    "jadx",
    "apktool",
    // === Memory Scanners / Editors / Cheat Tools ===
    "cheatengine",
    "cheat engine",
    "artmoney",
    "scanmem",
    "pymem",
    "memory-hacker",
    "gamecheat",
    "memedit",
    "ce64",
    "cheatengine64",
    // === Packers / Protectors / Unpackers ===
    "upx",
    "upx.exe",
    "pe-sieve",
    "scylla",
    "scylla.exe",
    "lordpe",
    "lordpe.exe",
    "import_reconstructor",
    "peid",
    "pestudio",
    "exeinfope",
    "pe-bear",
    "pebear",
    "reshacker",
    "resource_hacker",
    "aspack",
    "asprotect",
    "vmprotect",
    "themida",
    "enigma",
    "obsidium",
    "pelock",
    "armadillo",
    "safedisc",
    // === PE / Binary Analysis Tools ===
    "pestudio",
    "exeinfope",
    "peid",
    "pe-bear",
    "pebear",
    "capa",
    "floss",
    "pe-sieve",
    "totalcmd",
    "everything",
    "winhex",
    "hxd",
    "frhed",
    "bxedit",
    // === Process / Monitoring / API Spy (RE staples) ===
    "processhacker",
    "procmon",
    "procmon64",
    "procexp",
    "procexp64",
    "apimonitor",
    "api-monitor",
    "sysinternals",
    "tcpview",
    "autoruns",
    "process explorer",
    // === Injection / Hooking / Dynamic ===
    "frida",
    "frida-server",
    "frida-gadget",
    "frida-trace",
    "frida-ps",
    "hook",
    "easyhook",
    "detours",
    "minhook",
    "xposed",
    "magisk",
    "frida-inject",
    // === Network / MITM (used heavily with RE) ===
    "wireshark",
    "fiddler",
    "charles",
    "mitmproxy",
    "burp",
    "burpsuite",
    "owasp",
    "zap",
    "httpdebugger",
    "postman",
    "insomnia",
    // === Anti-anti-debug / Hiders / Sandboxes ===
    "scyllahide",
    "titanhide",
    "hollows_hunter",
    "x64dbg-plugin",
    "scylla_hide",
    "titan_hide",
    "anti-anti",
    "debug hider",
    "sandboxie",
    "sandboxiedcomlaunch",
    "vboxheadless",
    "vboxsvc",
    "vmware",
    "qemu",
    "hyper-v",
    // === Other common pro / advanced RE tools ===
    "ollydbg",
    "x64dbg",
    "cheatengine",
    "artmoney",
    "hiew",
    "010editor",
    "hexedit",
    "winhex",
    "total commander",
    "everything",
    "procmon",
    "processhacker",
    "apimonitor",
    "dependency walker",
    "pe explorer",
    "stud_pe",
    "petools",
    "qiling",
    "angr",
    "triton",
    "manticore", // advanced symbolic
];

// ── Public API ─────────────────────────────────────────────────────────────────

pub fn hardware_fingerprint() -> String {
    let cpu = cpu_brand();
    let disk = volume_serial();
    let name = std::env::var("COMPUTERNAME").unwrap_or_else(|_| "PC".into());
    let raw = format!("{}|{}|{}", cpu, disk, name);
    let mut h = Sha256::new();
    h.update(raw.as_bytes());
    format!("{:x}", h.finalize())
}

pub fn generate_server_token(hw_fp: &str) -> String {
    let mut key = Vec::new();
    key.extend_from_slice(KEY_A);
    key.extend_from_slice(KEY_B);
    key.extend_from_slice(KEY_C);
    let mut mac = HmacSha256::new_from_slice(&key).expect("HMAC key");
    mac.update(hw_fp.as_bytes());
    use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
    URL_SAFE_NO_PAD.encode(mac.finalize().into_bytes())
}

pub fn verify_request_token(token: &str, hw_fp: &str) -> bool {
    let expected = generate_server_token(hw_fp);
    if expected.len() != token.len() {
        return false;
    }
    expected
        .bytes()
        .zip(token.bytes())
        .fold(0u8, |acc, (a, b)| acc | (a ^ b))
        == 0
}

pub fn security_ok() -> bool {
    SECURITY_OK.load(Ordering::SeqCst)
}

/// Security checks are diagnostic by default for tester builds. Enforcement
/// must be explicitly enabled to avoid terminating legitimate environments.
pub fn security_mode() -> SecurityMode {
    if std::env::var("AGENTMAX_BETA_SECURITY")
        .ok()
        .is_some_and(|value| matches!(value.trim().to_lowercase().as_str(), "0" | "false" | "off"))
    {
        return SecurityMode::Off;
    }

    match std::env::var("AGENTMAX_SECURITY_MODE")
        .unwrap_or_else(|_| "audit".to_string())
        .trim()
        .to_lowercase()
        .as_str()
    {
        "0" | "false" | "off" => SecurityMode::Off,
        "1" | "true" | "on" | "enforce" | "strict" => SecurityMode::Enforce,
        _ => SecurityMode::Audit,
    }
}

pub fn security_findings() -> Vec<&'static str> {
    let mut findings = Vec::new();
    if is_debugger_present() {
        findings.push("debugger_present");
    }
    if cracker_running() {
        findings.push("analysis_tool_running");
    }
    if crate::anti_vm::is_vm() {
        findings.push("virtual_machine_guest");
    }
    if rdtsc_timing_anomaly() {
        findings.push("timing_anomaly");
    }
    findings
}

/// High-level security / anti-debug / anti-tamper check.
/// Returns false if the environment looks hostile (debugger, known cracker, VM, timing anomaly).
/// Used at startup and by the watchdog. In closed-source releases this can lead to immediate exit.
pub fn security_checks() -> bool {
    security_findings().is_empty()
}

pub fn start_security_watchdog() {
    if security_mode() != SecurityMode::Enforce {
        return;
    }

    #[cfg(not(debug_assertions))]
    std::thread::spawn(|| {
        // Watchdog de máxima dureza para closed source.
        // Corre en background y mata el proceso si detecta cualquier cosa hostil.
        // 5 segundos es un buen balance: lo suficientemente rápido para frustrar al atacante,
        // sin ser ruidoso.
        loop {
            std::thread::sleep(std::time::Duration::from_secs(5));
            let findings = security_findings();
            if !findings.is_empty() {
                SECURITY_OK.store(false, Ordering::SeqCst);
                crate::recovery::sink().record(
                    "error",
                    "security.watchdog",
                    "Security enforcement stopped AgentMax after a hostile-environment signal",
                    Some(serde_json::json!({ "findings": findings })),
                );
                // Terminación inmediata y silenciosa desde el punto de vista del atacante.
                std::process::exit(1);
            }
        }
    });
}

// ── Anti-debug (EL CHEQUEO - versión más dura y pulida para código cerrado) ─────
//
// Objetivo: hacer extremadamente difícil y molesto adjuntar un debugger,
// usar herramientas de RE, o correr bajo VM/sandbox para un atacante.
//
// Capas combinadas:
//   - APIs Win32 estándar (fáciles pero efectivas)
//   - NtQueryInformationProcess (baja nivel, atrapa kernel debuggers que ocultan las APIs altas)
//   - RDTSC timing (detecta single-step)
//   - Lista de procesos crackers (anti-tooling)
//   - VM detection
//   - Watchdog que mata el proceso periódicamente en release
//
// En builds release (no debug): cualquier detección → terminación inmediata (exit(1)).
// El chequeo se llama lo antes posible en main() y continuamente en background.

/// Versión más completa y dura de detección de debugger (anti-IDA Pro reforzado + general "mas duro").
/// Combina múltiples técnicas para resistir bypass comunes de herramientas profesionales como IDA Pro.
/// IDA Pro usa attach, kernel debugging, plugins y launchers que estas capas atrapan.
pub fn is_debugger_present() -> bool {
    #[cfg(target_os = "windows")]
    unsafe {
        use windows::Win32::System::Diagnostics::Debug::{
            CheckRemoteDebuggerPresent, IsDebuggerPresent,
        };
        use windows::Win32::System::Threading::GetCurrentProcess;

        // 1. Chequeo clásico (rápido y efectivo contra la mayoría de user-mode debuggers)
        if IsDebuggerPresent().as_bool() {
            return true;
        }

        // 2. Chequeo remoto (detecta si otro proceso nos está debugeando)
        let mut remote = windows::core::BOOL(0);
        let _ = CheckRemoteDebuggerPresent(GetCurrentProcess(), &mut remote);
        if remote.as_bool() {
            return true;
        }

        // 3. NtQuery - ProcessDebugPort (atrapa muchos kernel debuggers y bypasses de IsDebuggerPresent)
        // IDA Pro profesional a menudo usa estos vectores de bajo nivel.
        if nt_debug_port() {
            return true;
        }

        // 4. NtQuery - ProcessDebugObjectHandle (otro vector fuerte)
        if nt_debug_object_handle() {
            return true;
        }

        // 5. NtQuery - ProcessDebugFlags (31) - another strong indicator for pro debuggers like IDA Pro
        if nt_debug_flags() {
            return true;
        }

        // 6. Chequeo de proceso padre (muchos launchers de debuggers / injectors no son explorer)
        // Nota: versión ligera. Para más dureza se puede expandir a full process tree walk.
        if !parent_looks_like_normal_launch() {
            return true;
        }

        false
    }

    #[cfg(not(target_os = "windows"))]
    {
        // En plataformas no-Windows (dev o futuros ports) usamos señales de entorno.
        // En un build cerrado real para Windows esto no aplica.
        std::env::var("AGENTMAX_FORCE_DEBUG").is_ok()
    }
}

/// Chequeo de padre de proceso (capa extra anti-debug).
/// Devuelve true si parece un lanzamiento normal de usuario (explorer o similar).
/// Para más dureza se puede implementar full process tree walk con Toolhelp.
#[cfg(target_os = "windows")]
fn parent_looks_like_normal_launch() -> bool {
    // Implementación ligera. En la práctica las otras capas (NtQuery, process scan, timing)
    // ya atrapan la mayoría de launchers de debuggers/RE tools.
    // Retornamos true para evitar falsos positivos en usuarios normales.
    true
}

#[cfg(not(target_os = "windows"))]
fn parent_looks_like_normal_launch() -> bool {
    true
}

pub fn cracker_running() -> bool {
    #[cfg(target_os = "windows")]
    unsafe {
        use windows::Win32::Foundation::CloseHandle;
        use windows::Win32::System::ProcessStatus::{EnumProcesses, GetProcessImageFileNameW};
        use windows::Win32::System::Threading::{
            OpenProcess, PROCESS_QUERY_INFORMATION, PROCESS_VM_READ,
        };

        let mut pids = vec![0u32; 1024];
        let mut needed = 0u32;
        if EnumProcesses(pids.as_mut_ptr(), (pids.len() * 4) as u32, &mut needed).is_err() {
            return false;
        }
        let count = needed as usize / 4;
        for &pid in &pids[..count] {
            if pid == 0 {
                continue;
            }
            if let Ok(h) = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, false, pid) {
                let mut buf = vec![0u16; 260];
                let len = GetProcessImageFileNameW(h, &mut buf);
                let _ = CloseHandle(h);
                if len > 0 {
                    let name = String::from_utf16_lossy(&buf[..len as usize]).to_lowercase();
                    for &cracker in CRACKER_NAMES {
                        if name.contains(cracker) {
                            return true;
                        }
                    }
                }
            }
        }
        false
    }
    #[cfg(not(target_os = "windows"))]
    false
}

// ── NtQueryInformationProcess helpers ─────────────────────────────────────────

// Link ntdll directly rather than going through GetProcAddress; ntdll is always
// present on Windows and using extern avoids the Win32_System_LibraryLoader dep.
#[cfg(target_os = "windows")]
#[link(name = "ntdll")]
extern "system" {
    fn NtQueryInformationProcess(
        process_handle: isize,
        process_information_class: u32,
        process_information: *mut u8,
        process_information_length: u32,
        return_length: *mut u32,
    ) -> i32;
}

#[cfg(target_os = "windows")]
unsafe fn nt_debug_port() -> bool {
    let mut port: isize = 0;
    let mut ret: u32 = 0;
    // ProcessDebugPort = 7; non-zero means a kernel debugger is attached.
    let status = NtQueryInformationProcess(
        -1isize, // NtCurrentProcess
        7,
        &mut port as *mut isize as *mut u8,
        std::mem::size_of::<isize>() as u32,
        &mut ret,
    );
    status == 0 && port != 0
}

#[cfg(target_os = "windows")]
unsafe fn nt_debug_object_handle() -> bool {
    let mut handle: isize = 0;
    let mut ret: u32 = 0;
    // ProcessDebugObjectHandle = 30; STATUS_PORT_NOT_SET (0xC0000353) when no debugger.
    let status = NtQueryInformationProcess(
        -1isize,
        30,
        &mut handle as *mut isize as *mut u8,
        std::mem::size_of::<isize>() as u32,
        &mut ret,
    );
    // success (0) + non-null handle = debugger present
    status == 0 && handle != 0
}

#[cfg(target_os = "windows")]
unsafe fn nt_debug_flags() -> bool {
    let mut flags: u32 = 0;
    let mut ret: u32 = 0;
    // ProcessDebugFlags = 31; if the returned flags indicate no debug (0 means being debugged in some contexts)
    // Actually for this class, non-zero often means debugged; we treat any "debug present" signal.
    let status = NtQueryInformationProcess(
        -1isize,
        31,
        &mut flags as *mut u32 as *mut u8,
        std::mem::size_of::<u32>() as u32,
        &mut ret,
    );
    debug_flags_indicate_debugger(status, flags)
}

fn debug_flags_indicate_debugger(status: i32, flags: u32) -> bool {
    status == 0 && flags == 0
}

// ── RDTSC timing anti-debug ────────────────────────────────────────────────────

/// Detects single-step / software-breakpoint debugging via RDTSC delta.
/// A normal CPU executes the few instructions between the two RDTSC reads in
/// under 1,000 cycles; a debugger stepping through them takes millions.
fn rdtsc_timing_anomaly() -> bool {
    #[cfg(target_arch = "x86_64")]
    unsafe {
        use std::arch::x86_64::_rdtsc;
        // Serialise: lfence prevents out-of-order execution across the read.
        std::arch::x86_64::_mm_lfence();
        let t1 = _rdtsc();
        std::arch::x86_64::_mm_lfence();
        // A handful of trivial operations whose timing we measure.
        std::hint::black_box(t1.wrapping_add(1));
        std::arch::x86_64::_mm_lfence();
        let t2 = _rdtsc();
        // ~5 M cycles ≈ 1–2 ms on a 3 GHz CPU — well above normal, far below one step.
        t2.wrapping_sub(t1) > 5_000_000
    }
    #[cfg(not(target_arch = "x86_64"))]
    false
}

// ── Hardware helpers ───────────────────────────────────────────────────────────

fn volume_serial() -> String {
    #[cfg(target_os = "windows")]
    unsafe {
        use windows::core::PCWSTR;
        use windows::Win32::Storage::FileSystem::GetVolumeInformationW;
        let root: Vec<u16> = "C:\\\0".encode_utf16().collect();
        let mut serial = 0u32;
        let ok = GetVolumeInformationW(
            PCWSTR(root.as_ptr()),
            None,
            Some(&mut serial),
            None,
            None,
            None,
        );
        if ok.is_ok() {
            return format!("{:08X}", serial);
        }
        "00000000".to_string()
    }
    #[cfg(not(target_os = "windows"))]
    "00000000".to_string()
}

fn cpu_brand() -> String {
    #[cfg(target_arch = "x86_64")]
    unsafe {
        let mut brand = Vec::with_capacity(48);
        for leaf in 0x8000_0002u32..=0x8000_0004u32 {
            let r = std::arch::x86_64::__cpuid(leaf);
            brand.extend_from_slice(&r.eax.to_le_bytes());
            brand.extend_from_slice(&r.ebx.to_le_bytes());
            brand.extend_from_slice(&r.ecx.to_le_bytes());
            brand.extend_from_slice(&r.edx.to_le_bytes());
        }
        String::from_utf8_lossy(&brand)
            .trim_end_matches('\0')
            .trim()
            .to_string()
    }
    #[cfg(not(target_arch = "x86_64"))]
    "unknown_cpu".to_string()
}

#[cfg(test)]
mod tests {
    use super::debug_flags_indicate_debugger;

    #[test]
    fn process_debug_flags_zero_means_debugged() {
        assert!(debug_flags_indicate_debugger(0, 0));
        assert!(!debug_flags_indicate_debugger(0, 1));
        assert!(!debug_flags_indicate_debugger(-1, 0));
    }
}
