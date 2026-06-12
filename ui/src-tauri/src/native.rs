use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
pub struct ScreenSize {
    pub width: u32,
    pub height: u32,
}

// ── Screen size ────────────────────────────────────────────────────────────────

pub fn get_screen_size() -> ScreenSize {
    #[cfg(target_os = "windows")]
    unsafe {
        use windows::Win32::UI::WindowsAndMessaging::{GetSystemMetrics, SM_CXSCREEN, SM_CYSCREEN};
        return ScreenSize {
            width: GetSystemMetrics(SM_CXSCREEN) as u32,
            height: GetSystemMetrics(SM_CYSCREEN) as u32,
        };
    }
    #[cfg(not(target_os = "windows"))]
    ScreenSize {
        width: 1920,
        height: 1080,
    }
}

// ── GDI screenshot → PNG bytes ─────────────────────────────────────────────────

#[cfg(target_os = "windows")]
pub fn gdi_capture_png() -> Result<Vec<u8>, String> {
    use windows::Win32::Graphics::Gdi::{
        BitBlt, CreateCompatibleBitmap, CreateCompatibleDC, DeleteDC, DeleteObject, GetDC,
        GetDIBits, ReleaseDC, SelectObject, BITMAPINFO, BITMAPINFOHEADER, CAPTUREBLT,
        DIB_RGB_COLORS, SRCCOPY,
    };
    use windows::Win32::UI::WindowsAndMessaging::{GetSystemMetrics, SM_CXSCREEN, SM_CYSCREEN};

    unsafe {
        let sw = GetSystemMetrics(SM_CXSCREEN);
        let sh = GetSystemMetrics(SM_CYSCREEN);

        let hdc = GetDC(None);
        let mdc = CreateCompatibleDC(Some(hdc));
        let bmp = CreateCompatibleBitmap(hdc, sw, sh);
        let old = SelectObject(mdc, bmp.into());

        let _ = BitBlt(mdc, 0, 0, sw, sh, Some(hdc), 0, 0, SRCCOPY | CAPTUREBLT);

        // Zero-init then set required fields (biCompression stays 0 = BI_RGB)
        let mut bmi: BITMAPINFO = std::mem::zeroed();
        bmi.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
        bmi.bmiHeader.biWidth = sw;
        bmi.bmiHeader.biHeight = -sh; // top-down scan
        bmi.bmiHeader.biPlanes = 1;
        bmi.bmiHeader.biBitCount = 32;

        let pixel_count = (sw * sh) as usize;
        let mut pixels = vec![0u8; pixel_count * 4];

        GetDIBits(
            mdc,
            bmp,
            0,
            sh as u32,
            Some(pixels.as_mut_ptr() as *mut _),
            &mut bmi,
            DIB_RGB_COLORS,
        );

        let _ = SelectObject(mdc, old);
        let _ = DeleteObject(bmp.into());
        let _ = DeleteDC(mdc);
        let _ = ReleaseDC(None, hdc);

        // BGRA → RGBA (GDI returns BGRA)
        for chunk in pixels.chunks_exact_mut(4) {
            chunk.swap(0, 2);
        }

        encode_png(&pixels, sw as u32, sh as u32)
    }
}

#[cfg(not(target_os = "windows"))]
pub fn gdi_capture_png() -> Result<Vec<u8>, String> {
    Err("Screen capture only available on Windows".into())
}

fn encode_png(rgba: &[u8], w: u32, h: u32) -> Result<Vec<u8>, String> {
    let mut out: Vec<u8> = Vec::new();
    let mut enc = png::Encoder::new(&mut out, w, h);
    enc.set_color(png::ColorType::Rgba);
    enc.set_depth(png::BitDepth::Eight);
    let mut writer = enc
        .write_header()
        .map_err(|e| format!("PNG header: {}", e))?;
    writer
        .write_image_data(rgba)
        .map_err(|e| format!("PNG data: {}", e))?;
    drop(writer);
    Ok(out)
}

// ── Mouse move (no click) ─────────────────────────────────────────────────────

#[cfg(target_os = "windows")]
pub fn inject_mouse_move(x: i32, y: i32) {
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        SendInput, INPUT, INPUT_0, INPUT_MOUSE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_MOVE, MOUSEINPUT,
    };
    use windows::Win32::UI::WindowsAndMessaging::{GetSystemMetrics, SM_CXSCREEN, SM_CYSCREEN};
    unsafe {
        let sw = GetSystemMetrics(SM_CXSCREEN);
        let sh = GetSystemMetrics(SM_CYSCREEN);
        if sw == 0 || sh == 0 {
            return;
        }
        let nx = (x * 65535) / sw;
        let ny = (y * 65535) / sh;
        let input = INPUT {
            r#type: INPUT_MOUSE,
            Anonymous: INPUT_0 {
                mi: MOUSEINPUT {
                    dx: nx,
                    dy: ny,
                    mouseData: 0,
                    dwFlags: MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
                    time: 0,
                    dwExtraInfo: 0,
                },
            },
        };
        SendInput(&[input], std::mem::size_of::<INPUT>() as i32);
    }
}

#[cfg(not(target_os = "windows"))]
pub fn inject_mouse_move(_x: i32, _y: i32) {}

// ── Unicode text injection ─────────────────────────────────────────────────────

#[cfg(target_os = "windows")]
pub fn inject_text(text: &str) {
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE,
        VIRTUAL_KEY,
    };
    let utf16: Vec<u16> = text.encode_utf16().collect();
    let mut inputs: Vec<INPUT> = Vec::with_capacity(utf16.len() * 2);
    for &ch in &utf16 {
        inputs.push(INPUT {
            r#type: INPUT_KEYBOARD,
            Anonymous: INPUT_0 {
                ki: KEYBDINPUT {
                    wVk: VIRTUAL_KEY(0),
                    wScan: ch,
                    dwFlags: KEYEVENTF_UNICODE,
                    time: 0,
                    dwExtraInfo: 0,
                },
            },
        });
        inputs.push(INPUT {
            r#type: INPUT_KEYBOARD,
            Anonymous: INPUT_0 {
                ki: KEYBDINPUT {
                    wVk: VIRTUAL_KEY(0),
                    wScan: ch,
                    dwFlags: KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                    time: 0,
                    dwExtraInfo: 0,
                },
            },
        });
    }
    if !inputs.is_empty() {
        unsafe {
            SendInput(&inputs, std::mem::size_of::<INPUT>() as i32);
        }
    }
}

#[cfg(not(target_os = "windows"))]
pub fn inject_text(_text: &str) {}

// ── Focused window title ───────────────────────────────────────────────────────

pub fn focused_window_title() -> Option<String> {
    #[cfg(target_os = "windows")]
    unsafe {
        use windows::Win32::UI::WindowsAndMessaging::{
            GetForegroundWindow, GetWindowTextLengthW, GetWindowTextW,
        };
        let hwnd = GetForegroundWindow();
        let len = GetWindowTextLengthW(hwnd);
        if len > 0 {
            let mut buf = vec![0u16; (len + 1) as usize];
            GetWindowTextW(hwnd, &mut buf);
            return Some(String::from_utf16_lossy(&buf[..len as usize]));
        }
        None
    }
    #[cfg(not(target_os = "windows"))]
    None
}

// ── Keyboard injection ─────────────────────────────────────────────────────────

#[cfg(target_os = "windows")]
pub fn inject_key_event(vk_code: u16, is_down: bool) {
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYBD_EVENT_FLAGS, KEYEVENTF_KEYUP,
        VIRTUAL_KEY,
    };
    let flags = if is_down {
        KEYBD_EVENT_FLAGS(0)
    } else {
        KEYEVENTF_KEYUP
    };
    let input = INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: VIRTUAL_KEY(vk_code),
                wScan: 0,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    };
    unsafe {
        SendInput(&[input], std::mem::size_of::<INPUT>() as i32);
    }
}

#[cfg(not(target_os = "windows"))]
pub fn inject_key_event(_vk_code: u16, _is_down: bool) {}

// ── Mouse injection ────────────────────────────────────────────────────────────

#[cfg(target_os = "windows")]
pub fn inject_mouse_click(x: i32, y: i32, button: &str) {
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        SendInput, INPUT, INPUT_0, INPUT_MOUSE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_LEFTDOWN,
        MOUSEEVENTF_LEFTUP, MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, MOUSEINPUT,
        MOUSE_EVENT_FLAGS,
    };
    use windows::Win32::UI::WindowsAndMessaging::{GetSystemMetrics, SM_CXSCREEN, SM_CYSCREEN};

    unsafe {
        let sw = GetSystemMetrics(SM_CXSCREEN);
        let sh = GetSystemMetrics(SM_CYSCREEN);
        if sw == 0 || sh == 0 {
            return;
        }
        let nx = (x * 65535) / sw;
        let ny = (y * 65535) / sh;

        let (dn, up): (MOUSE_EVENT_FLAGS, MOUSE_EVENT_FLAGS) = match button {
            "right" => (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
            _ => (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
        };

        let make = |flags: MOUSE_EVENT_FLAGS| INPUT {
            r#type: INPUT_MOUSE,
            Anonymous: INPUT_0 {
                mi: MOUSEINPUT {
                    dx: nx,
                    dy: ny,
                    mouseData: 0,
                    dwFlags: flags | MOUSEEVENTF_ABSOLUTE,
                    time: 0,
                    dwExtraInfo: 0,
                },
            },
        };
        SendInput(&[make(dn), make(up)], std::mem::size_of::<INPUT>() as i32);
    }
}

#[cfg(not(target_os = "windows"))]
pub fn inject_mouse_click(_x: i32, _y: i32, _button: &str) {}
