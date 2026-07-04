#!/usr/bin/env python3
"""SuperLookup — standalone desktop app.

Type a term (or select one anywhere and press the global hotkey), pick a
language pair, and every reference resource opens in its own *embedded* browser
tab — Beijerterm, IATE, Linguee, ProZ, Reverso, Juremy, BabelNet, Wikipedia,
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
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from urllib.parse import quote
from urllib.request import Request, urlopen

from PyQt6.QtCore import Qt, QUrl, QObject, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence, QDesktopServices
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest
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


VERSION = "0.1.7"
WEBSITE = "https://superlookup.io"
REPO = "https://github.com/michaelbeijer/superlookup-desktop"

HOTKEY = "<ctrl>+<alt>+l"          # pynput fallback
DEFAULT_HOTKEY_QT = "Ctrl+Alt+L"   # human/Qt form stored in config


# ── Auto-update ─────────────────────────────────────────────────────────────
# Checks GitHub Releases for a newer tag, downloads the per-OS asset, and hands
# off to a tiny detached helper that waits for this process to exit, swaps the
# new build over the old, and relaunches. Only runs in a packaged (frozen) build;
# from source it just points you at `git pull`. Safe because settings live in the
# per-user data folder, so replacing the app never touches them.
GITHUB_LATEST_API = "https://api.github.com/repos/michaelbeijer/superlookup-desktop/releases/latest"
UPDATE_ASSET = {"win32": "SuperLookup-windows.zip",
                "darwin": "SuperLookup-macos.zip"}.get(sys.platform, "SuperLookup-linux.zip")


def _version_tuple(s):
    s = (s or "").lstrip("vV").split("-")[0].split("+")[0]
    out = []
    for part in s.split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out) or (0,)


def fetch_latest_release():
    """Return {'version','tag','asset_url','notes_url'} if GitHub's latest
    release is newer than VERSION, else None. Network-bound — call off the UI
    thread."""
    try:
        req = Request(GITHUB_LATEST_API, headers={
            "User-Agent": "SuperLookup", "Accept": "application/vnd.github+json"})
        data = json.loads(urlopen(req, timeout=15).read().decode("utf-8"))
    except Exception:
        return None
    tag = data.get("tag_name") or ""
    if _version_tuple(tag) <= _version_tuple(VERSION):
        return None
    asset_url = next((a.get("browser_download_url") for a in data.get("assets", [])
                      if a.get("name") == UPDATE_ASSET), None)
    return {"version": tag.lstrip("vV"), "tag": tag, "asset_url": asset_url,
            "notes_url": data.get("html_url") or (REPO + "/releases/latest")}


def _install_root():
    """(kind, path) for the current packaged app. 'exe'/'bin' → path is the
    executable to replace; 'app' → path is the macOS .app bundle to replace."""
    exe = os.path.abspath(sys.executable)
    if sys.platform == "darwin" and ".app/Contents/MacOS/" in exe:
        return "app", exe.split(".app/Contents/MacOS/")[0] + ".app"
    return ("exe" if sys.platform == "win32" else "bin"), exe


def apply_update(zip_path):
    """Extract the release zip and launch a detached helper that waits for this
    process to exit, swaps the new build over the old, and relaunches. Raises on
    any problem BEFORE the app quits (so a bad download leaves the current
    version intact). Returns True once the helper is launched — caller then quits."""
    kind, target = _install_root()
    staging = tempfile.mkdtemp(prefix="superlookup-update-")
    with zipfile.ZipFile(zip_path) as zf:      # raises BadZipFile on a corrupt download
        zf.extractall(staging)
    pid = os.getpid()

    if kind == "exe":
        new = os.path.join(staging, "SuperLookup.exe")
        if not os.path.exists(new):
            raise FileNotFoundError("SuperLookup.exe missing from the downloaded package")
        bat = os.path.join(staging, "_swap.bat")
        with open(bat, "w", encoding="ascii") as fh:
            fh.write("@echo off\r\n:loop\r\n"
                     f'tasklist /fi "pid eq {pid}" | find "{pid}" >nul && (timeout /t 1 /nobreak >nul & goto loop)\r\n'
                     f'copy /y "{new}" "{target}" >nul\r\n'
                     f'start "" "{target}"\r\ndel "%~f0"\r\n')
        subprocess.Popen(["cmd", "/c", bat], creationflags=0x00000008 | 0x08000000)
        return True

    if kind == "app":
        new = os.path.join(staging, "SuperLookup.app")
        if not os.path.isdir(new):
            raise FileNotFoundError("SuperLookup.app missing from the downloaded package")
        sh = os.path.join(staging, "_swap.sh")
        with open(sh, "w") as fh:
            fh.write("#!/bin/sh\n"
                     f'while kill -0 {pid} 2>/dev/null; do sleep 0.5; done\n'
                     f'/usr/bin/ditto "{new}" "{target}.new" && rm -rf "{target}" && mv "{target}.new" "{target}"\n'
                     f'open "{target}"\nrm -- "$0"\n')
        os.chmod(sh, 0o755)
        subprocess.Popen(["/bin/sh", sh], start_new_session=True)
        return True

    new = os.path.join(staging, "SuperLookup")   # linux binary
    if not os.path.exists(new):
        raise FileNotFoundError("SuperLookup binary missing from the downloaded package")
    sh = os.path.join(staging, "_swap.sh")
    with open(sh, "w") as fh:
        fh.write("#!/bin/sh\n"
                 f'while kill -0 {pid} 2>/dev/null; do sleep 0.5; done\n'
                 f'cp "{new}" "{target}" && chmod +x "{target}"\n'
                 f'"{target}" &\nrm -- "$0"\n')
    os.chmod(sh, 0o755)
    subprocess.Popen(["/bin/sh", sh], start_new_session=True)
    return True


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

# ── User data location ──────────────────────────────────────────────────────
# Settings, cookies/logins and caches live in a per-user folder (NOT next to the
# executable), matching Supervertaler's convention:
#   Windows: %APPDATA%\SuperLookup   macOS: ~/Library/Application Support/SuperLookup
#   Linux:   ~/.config/SuperLookup
# Anchoring here (rather than __file__) is what makes persistence work: a
# PyInstaller --onefile build otherwise writes into its temp _MEIxxxx dir (wiped
# every run), and a macOS .app can't be written into without breaking its code
# signature. It also lets a future auto-updater overwrite the app without
# touching settings. Bundled read-only assets use resource_path() instead.
def user_data_dir(app="SuperLookup"):
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser(r"~\AppData\Roaming")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    d = os.path.join(base, app)
    os.makedirs(d, exist_ok=True)
    return d


def resource_path(rel):
    """Absolute path to a READ-ONLY bundled asset — works both in dev and in a
    PyInstaller build, where bundled files live under sys._MEIPASS."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def _migrate_legacy_data(new_dir):
    """One-time carry-over of pre-0.1.6 data that older *source* runs wrote next
    to the script, so existing searches, zoom, hotkey and logins survive the
    move to the per-user folder. (Packaged builds never persisted, so there's
    nothing to migrate there.)"""
    old_dir = os.path.dirname(os.path.abspath(__file__))
    if os.path.abspath(old_dir) == os.path.abspath(new_dir):
        return
    for name in ("config.json", "easylist_domains.txt", "webdata"):
        src, dst = os.path.join(old_dir, name), os.path.join(new_dir, name)
        if os.path.exists(src) and not os.path.exists(dst):
            try:
                shutil.copytree(src, dst) if os.path.isdir(src) else shutil.copy2(src, dst)
            except OSError:
                pass


DATA_DIR = user_data_dir()
_migrate_legacy_data(DATA_DIR)
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
# and includes broad entries (e.g. "workers.dev", which is where Beijerterm's own
# search API lives), so an allowlist keeps core functionality working. Ads live
# on separate domains, so they're still blocked.
ALLOW_HOSTS = frozenset({
    "beijerterm.com", "superterm.io", "michaelbeijer-co-uk.workers.dev",
    "europa.eu", "linguee.com", "proz.com", "reverso.net",
    "juremy.com", "babelnet.org", "wikipedia.org", "wiktionary.org",
    "google.com", "gstatic.com",
    # Bot-protection challenge infrastructure — blocking any of it makes the
    # "verify you are human" check (e.g. ProZ) loop forever.
    "cloudflare.com", "hcaptcha.com", "recaptcha.net",
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
    {"id": "superterm", "icon": "📚", "name": "Beijerterm",
     "url": "https://beijerterm.com/?q={query}&from={sl}&to={tl}", "fmt": "iso2"},
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
     "url": "https://{sl}.wikipedia.org/w/index.php?search={query}&title=Special:Search&fulltext=1", "fmt": "iso2", "wiki": "wikipedia"},
    {"id": "wiktionary", "icon": "📓", "name": "Wiktionary",
     "url": "https://{sl}.wiktionary.org/w/index.php?search={query}&title=Special:Search&fulltext=1", "fmt": "iso2", "wiki": "wiktionary"},
    {"id": "wikidata", "icon": "🔗", "name": "Wikidata",
     "url": "https://www.wikidata.org/w/index.php?search={query}&title=Special:Search&fulltext=1", "fmt": None, "wiki": "wikidata"},
    {"id": "acronymfinder", "icon": "🔤", "name": "AcronymFinder",
     "url": "https://www.acronymfinder.com/~/search/af.aspx?string=exact&Acronym={query}", "fmt": None},
    {"id": "opus", "icon": "🗂️", "name": "OPUS Corpus",
     "url": "https://opus.nlpl.eu/bin/opuscqp.pl?corpus=DGT;lang={sl};cqp={query};align={tl}", "fmt": "iso2"},
    {"id": "google", "icon": "🔍", "name": "Google",
     "url": "https://www.google.com/search?q={query}", "fmt": None},
    {"id": "google_patents", "icon": "📜", "name": "Google Patents",
     "url": "https://patents.google.com/?q=\"{query}\"", "fmt": None},
    {"id": "github_code", "icon": "💻", "name": "GitHub Code",
     "url": "https://github.com/search?q={query}&type=code", "fmt": None},
    # ── Bilingual dictionaries ──────────────────────────────────────────────
    {"id": "glosbe", "icon": "📘", "name": "Glosbe",
     "url": "https://glosbe.com/{sl}/{tl}/{query}", "fmt": "iso2"},
    {"id": "babla", "icon": "🗣️", "name": "bab.la",
     "url": "https://en.bab.la/dictionary/{sl_full}-{tl_full}/{query}", "fmt": None},
    {"id": "wordreference", "icon": "📕", "name": "WordReference",
     "url": "https://www.wordreference.com/{sl}{tl}/{query}", "fmt": "iso2"},
    {"id": "keybot", "icon": "🔑", "name": "Keybot",
     "url": "https://www.keybot.com/{sl_full}-{tl_full}/{query}.htm", "fmt": None},
    {"id": "sensagent", "icon": "📐", "name": "Sensagent",
     "url": "https://dictionary.sensagent.com/{query}/{sl}-{tl}/", "fmt": "iso2"},
    {"id": "bing_translator", "icon": "🌉", "name": "Bing Translator",
     "url": "https://www.bing.com/translator/?from={sl}&to={tl}&text={query}", "fmt": "iso2"},
    {"id": "twolingual", "icon": "🔀", "name": "2lingual",
     "url": "https://www.2lingual.com/2lingual-google/google-search?q={query}&lr1=lang_{sl}&lr2=lang_{tl}", "fmt": "iso2"},
    # ── Dutch ───────────────────────────────────────────────────────────────
    {"id": "woordenlijst", "icon": "🇳🇱", "name": "Woordenlijst (Taalunie)",
     "url": "https://woordenlijst.org/#/?q={query}", "fmt": None},
    {"id": "synoniemen", "icon": "🔁", "name": "Synoniemen.net",
     "url": "https://synoniemen.net/index.php?zoekterm={query}", "fmt": None},
    {"id": "dfbonline", "icon": "💶", "name": "Financiële Begrippenlijst",
     "url": "https://www.dfbonline.nl/begrippen/{query}", "fmt": None},
    # ── EU / terminology ────────────────────────────────────────────────────
    {"id": "eurlex", "icon": "📜", "name": "EUR-Lex",
     "url": "https://eur-lex.europa.eu/search.html?text={query}&scope=EURLEX&type=quick", "fmt": None},
    {"id": "eurotermbank", "icon": "🏛️", "name": "EuroTermBank",
     "url": "https://www.eurotermbank.com/search/{query}", "fmt": None},
    {"id": "gemet", "icon": "🌍", "name": "GEMET Thesaurus",
     "url": "https://www.eionet.europa.eu/gemet/en/search/?query={query}", "fmt": None},
    # ── English monolingual / writing ───────────────────────────────────────
    {"id": "collins", "icon": "📙", "name": "Collins",
     "url": "https://www.collinsdictionary.com/dictionary/english/{query}", "fmt": None},
    {"id": "thefreedictionary", "icon": "📔", "name": "TheFreeDictionary",
     "url": "https://www.thefreedictionary.com/{query}", "fmt": None},
    {"id": "merriam_thesaurus", "icon": "📗", "name": "Merriam-Webster Thesaurus",
     "url": "https://www.merriam-webster.com/thesaurus/{query}", "fmt": None},
    {"id": "thesaurus_com", "icon": "🗂️", "name": "Thesaurus.com",
     "url": "https://www.thesaurus.com/browse/{query}", "fmt": None},
    {"id": "freecollocation", "icon": "🔗", "name": "Oxford Collocations",
     "url": "http://www.freecollocation.com/search?word={query}", "fmt": None},
    {"id": "skell", "icon": "📊", "name": "SkELL Concordance",
     "url": "https://skell.sketchengine.eu/#result?lang=en&query={query}", "fmt": None},
    {"id": "etymonline", "icon": "🏺", "name": "Etymonline",
     "url": "https://www.etymonline.com/search?q={query}", "fmt": None},
    {"id": "wordnik", "icon": "🔤", "name": "Wordnik",
     "url": "https://www.wordnik.com/words/{query}", "fmt": None},
    {"id": "visuwords", "icon": "🕸️", "name": "Visuwords",
     "url": "https://visuwords.com/{query}", "fmt": None},
    {"id": "howmanysyllables", "icon": "🎵", "name": "Syllable Dictionary",
     "url": "https://www.howmanysyllables.com/words/{query}", "fmt": None},
    # ── Medical / technical ─────────────────────────────────────────────────
    {"id": "ema", "icon": "💊", "name": "EMA Medicines",
     "url": "https://www.ema.europa.eu/en/medicines?search_api_views_fulltext={query}", "fmt": None},
    {"id": "emc", "icon": "💉", "name": "EMC Medicines",
     "url": "https://www.medicines.org.uk/emc/search?q={query}", "fmt": None},
    {"id": "chemindustry", "icon": "🧪", "name": "ChemIndustry",
     "url": "http://www.chemindustry.com/apps/chemicals?m=s&t={query}", "fmt": None},
    # ── Media ───────────────────────────────────────────────────────────────
    {"id": "imdb", "icon": "🎬", "name": "IMDb",
     "url": "https://www.imdb.com/find?q={query}&s=tt", "fmt": None},
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
                req = Request(EASYLIST_URL, headers={"User-Agent": "superlookup-desktop"})
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

    class Page(QWebEnginePage):
        """Open would-be-new-window links (target=_blank / window.open) in the
        same view, so clicking e.g. a ProZ result navigates in place — and via
        a real navigation, so it gets a history entry (Back works)."""
        def __init__(self, profile, view):
            super().__init__(profile, view)
            self._view = view

        def createWindow(self, _type):
            # Catch the popup on a throwaway page, then load its target into
            # the main view. This adds a proper history entry (unlike returning
            # self), so right-click → Back returns to the results page.
            catcher = QWebEnginePage(self.profile(), self._view)

            def _redirect(url, _p=catcher):
                if url.isValid() and not url.isEmpty():
                    self._view.setUrl(url)
                _p.deleteLater()

            catcher.urlChanged.connect(_redirect)
            return catcher

    class WebView(QWebEngineView):
        """Right-click adds 'Open in browser' for the current page (and any
        link under the cursor), so you can pop out to your real browser.

        Also detects a Cloudflare "verify you are human" wall (which the
        embedded engine can't pass) and bounces that page to the real browser
        instead of leaving you stuck in the challenge loop."""

        _CF_JS = (
            "(function(){try{"
            "var t=document.title||'';"
            "if(/^(just a moment|attention required|access denied|please wait)/i.test(t))return true;"
            "if(document.querySelector('iframe[src*=\"challenges.cloudflare.com\"],"
            "#challenge-form,#challenge-running,#cf-challenge-running,.cf-turnstile,#turnstile-wrapper'))return true;"
            "var b=(document.body&&document.body.innerText)||'';"
            "return /performing security verification|checking your browser|"
            "verify you are human|review the security of your connection|"
            "uses a security service to protect|enable javascript and cookies to continue/i.test(b);"
            "}catch(e){return false;}})()"
        )

        def __init__(self, *args):
            super().__init__(*args)
            self._cf_seen = set()
            self._first_done = False
            self.loadFinished.connect(self._maybe_escape_cf)

        def _maybe_escape_cf(self, ok):
            # Leave the tab's FIRST page (the search-results page) alone — it's
            # the reason the tab exists and usually loads fine. Only bounce the
            # pages the user clicks *into* from there, which is where sites like
            # ProZ throw a Cloudflare wall the embedded engine can't pass.
            if not self._first_done:
                self._first_done = True
                return
            # Cloudflare challenge pages come back as HTTP 403, so `ok` is False
            # even though the wall rendered fine — don't bail on that. The JS
            # probe is harmless on genuine error/blank pages (returns false).
            url = self.url()
            us = url.toString()
            if not us or us in self._cf_seen:
                return
            self.page().runJavaScript(
                self._CF_JS,
                lambda hit, u=QUrl(url), us=us: self._on_cf(hit, u, us))

        def _on_cf(self, hit, url, us):
            if not hit:
                return
            self._cf_seen.add(us)
            QDesktopServices.openUrl(url)
            host = url.host()
            self.setHtml(
                "<body style='font-family:Segoe UI,Arial;padding:48px;color:#333;'>"
                "<h2 style='margin:0 0 .4em;'>Opened in your browser ↗</h2>"
                f"<p style='font-size:15px;color:#555;max-width:34em;'><b>{host}</b> "
                "uses a human-verification check that blocks this embedded view, "
                "so the page was opened in your default browser (where you're "
                "signed in and it passes instantly).</p></body>", url)

        def contextMenuEvent(self, event):
            menu = self.createStandardContextMenu()
            menu.addSeparator()
            req = (self.lastContextMenuRequest()
                   if hasattr(self, "lastContextMenuRequest") else None)
            link = req.linkUrl() if req is not None else None
            if link is not None and not link.isEmpty():
                a = menu.addAction("Open link in browser")
                a.triggered.connect(lambda _=False, u=QUrl(link): QDesktopServices.openUrl(u))
            page_url = self.url()
            a2 = menu.addAction("Open this page in browser")
            a2.triggered.connect(lambda _=False, u=QUrl(page_url): QDesktopServices.openUrl(u))
            menu.exec(event.globalPos())

    def make_view(profile, zoom=1.0):
        view = WebView()
        if profile is not None:
            view.setPage(Page(profile, view))
        view.setZoomFactor(zoom)
        return view


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
DEFAULTS_REV = 6  # bump when the built-in search set or their URLs change

# Built-in searches that were shipped and later pulled. Dropped from any saved
# set on load, so a retired search doesn't linger as if it were a custom one.
RETIRED_IDS = set()


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


def merge_resources(saved, saved_rev):
    """The user's saved resources, kept in sync with the built-ins.

    On a defaults-revision bump, each built-in search has its definition
    (url/name/icon/fmt) refreshed from the current defaults — while keeping the
    user's enabled state and ordering. Custom searches are left untouched, and
    any newly-shipped built-ins are appended.
    """
    if not isinstance(saved, list) or not saved:
        return default_resources()
    defaults = {d["id"]: d for d in default_resources()}
    refresh = saved_rev != DEFAULTS_REV
    out, seen = [], set()
    for r in saved:
        rid = r.get("id")
        if rid in RETIRED_IDS:
            continue
        seen.add(rid)
        if refresh and rid in defaults:
            d = dict(defaults[rid])
            d["enabled"] = r.get("enabled", True)  # keep the user's on/off choice
            out.append(d)
        else:
            out.append(r)
    for d in default_resources():
        if d["id"] not in seen:
            out.append(d)
    return out


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

    def __init__(self, resources, hotkey_qt, zoom=1.0, parent=None):
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

        z_row = QHBoxLayout()
        z_row.addWidget(QLabel("Default page zoom:"))
        self.zoom_cb = QComboBox()
        for pct in (50, 67, 75, 80, 90, 100, 110, 125, 150):
            self.zoom_cb.addItem(f"{pct}%", pct / 100.0)
        cur_i = min(range(self.zoom_cb.count()),
                    key=lambda i: abs(self.zoom_cb.itemData(i) - zoom))
        self.zoom_cb.setCurrentIndex(cur_i)
        z_row.addWidget(self.zoom_cb)
        z_row.addStretch(1)
        v.addLayout(z_row)
        z_hint = QLabel(
            "Applied to every search tab. Handy for sites like Beijerterm that "
            "show more when zoomed out. (Ctrl +/– still zooms a single tab.)")
        z_hint.setStyleSheet("color:#666; font-size:11px;")
        z_hint.setWordWrap(True)
        v.addWidget(z_hint)

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

    def zoom_value(self):
        return self.zoom_cb.currentData()

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


class MediaWikiTab(QWidget):
    """Wikipedia / Wiktionary / Wikidata tab: a native list of matching pages
    (MediaWiki OpenSearch) above an embedded view that loads whichever page you
    pick — like the web version's inline suggestions."""

    def __init__(self, resource, profile, parent=None):
        super().__init__(parent)
        self.resource = resource
        self._nam = QNetworkAccessManager(self)
        self._count = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # A thin bar that collapses/expands the suggestion list.
        self.toggle = QPushButton()
        self.toggle.setObjectName("wikitoggle")
        self.toggle.setFlat(True)
        self.toggle.clicked.connect(self._toggle_list)
        lay.addWidget(self.toggle)

        self.list = QListWidget()
        self.list.setObjectName("wikilist")
        self.list.setMaximumHeight(210)
        self.list.itemClicked.connect(self._open)
        lay.addWidget(self.list)

        self.view = make_view(profile)
        lay.addWidget(self.view, 1)
        self._update_toggle()

    def search(self, query, from_lang, to_lang):
        query = (query or "").strip()
        if not query:
            return
        wiki = self.resource.get("wiki")
        host = ("https://www.wikidata.org" if wiki == "wikidata"
                else f"https://{from_lang}.{wiki}.org")
        api = (f"{host}/w/api.php?action=opensearch&limit=15&namespace=0"
               f"&format=json&search={quote(query)}")
        self._count = 0
        self.list.clear()
        self.list.addItem("Searching…")
        self.list.setVisible(True)
        self._update_toggle()
        req = QNetworkRequest(QUrl(api))
        # Wikimedia's API blocks requests without a descriptive User-Agent.
        req.setRawHeader(b"User-Agent", b"SuperLookup-desktop (+https://superlookup.io)")
        reply = self._nam.get(req)
        reply.finished.connect(lambda: self._on_reply(reply))

    def _on_reply(self, reply):
        try:
            raw = bytes(reply.readAll()).decode("utf-8", "replace")
            _, titles, descs, urls = json.loads(raw)
        except Exception:
            titles, urls, descs = [], [], []
        finally:
            reply.deleteLater()

        self.list.clear()
        self._count = len(titles)
        if not titles:
            self.list.addItem("No matching pages.")
            self._update_toggle()
            return
        for i, title in enumerate(titles):
            it = QListWidgetItem(title)
            if i < len(descs) and descs[i]:
                it.setToolTip(descs[i])
            if i < len(urls):
                it.setData(Qt.ItemDataRole.UserRole, urls[i])
            self.list.addItem(it)
        # Load the top hit so the view isn't blank; the list switches pages.
        if urls:
            self.view.load(QUrl(urls[0]))
            self.list.setCurrentRow(0)
        self._update_toggle()

    def _open(self, item):
        url = item.data(Qt.ItemDataRole.UserRole)
        if url:
            self.view.load(QUrl(url))
            self.list.setVisible(False)  # collapse to give the page full height
            self._update_toggle()

    def _toggle_list(self):
        self.list.setVisible(not self.list.isVisible())
        self._update_toggle()

    def _update_toggle(self):
        shown = self.list.isVisible()
        n = self._count
        label = f"{n} matching page{'' if n == 1 else 's'}" if n else "matching pages"
        self.toggle.setText(("▴  " if shown else "▾  ") + label)


class SuperLookup(QMainWindow):
    # Auto-update signals (emitted from background threads, handled on the UI thread).
    _update_found = pyqtSignal(dict)     # a newer release exists ({} extra key "_manual" if user-triggered)
    _update_none = pyqtSignal()          # manual check found nothing newer
    _dl_progress = pyqtSignal(int)       # download percentage
    _dl_done = pyqtSignal(str, str)      # (zip_path, error) — both "" means cancelled

    def __init__(self, profile=None):
        super().__init__()
        self.profile = profile
        self._update_info = None
        self._config = load_config()
        self.resources = merge_resources(
            self._config.get("resources"), self._config.get("defaults_rev"))
        self._hotkey = None
        self.hotkey_qt = self._config.get("hotkey") or DEFAULT_HOTKEY_QT
        try:
            self.zoom = float(self._config.get("zoom") or 1.0)
        except (TypeError, ValueError):
            self.zoom = 1.0
        self.setWindowTitle("SuperLookup")
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
        swap.setObjectName("iconbtn")
        swap.setFixedWidth(34)
        swap.clicked.connect(self.swap)

        self.query = QLineEdit()
        self.query.setPlaceholderText("Search a term, then press Enter…")
        self.query.setMinimumWidth(240)
        self.query.setMaximumWidth(520)  # roomy but not the whole window wide
        self.query.returnPressed.connect(self.search)

        go = QPushButton("Search")
        go.setObjectName("go")
        go.clicked.connect(self.search)

        settings_btn = QPushButton("⚙")
        settings_btn.setObjectName("iconbtn")
        settings_btn.setFixedWidth(34)
        settings_btn.setToolTip("Settings — enable/disable and manage searches")
        settings_btn.clicked.connect(self.open_settings)

        self.update_btn = QPushButton("⬆ Update")
        self.update_btn.setObjectName("updatebtn")
        self.update_btn.setToolTip("A new version is available — click to install")
        self.update_btn.clicked.connect(lambda: self._show_update_dialog(self._update_info))
        self.update_btn.hide()

        help_btn = QPushButton("?")
        help_btn.setObjectName("iconbtn")
        help_btn.setFixedWidth(34)
        help_btn.setToolTip("Help & about")
        self._help_menu = self._build_help_menu()
        help_btn.clicked.connect(lambda: self._help_menu.exec(
            help_btn.mapToGlobal(help_btn.rect().bottomLeft())))

        bar.addWidget(QLabel("From"))
        bar.addWidget(self.from_cb)
        bar.addWidget(swap)
        bar.addWidget(QLabel("To"))
        bar.addWidget(self.to_cb)
        bar.addSpacing(8)
        bar.addWidget(self.query)
        bar.addWidget(go)
        bar.addStretch(1)
        bar.addWidget(self.update_btn)
        bar.addWidget(help_btn)
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
        self._last = ("", "nl", "en")  # last (query, from, to) for lazy tab loads

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

        # Auto-update: wire signals and kick off a silent background check.
        self._update_found.connect(self._on_update_found)
        self._update_none.connect(lambda: QMessageBox.information(
            self, "SuperLookup", f"You’re on the latest version ({VERSION})."))
        self._dl_progress.connect(lambda pct: self._dl_dialog and self._dl_dialog.setValue(pct))
        self._dl_done.connect(self._on_dl_done)
        self._dl_dialog = None
        self._check_for_updates(manual=False)

    # ── Auto-update ─────────────────────────────────────────────────────────
    def _check_for_updates(self, manual=False):
        def work():
            info = fetch_latest_release()
            if info:
                if manual:
                    info = dict(info, _manual=True)
                self._update_found.emit(info)
            elif manual:
                self._update_none.emit()
        threading.Thread(target=work, daemon=True).start()

    def _on_update_found(self, info):
        self._update_info = info
        self.update_btn.setText(f"⬆ Update to {info['version']}")
        self.update_btn.show()
        if info.get("_manual"):
            self._show_update_dialog(info)

    def _show_update_dialog(self, info):
        if not info:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Update available")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(f"<b>SuperLookup {info['version']}</b> is available "
                    f"(you have {VERSION}).")
        whatsnew = box.addButton("What’s new", QMessageBox.ButtonRole.ActionRole)
        if not getattr(sys, "frozen", False):
            box.setInformativeText("You’re running from source — update with "
                                   "<code>git pull</code>.")
            box.addButton("OK", QMessageBox.ButtonRole.RejectRole)
        elif not info.get("asset_url"):
            box.setInformativeText("This release has no installer for your OS yet — "
                                   "open the releases page to grab it manually.")
            box.addButton("Open releases", QMessageBox.ButtonRole.AcceptRole).setProperty("act", "open")
            box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        else:
            box.setInformativeText("Install it now? SuperLookup will download the "
                                   "update, replace itself, and reopen. Your settings "
                                   "and logins are kept.")
            box.addButton("Install now", QMessageBox.ButtonRole.AcceptRole).setProperty("act", "install")
            box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is whatsnew:
            QDesktopServices.openUrl(QUrl(info["notes_url"]))
        elif clicked and clicked.property("act") == "open":
            QDesktopServices.openUrl(QUrl(info["notes_url"]))
        elif clicked and clicked.property("act") == "install":
            self._do_install(info)

    def _do_install(self, info):
        from PyQt6.QtWidgets import QProgressDialog
        self._dl_dialog = QProgressDialog("Downloading update…", "Cancel", 0, 100, self)
        self._dl_dialog.setWindowTitle("Updating SuperLookup")
        self._dl_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self._dl_dialog.setMinimumDuration(0)
        self._dl_cancel = False
        self._dl_dialog.canceled.connect(lambda: setattr(self, "_dl_cancel", True))
        dest = os.path.join(tempfile.gettempdir(), UPDATE_ASSET)

        def work():
            try:
                req = Request(info["asset_url"], headers={"User-Agent": "SuperLookup"})
                with urlopen(req, timeout=30) as resp, open(dest, "wb") as fh:
                    total = int(resp.headers.get("Content-Length") or 0)
                    got = 0
                    while not self._dl_cancel:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        fh.write(chunk)
                        got += len(chunk)
                        if total:
                            self._dl_progress.emit(int(got * 100 / total))
                self._dl_done.emit("" if self._dl_cancel else dest, "")
            except Exception as e:
                self._dl_done.emit("", str(e))
        threading.Thread(target=work, daemon=True).start()

    def _on_dl_done(self, zip_path, err):
        if self._dl_dialog:
            self._dl_dialog.close()
            self._dl_dialog = None
        if err:
            QMessageBox.warning(self, "Update failed",
                                f"Couldn’t download the update:\n{err}")
            return
        if not zip_path:   # cancelled
            return
        try:
            launched = apply_update(zip_path)
        except Exception as e:
            QMessageBox.warning(self, "Update failed",
                                f"Downloaded, but couldn’t apply the update:\n{e}\n\n"
                                "Your current version is untouched.")
            return
        if launched:
            app = QApplication.instance()
            app.setQuitOnLastWindowClosed(True)
            app.quit()

    def _build_help_menu(self):
        m = QMenu(self)

        def link(label, url):
            m.addAction(label).triggered.connect(
                lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))

        link("Help", WEBSITE + "/help")
        link("SuperLookup on the web", WEBSITE)
        link("Downloads && updates", REPO + "/releases/latest")
        link("Report an issue", REPO + "/issues")
        m.addSeparator()
        m.addAction("Check for updates…").triggered.connect(
            lambda _=False: self._check_for_updates(manual=True))
        m.addAction("About SuperLookup").triggered.connect(self.show_about)
        return m

    def show_about(self):
        QMessageBox.about(
            self, "About SuperLookup",
            f"<h3>SuperLookup {VERSION}</h3>"
            "<p>One search box for a translator’s reference web — each site opens "
            "in its own embedded, ad-free tab, summoned by a global hotkey.</p>"
            f"<p>By <a href='https://beijer.uk'>Michael Beijer</a>. A companion to "
            "<a href='https://supervertaler.com'>Supervertaler</a>.</p>"
            f"<p><a href='{WEBSITE}'>superlookup.io</a> &nbsp;·&nbsp; "
            f"<a href='{REPO}'>GitHub</a></p>")

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
        self._last = (query, frm, to)
        for res in self.resources:
            if not res.get("enabled", True):
                continue
            if res.get("wiki"):
                tab = MediaWikiTab(res, self.profile)
                tab.view.setZoomFactor(self.zoom)
                tab.view.iconChanged.connect(
                    lambda ic, w=tab: self._set_tab_icon(w, ic))
                idx = self.tabs.addTab(tab, f"{res['icon']}  {res['name']}")
                self._pending[idx] = ("wiki", None)
            else:
                view = make_view(self.profile, self.zoom)
                view.iconChanged.connect(
                    lambda ic, w=view: self._set_tab_icon(w, ic))
                idx = self.tabs.addTab(view, f"{res['icon']}  {res['name']}")
                self._pending[idx] = ("url", build_url(res, query, frm, to))

        if self.tabs.count():
            self.tabs.setCurrentIndex(0)
            self.load_tab(0)

    def _save(self):
        save_config({
            "resources": self.resources,
            "from": self.from_cb.currentData(),
            "to": self.to_cb.currentData(),
            "hotkey": self.hotkey_qt,
            "zoom": self.zoom,
            "defaults_rev": DEFAULTS_REV,
        })

    def open_settings(self):
        dlg = SettingsDialog(self.resources, self.hotkey_qt, self.zoom, self)
        if dlg.exec():
            self.resources = dlg.result_resources()
            self.hotkey_qt = dlg.hotkey_value() or self.hotkey_qt
            self.zoom = dlg.zoom_value()
            self._save()
            self._refresh_hotkey_ui()
            if self._hotkey is not None:
                self._hotkey.rebind(qt_to_pynput(self.hotkey_qt) or HOTKEY)
            if self.query.text().strip():
                self.search()  # re-open tabs to reflect enable/disable changes

    def _set_tab_icon(self, widget, icon):
        """Show a loaded page's real favicon on its tab. The emoji is only a
        pre-load placeholder in the tab text, so drop it once the real favicon
        arrives — otherwise the tab shows both the favicon and the emoji."""
        if icon is None or icon.isNull():
            return
        i = self.tabs.indexOf(widget)
        if i >= 0:
            self.tabs.setTabIcon(i, icon)
            text = self.tabs.tabText(i)
            if "  " in text:  # strip the "{emoji}  " placeholder prefix
                self.tabs.setTabText(i, text.split("  ", 1)[1])

    def on_tab_changed(self, idx):
        self.load_tab(idx)

    def load_tab(self, idx):
        pending = self._pending.pop(idx, None)
        if pending is None:
            return
        kind, payload = pending
        w = self.tabs.widget(idx)
        if kind == "url" and isinstance(w, QWebEngineView):
            w.load(QUrl(payload))
        elif kind == "wiki" and isinstance(w, MediaWikiTab):
            q, frm, to = self._last
            w.search(q, frm, to)


STYLE = """
* { font-family: "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif; }
QMainWindow, QDialog, QWidget { background: #ffffff; color: #111111; font-size: 10pt; }
QLabel { color: #333333; }

QLineEdit {
    border: 1px solid #d5d9e0; border-radius: 8px; padding: 6px 10px;
    background: #ffffff; selection-background-color: #3366cc; selection-color: #ffffff;
}
QLineEdit:focus { border: 1px solid #3366cc; }

QComboBox {
    border: 1px solid #d5d9e0; border-radius: 8px; padding: 5px 8px;
    background: #ffffff; min-height: 20px;
}
QComboBox:hover { border-color: #b8c0cc; }
QComboBox QAbstractItemView {
    border: 1px solid #d5d9e0; selection-background-color: #eaf0fb;
    selection-color: #111111; outline: 0;
}

QPushButton {
    border: 1px solid #d5d9e0; border-radius: 8px; padding: 6px 12px;
    background: #f5f6f8; color: #111111;
}
QPushButton:hover { background: #eceef2; border-color: #c7ccd6; }
QPushButton:pressed { background: #e2e5ea; }
QPushButton#go {
    background: #3366cc; border: 1px solid #3366cc; color: #ffffff; font-weight: 600;
}
QPushButton#go:hover { background: #2f5cbb; border-color: #2f5cbb; }
QPushButton#go:pressed { background: #274ea3; }

QPushButton#iconbtn { padding: 4px 0; font-size: 14px; }
QPushButton#updatebtn {
    background: #1a7f5a; border: 1px solid #1a7f5a; color: #ffffff;
    font-weight: 600; padding: 6px 12px; border-radius: 8px;
}
QPushButton#updatebtn:hover { background: #16704f; border-color: #16704f; }

QTabWidget::pane { border: none; border-top: 1px solid #cfd5de; background: #ffffff; }
QTabBar { qproperty-drawBase: 0; }
QTabBar::tab {
    background: #e9edf2;
    color: #5a5a5a;
    padding: 7px 15px;
    margin-right: 3px;
    border: 1px solid #cfd5de;
    border-bottom: none;
    border-top-left-radius: 9px;
    border-top-right-radius: 9px;
    min-width: 56px;
}
QTabBar::tab:hover { background: #dfe4ec; color: #111111; }
QTabBar::tab:selected {
    background: #ffffff;
    color: #1a1a1a;
    margin-bottom: -1px;   /* overlap the pane border → tab connects to the page */
}

QListWidget {
    border: 1px solid #e1e1e1; border-radius: 8px; padding: 4px; background: #ffffff;
}
QListWidget::item { padding: 5px 6px; border-radius: 6px; }
QListWidget::item:selected { background: #eaf0fb; color: #111111; }
QListWidget#wikilist { border: none; border-bottom: 1px solid #e1e1e1; border-radius: 0; }
QPushButton#wikitoggle {
    text-align: left; padding: 5px 10px; border: none; border-radius: 0;
    border-bottom: 1px solid #e1e1e1; background: #f6f8fc; color: #5a5a5a;
    font-size: 12px;
}
QPushButton#wikitoggle:hover { background: #eef2f9; color: #111111; }

QScrollBar:vertical { border: none; background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #cfd4dc; border-radius: 5px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #b8bec8; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")       # consistent base across Win/macOS/Linux
    app.setStyleSheet(STYLE)

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
        # Present a vanilla Chrome user-agent (drop the "QtWebEngine/x.y.z" token),
        # so sites like Google flag the embedded browser as a bot less often — the
        # main cause of the repeated captchas.
        ua = re.sub(r"QtWebEngine/\S+\s*", "", profile.httpUserAgent()).strip()
        profile.setHttpUserAgent(ua)
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
