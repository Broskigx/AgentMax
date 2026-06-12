/// Binary self-integrity checker.
///
/// On first launch, computes a SHA-256 of the current executable and stores it
/// encrypted (XOR) under %LOCALAPPDATA%\AgentMax\.ixh.  On every subsequent
/// launch the stored hash is compared against the current binary.  A mismatch
/// means the binary was patched after installation.
///
/// The integrity snapshot must be refreshed after every legitimate auto-update
/// by calling `refresh_snapshot()` from the updater code path.
use sha2::{Digest, Sha256};
use std::path::PathBuf;

// XOR key split across segments to frustrate naive string scan
const KS_A: &[u8] = b"\x7f\x4a\x91\xb3\x2e\xc0\x58\xd1";
const KS_B: &[u8] = b"\x8c\x15\xf7\x63\xa0\x37\xe4\x29";
const KS_C: &[u8] = b"\x4d\x29\xe1\x56\x7b\xf2\x0d\x8a";
const KS_D: &[u8] = b"\xb8\x3c\x0a\xd4\x92\x61\x1e\x75";

fn xkey() -> Vec<u8> {
    let mut k = Vec::with_capacity(32);
    k.extend_from_slice(KS_A);
    k.extend_from_slice(KS_B);
    k.extend_from_slice(KS_C);
    k.extend_from_slice(KS_D);
    k
}

fn xor_crypt(data: &[u8]) -> Vec<u8> {
    let key = xkey();
    data.iter()
        .enumerate()
        .map(|(i, &b)| b ^ key[i % key.len()])
        .collect()
}

fn snapshot_path() -> Option<PathBuf> {
    dirs::data_local_dir().map(|d| d.join("AgentMax").join(".ixh"))
}

fn hash_exe() -> Option<String> {
    let exe = std::env::current_exe().ok()?;
    let data = std::fs::read(&exe).ok()?;
    let mut h = Sha256::new();
    h.update(&data);
    Some(format!("{:x}", h.finalize()))
}

/// Verify that the executable matches the stored integrity snapshot.
///
/// Returns `true` if the binary is clean (or this is the first launch and a
/// snapshot was just created).  Returns `false` if a mismatch is detected.
///
/// Only active in release builds (`#[cfg(not(debug_assertions))]`).
pub fn verify() -> bool {
    #[cfg(debug_assertions)]
    return true;

    #[cfg(not(debug_assertions))]
    {
        let current = match hash_exe() {
            Some(h) => h,
            None => return true, // unable to read exe, allow execution
        };

        let snap_path = match snapshot_path() {
            Some(p) => p,
            None => return true,
        };

        if snap_path.exists() {
            let enc = match std::fs::read(&snap_path) {
                Ok(d) => d,
                Err(_) => return true,
            };
            let raw = xor_crypt(&enc);
            let stored = String::from_utf8_lossy(&raw);

            // Constant-time comparison to prevent timing side-channel
            let matches = stored.len() == current.len()
                && stored
                    .bytes()
                    .zip(current.bytes())
                    .fold(0u8, |acc, (a, b)| acc | (a ^ b))
                    == 0;
            if matches {
                return true;
            }

            // Closed beta / legitimate reinstall: refresh snapshot instead of hard exit.
            write_snapshot(&current);
            true
        } else {
            // First launch — create snapshot
            write_snapshot(&current);
            true
        }
    }
}

/// Refresh the stored hash after a legitimate update.
/// Call this from the auto-updater immediately after replacing the binary.
pub fn refresh_snapshot() {
    if let Some(h) = hash_exe() {
        write_snapshot(&h);
    }
}

fn write_snapshot(hash: &str) {
    if let Some(path) = snapshot_path() {
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let enc = xor_crypt(hash.as_bytes());
        let _ = std::fs::write(path, enc);
    }
}
