# ADR-0004: Tauri for the desktop app, sharing the extension's UI

**Date:** 2026-09-24
**Status:** accepted
**Decided by:** FDE
**SA reviewed:** n/a (capstone)

## Context
The sidebar only exists inside Chrome. Traders also work in desktop apps
(broker terminals, spreadsheets), where a Chrome side panel isn't visible. The
desktop version needs to stay on top of other windows, dock to the screen
edge, live in the menu bar (Windows: system tray), and toggle with a global
hotkey. It should reuse the React UI rather than fork it, and hold the same
security line as the extension: tokens in memory only, no secrets shipped,
links opened only when they are verified sources.

## Decision
Build it with **Tauri 2**. The UI in `extension/src` is shared as-is; the only
host-specific code sits behind `platform.ts` (storage, sign-in, opening links,
hiding the window), with `chromePlatform.ts` for the extension and
`desktop/src/tauriPlatform.ts` for the app. Sign-in opens Cognito's hosted
login in the system browser and catches the redirect on a loopback listener
(`desktop/src-tauri/src/login.rs`, `http://localhost:47813/callback`), then
finishes the same PKCE exchange the extension uses.

## Alternatives considered
| Option | Why not |
|---|---|
| Electron | Same UI reuse, but ships its own Chromium (100+ MB against ~10 MB here) and runs a Node process next to the page, which takes more work to lock down |
| Native (SwiftUI / WinUI) | Best OS fit, but a UI rewrite per platform and two codebases to keep in step with the extension |
| PWA / web page | Can't stay on top, register a global hotkey, or live in the menu bar |
| Wails (Go) | Close to Tauri in design, with a smaller ecosystem and fewer ready-made tray and hotkey plugins |
| Flutter | A UI rewrite in Dart |

## Consequences
- Easier: one UI for both hosts. A ~10 MB macOS bundle. Deny-by-default
  permissions: the page may only hide its window and open `https://` links
  (`capabilities/default.json`), with no Node or filesystem access, and the CSP
  limits network calls to AgentCore and Cognito.
- Harder: the app shell is Rust, and building it needs a Rust toolchain. The web view is the OS
  one (WebKit on macOS, WebView2 on Windows), so rendering can differ slightly
  between them. There's no `chrome.identity`, so sign-in needs its own loopback
  listener and a second Cognito callback URL (`infra/login_setup.sh`).
- Loopback redirect: the listener binds 127.0.0.1/::1 only, answers one
  `/callback` request, and times out after 5 minutes. `state` and PKCE are
  checked in the shared `auth.ts`, so a local process that reaches the port
  can't use the code without the verifier.
- Follow-ups: code signing and notarization for distribution; auto-update
  (Tauri updater plugin); fall back to another port if 47813 is taken.
