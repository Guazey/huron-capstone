// Auto-update from GitHub Releases. The app reads latest.json from the fixed
// desktop-latest release (see desktop/release.sh), downloads the build it
// names, and checks its signature against the public key in tauri.conf.json:
// anything unsigned, or signed by another key, is refused. Downloads happen in
// the background; installing waits for the user to pick "Restart to update",
// because a restart clears the chat and the sign-in (both are memory-only).

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;

use tauri::menu::MenuItem;
use tauri::{AppHandle, Manager, Wry};
use tauri_plugin_updater::{Error, Update, UpdaterExt};

const CHECK_EVERY: Duration = Duration::from_secs(6 * 60 * 60);
// Without these a stalled connection (sleep, Wi-Fi change) hangs the check
// forever and the menu stays on "Checking…".
const CHECK_TIMEOUT: Duration = Duration::from_secs(30);
const DOWNLOAD_TIMEOUT: Duration = Duration::from_secs(10 * 60);

/// The tray menu item that shows update status, and a downloaded update.
pub struct Updates {
    item: MenuItem<Wry>,
    ready: Mutex<Option<(Update, Vec<u8>)>>,
    checking: AtomicBool,
}

impl Updates {
    pub fn new(item: MenuItem<Wry>) -> Self {
        Self { item, ready: Mutex::new(None), checking: AtomicBool::new(false) }
    }
}

/// Check now, then every few hours for as long as the app runs.
pub fn start(app: &AppHandle) {
    let app = app.clone();
    thread::spawn(move || loop {
        tauri::async_runtime::block_on(check(&app));
        thread::sleep(CHECK_EVERY);
    });
}

/// The tray item was clicked: install a downloaded update, or check for one.
pub fn clicked(app: &AppHandle) {
    let updates = app.state::<Updates>();
    let ready = updates.ready.lock().unwrap().take();
    match ready {
        Some((update, bytes)) => match update.install(&bytes) {
            Ok(()) => app.restart(),
            Err(e) => {
                eprintln!("update install failed: {e}");
                let _ = updates.item.set_text("Update failed. Click to try again");
            }
        },
        None => {
            let app = app.clone();
            tauri::async_runtime::spawn(async move { check(&app).await });
        }
    }
}

async fn check(app: &AppHandle) {
    let updates = app.state::<Updates>();
    if updates.ready.lock().unwrap().is_some() || updates.checking.swap(true, Ordering::SeqCst) {
        return;
    }
    let _ = updates.item.set_text("Checking for updates…");
    let text = match download(app).await {
        Ok(Some((update, bytes))) => {
            let text = format!("Restart to update to v{}", update.version);
            *updates.ready.lock().unwrap() = Some((update, bytes));
            text
        }
        Ok(None) => format!("Up to date (v{})", app.package_info().version),
        // latest.json has no build for this OS or CPU (releases are macOS only).
        Err(Error::TargetNotFound(_)) => {
            format!("No updates for this platform (v{})", app.package_info().version)
        }
        Err(e) => {
            eprintln!("update check failed: {e}");
            "Couldn't check for updates. Click to retry".into()
        }
    };
    let _ = updates.item.set_text(text);
    updates.checking.store(false, Ordering::SeqCst);
}

/// The newer release, downloaded and signature-checked, or None if current.
async fn download(app: &AppHandle) -> Result<Option<(Update, Vec<u8>)>, Error> {
    let updater = app.updater_builder().timeout(CHECK_TIMEOUT).build()?;
    let Some(mut update) = updater.check().await? else { return Ok(None) };
    update.timeout = Some(DOWNLOAD_TIMEOUT);
    let bytes = update.download(|_, _| {}, || {}).await?;
    Ok(Some((update, bytes)))
}
