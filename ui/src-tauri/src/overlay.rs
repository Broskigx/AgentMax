/// Windows 11 Mica/Acrylic blur effect via DWM attributes.
use tauri::WebviewWindow;

#[cfg(target_os = "windows")]
pub fn apply_blur_effect(window: &WebviewWindow) {
    use windows::Win32::Graphics::Dwm::{
        DwmSetWindowAttribute, DWMWA_SYSTEMBACKDROP_TYPE, DWM_SYSTEMBACKDROP_TYPE,
    };

    let hwnd = window.hwnd().unwrap();

    // DWMSBT_MAINWINDOW = 2 (Mica), DWMSBT_TRANSIENTWINDOW = 3 (Acrylic)
    let backdrop_type: DWM_SYSTEMBACKDROP_TYPE = DWM_SYSTEMBACKDROP_TYPE(3);
    unsafe {
        let _ = DwmSetWindowAttribute(
            hwnd,
            DWMWA_SYSTEMBACKDROP_TYPE,
            &backdrop_type as *const _ as *const std::ffi::c_void,
            std::mem::size_of::<DWM_SYSTEMBACKDROP_TYPE>() as u32,
        );
    }
}

#[cfg(not(target_os = "windows"))]
pub fn apply_blur_effect(_window: &WebviewWindow) {}
