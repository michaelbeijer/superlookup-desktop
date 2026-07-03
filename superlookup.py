#!/usr/bin/env python3
"""SuperLookup — standalone desktop mockup.

Type a term (or select one anywhere and press the global hotkey), pick a
language pair, and every reference resource opens in its own *embedded* browser
tab — Superterm, IATE, Linguee, ProZ, Reverso, Juremy, BabelNet, Wikipedia,
Wiktionary, Google — with ads and trackers blocked. A desktop app can embed a
real (top-level) browser view, which a website can't: sites block being put in
an <iframe>.

Extracted and simplified from the SuperLookup feature in the Supervertaler
Workbench (the resource list, per-site language codes, and clipboard-capture
approach are the same).

Global hotkey:  Ctrl+Alt+L  — copies the current selection and searches it.

Run:
    python superlookup.py
Needs:
    pip install PyQt6 PyQt6-WebEngine pynput pyperclip
"""

import json
import os
import re
import sys
import threading
import time
from urllib.parse import quote
from urllib.request import Request, urlopen

from PyQt6.QtCore import Qt, QUrl, QObject, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QComboBox, QPushButton, QTabWidget, QLabel,
    QSystemTrayIcon, QMenu, QStyle,
)

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import (
        QWebEngineProfile, QWebEnginePage,
        QWebEngineUrlRequestInterceptor, QWebEngineScript,
    )
    HAVE_WEBENGINE = True
except ImportError:
    QWebEngineView = None
    HAVE_WEBENGINE = False

try:
    from pynput import keyboard as _pk
    from pynput.keyboard import Controller as _KbController, Key as _KbKey
    import pyperclip
    HAVE_HOTKEY = True
except Exception:
    HAVE_HOTKEY = False


HOTKEY = "<ctrl>+<alt>+l"

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
WEBDATA_DIR = os.path.join(DATA_DIR, "webdata")  # persistent cookies/logins/cache
EASYLIST_URL = "https://easylist.to/easylist/easylist.txt"
EASYLIST_CACHE = os.path.join(DATA_DIR, "easylist_domains.txt")
CACHE_MAX_AGE = 7 * 24 * 3600  # refresh weekly

# Offline fallback if EasyList can't be fetched: the most common ad/tracker hosts.
BUILTIN_AD_HOSTS = frozenset({
    "doubleclick.net", "googlesyndication.com", "googleadservices.com",
    "googletagservices.com", "googletagmanager.com", "google-analytics.com",
    "adservice.google.com", "2mdn.net", "amazon-adsystem.com", "adnxs.com",
    "criteo.com", "criteo.net", "taboola.com", "outbrain.com", "pubmatic.com",
    "rubiconproject.com", "openx.net", "adsafeprotected.com", "moatads.com",
    "scorecardresearch.com", "quantserve.com", "adform.net", "casalemedia.com",
    "smartadserver.com", "yieldmo.com", "teads.tv", "bidswitch.net",
    "serving-sys.com", "media.net", "bat.bing.com", "adroll.com",
    "sharethrough.com", "gumgum.com", "3lift.com", "indexww.com",
})

# Never block these — the resource sites and their APIs. EasyList is aggressive
# and includes broad entries (e.g. "workers.dev", which is where Superterm's own
# search API lives), so an allowlist keeps core functionality working. Ads live
# on separate domains, so they're still blocked.
ALLOW_HOSTS = frozenset({
    "superterm.io", "michaelbeijer-co-uk.workers.dev",
    "europa.eu", "linguee.com", "proz.com", "reverso.net",
    "juremy.com", "babelnet.org", "wikipedia.org", "wiktionary.org",
    "google.com", "gstatic.com",
})

# Cosmetic filtering: collapse leftover ad containers (so blocked ads don't
# leave blank gaps). Conservative selectors to avoid hiding real content.
HIDE_CSS = (
    "ins.adsbygoogle,[id^='google_ads'],[id^='div-gpt-ad'],[id*='_ad_'],"
    "[class*='ad-slot'],[class*='adslot'],[class*='advert'],[data-ad],"
    "[aria-label='Advertisement'],iframe[src*='doubleclick'],"
    "iframe[src*='googlesyndication'],iframe[src*='/ads/']"
    "{display:none !important;}"
)


# ── Languages (value = ISO 639-1) ───────────────────────────────────────────
LANGUAGES = [
    ("Dutch", "nl"), ("English", "en"), ("German", "de"),
    ("French", "fr"), ("Spanish", "es"), ("Italian", "it"),
]
ISO3_BIBLIO = {"en": "eng", "nl": "dut", "de": "ger", "fr": "fre", "es": "spa", "it": "ita"}
ISO639_3 = {"en": "eng", "nl": "nld", "de": "deu", "fr": "fra", "es": "spa", "it": "ita"}
FULL_LOWER = {"en": "english", "nl": "dutch", "de": "german", "fr": "french", "es": "spanish", "it": "italian"}


def lang_code(code, fmt):
    c = (code or "").lower()
    if fmt == "iso2":
        return c
    if fmt == "iso3":
        return ISO3_BIBLIO.get(c, "eng")
    if fmt == "iso639_3":
        return ISO639_3.get(c, "eng")
    if fmt == "full_lower":
        return FULL_LOWER.get(c, "english")
    return c


RESOURCES = [
    {"id": "superterm", "icon": "📚", "name": "Superterm",
     "url": "https://superterm.io/?q={query}&from={sl}&to={tl}", "fmt": "iso2"},
    {"id": "iate", "icon": "🇪🇺", "name": "IATE",
     "url": "https://iate.europa.eu/search/byUrl?term={query}&sl={sl}&tl={tl}", "fmt": "iso2"},
    {"id": "linguee", "icon": "📗", "name": "Linguee",
     "url": "https://www.linguee.com/{sl_full}-{tl_full}/search?source=auto&query={query}", "fmt": "full_lower"},
    {"id": "proz", "icon": "💬", "name": "ProZ.com",
     "url": "https://www.proz.com/search/?term={query}&from={sl}&to={tl}&results_per_page=25&es=1", "fmt": "iso3"},
    {"id": "reverso", "icon": "🔄", "name": "Reverso",
     "url": "https://context.reverso.net/translation/{sl_full}-{tl_full}/{query}", "fmt": "full_lower"},
    {"id": "juremy", "icon": "⚖️", "name": "Juremy",
     "url": "https://juremy.com/search?src={sl}&dst={tl}&q={query}&opts=ia&tool=iws", "fmt": "iso639_3"},
    {"id": "babelnet", "icon": "🌐", "name": "BabelNet",
     "url": "https://babelnet.org/search?word={query}&lang={sl_upper}&transLang={tl_upper}", "fmt": "iso2"},
    {"id": "wikipedia", "icon": "📖", "name": "Wikipedia",
     "url": "https://{sl}.wikipedia.org/w/index.php?search={query}", "fmt": "iso2"},
    {"id": "wiktionary", "icon": "📓", "name": "Wiktionary",
     "url": "https://{sl}.wiktionary.org/wiki/{query}", "fmt": "iso2"},
    {"id": "google", "icon": "🔍", "name": "Google",
     "url": "https://www.google.com/search?q={query}", "fmt": None},
]


def build_url(res, query, frm, to):
    q = quote(query)
    fmt = res.get("fmt")
    sl = lang_code(frm, fmt) if fmt else ""
    tl = lang_code(to, fmt) if fmt else ""
    sl_full = lang_code(frm, "full_lower")
    tl_full = lang_code(to, "full_lower")
    sl_upper = lang_code(frm, "iso2").upper()
    tl_upper = lang_code(to, "iso2").upper()

    if res["id"] == "linguee":
        if "english" in (sl_full, tl_full):
            if sl_full != "english":
                sl_full, tl_full = tl_full, sl_full
        elif sl_full > tl_full:
            sl_full, tl_full = tl_full, sl_full

    url = res["url"]
    for token, value in (
        ("{query}", q), ("{sl}", sl), ("{tl}", tl),
        ("{sl_full}", sl_full), ("{tl_full}", tl_full),
        ("{sl_upper}", sl_upper), ("{tl_upper}", tl_upper),
    ):
        url = url.replace(token, value)
    return url


# ── Ad blocking ─────────────────────────────────────────────────────────────
class AdBlock:
    """Domain blocklist. Starts with the built-in list, then (in the
    background) upgrades to the full EasyList domain rules, cached locally."""

    _RULE = re.compile(r"^\|\|([a-z0-9.\-]+)\^")

    def __init__(self):
        self.domains = set(BUILTIN_AD_HOSTS)
        self.allow = set(ALLOW_HOSTS)

    def blocked(self, host):
        if not host:
            return False
        labels = host.split(".")
        # Each suffix of >= 2 labels (proper domain match, no false positives).
        suffixes = [".".join(labels[i:]) for i in range(len(labels) - 1)]
        if any(s in self.allow for s in suffixes):
            return False  # allowlist wins
        return any(s in self.domains for s in suffixes)

    def load_async(self):
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        try:
            fresh = (os.path.exists(EASYLIST_CACHE)
                     and time.time() - os.path.getmtime(EASYLIST_CACHE) < CACHE_MAX_AGE)
            if fresh:
                with open(EASYLIST_CACHE, encoding="utf-8") as fh:
                    domains = {d.strip() for d in fh if d.strip()}
            else:
                req = Request(EASYLIST_URL, headers={"User-Agent": "superlookup-mockup"})
                raw = urlopen(req, timeout=20).read().decode("utf-8", "replace")
                domains = self._parse(raw)
                try:
                    with open(EASYLIST_CACHE, "w", encoding="utf-8") as fh:
                        fh.write("\n".join(sorted(domains)))
                except OSError:
                    pass
            if domains:
                self.domains = domains | BUILTIN_AD_HOSTS
                print(f"[adblock] {len(self.domains)} domains active"
                      f" ({'cache' if fresh else 'EasyList'})")
        except Exception as e:
            print(f"[adblock] EasyList unavailable, using built-in list ({e})")

    @classmethod
    def _parse(cls, raw):
        domains = set()
        for line in raw.splitlines():
            line = line.strip()
            if not line or line[0] in "!@[":
                continue
            m = cls._RULE.match(line)
            if m and line[m.end():] == "":
                # Only option-free "||domain^" rules — these are dedicated
                # ad/tracker domains. Rules WITH $-options (e.g. $third-party on
                # amazonaws.com / workers.dev) are scoped; stripping the options
                # would turn them into blanket blocks of legit cloud/CDN roots.
                domains.add(m.group(1))
        return domains


ADBLOCK = AdBlock()

if HAVE_WEBENGINE:
    class AdBlocker(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            if ADBLOCK.blocked(info.requestUrl().host()):
                info.block(True)


# ── Global hotkey + clipboard capture ───────────────────────────────────────
if HAVE_HOTKEY:
    class Hotkey(QObject):
        """Ctrl+Alt+L: copy the current selection (in whatever app is focused),
        then bring SuperLookup to the front and search it."""
        _fired = pyqtSignal()        # from the pynput listener thread
        _captured = pyqtSignal(str)  # from the capture worker thread

        def __init__(self, window):
            super().__init__()
            self.window = window
            self._kbd = _KbController()
            self._fired.connect(self._on_fired)
            self._captured.connect(self._on_captured)
            self._listener = _pk.GlobalHotKeys({HOTKEY: self._fired.emit})
            self._listener.daemon = True
            self._listener.start()

        def _on_fired(self):
            threading.Thread(target=self._capture, daemon=True).start()

        def _capture(self):
            text = ""
            try:
                time.sleep(0.25)  # let the hotkey keys release
                self._kbd.press(_KbKey.ctrl); self._kbd.press("c")
                self._kbd.release("c"); self._kbd.release(_KbKey.ctrl)
                time.sleep(0.2)   # let the clipboard update
                text = pyperclip.paste() or ""
            except Exception:
                pass
            self._captured.emit(text)

        def _on_captured(self, text):
            w = self.window
            w.showNormal(); w.raise_(); w.activateWindow()
            lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
            if lines:
                w.query.setText(lines[0])
                w.search()
            else:
                w.query.setFocus()


class SuperLookup(QMainWindow):
    def __init__(self, profile=None):
        super().__init__()
        self.profile = profile
        self.setWindowTitle("SuperLookup — standalone mockup")
        self.resize(1150, 820)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 8)

        bar = QHBoxLayout()
        self.from_cb = QComboBox()
        self.to_cb = QComboBox()
        for name, code in LANGUAGES:
            self.from_cb.addItem(name, code)
            self.to_cb.addItem(name, code)
        self.from_cb.setCurrentText("Dutch")
        self.to_cb.setCurrentText("English")

        swap = QPushButton("⇄")
        swap.setFixedWidth(36)
        swap.clicked.connect(self.swap)

        self.query = QLineEdit()
        self.query.setPlaceholderText("Search a term, then press Enter…")
        self.query.returnPressed.connect(self.search)

        go = QPushButton("Search")
        go.clicked.connect(self.search)

        bar.addWidget(QLabel("From"))
        bar.addWidget(self.from_cb)
        bar.addWidget(swap)
        bar.addWidget(QLabel("To"))
        bar.addWidget(self.to_cb)
        bar.addSpacing(8)
        bar.addWidget(self.query, 1)
        bar.addWidget(go)
        layout.addLayout(bar)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.currentChanged.connect(self.on_tab_changed)
        layout.addWidget(self.tabs, 1)

        hotkey_note = ("  Select text anywhere and press Ctrl+Alt+L to search it."
                       if HAVE_HOTKEY else "")
        note = QLabel(
            "Each resource opens in its own embedded browser tab (loaded when you "
            "first click it), with ads and trackers blocked. The full Supervertaler "
            "version also searches your local termbases and translation memories."
            + hotkey_note
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666; padding: 4px 2px;")
        layout.addWidget(note)

        self._pending = {}

        if not HAVE_WEBENGINE:
            self.query.setEnabled(False)
            go.setEnabled(False)
            msg = QLabel(
                "PyQt6-WebEngine isn't installed, so the embedded browser is "
                "unavailable.\n\nInstall it with:\n\n    pip install PyQt6-WebEngine\n\n"
                "then run this again."
            )
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            msg.setStyleSheet("font-size: 13px; color: #444;")
            self.tabs.addTab(msg, "Setup needed")
        else:
            self.query.setFocus()

        # System tray: closing the window hides it instead of quitting, so the
        # global hotkey stays alive. The app quits only from the tray menu.
        self.tray = None
        self._informed_tray = False
        if QSystemTrayIcon.isSystemTrayAvailable():
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView)
            self.setWindowIcon(icon)
            self.tray = QSystemTrayIcon(icon, self)
            self.tray.setToolTip("SuperLookup — press Ctrl+Alt+L")
            menu = QMenu()
            act_show = QAction("Show SuperLookup", self)
            act_show.triggered.connect(self.show_window)
            act_quit = QAction("Quit", self)
            act_quit.triggered.connect(QApplication.quit)
            menu.addAction(act_show)
            menu.addSeparator()
            menu.addAction(act_quit)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(self._tray_activated)
            self.tray.show()

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_window()

    def closeEvent(self, event):
        # With a tray icon, hide to tray instead of quitting (keeps the hotkey live).
        if self.tray is not None:
            event.ignore()
            self.hide()
            if not self._informed_tray:
                self._informed_tray = True
                self.tray.showMessage(
                    "SuperLookup is still running",
                    "Press Ctrl+Alt+L any time, or click the tray icon. "
                    "Quit from the tray menu.",
                    QSystemTrayIcon.MessageIcon.Information, 4000,
                )
        else:
            event.accept()

    def swap(self):
        i, j = self.from_cb.currentIndex(), self.to_cb.currentIndex()
        self.from_cb.setCurrentIndex(j)
        self.to_cb.setCurrentIndex(i)
        if self.query.text().strip():
            self.search()

    def clear_tabs(self):
        while self.tabs.count():
            w = self.tabs.widget(0)
            self.tabs.removeTab(0)
            w.deleteLater()
        self._pending.clear()

    def search(self):
        query = self.query.text().strip()
        if not query or not HAVE_WEBENGINE:
            return
        frm = self.from_cb.currentData()
        to = self.to_cb.currentData()

        self.clear_tabs()
        for res in RESOURCES:
            view = QWebEngineView()
            if self.profile is not None:
                view.setPage(QWebEnginePage(self.profile, view))
            idx = self.tabs.addTab(view, f"{res['icon']}  {res['name']}")
            self._pending[idx] = build_url(res, query, frm, to)

        if self.tabs.count():
            self.tabs.setCurrentIndex(0)
            self.load_tab(0)

    def on_tab_changed(self, idx):
        self.load_tab(idx)

    def load_tab(self, idx):
        url = self._pending.pop(idx, None)
        if url is None:
            return
        widget = self.tabs.widget(idx)
        if isinstance(widget, QWebEngineView):
            widget.load(QUrl(url))


def main():
    app = QApplication(sys.argv)

    profile = None
    if HAVE_WEBENGINE:
        global _AD_INTERCEPTOR, _ADHIDE_SCRIPT, _PROFILE
        os.makedirs(WEBDATA_DIR, exist_ok=True)
        # A *named* profile with an on-disk path persists cookies, logins and
        # localStorage across launches (the default profile is in-memory).
        profile = QWebEngineProfile("superlookup", app)
        profile.setPersistentStoragePath(os.path.join(WEBDATA_DIR, "storage"))
        profile.setCachePath(os.path.join(WEBDATA_DIR, "cache"))
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        _PROFILE = profile

        _AD_INTERCEPTOR = AdBlocker()
        profile.setUrlRequestInterceptor(_AD_INTERCEPTOR)

        _ADHIDE_SCRIPT = QWebEngineScript()
        _ADHIDE_SCRIPT.setName("superlookup-adhide")
        _ADHIDE_SCRIPT.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        _ADHIDE_SCRIPT.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        _ADHIDE_SCRIPT.setRunsOnSubFrames(True)
        _ADHIDE_SCRIPT.setSourceCode(
            "(function(){try{var s=document.createElement('style');"
            "s.textContent=%s;(document.head||document.documentElement)"
            ".appendChild(s);}catch(e){}})();" % json.dumps(HIDE_CSS)
        )
        profile.scripts().insert(_ADHIDE_SCRIPT)

        ADBLOCK.load_async()

    win = SuperLookup(profile)
    win.show()
    if win.tray is not None:
        # Closing the window keeps the app (and hotkey) alive in the tray.
        app.setQuitOnLastWindowClosed(False)
    if HAVE_HOTKEY:
        win._hotkey = Hotkey(win)  # keep a reference alive

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
