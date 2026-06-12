/// VM / hypervisor environment detection.
/// Combines CPUID hypervisor bit, registry artifacts, and VM process names.
/// Result is cached after the first call to avoid repeated overhead.
use std::sync::atomic::{AtomicBool, Ordering};

static VM_CHECKED: AtomicBool = AtomicBool::new(false);
static VM_DETECTED: AtomicBool = AtomicBool::new(false);

const VM_PROCESS_NAMES: &[&str] = &[
    "vboxservice",
    "vboxtray",
    "vmtoolsd",
    "vmwaretray",
    "vmwareuser",
    "vmacthlp",
    "vmsrvc",
    "vmusrvc",
    "qemu-ga",
    "xenservice",
    "vmicvss",
    "vmicshutdown",
    "vmicguestinterface",
    "vmicheartbeat",
    "prl_tools",
    "prl_cc",
    "sandboxiedcomlaunch",
    "sandboxierpcss",
    "vdagent",
    "vboxheadless",
    "vboxsvc",
    "vboxmanage",
];

/// Returns true if any VM/hypervisor indicator is detected.
/// Safe to call repeatedly — result is cached after the first check.
pub fn is_vm() -> bool {
    if VM_CHECKED.load(Ordering::Relaxed) {
        return VM_DETECTED.load(Ordering::Relaxed);
    }
    // CPUID hypervisor bit alone is true on many Windows 11 hosts (VBS/Hyper-V/WSL2).
    // Closed beta: only treat guest artifacts as a VM signal.
    let found = vm_registry_keys() || vm_processes();
    VM_DETECTED.store(found, Ordering::Relaxed);
    VM_CHECKED.store(true, Ordering::Relaxed);
    found
}

/// CPUID leaf 1, ECX bit 31 — hypervisor-present flag defined by Intel/AMD.
fn cpuid_hypervisor() -> bool {
    #[cfg(target_arch = "x86_64")]
    {
        let result = unsafe { std::arch::x86_64::__cpuid(1) };
        (result.ecx >> 31) & 1 == 1
    }
    #[cfg(not(target_arch = "x86_64"))]
    false
}

/// Check for well-known VM registry artifacts (VirtualBox, VMware, Hyper-V).
fn vm_registry_keys() -> bool {
    #[cfg(target_os = "windows")]
    {
        use windows::core::PCWSTR;
        use windows::Win32::System::Registry::{
            RegCloseKey, RegOpenKeyExW, HKEY_LOCAL_MACHINE, KEY_READ,
        };

        const KEYS: &[&str] = &[
            "SOFTWARE\\Oracle\\VirtualBox Guest Additions",
            "SOFTWARE\\VMware, Inc.\\VMware Tools",
            "SOFTWARE\\Microsoft\\Virtual Machine\\Guest\\Parameters",
            "SYSTEM\\CurrentControlSet\\Services\\VBoxGuest",
            "SYSTEM\\CurrentControlSet\\Services\\VBoxMouse",
            "SYSTEM\\CurrentControlSet\\Services\\VBoxSF",
            "SYSTEM\\CurrentControlSet\\Services\\vmci",
            "SYSTEM\\CurrentControlSet\\Services\\vmhgfs",
            "SYSTEM\\CurrentControlSet\\Services\\VMMouse",
        ];

        for &key_path in KEYS {
            let wide: Vec<u16> = key_path.encode_utf16().chain(std::iter::once(0)).collect();
            let mut hkey = windows::Win32::System::Registry::HKEY(std::ptr::null_mut());
            let result = unsafe {
                RegOpenKeyExW(
                    HKEY_LOCAL_MACHINE,
                    PCWSTR(wide.as_ptr()),
                    Some(0),
                    KEY_READ,
                    &mut hkey,
                )
            };
            if result.is_ok() {
                unsafe {
                    let _ = RegCloseKey(hkey);
                }
                return true;
            }
        }
        false
    }
    #[cfg(not(target_os = "windows"))]
    false
}

/// Scan running processes for known VM guest agent names.
fn vm_processes() -> bool {
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
        let count = (needed as usize) / 4;
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
                    if VM_PROCESS_NAMES.iter().any(|&p| name.contains(p)) {
                        return true;
                    }
                }
            }
        }
        false
    }
    #[cfg(not(target_os = "windows"))]
    false
}
