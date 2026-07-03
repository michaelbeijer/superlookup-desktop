# SuperLookup — standalone desktop mockup

A quick, runnable prototype of a standalone **SuperLookup**, extracted from the
SuperLookup feature in the Supervertaler Workbench.

Type a term (or select one anywhere and hit the global hotkey), pick a language
pair, and every reference resource opens in its own **embedded browser tab** —
Superterm, IATE, Linguee, ProZ, Reverso, Juremy, BabelNet, Wikipedia,
Wiktionary, Google. You flip between them and read each site in place, **with
ads and trackers blocked**.

This is the thing the **web** version (superlookup.io) can't do: websites can
only use `<iframe>`, and these sites block being framed. A desktop app embeds a
top-level browser view, so it just works — and it can strip ads, which a plain
browser tab won't.

## Run

```bash
pip install PyQt6 PyQt6-WebEngine pynput pyperclip
python superlookup.py
```

## Features

- **Embedded browser tabs** for every web resource, lazily loaded.
- **Correct per-site language codes** (ProZ `dut/eng`, Juremy `nld/eng`,
  Linguee's English-first ordering, BabelNet uppercase, etc.), same as the
  Workbench.
- **Global hotkey — Ctrl+Alt+L:** select a term in any app, press it, and
  SuperLookup jumps to the front already searching it (clipboard capture via
  `pynput` + `pyperclip`).
- **Ad/tracker blocking:** a request interceptor drops known ad domains. It
  starts with a built-in list and upgrades in the background to the full
  **EasyList** domain rules (cached to `easylist_domains.txt`, refreshed
  weekly). Plus a cosmetic stylesheet to collapse leftover ad gaps.

## What's a mockup here (vs. the full idea)

- **In:** search, language handling, embedded tabs, hotkey capture, ad-blocking.
- **Not:** the local **termbase** / **translation-memory** tiers — by design, a
  standalone user who wants those runs the full Supervertaler Workbench.
