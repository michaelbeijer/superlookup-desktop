# SuperLookup — standalone desktop app

A standalone **SuperLookup** desktop app, extracted from the SuperLookup feature
in the Supervertaler Workbench.

Type a term (or select one anywhere and hit the global hotkey), pick a language
pair, and every reference resource opens in its own **embedded browser tab** —
Superterm, IATE, Linguee, ProZ, Reverso, Juremy, BabelNet, Wikipedia,
Wiktionary, Google. You flip between them and read each site in place, **with
ads and trackers blocked**.

This is the thing the **web** version (superlookup.io) can't do: websites can
only use `<iframe>`, and these sites block being framed. A desktop app embeds a
top-level browser view, so it just works — and it can strip ads, which a plain
browser tab won't.

## Download (no install)

Grab a self-contained build for your OS from the
[Releases](../../releases) page (or the latest
[Actions run](../../actions) for bleeding-edge builds).
What's new in each version: [CHANGELOG.md](CHANGELOG.md).

- **Windows** — `SuperLookup.exe`, just double-click.
- **macOS** — `SuperLookup.app` (unsigned: right-click → Open the first time).
- **Linux** — `SuperLookup` binary; `chmod +x` and run.

Nothing to install — each build bundles Python + Qt + Chromium, so it's large
(~150–250 MB) and takes a couple of seconds to start (a one-file build unpacks
to a temp folder on launch).

## Run from source

```bash
pip install -r requirements.txt
python superlookup.py
```

## Build your own binary

```bash
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean --onefile --windowed --name SuperLookup \
  --exclude-module PyQt5 --exclude-module PySide6 --exclude-module PySide2 \
  superlookup.py
# → dist/SuperLookup(.exe/.app)
```

Binaries can't be cross-compiled (a Windows `.exe` must be built on Windows,
etc.), so `.github/workflows/build.yml` builds all three on GitHub's Windows /
macOS / Linux runners — automatically, and attached to a Release when you push a
`v*` tag.

## Features

- **Embedded browser tabs** for every web resource, lazily loaded.
- **Settings (⚙):** enable/disable searches, reorder them, add/edit/remove your
  own (name, icon, URL template, language codes), and import/export the set as
  JSON to share. Saved per-user to `config.json`.
- **Correct per-site language codes** (ProZ `dut/eng`, Juremy `nld/eng`,
  Linguee's English-first ordering, BabelNet uppercase, etc.), same as the
  Workbench.
- **Global hotkey — Ctrl+Alt+L:** select a term in any app, press it, and
  SuperLookup jumps to the front already searching it (clipboard capture via
  `pynput` + `pyperclip`). Runs in the system tray, so the hotkey stays live
  when the window is closed.
- **Persistent logins:** cookies/sessions are kept on disk (`webdata/`), so
  sites you log into stay logged in across launches.
- **Ad/tracker blocking:** a request interceptor drops known ad domains. It
  starts with a built-in list and upgrades in the background to the full
  **EasyList** domain rules (cached to `easylist_domains.txt`, refreshed
  weekly). Plus a cosmetic stylesheet to collapse leftover ad gaps.

## Scope

- **In:** search, language handling, embedded tabs, hotkey capture, ad-blocking.
- **Not:** the local **termbase** / **translation-memory** tiers — by design, a
  standalone user who wants those runs the full Supervertaler Workbench.
