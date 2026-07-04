# Changelog

All notable changes to SuperLookup are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.9] — 2026-07-05

### Fixed
- **macOS: hardened the global-hotkey copy against a remaining crash.** The copy
  now presses `C` by its raw virtual keycode (`kVK_ANSI_C` = 8) instead of the
  character `"c"`, so pynput never routes through the Carbon Text-Input-Source
  APIs (`TSMGetInputSourceProperty`) that assert the main dispatch queue and
  `SIGTRAP`. Moving the copy to the GUI thread (0.1.8) didn't always satisfy that
  assertion; sending a raw keycode avoids the API path entirely.

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

[0.1.8]: https://github.com/michaelbeijer/superlookup-desktop/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/michaelbeijer/superlookup-desktop/compare/v0.1.4...v0.1.7
[0.1.4]: https://github.com/michaelbeijer/superlookup-desktop/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/michaelbeijer/superlookup-desktop/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/michaelbeijer/superlookup-desktop/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/michaelbeijer/superlookup-desktop/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/michaelbeijer/superlookup-desktop/releases/tag/v0.1.0
