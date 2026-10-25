# ADR-0008: Page context from the browser tab beside the sidebar

**Date:** 2026-10-24
**Status:** accepted
**Decided by:** FDE
**SA reviewed:** n/a (capstone)

## Context
The sidebar sits next to a chart or quote page, but it didn't know which
one. "Is this a good entry?" got "which stock?", and every question had to
name the ticker. This was the one open item in `architecture.md`. Matt uses
the desktop app, so the page is in another app's window, not a tab the
sidebar owns.

## Decision
1. **Read the browser tab when the sidebar is focused** (`desktop/src-tauri/src/page.rs`).
   - Which app the user came from: `NSWorkspace.menuBarOwningApplication`.
     A menu bar app never owns the menu bar, so this still names the browser
     while the sidebar has focus. Fallback: the front app recorded just
     before the hotkey or tray click shows the window.
   - The tab's URL and title: one AppleScript call for Chrome, Arc, Brave,
     Edge, or Safari, in a background thread, sent to the UI as a `page`
     event.
   - macOS asks once per browser for permission. `Info.plist` says why. A
     "no" just means no page context.
2. **Turn it into a ticker in the shared UI** (`extension/src/pageTicker.ts`).
   - URL patterns for 14 finance sites: TradingView, Yahoo, Google Finance,
     Robinhood, Webull, Seeking Alpha, MarketWatch, CNBC, Nasdaq, Stock
     Analysis, Finviz, Fidelity, Schwab, E-Trade.
   - A page's title is used only where the URL doesn't carry the ticker: a
     TradingView chart layout, or a brokerage quote page.
   - HTTPS only, and only exact hosts or their subdomains.
3. **Show it and let the user drop it.** A chip above the input reads
   "Viewing AAPL on TradingView ×", and the placeholder becomes "Ask about
   AAPL…". A dismissed page stays dismissed until a different one opens.
4. **Send only `{ticker, source}`** with the question (`app.py`).
   - Both fields are regex-checked; anything else is a 400.
   - `prompts.user_turn()` puts one line before the question: "[Open in the
     user's window: AAPL on TradingView]".
   - The system prompt: "this", "here" and "the chart" mean that ticker; a
     company the question names wins.
   - The request log records the site, never the ticker.

## Alternatives considered
| Option | Why not |
|---|---|
| Send the page title or text to the agent | A title is written by whoever made the page; sending it is a prompt-injection path. A validated ticker carries everything the agent needs |
| Accessibility API (window titles of any app) | Needs the Accessibility permission, which grants far more (reading and controlling every app). Worth it only for desktop trading apps (TradingView desktop, thinkorswim); a follow-up if Matt uses one |
| Screen Recording permission (window names via CGWindowList) | Same over-broad permission problem, and macOS shows a recording indicator |
| Poll the front app continuously | Costs battery and reads pages the user never asked about. Reading on focus means it only looks when the user is about to ask |
| Detect the ticker in Rust | The Chrome extension can reuse the TypeScript version (`chrome.tabs`) without a second implementation |

## Consequences
- Easier: "this", "here", "the chart" work with no ticker typed. Eval cases
  in the new `page` category: page ticker used for "this"; a named company
  wins over the page.
- Harder: one permission prompt per browser on first use.
- Known limits:
  - Firefox has no AppleScript for tabs, and desktop trading apps aren't
    read.
  - Windows gets no page context (`front_app` returns nothing there).
  - The Chrome extension doesn't implement `onPageChange` yet. It would need
    the `tabs` permission.
  - The focus behaviour (`menuBarOwningApplication` for a menu bar app) is
    built but not yet checked by hand in the running app.
