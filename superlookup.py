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
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QComboBox, QPushButton, QTabWidget, QLabel,
    QSystemTrayIcon, QMenu, QStyle,
    QDialog, QListWidget, QListWidgetItem, QDialogButtonBox, QFormLayout,
    QFileDialog, QMessageBox, QKeySequenceEdit,
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


HOTKEY = "<ctrl>+<alt>+l"          # pynput fallback
DEFAULT_HOTKEY_QT = "Ctrl+Alt+L"   # human/Qt form stored in config


def qt_to_pynput(seq):
    """Convert a Qt key-sequence string ("Ctrl+Alt+L") to pynput's GlobalHotKeys
    format ("<ctrl>+<alt>+l"). Returns None if there's no modifier + key."""
    if not seq:
        return None
    mods = {"ctrl": "<ctrl>", "alt": "<alt>", "shift": "<shift>", "meta": "<cmd>"}
    out, key = [], None
    for part in seq.split("+"):
        p = part.strip()
        if p.lower() in mods:
            out.append(mods[p.lower()])
        elif p:
            key = p
    if not key or not out:  # a global hotkey needs at least one modifier + a key
        return None
    if len(key) == 1:
        out.append(key.lower())
    elif re.fullmatch(r"[Ff]\d{1,2}", key):
        out.append(f"<{key.lower()}>")
    else:
        out.append(f"<{key.lower().replace(' ', '_')}>")
    return "+".join(out)

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


DEFAULT_RESOURCES = [
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

        def __init__(self, window, combo):
            super().__init__()
            self.window = window
            self._kbd = _KbController()
            self._listener = None
            self._fired.connect(self._on_fired)
            self._captured.connect(self._on_captured)
            self.rebind(combo)

        def rebind(self, combo):
            if self._listener is not None:
                try:
                    self._listener.stop()
                except Exception:
                    pass
                self._listener = None
            try:
                self._listener = _pk.GlobalHotKeys({combo: self._fired.emit})
                self._listener.daemon = True
                self._listener.start()
            except Exception as e:
                print(f"[hotkey] could not register {combo!r}: {e}")

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
            w.show_window()
            lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
            if lines:
                w.query.setText(lines[0])
                w.search()
            else:
                w.query.setFocus()


# ── User config: which searches are enabled, plus custom ones ───────────────
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")


def _slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "search"


def default_resources():
    return [dict(r, enabled=True) for r in DEFAULT_RESOURCES]


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def merge_resources(saved):
    """The user's saved resources, with any newly-shipped defaults appended."""
    if isinstance(saved, list) and saved:
        seen = {r.get("id") for r in saved}
        for d in default_resources():
            if d["id"] not in seen:
                saved.append(d)
        return saved
    return default_resources()


def save_config(data):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"[config] could not save: {e}")


class ResourceEditDialog(QDialog):
    """Add or edit a single search (name, icon, URL template, language codes)."""

    FORMATS = [
        ("Auto / none (no language codes)", None),
        ("ISO 639-1 — nl, en", "iso2"),
        ("ISO 639-2/B — dut, eng (ProZ)", "iso3"),
        ("ISO 639-3 — nld, eng (Juremy)", "iso639_3"),
        ("Full name — dutch, english (Linguee)", "full_lower"),
    ]

    def __init__(self, resource=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit search" if resource else "Add search")
        self.resize(540, 240)
        self._orig = resource or {}
        form = QFormLayout(self)
        self.name = QLineEdit(self._orig.get("name", ""))
        self.icon = QLineEdit(self._orig.get("icon", "🔎"))
        self.icon.setMaxLength(4)
        self.url = QLineEdit(self._orig.get("url", ""))
        self.url.setPlaceholderText("https://example.com/search?q={query}&sl={sl}&tl={tl}")
        self.fmt = QComboBox()
        for label, val in self.FORMATS:
            self.fmt.addItem(label, val)
        cur = self._orig.get("fmt")
        self.fmt.setCurrentIndex(next((i for i, (l, v) in enumerate(self.FORMATS) if v == cur), 0))
        form.addRow("Name", self.name)
        form.addRow("Icon", self.icon)
        form.addRow("URL", self.url)
        form.addRow("Language codes", self.fmt)
        tip = QLabel("Placeholders: {query}, {sl}, {tl}, {sl_full}, {tl_full}, {sl_upper}, {tl_upper}")
        tip.setStyleSheet("color:#666; font-size:11px;")
        tip.setWordWrap(True)
        form.addRow(tip)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _ok(self):
        if not self.name.text().strip() or not self.url.text().strip():
            QMessageBox.warning(self, "Missing", "Name and URL are both required.")
            return
        self.accept()

    def resource(self):
        name = self.name.text().strip()
        return {
            "id": self._orig.get("id") or _slug(name),
            "icon": self.icon.text().strip() or "🔎",
            "name": name,
            "url": self.url.text().strip(),
            "fmt": self.fmt.currentData(),
            "enabled": self._orig.get("enabled", True),
        }


class SettingsDialog(QDialog):
    """Enable/disable, reorder, add/edit/remove, and import/export searches."""

    def __init__(self, resources, hotkey_qt, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SuperLookup — Settings")
        self.resize(640, 560)
        self.resources = [dict(r) for r in resources]

        v = QVBoxLayout(self)

        hk_row = QHBoxLayout()
        hk_row.addWidget(QLabel("Global hotkey:"))
        self.hk_edit = QKeySequenceEdit(QKeySequence(hotkey_qt))
        if hasattr(self.hk_edit, "setMaximumSequenceLength"):
            self.hk_edit.setMaximumSequenceLength(1)
        hk_row.addWidget(self.hk_edit, 1)
        v.addLayout(hk_row)
        hk_hint = QLabel(
            "Click the field and press your shortcut. Select text anywhere, then "
            "press it to search — needs a modifier (Ctrl / Alt / Shift / ⌘).")
        hk_hint.setStyleSheet("color:#666; font-size:11px;")
        hk_hint.setWordWrap(True)
        v.addWidget(hk_hint)

        v.addWidget(QLabel(
            "Tick the searches you want as tabs. Add your own, reorder with ↑ / ↓, "
            "and Import/Export to share them."))
        self.list = QListWidget()
        v.addWidget(self.list, 1)
        self._reload()

        row = QHBoxLayout()
        for label, slot in (("Add…", self.add), ("Edit…", self.edit), ("Remove", self.remove)):
            b = QPushButton(label)
            b.clicked.connect(slot)
            row.addWidget(b)
        up = QPushButton("↑"); up.setFixedWidth(32); up.clicked.connect(lambda: self.move(-1))
        dn = QPushButton("↓"); dn.setFixedWidth(32); dn.clicked.connect(lambda: self.move(1))
        row.addWidget(up)
        row.addWidget(dn)
        defaults_btn = QPushButton("Restore defaults")
        defaults_btn.clicked.connect(self.restore_defaults)
        row.addWidget(defaults_btn)
        row.addStretch()
        for label, slot in (("Import…", self.do_import), ("Export…", self.do_export)):
            b = QPushButton(label)
            b.clicked.connect(slot)
            row.addWidget(b)
        v.addLayout(row)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._accept_dialog)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _accept_dialog(self):
        seq = self.hk_edit.keySequence().toString()
        if seq and not any(m in seq for m in ("Ctrl", "Alt", "Shift", "Meta")):
            QMessageBox.warning(
                self, "Add a modifier",
                "A global shortcut needs at least one modifier (Ctrl, Alt, Shift, or ⌘) "
                "so it doesn't fire on every keypress.")
            return
        self.accept()

    def hotkey_value(self):
        return self.hk_edit.keySequence().toString()

    def _reload(self):
        self.list.clear()
        for r in self.resources:
            it = QListWidgetItem(f"{r.get('icon', '')}  {r['name']}")
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if r.get("enabled", True) else Qt.CheckState.Unchecked)
            it.setData(Qt.ItemDataRole.UserRole, r)
            self.list.addItem(it)

    def _sync(self):
        """Read the list widget (order + check states) back into self.resources."""
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            r = dict(it.data(Qt.ItemDataRole.UserRole))
            r["enabled"] = it.checkState() == Qt.CheckState.Checked
            out.append(r)
        self.resources = out

    def result_resources(self):
        self._sync()
        return self.resources

    def add(self):
        self._sync()
        dlg = ResourceEditDialog(parent=self)
        if dlg.exec():
            self.resources.append(dlg.resource())
            self._reload()
            self.list.setCurrentRow(self.list.count() - 1)

    def edit(self):
        row = self.list.currentRow()
        if row < 0:
            return
        self._sync()
        dlg = ResourceEditDialog(self.resources[row], parent=self)
        if dlg.exec():
            self.resources[row] = dlg.resource()
            self._reload()
            self.list.setCurrentRow(row)

    def remove(self):
        row = self.list.currentRow()
        if row < 0:
            return
        self._sync()
        del self.resources[row]
        self._reload()
        self.list.setCurrentRow(min(row, self.list.count() - 1))

    def move(self, delta):
        row = self.list.currentRow()
        new = row + delta
        if row < 0 or not (0 <= new < self.list.count()):
            return
        self._sync()
        self.resources[row], self.resources[new] = self.resources[new], self.resources[row]
        self._reload()
        self.list.setCurrentRow(new)

    def restore_defaults(self):
        if QMessageBox.question(
            self, "Restore defaults",
            "Replace your list with the built-in searches? Your custom searches "
            "and on/off choices will be lost.",
        ) == QMessageBox.StandardButton.Yes:
            self.resources = default_resources()
            self._reload()

    def do_export(self):
        self._sync()
        path, _ = QFileDialog.getSaveFileName(
            self, "Export searches", "superlookup-searches.json", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"resources": self.resources}, fh, ensure_ascii=False, indent=2)
        except OSError as e:
            QMessageBox.warning(self, "Export failed", str(e))

    def do_import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import searches", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            incoming = data.get("resources") if isinstance(data, dict) else data
            if not isinstance(incoming, list):
                raise ValueError("no list of searches found in the file")
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Import failed", str(e))
            return
        self._sync()
        index = {r.get("id"): i for i, r in enumerate(self.resources)}
        added = updated = 0
        for raw in incoming:
            r = self._clean(raw)
            if not r:
                continue
            if r["id"] in index:
                self.resources[index[r["id"]]] = r
                updated += 1
            else:
                index[r["id"]] = len(self.resources)
                self.resources.append(r)
                added += 1
        self._reload()
        QMessageBox.information(self, "Import complete", f"Added {added}, updated {updated}.")

    @staticmethod
    def _clean(raw):
        if not isinstance(raw, dict):
            return None
        name = str(raw.get("name", "")).strip()
        url = str(raw.get("url", "")).strip()
        if not name or not url:
            return None
        fmt = raw.get("fmt")
        return {
            "id": raw.get("id") or _slug(name),
            "icon": (str(raw.get("icon", "🔎")).strip() or "🔎")[:4],
            "name": name,
            "url": url,
            "fmt": fmt if fmt in (None, "iso2", "iso3", "iso639_3", "full_lower") else None,
            "enabled": bool(raw.get("enabled", True)),
        }


class SuperLookup(QMainWindow):
    def __init__(self, profile=None):
        super().__init__()
        self.profile = profile
        self._config = load_config()
        self.resources = merge_resources(self._config.get("resources"))
        self._hotkey = None
        self.hotkey_qt = self._config.get("hotkey") or DEFAULT_HOTKEY_QT
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
        # Restore the last-used language pair, if any.
        for combo, key in ((self.from_cb, "from"), (self.to_cb, "to")):
            saved = self._config.get(key)
            if saved:
                i = combo.findData(saved)
                if i >= 0:
                    combo.setCurrentIndex(i)

        swap = QPushButton("⇄")
        swap.setFixedWidth(36)
        swap.clicked.connect(self.swap)

        self.query = QLineEdit()
        self.query.setPlaceholderText("Search a term, then press Enter…")
        self.query.returnPressed.connect(self.search)

        go = QPushButton("Search")
        go.clicked.connect(self.search)

        settings_btn = QPushButton("⚙")
        settings_btn.setFixedWidth(36)
        settings_btn.setToolTip("Settings — enable/disable and manage searches")
        settings_btn.clicked.connect(self.open_settings)

        bar.addWidget(QLabel("From"))
        bar.addWidget(self.from_cb)
        bar.addWidget(swap)
        bar.addWidget(QLabel("To"))
        bar.addWidget(self.to_cb)
        bar.addSpacing(8)
        bar.addWidget(self.query, 1)
        bar.addWidget(go)
        bar.addWidget(settings_btn)
        layout.addLayout(bar)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.currentChanged.connect(self.on_tab_changed)
        layout.addWidget(self.tabs, 1)

        self._note = QLabel()
        self._note.setWordWrap(True)
        self._note.setStyleSheet("color: #666; padding: 4px 2px;")
        layout.addWidget(self._note)

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
        self._saved_geometry = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView)
            self.setWindowIcon(icon)
            self.tray = QSystemTrayIcon(icon, self)
            self.tray.setToolTip(f"SuperLookup — press {self.hotkey_qt}")
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

        self._refresh_hotkey_ui()

    def _refresh_hotkey_ui(self):
        hk = self.hotkey_qt
        extra = (f"  Select text anywhere and press {hk} to search it."
                 if HAVE_HOTKEY else "")
        self._note.setText(
            "Each resource opens in its own embedded browser tab (loaded when you "
            "first click it), with ads and trackers blocked. The full Supervertaler "
            "version also searches your local termbases and translation memories." + extra)
        if getattr(self, "tray", None) is not None:
            self.tray.setToolTip(f"SuperLookup — press {hk}" if HAVE_HOTKEY else "SuperLookup")

    def show_window(self):
        # Restore the exact geometry we had when hidden (saveGeometry captures
        # the maximized/fullscreen state too), instead of forcing "normal".
        if self._saved_geometry is not None:
            self.restoreGeometry(self._saved_geometry)
        self.show()
        self.raise_()
        self.activateWindow()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_window()

    def closeEvent(self, event):
        # With a tray icon, hide to tray instead of quitting (keeps the hotkey live).
        if self.tray is not None:
            event.ignore()
            self._saved_geometry = self.saveGeometry()  # remember size/position/state
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
        self._save()  # remember the language pair for next launch

        self.clear_tabs()
        for res in self.resources:
            if not res.get("enabled", True):
                continue
            view = QWebEngineView()
            if self.profile is not None:
                view.setPage(QWebEnginePage(self.profile, view))
            idx = self.tabs.addTab(view, f"{res['icon']}  {res['name']}")
            self._pending[idx] = build_url(res, query, frm, to)

        if self.tabs.count():
            self.tabs.setCurrentIndex(0)
            self.load_tab(0)

    def _save(self):
        save_config({
            "resources": self.resources,
            "from": self.from_cb.currentData(),
            "to": self.to_cb.currentData(),
            "hotkey": self.hotkey_qt,
        })

    def open_settings(self):
        dlg = SettingsDialog(self.resources, self.hotkey_qt, self)
        if dlg.exec():
            self.resources = dlg.result_resources()
            self.hotkey_qt = dlg.hotkey_value() or self.hotkey_qt
            self._save()
            self._refresh_hotkey_ui()
            if self._hotkey is not None:
                self._hotkey.rebind(qt_to_pynput(self.hotkey_qt) or HOTKEY)
            if self.query.text().strip():
                self.search()  # re-open tabs to reflect enable/disable changes

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
        win._hotkey = Hotkey(win, qt_to_pynput(win.hotkey_qt) or HOTKEY)  # keep a ref alive

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
