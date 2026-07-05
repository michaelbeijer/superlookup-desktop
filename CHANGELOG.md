# Changelog

All notable changes to SuperLookup are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.19] — 2026-07-05

### Fixed
- **macOS: the window now actually comes to the front on the hotkey.** With the
  tap fixed in 0.1.18, the search ran reliably but the window often stayed
  behind the app you copied from — macOS 26 largely ignores
  `activateIgnoringOtherApps` for a background app. SuperLookup now also orders
  its native `NSWindow` front directly (`makeKeyAndOrderFront` +
  `orderFrontRegardless`), which bypasses the "app is not active" restriction,
  so it pops forward with your results instead of searching invisibly.

_macOS-only release; Windows/Linux behaviour is unchanged._

## [0.1.18] — 2026-07-05

### Fixed
- **macOS: the real fix for the hotkey intermittently going dead.** The key-tap
  ran on the app's main thread, so when that thread got busy (embedded web
  views) macOS quietly stopped feeding the tap — a "zombie" tap that still
  reported enabled, which no re-enable could revive (that's why only a relaunch
  helped, and why 0.1.17's heartbeat couldn't catch it). The tap now runs on its
  **own dedicated thread** with its own run loop (the same pattern pynput uses),
  so a busy main thread can never starve it. To stay off the Carbon
  Text-Input-Source APIs on that thread (which crash off-main — the original
  0.1.7 crash), the hotkey is matched by **hardware keycode + modifier flags**
  instead of `charactersIgnoringModifiers`, and the match is marshalled back to
  the GUI thread via a queued signal.

_macOS-only release; Windows/Linux behaviour is unchanged._

## [0.1.17] — 2026-07-05

### Fixed
- **macOS: the hotkey no longer silently stops working until you relaunch.** The
  key-tap runs on the app's main thread; when that thread got busy (loading the
  embedded web views), macOS could disable the tap on timeout, and because the
  re-enable was also stuck on the busy thread it never recovered — so the hotkey
  went dead until you quit and reopened the app. Two changes fix this:
  a **fast modifier pre-check** so the per-keystroke callback stays quick (it no
  longer builds an NSEvent for keys that don't carry the hotkey's modifiers),
  which makes a timeout far less likely; and a **2-second heartbeat that
  re-enables the tap** whenever macOS is found to have disabled it, so it
  self-heals within a couple of seconds instead of needing a relaunch.

_macOS-only release; Windows/Linux behaviour is unchanged._

## [0.1.16] — 2026-07-05

### Fixed
- **macOS: no more spurious "grant Accessibility" prompt after an auto-update.**
  When the updater swaps in the new bundle and relaunches, macOS can take a few
  seconds to re-associate an existing Accessibility grant with the new copy —
  during which the startup check read as untrusted and fired the system prompt
  even though the grant was fine. The check now retries for ~8 seconds before
  ever prompting, so a normal update no longer nags you. (If the grant is
  genuinely missing it still prompts and explains how to fix a stale entry.)
- **macOS: the window now comes to the front reliably from a cold start.**
  Previously the search ran but the window stayed behind the active app until
  you'd opened SuperLookup once — a tray app's first `activateIgnoringOtherApps`
  is dropped before its window has been realised. Activation is now asserted
  again on the next event-loop turn (after the window is shown), so it comes
  forward the first time too.

_macOS-only release; Windows/Linux behaviour is unchanged from 0.1.15._

## [0.1.15] — 2026-07-05

### Changed
- **The default hotkey on Windows and Linux is now `Ctrl+Shift+L`** (macOS keeps
  `⌘⌥L`). This leaves `Ctrl+Alt+L` free for Supervertaler and the Workbench's
  built-in SuperLookup. Anyone still on the old `Ctrl+Alt+L` default is migrated
  across once; if you deliberately set `Ctrl+Alt+L` it's left untouched.

### Fixed
- **Windows: the hotkey now brings SuperLookup to the front even when it's
  behind another app.** Windows blocks a background process from stealing focus,
  so `raise()`/`activateWindow()` left the window behind (e.g. behind Trados) —
  it only worked from the tray or when already visible. It now uses the same
  proven foreground-grab chain as the Supervertaler Workbench: a synthetic
  Alt-key press (which satisfies `SetForegroundWindow`'s documented "Alt pressed"
  exception), the `AttachThreadInput` dance, then `BringWindowToTop` +
  `SetForegroundWindow` + `SwitchToThisWindow`.

## [0.1.14] — 2026-07-05

### Fixed
- **Windows: the one-click updater now actually replaces the app.** The swap
  helper copied the new `.exe` over the old one the moment the app disappeared
  from the task list, but Windows hadn't released the file handle yet, so the
  copy failed silently and the old version stayed (it kept re-offering the
  update). The helper now retries the copy until the file is free — which also
  waits for the app to fully exit — then relaunches, and gives up gracefully
  after ~30s rather than looping.

## [0.1.13] — 2026-07-05

### Changed
- Updated the About dialog and in-window help text to match superlookup.io's
  current wording ("one-keystroke terminology research", "terminology sources",
  "Supervertaler Workbench").

## [0.1.12] — 2026-07-05

### Fixed
- **macOS: the global hotkey no longer leaks into the app you copy from.** The
  previous watch-only NSEvent monitor didn't consume the keystroke, so the combo
  also triggered whatever the foreground app binds it to (e.g. Chrome opened its
  Downloads page on ⌘⌥L). SuperLookup now uses a `CGEventTap` that intercepts and
  **consumes** the hotkey, so it never reaches other apps — it works everywhere
  (Chrome included) and can't disturb your selection. Falls back to the old
  watch-only monitor if a tap can't be created.

## [0.1.11] — 2026-07-05

### Fixed
- **macOS: the hotkey no longer disturbs the app you copy from, and now brings
  SuperLookup to the front.** The global key monitor doesn't consume the
  keystroke, so the shortcut also reached the foreground app; with a Control-
  based combo that meant text views mangled the selection. The macOS modifier
  mapping now follows Qt's convention (Qt "Ctrl" → ⌘, "Meta" → ⌃), so the
  default shortcut is **⌘⌥L (Command+Option+L)** — an inert combo no app reacts
  to, matching Supervertaler. This also aligns the global hotkey with what Qt's
  in-app shortcut uses.
- macOS: after capturing the selection, the window is forced to the front via
  `NSApplication.activateIgnoringOtherApps` (Qt's `raise()`/`activateWindow()`
  can't pull a tray app in front of the active app on macOS).

## [0.1.10] — 2026-07-05

### Fixed
- **macOS: the global hotkey did nothing because the app never asked for
  Accessibility permission.** Detecting the hotkey and synthesizing the Cmd+C
  that grabs your selection both require it; without an effective grant the
  keystroke is silently dropped and the search box stays empty. SuperLookup now
  checks Accessibility on launch, triggers the system prompt, and explains how
  to enable it — including how to fix a *stale* grant (remove and re-add the
  app), which is easy to end up with after updating.
- macOS: the clipboard is now read via `NSPasteboard` directly instead of
  pyperclip, avoiding a `pbpaste` lookup that can misbehave inside the packaged
  `.app`.

### Internal
- The Accessibility-trust check is imported separately, so if it is ever
  unavailable it can't disable the working NSEvent/Quartz hotkey path.

## [0.1.9] — 2026-07-05

### Fixed
- **macOS: fixed the remaining global-hotkey crashes** (on pressing the
  shortcut and on changing it in Settings). The 0.1.8 fix covered the copy
  keystroke but not pynput's hotkey *listener*, which starts its own thread and
  calls Carbon Text-Input-Source APIs from it (`keycode_context()` in pynput's
  `_darwin.py`) — the same main-thread assertion trap, re-triggered on every
  shortcut change because rebinding restarted the listener. macOS now uses
  native main-thread NSEvent global/local monitors for hotkey detection and a
  Quartz `CGEvent` (raw `kVK_ANSI_C` keycode) for the Cmd+C copy; pynput is no
  longer used at all on macOS. Windows and Linux keep pynput unchanged.
- macOS: changing the shortcut no longer restarts a listener thread — the
  monitors persist and only the match target changes.

### Added
- macOS: function-key shortcuts (F1–F12) are matched by hardware keycode, and
  held-down hotkeys no longer fire repeatedly (key-repeat is ignored).

## [0.1.8] — 2026-07-05

### Fixed
- **macOS: fixed a crash when pressing the global hotkey.** The copy keystroke
  ran on a background thread, where pynput's character-to-keycode translation
  calls Carbon Text-Input-Source APIs that now assert main-thread on current
  macOS (`EXC_BREAKPOINT` / `SIGTRAP` in `dispatch_assert_queue`). The copy now
  runs on the GUI thread via a `QTimer` chain.
- **macOS: the selection is now copied with Cmd+C** (was Ctrl+C, which never
  copied on macOS). Windows and Linux continue to use Ctrl+C.
- The macOS app now reports its real version in `Info.plist` and crash reports
  (previously `0.0.0`).

### Added
- Reusable signed-and-notarized macOS build pipeline (`build_macos.sh` and
  `packaging/entitlements.plist`) for producing the distributed `.dmg`.

## [0.1.7] — 2026-07-04

### Added
- Auto-update: checks GitHub Releases and offers one-click download/install. On
  macOS it uses the hand-signed `.dmg` (mounts it rather than unzipping).

### Changed
- User data (cookies/logins, EasyList cache, settings) is now stored in a
  per-user folder — `~/Library/Application Support/SuperLookup` on macOS,
  `%APPDATA%` on Windows, XDG on Linux — instead of next to the app.
- Rebranded the Superterm resource back to Beijerterm (beijerterm.com).
- CI no longer attaches a macOS asset; the signed `.dmg` is uploaded manually.

### Fixed
- Double tab icons: the placeholder emoji is dropped once the real favicon loads.

## [0.1.4] — 2026-07-03

### Added
- ~27 built-in searches, plus a customizable default page zoom.
- Collapsible suggestion list with in-app link navigation.
- Native suggestion lists for Wikipedia/Wiktionary/Wikidata, with
  auto-refreshing built-ins.

### Changed
- MediaWiki suggestions now send a User-Agent (required by the Wikimedia API).
- Real favicons on tabs; dropped the Cloudflare-walled Sensagent resource.

### Fixed
- In-tab link navigation: working Back button and fewer Cloudflare loops.
- Cloudflare-walled pages bounce to the real browser instead of looping
  (only click-throughs are bounced, not the first page).

## [0.1.3] — 2026-07-03

### Added
- Help/About, reachable from a compact "?" toolbar button.

## [0.1.2] — 2026-07-03

### Changed
- Dropped the "mockup" label — SuperLookup is a released app.

## [0.1.1] — 2026-07-03

### Changed
- Visual pass: Fusion base with a cohesive theme and Chrome-style tabs.
- Tighter search box and fewer captchas.

## [0.1.0] — 2026-07-03

Initial release.

### Added
- Embedded reference tabs, ad-blocking, and a global hotkey.
- Enable/disable, reorder, add/edit, and import/export of searches.
- Customizable global hotkey; window position restored on hotkey recall.
- Cross-platform packaging (macOS, Windows, Linux).

[0.1.19]: https://github.com/michaelbeijer/superlookup-desktop/compare/v0.1.18...v0.1.19
[0.1.18]: https://github.com/michaelbeijer/superlookup-desktop/compare/v0.1.17...v0.1.18
[0.1.17]: https://github.com/michaelbeijer/superlookup-desktop/compare/v0.1.16...v0.1.17
[0.1.16]: https://github.com/michaelbeijer/superlookup-desktop/compare/v0.1.15...v0.1.16
[0.1.15]: https://github.com/michaelbeijer/superlookup-desktop/compare/v0.1.14...v0.1.15
[0.1.14]: https://github.com/michaelbeijer/superlookup-desktop/compare/v0.1.13...v0.1.14
[0.1.13]: https://github.com/michaelbeijer/superlookup-desktop/compare/v0.1.12...v0.1.13
[0.1.12]: https://github.com/michaelbeijer/superlookup-desktop/compare/v0.1.11...v0.1.12
[0.1.11]: https://github.com/michaelbeijer/superlookup-desktop/compare/v0.1.10...v0.1.11
[0.1.10]: https://github.com/michaelbeijer/superlookup-desktop/compare/v0.1.9...v0.1.10
[0.1.9]: https://github.com/michaelbeijer/superlookup-desktop/compare/v0.1.8...v0.1.9
[0.1.8]: https://github.com/michaelbeijer/superlookup-desktop/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/michaelbeijer/superlookup-desktop/compare/v0.1.4...v0.1.7
[0.1.4]: https://github.com/michaelbeijer/superlookup-desktop/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/michaelbeijer/superlookup-desktop/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/michaelbeijer/superlookup-desktop/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/michaelbeijer/superlookup-desktop/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/michaelbeijer/superlookup-desktop/releases/tag/v0.1.0
