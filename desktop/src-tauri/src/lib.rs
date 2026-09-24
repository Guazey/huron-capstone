// Market Sidebar as a desktop app: the shared React UI in an always-on-top
// window docked to the right edge of the screen. It lives in the menu bar
// (Windows: system tray) and toggles with a global hotkey.

mod login;

use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, PhysicalPosition, PhysicalSize, WebviewWindow};
use tauri_plugin_global_shortcut::ShortcutState;
use tauri_plugin_opener::OpenerExt;

const HOTKEY: &str = "Alt+Shift+M";
const WIDTH: f64 = 400.0;

#[tauri::command]
async fn sign_in(app: AppHandle, url: String, port: u16) -> Result<String, String> {
    if !url.starts_with("https://") {
        return Err("Refusing to open a non-HTTPS login page.".into());
    }
    tauri::async_runtime::spawn_blocking(move || {
        login::wait_for_redirect(port, || {
            app.opener().open_url(url, None::<&str>).map_err(|e| e.to_string())
        })
    })
    .await
    .map_err(|e| e.to_string())?
}

fn main_window(app: &AppHandle) -> Option<WebviewWindow> {
    app.get_webview_window("main")
}

fn toggle(app: &AppHandle) {
    let Some(window) = main_window(app) else { return };
    if window.is_visible().unwrap_or(false) {
        let _ = window.hide();
    } else {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

/// Full height of the screen's usable area (below the menu bar, above the
/// Dock or taskbar), pinned to its right edge.
fn dock_right(window: &WebviewWindow) -> tauri::Result<()> {
    let Some(monitor) = window.primary_monitor()? else { return Ok(()) };
    let area = monitor.work_area();
    let width = (WIDTH * monitor.scale_factor()) as u32;
    window.set_size(PhysicalSize::new(width, area.size.height))?;
    window.set_position(PhysicalPosition::new(
        area.position.x + area.size.width as i32 - width as i32,
        area.position.y,
    ))
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_shortcuts([HOTKEY])
                .expect("valid hotkey")
                .with_handler(|app, _, event| {
                    if event.state == ShortcutState::Pressed {
                        toggle(app);
                    }
                })
                .build(),
        )
        .invoke_handler(tauri::generate_handler![sign_in])
        .setup(|app| {
            // A menu bar utility: no Dock icon, no app switcher entry.
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);

            let toggle_item = MenuItem::with_id(app, "toggle", format!("Show/Hide ({HOTKEY})"), true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "Quit Market Sidebar", true, None::<&str>)?;
            TrayIconBuilder::new()
                .icon(app.default_window_icon().expect("bundle icon").clone())
                .tooltip("Market Sidebar")
                .menu(&Menu::with_items(app, &[&toggle_item, &quit_item])?)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "toggle" => toggle(app),
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        toggle(tray.app_handle());
                    }
                })
                .build(app)?;

            // The window starts hidden so it doesn't flash before docking.
            if let Some(window) = main_window(app.handle()) {
                dock_right(&window)?;
                window.show()?;
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Market Sidebar");
}
