import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { openUrl } from "@tauri-apps/plugin-opener";

import type { Platform } from "../../extension/src/platform";

// Sign-in finishes in the system browser, which redirects to a one-shot
// listener on localhost. The Rust side picks the port (the first free one of
// the few infra/login_setup.sh registers with Cognito) and returns the URL.

// Rust errors arrive as plain strings; the UI shows Error messages.
function rethrow(e: unknown): never {
  throw new Error(String(e));
}

// Plain memory: the webview lives as long as the app, hidden or not, so this
// matches the extension's chrome.storage.session (gone on quit).
const memory = new Map<string, unknown>();

export const tauriPlatform: Platform = {
  storage: {
    get: async <T>(key: string) => memory.get(key) as T | undefined,
    set: async (key, value) => void memory.set(key, value),
    remove: async (key) => void memory.delete(key),
  },
  redirectUri: () => invoke<string>("start_sign_in").catch(rethrow),
  launchAuthFlow: (url) => invoke<string>("sign_in", { url }).catch(rethrow),
  cancelAuthFlow: () => void invoke("cancel_sign_in"),
  openExternal: (url) =>
    void openUrl(url).catch((e: unknown) => console.error("couldn't open link", e)),
  hide: () => void getCurrentWindow().hide(),
};
