// The page the user has open beside the sidebar: the active tab of the
// browser they were in when the sidebar took focus. The UI turns its URL and
// title into a ticker; nothing else about it leaves the machine.
//
// macOS only. Reading a browser's tab goes through AppleScript, so macOS asks
// once per browser ("Market Sidebar wants to control Google Chrome"). Saying
// no just means no page context. Other apps and other platforms give none.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use serde::Serialize;
use tauri::{AppHandle, Emitter};

/// What the UI's tickerFromPage() reads. Either field may be missing.
#[derive(Clone, Serialize)]
pub struct OpenPage {
    url: Option<String>,
    title: Option<String>,
}

/// The app in front just before the sidebar was shown, for when macOS
/// reports the sidebar itself as the front app.
static LAST_FRONT: Mutex<Option<String>> = Mutex::new(None);
/// A read in progress: a permission prompt takes focus and gives it back,
/// which would otherwise start a second read behind the first.
static READING: AtomicBool = AtomicBool::new(false);

/// Remember the front app; call right before showing the window.
pub fn remember_front(app: &AppHandle) {
    if let Some(id) = front_app(app) {
        *LAST_FRONT.lock().unwrap() = Some(id);
    }
}

/// Read the front browser's tab in the background and send it to the UI as a
/// "page" event (null when there's nothing to read).
pub fn refresh(app: &AppHandle) {
    let Some(bundle_id) = front_app(app).or_else(|| LAST_FRONT.lock().unwrap().clone()) else {
        let _ = app.emit("page", None::<OpenPage>);
        return;
    };
    if READING.swap(true, Ordering::SeqCst) {
        return;
    }
    let app = app.clone();
    std::thread::spawn(move || {
        let page = read_tab(&bundle_id);
        READING.store(false, Ordering::SeqCst);
        let _ = app.emit("page", page);
    });
}

/// The bundle ID of the app the user is looking at, unless it's the sidebar.
#[cfg(target_os = "macos")]
fn front_app(app: &AppHandle) -> Option<String> {
    use objc2_app_kit::NSWorkspace;

    let workspace = NSWorkspace::sharedWorkspace();
    // A menu bar app never owns the menu bar, so this stays on the app the
    // user came from even while the sidebar has focus.
    let front = workspace.menuBarOwningApplication().or_else(|| workspace.frontmostApplication())?;
    let id = front.bundleIdentifier()?.to_string();
    (id != app.config().identifier).then_some(id)
}

#[cfg(not(target_os = "macos"))]
fn front_app(_app: &AppHandle) -> Option<String> {
    None
}

/// AppleScript that returns the active tab's URL and title on two lines.
fn tab_script(bundle_id: &str) -> Option<String> {
    let (tab, title) = match bundle_id {
        // Chromium browsers share Chrome's scripting dictionary.
        "com.google.Chrome" | "com.google.Chrome.beta" | "com.brave.Browser"
        | "com.microsoft.edgemac" | "company.thebrowser.Browser" => ("active tab", "title"),
        "com.apple.Safari" => ("current tab", "name"),
        _ => return None,
    };
    Some(format!(
        "tell application id \"{bundle_id}\"\n\
         if (count of windows) is 0 then return \"\"\n\
         set t to {tab} of front window\n\
         return (URL of t) & linefeed & ({title} of t)\n\
         end tell"
    ))
}

fn read_tab(bundle_id: &str) -> Option<OpenPage> {
    let script = tab_script(bundle_id)?;
    let out = std::process::Command::new("osascript").args(["-e", &script]).output().ok()?;
    if !out.status.success() {
        // Most often the user said no to the permission prompt.
        return None;
    }
    let text = String::from_utf8_lossy(&out.stdout);
    let mut lines = text.trim_end().splitn(2, '\n');
    let url = lines.next().filter(|u| !u.is_empty())?.to_string();
    Some(OpenPage { url: Some(url), title: lines.next().map(str::to_string) })
}

#[cfg(test)]
mod tests {
    use super::tab_script;

    #[test]
    fn scripts_only_known_browsers() {
        assert!(tab_script("com.google.Chrome").unwrap().contains("active tab"));
        assert!(tab_script("com.apple.Safari").unwrap().contains("current tab"));
        assert!(tab_script("org.mozilla.firefox").is_none());
        assert!(tab_script("com.tradingview.tradingviewapp.desktop").is_none());
    }
}
