import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { openUrl } from "@tauri-apps/plugin-opener";

import type { Platform } from "../../extension/src/platform";

// Sign-in finishes in the system browser, which redirects to a one-shot
// listener the Rust side opens on this port. Cognito matches callback URLs
// exactly, so infra/login_setup.sh registers this same URL.
const LOGIN_PORT = 47813;

// Plain memory: the webview lives as long as the app, hidden or not, so this
// matches the extension's chrome.storage.session (gone on quit).
const memory = new Map<string, unknown>();

export const tauriPlatform: Platform = {
  storage: {
    get: async <T>(key: string) => memory.get(key) as T | undefined,
    set: async (key, value) => void memory.set(key, value),
    remove: async (key) => void memory.delete(key),
  },
  redirectUri: () => `http://localhost:${LOGIN_PORT}/callback`,
  // Rust errors arrive as plain strings; the UI shows Error messages.
  launchAuthFlow: (url) =>
    invoke<string>("sign_in", { url, port: LOGIN_PORT }).catch((e: unknown) => {
      throw new Error(String(e));
    }),
  cancelAuthFlow: () => void invoke("cancel_sign_in"),
  openExternal: (url) =>
    void openUrl(url).catch((e: unknown) => console.error("couldn't open link", e)),
  hide: () => void getCurrentWindow().hide(),
};
