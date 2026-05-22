"""
tui — DuneOS dbt full-screen TUI.

Two-panel btop/ranger layout:
  - left: OptionList nav with border_title  (thin solid border = ┌─ ACTIONS ─┐)
  - right: RichLog output stream             (thin solid border = ┌─ OUTPUT  ─┐)

All long-running ops run in @work threads and stream output line-by-line.
Errors surface as modal overlays.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Footer, Input, Label, OptionList, RichLog, Select, Static,
)
from textual.widgets.option_list import Option
from rich.text import Text

from .constants import DUNEOS_ROOT
from .setup import _BOARD_FILE, _PORT_FILE, _list_boards, _list_ports, find_idf_root
from .manifest import find_apps
from .flashimg import _stage, _create_image, _find_esptool, _get_sysbin_offset, _get_board_name
from .bspgen import list_boards as bspgen_list_boards, generate_for_board


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _board() -> str:
    return _BOARD_FILE.read_text().strip() if _BOARD_FILE.exists() else ""

def _port() -> str:
    return _PORT_FILE.read_text().strip() if _PORT_FILE.exists() else ""

_SD_FILE = DUNEOS_ROOT / ".duneos_sd"

def _sd_path() -> str:
    return _SD_FILE.read_text().strip() if _SD_FILE.exists() else ""

def _idf_export() -> Path | None:
    root = find_idf_root()
    if not root:
        return None
    name = "export.bat" if platform.system() == "Windows" else "export.sh"
    p = root / name
    return p if p.exists() else None

def _opt(key: str, label: str, action_id: str) -> Option:
    t = Text()
    t.append(f" [{key}]", style="bold #58a6ff")
    t.append(f" {label}", style="#c9d1d9")
    return Option(t, id=action_id)


# ---------------------------------------------------------------------------
# CSS — btop palette + solid borders with embedded titles
# ---------------------------------------------------------------------------

CSS = """
Screen {
    background: #0d1117;
    color: #c9d1d9;
}

/* status bar */
#status {
    height: 1;
    background: #161b22;
    color: #8b949e;
    padding: 0 1;
}

/* two-column body fills the rest */
#body {
    height: 1fr;
}

/* ── left nav panel ── */
#nav {
    width: 26;
    border: solid #30363d;
    border-title-color: #58a6ff;
    border-title-style: bold;
    padding: 0;
}

OptionList {
    background: transparent;
    border: none;
    height: 1fr;
    padding: 0;
    scrollbar-size: 0 0;
}

OptionList:focus {
    border: none;
}

OptionList > .option-list--option {
    padding: 0 0;
    height: 1;
    color: #c9d1d9;
}

OptionList > .option-list--option-highlighted {
    background: #1f6feb;
    color: #ffffff;
    text-style: bold;
}

OptionList:focus > .option-list--option-highlighted {
    background: #1f6feb;
    color: #ffffff;
}

OptionList > .option-list--separator {
    color: #30363d;
    height: 1;
    padding: 0;
}

/* ── right output panel ── */
#output {
    width: 1fr;
    border: solid #30363d;
    border-title-color: #8b949e;
    padding: 0;
}

#log {
    height: 1fr;
    background: transparent;
    padding: 0 1;
    border: none;
}

/* ── app-select screen ── */
AppSelectScreen {
    background: #0d1117;
}

#apppanel {
    height: 1fr;
    border: solid #30363d;
    border-title-color: #58a6ff;
    border-title-style: bold;
    margin: 1 4;
    padding: 0;
}

#applist {
    height: 1fr;
    border: none;
    background: transparent;
    padding: 0;
}

#applist > .option-list--option {
    padding: 0 1;
    height: 1;
    color: #c9d1d9;
}

#applist > .option-list--option-highlighted {
    background: #1f6feb;
    color: #ffffff;
    text-style: bold;
}

#apphint {
    height: 1;
    background: #161b22;
    color: #6e7681;
    padding: 0 2;
    dock: bottom;
}

/* ── profile editor: two columns (flash / SD) side-by-side ── */
#profileheader {
    height: auto;
    background: #161b22;
    color: #58a6ff;
    border: tall #1f6feb;
    border-title-color: #58a6ff;
    border-title-style: bold;
    padding: 0 2;
    margin: 1 4 0 4;
}

#profile2col {
    height: 1fr;
    margin: 0 4 0 4;
}

.profilecolumn {
    width: 1fr;
    border: solid #30363d;
    border-title-color: #58a6ff;
    border-title-style: bold;
    padding: 0;
    margin: 1 1 0 1;
}

.profilecolumn:focus-within {
    border: solid #1f6feb;
}

.profilelist {
    height: 1fr;
    border: none;
    background: transparent;
    padding: 0;
}

.profilelist > .option-list--option {
    padding: 0 1;
    height: 1;
    color: #c9d1d9;
}

.profilelist > .option-list--option-highlighted {
    background: #1f6feb;
    color: #ffffff;
    text-style: bold;
}

/* ── modal shared box ── */
#errbox {
    width: 64;
    height: auto;
    border: solid #f85149;
    background: #161b22;
    border-title-color: #f85149;
    border-title-style: bold;
    padding: 1 2;
}

#setupbox {
    width: 68;
    height: auto;
    border: solid #1f6feb;
    background: #161b22;
    border-title-color: #58a6ff;
    border-title-style: bold;
    padding: 1 2;
}

#setupbox Label {
    margin-top: 1;
    color: #8b949e;
}

ErrorModal {
    align: center middle;
}

#splashbox {
    width: 48;
    height: auto;
    border: solid #1f6feb;
    background: #0d1117;
    border-title-color: #58a6ff;
    border-title-style: bold;
    padding: 1 2;
    content-align: center middle;
}

#splashart {
    width: 100%;
    height: auto;
    text-align: center;
    color: #58a6ff;
}

#splashtag {
    width: 100%;
    height: 1;
    text-align: center;
    color: #d29922;
    margin-top: 1;
}

SplashScreen {
    align: center middle;
}

#previewbox {
    width: 90;
    height: 80%;
    border: solid #1f6feb;
    background: #161b22;
    border-title-color: #58a6ff;
    border-title-style: bold;
    padding: 1 2;
}

#previewscroll {
    width: 100%;
    height: 1fr;
    background: #0d1117;
}

#previewbody {
    width: auto;
    height: auto;
    color: #c9d1d9;
    background: #0d1117;
    padding: 0 1;
}

#previewhint {
    width: 100%;
    height: 1;
    color: #6e7681;
    margin-top: 1;
}

ProfilePreviewModal {
    align: center middle;
}

SetupScreen {
    align: center middle;
}

SdPathModal {
    align: center middle;
}

Footer {
    background: #161b22;
    color: #6e7681;
}
"""


# ---------------------------------------------------------------------------
# Splash screen — shown briefly at TUI startup
# ---------------------------------------------------------------------------

_SPLASH_ART = r"""
  ____                    ___  ____
 |  _ \ _   _ _ __   ___ / _ \/ ___|
 | | | | | | | '_ \ / _ \ | | \___ \
 | |_| | |_| | | | |  __/ |_| |___) |
 |____/ \__,_|_| |_|\___|\___/|____/
"""

_SPLASH_TAG = "desert ops since 2026"


class SplashScreen(ModalScreen):
    """One-shot ASCII logo shown for ~1 s at startup.

    Any keypress dismisses it early. The DbtApp pushes this from on_mount and
    a timer pops it on the configured delay so the user reaches the main menu
    even if they don't touch the keyboard.
    """

    BINDINGS = [("escape", "dismiss", "")]

    def __init__(self, hold_s: float = 1.0) -> None:
        super().__init__()
        self._hold_s = hold_s

    def compose(self) -> ComposeResult:
        with Vertical(id="splashbox") as v:
            v.border_title = "  DuneOS  "
            yield Static(f"[#58a6ff]{_SPLASH_ART}[/#58a6ff]", id="splashart")
            yield Static(f"[#d29922]{_SPLASH_TAG}[/#d29922]", id="splashtag")

    def _close(self) -> None:
        # Use App.pop_screen (sync) instead of Screen.dismiss (async). dismiss()
        # can't be awaited from the screen's own message handler / timer, which
        # raises ScreenError. pop_screen is safe from both.
        if self.is_attached:
            self.app.pop_screen()

    def on_mount(self) -> None:
        self.set_timer(self._hold_s, self._close)

    def on_key(self, _) -> None:
        self._close()


# ---------------------------------------------------------------------------
# Error modal
# ---------------------------------------------------------------------------

class ErrorModal(ModalScreen):
    BINDINGS = [("escape", "dismiss", ""), ("enter", "dismiss", "")]

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self._t = title
        self._b = body

    def compose(self) -> ComposeResult:
        with Vertical(id="errbox") as v:
            v.border_title = f"  {self._t}  "
            yield Label(f"[#c9d1d9]{self._b}[/#c9d1d9]")
            yield Label("[dim]  Enter / Esc  close[/dim]")

    def on_key(self, _) -> None:
        self.dismiss()


# ---------------------------------------------------------------------------
# Setup modal
# ---------------------------------------------------------------------------

class SetupScreen(ModalScreen):
    BINDINGS = [("escape", "dismiss", "Cancel"), ("s", "action_save", "Save")]

    def compose(self) -> ComposeResult:
        boards    = _list_boards()
        ports     = _list_ports()
        cur_board = _board()
        cur_port  = _port()
        idf       = find_idf_root()
        idf_s     = (f"[#3fb950]{idf}[/#3fb950]" if idf
                     else "[#f85149]not found — echo ~/esp/esp-idf > .duneos_idf[/#f85149]")

        with Vertical(id="setupbox") as v:
            v.border_title = "  Setup  "
            yield Label("  Board")
            yield Select(
                [(n, n) for n, _ in boards],
                value=cur_board if cur_board else Select.BLANK,
                id="sel-board",
            )
            yield Label("  Serial port")
            if ports:
                yield Select(
                    [(p, p) for p in ports],
                    value=cur_port if cur_port in ports else Select.BLANK,
                    id="sel-port",
                    allow_blank=True,
                )
            yield Input(
                value=cur_port,
                placeholder="/dev/ttyUSB0  or  COM13",
                id="inp-port",
            )
            yield Label(f"  ESP-IDF v6.0.x  {idf_s}")
            yield Label("  [dim]S: save   Esc: cancel[/dim]")

    def action_save(self) -> None:
        self._save()
        self.dismiss()

    def _save(self) -> None:
        try:
            v = self.query_one("#sel-board", Select).value
            if v and v != Select.BLANK:
                _BOARD_FILE.write_text(str(v) + "\n")
        except Exception:
            pass
        inp = self.query_one("#inp-port", Input).value.strip()
        if inp:
            _PORT_FILE.write_text(inp + "\n")
            return
        try:
            v = self.query_one("#sel-port", Select).value
            if v and v != Select.BLANK:
                _PORT_FILE.write_text(str(v) + "\n")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# SD path modal
# ---------------------------------------------------------------------------

class SdPathModal(ModalScreen):
    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    def __init__(self, app_name: str = "") -> None:
        super().__init__()
        self._app_name = app_name
        self._saved    = _sd_path()

    def compose(self) -> ComposeResult:
        lbl = f" App: {self._app_name}" if self._app_name else ""
        with Vertical(id="setupbox") as v:
            v.border_title = "  Flash SD  "
            if lbl:
                yield Label(f"[bold #c9d1d9]{lbl}[/bold #c9d1d9]")
            yield Label("  SD card mount point:")
            yield Input(
                value=self._saved,
                placeholder="/mnt/sd  or  /media/user/SD",
                id="sd-inp",
            )
            yield Label("  [dim]Enter: confirm   Esc: cancel[/dim]")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)


# ---------------------------------------------------------------------------
# Board / port quick-select screens
# ---------------------------------------------------------------------------

class BoardPickScreen(Screen):
    """OptionList of all boards — Enter saves and dismisses."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def compose(self) -> ComposeResult:
        boards = _list_boards()
        cur    = _board()
        opts   = []
        for name, desc in boards:
            t = Text()
            t.append(f" {name:<30}", style="bold #c9d1d9")
            if desc:
                t.append(desc, style="#6e7681")
            if name == cur:
                t.append("  [active]", style="#3fb950")
            opts.append(Option(t, id=name))
        idf   = find_idf_root()
        idf_s = "  IDF: [#3fb950]found[/#3fb950]" if idf else "  IDF: [#f85149]not found[/#f85149]"
        with Vertical(id="apppanel") as v:
            v.border_title = "  Select Board  "
            yield OptionList(*opts, id="applist")
        yield Static(f"  \u2191\u2193 nav   Enter select   Esc cancel{idf_s}", id="apphint")

    def on_mount(self) -> None:
        cur   = _board()
        names = [n for n, _ in _list_boards()]
        if cur in names:
            try:
                self.query_one("#applist", OptionList).highlighted = names.index(cur)
            except Exception:
                pass

    @on(OptionList.OptionSelected, "#applist")
    def _pick(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_id)


class PortInputModal(ModalScreen):
    """Text-input fallback for ports not appearing in the scan."""

    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="setupbox") as v:
            v.border_title = "  Enter Port  "
            yield Label("  Serial port:")
            yield Input(
                value=_port(),
                placeholder="/dev/ttyUSB0  or  COM13",
                id="port-inp",
            )
            yield Label("  [dim]Enter: confirm   Esc: cancel[/dim]")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)


class PortPickScreen(Screen):
    """OptionList of detected serial ports + manual-entry fallback."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def compose(self) -> ComposeResult:
        ports = _list_ports()
        cur   = _port()
        opts  = []
        for p in ports:
            t = Text()
            t.append(f" {p}", style="bold #c9d1d9")
            if p == cur:
                t.append("  [active]", style="#3fb950")
            opts.append(Option(t, id=p))
        opts.append(Option(Text("  \u270f  Enter manually\u2026", style="#58a6ff"), id="__manual__"))
        with Vertical(id="apppanel") as v:
            v.border_title = "  Select Port  "
            yield OptionList(*opts, id="applist")
        yield Static("  \u2191\u2193 nav   Enter select   Esc cancel", id="apphint")

    def on_mount(self) -> None:
        ports = _list_ports()
        cur   = _port()
        if cur in ports:
            try:
                self.query_one("#applist", OptionList).highlighted = ports.index(cur)
            except Exception:
                pass

    @on(OptionList.OptionSelected, "#applist")
    def _pick(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_id)


# ---------------------------------------------------------------------------
# App-select screen
# ---------------------------------------------------------------------------

class AppSelectScreen(Screen):
    BINDINGS = [Binding("escape", "dismiss(None)", "Back")]

    def __init__(self, title: str) -> None:
        super().__init__()
        self._screen_title = title
        self._apps: list[tuple[Path, bool]] = []

    def compose(self) -> ComposeResult:
        self._apps = find_apps()
        opts = []
        for app_dir, is_bin in self._apps:
            rel = app_dir.relative_to(DUNEOS_ROOT)
            t = Text()
            t.append(f" {app_dir.name:<20}", style="bold #c9d1d9")
            t.append(str(rel.parent), style="#6e7681")
            opts.append(Option(t))

        with Vertical(id="apppanel") as v:
            v.border_title = f"  {self._screen_title}  "
            yield OptionList(*opts, id="applist")
        yield Static(
            "  ↑↓ navigate   Enter select   Esc back",
            id="apphint",
        )

    @on(OptionList.OptionSelected, "#applist")
    def _pick(self, event: OptionList.OptionSelected) -> None:
        idx = event.option_index
        if 0 <= idx < len(self._apps):
            self.dismiss(self._apps[idx])
        else:
            self.dismiss(None)


# ---------------------------------------------------------------------------
# Init Config screen (edit boards/<board>/init.yaml)
# ---------------------------------------------------------------------------

_RESTART_CYCLE   = ["always", "on-failure", "no"]


class ProfilePickScreen(Screen):
    """OptionList of profiles/<name>/profile.yaml — Enter sets the active profile."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def compose(self) -> ComposeResult:
        from .system import list_profiles, load_profile, active_profile_name
        cur = active_profile_name()
        opts = []
        for p in list_profiles():
            name = p.parent.name
            t    = Text()
            t.append(f" {name:<30}", style="bold #c9d1d9")
            try:
                cfg = load_profile(name)
                t.append(f"{cfg.get('board','?'):<24}", style="#58a6ff")
                desc = cfg.get("description", "")
                if desc:
                    t.append(desc, style="#6e7681")
            except SystemExit as e:
                t.append(f"  [load failed: {e}]", style="#f85149")
            if name == cur:
                t.append("  [active]", style="#3fb950")
            opts.append(Option(t, id=name))
        with Vertical(id="apppanel") as v:
            v.border_title = "  Select Profile  "
            if not opts:
                yield Static(
                    "  No profiles yet. Create profiles/<name>/profile.yaml — see\n"
                    "  profiles/cardputer-default/ for a template.",
                    id="applist",
                )
            else:
                yield OptionList(*opts, id="applist")
        yield Static(
            "  ↑↓ nav   Enter set active   Esc cancel",
            id="apphint",
        )

    @on(OptionList.OptionSelected, "#applist")
    def _pick(self, event: OptionList.OptionSelected) -> None:
        from .system import ACTIVE_PROFILE_FILE, load_profile
        name = event.option_id
        ACTIVE_PROFILE_FILE.write_text(name + "\n")
        # Align .duneos_board so the rest of dbt picks up the same target.
        cfg = load_profile(name)
        board_file = DUNEOS_ROOT / ".duneos_board"
        if not board_file.exists() or board_file.read_text().strip() != cfg["board"]:
            board_file.write_text(cfg["board"] + "\n")
        self.dismiss(name)


def _is_bin(app_dir: Path) -> bool:
    """Returns True if the app lives under apps/system/bin/ (→ /sd/bin/)."""
    bin_root = DUNEOS_ROOT / "apps" / "system" / "bin"
    try:
        app_dir.relative_to(bin_root)
        return True
    except ValueError:
        return False


class ProfilePreviewModal(ModalScreen):
    """Read-only YAML preview of the profile state about to be saved.

    Useful before pressing S in the editor to see exactly what will land in
    profile.yaml (and therefore in the staged init.yaml on /flash). The body
    is in a VerticalScroll so long profiles scroll with PgUp/PgDn/arrows.
    Closed with Enter / Esc / q; the editor stays open behind it.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q",      "dismiss", "Close", show=False),
        # Enter is intentionally NOT bound to dismiss — it would prevent the
        # scroll widget from reaching the bottom of long previews.
    ]

    def __init__(self, profile_name: str, yaml_text: str) -> None:
        super().__init__()
        self._name = profile_name
        self._yaml = yaml_text

    def compose(self) -> ComposeResult:
        with Vertical(id="previewbox") as v:
            v.border_title = f"  Preview: profiles/{self._name}/profile.yaml  "
            with VerticalScroll(id="previewscroll"):
                yield Static(self._yaml, id="previewbody")
            yield Static(
                "[dim]  ↑↓ PgUp PgDn  scroll   Esc / q  close[/dim]",
                id="previewhint",
            )

    def on_mount(self) -> None:
        # Focus the scroll container so arrow keys / PgUp/PgDn drive the
        # scrolling immediately (no need to Tab into it first).
        try:
            self.query_one("#previewscroll", VerticalScroll).focus()
        except Exception:
            pass


class ProfileEditorScreen(Screen):
    """Two-column flash/SD profile editor.

    Each column lists every available app. Per-app state cycles through:
      □  not staged
      ☑  staged in apps_<flash|sd>
      ☑  staged AND in init_<flash|sd>  with restart policy (always/on-failure/no)

    Keys:
      ←/→     switch active column
      ↑/↓     move cursor in the active column
      Space   cycle state for the highlighted app
      R       cycle restart policy (only when the app is in init)
      S       save → profiles/<active>/profile.yaml
      Esc     discard
    """

    # `priority=True` makes these fire even when the OptionList child has
    # focus; otherwise Textual lets the focused widget see the keypress
    # first and the screen-level binding is shadowed for plain letter keys
    # (we hit this on "a" / "r").
    BINDINGS = [
        Binding("escape", "discard",            "Discard",         priority=True),
        Binding("s",      "save",               "Save",            priority=True),
        Binding("p",      "preview",            "Preview YAML",    priority=True),
        Binding("space",  "cycle_state",        "Cycle state",     priority=True),
        Binding("enter",  "cycle_state",        "Cycle state",     show=False, priority=True),
        Binding("r",      "cycle_restart",      "Restart policy",  priority=True),
        Binding("a",      "cycle_after",        "After: dep",      priority=True),
        Binding("left",   "focus_col('flash')", "Flash col",       priority=True),
        Binding("right",  "focus_col('sd')",    "SD col",          priority=True),
        Binding("tab",    "switch_col",         "Switch col",      show=False, priority=True),
    ]

    def __init__(self, profile_name: str) -> None:
        super().__init__()
        from .system import load_profile, parse_partition_sizes
        self._name    = profile_name
        self._profile = load_profile(profile_name)
        self._board   = self._profile["board"]

        # Build the union of all known apps; per app, store (staged?, restart?).
        # `restart=None` means the app is in apps_<col> but not in init_<col>.
        self._apps:  list[tuple[str, bool]] = []   # (name, is_bin)
        self._state: dict[str, dict] = {
            # name → {"flash": (staged, restart|None), "sd": (staged, restart|None)}
        }
        self._cursor: dict[str, int] = {"flash": 0, "sd": 0}
        self._col: str = "flash"

        # Phase 25.3: cached size + permission-warning data for live UI.
        self._sizes:    dict[str, int]       = {}     # app → ELF size in bytes
        self._warnings: dict[str, list[str]] = {}     # app → list of warning strings
        parts = parse_partition_sizes(self._board)
        self._flash_max = parts.get("sysbin", 0)

    def _load_apps(self) -> None:
        from .manifest import load_manifest
        from .system import _read_sdkconfig
        from .capability_map import check_app
        flash_set = set(self._profile.get("apps_flash", []))
        sd_set    = set(self._profile.get("apps_sd",    []))
        # Each init_<col> entry maps app_name → (restart, after-or-empty). The
        # `after:` field is optional in the YAML; treat missing as "" (no dep).
        init_flash = {
            self._app_from_path(e.get("path", "")):
                (e.get("restart", "always"), e.get("after", ""))
            for e in self._profile.get("init_flash", [])
        }
        init_sd    = {
            self._app_from_path(e.get("path", "")):
                (e.get("restart", "always"), e.get("after", ""))
            for e in self._profile.get("init_sd", [])
        }
        try:
            sdk_lines = _read_sdkconfig(self._board)
        except SystemExit:
            sdk_lines = []
        seen = set()
        for app_dir, _ in find_apps():
            try:
                m    = load_manifest(app_dir)
                name = m["name"]
            except Exception:
                name = app_dir.name
                m    = {}
            if name in seen:
                continue
            seen.add(name)
            self._apps.append((name, _is_bin(app_dir)))
            flash_init = init_flash.get(name)
            sd_init    = init_sd.get(name)
            # State per (app, col): (staged, restart-or-None, after-or-"")
            #   restart=None  ⇒ in apps_<col> only, not in init_<col>
            #   after=""      ⇒ no dependency (launch immediately)
            self._state[name] = {
                "flash": (name in flash_set,
                          flash_init[0] if flash_init else None,
                          flash_init[1] if flash_init else ""),
                "sd":    (name in sd_set,
                          sd_init[0]    if sd_init    else None,
                          sd_init[1]    if sd_init    else ""),
            }
            # Cache ELF size + permission warnings for live display.
            elf = app_dir / "build" / "app.elf"
            self._sizes[name] = elf.stat().st_size if elf.exists() else 0
            mask = int(m.get("permissions", 0)) if m else 0
            self._warnings[name] = check_app(name, mask, sdk_lines) if sdk_lines else []

    @staticmethod
    def _app_from_path(path: str) -> str:
        """Extract app name from /flash/bin/X.dap or /sd/{bin,apps}/X.dap."""
        if path.endswith(".dap"):
            return path.rsplit("/", 1)[-1][:-len(".dap")]
        return ""

    def compose(self) -> ComposeResult:
        self._load_apps()
        desc = self._profile.get("description", "")
        with Vertical(id="profileheader") as h:
            h.border_title = (
                f"  Profile: {self._name}  (board: {self._board})  "
            )
            if desc:
                yield Static(desc)
        with Horizontal(id="profile2col"):
            with Vertical(classes="profilecolumn", id="flashcol") as fc:
                fc.border_title = self._flash_bar()
                yield OptionList(*self._make_opts("flash"),
                                 id="flashlist", classes="profilelist")
            with Vertical(classes="profilecolumn", id="sdcol") as sc:
                sc.border_title = self._sd_bar()
                yield OptionList(*self._make_opts("sd"),
                                 id="sdlist", classes="profilelist")
        yield Static(
            "  ←→ col   ↑↓ nav   Space cycle (□/☑/☑+init)   R restart   A after-dep   P preview   S save   Esc discard",
            id="apphint",
        )

    def on_mount(self) -> None:
        # Pre-position cursors at 0 of each list; focus flash column initially.
        try:
            self.query_one("#flashlist", OptionList).focus()
        except Exception:
            pass

    def on_key(self, event: events.Key) -> None:
        """Letter-key fallback for the editor actions.

        `Binding(..., priority=True)` on the screen *should* fire before the
        focused OptionList consumes the key, but in practice (Textual 0.x
        behaviour observed on Linux terminals) some letter keys still get
        eaten — A and P most notably. This handler runs before the bindings
        machinery and explicitly stops propagation, so the action runs no
        matter which child has focus. Keys not listed here fall through to
        the OptionList for navigation / typeahead.
        """
        action_map = {
            "a": self.action_cycle_after,
            "r": self.action_cycle_restart,
            "p": self.action_preview,
            "s": self.action_save,
        }
        fn = action_map.get(event.key)
        if fn:
            fn()
            event.stop()
            event.prevent_default()

    def _init_predecessors(self, col: str, exclude: str) -> list[str]:
        """Apps eligible as `after:` predecessors in `col` (in init, not self)."""
        return [n for n, _ in self._apps
                if n != exclude and self._state[n][col][0]
                and self._state[n][col][1] is not None]

    def _make_opts(self, col: str) -> list[Option]:
        opts = []
        for name, _is_bin_ in self._apps:
            staged, restart, after = self._state[name][col]
            warn  = bool(self._warnings.get(name))
            size  = self._sizes.get(name, 0)
            size_s = f"{size/1024:5.1f}KB" if size else "  ?  "
            t = Text()
            # Warning marker (permission/CONFIG mismatch) prefixed before checkbox.
            t.append(" ⚠ " if warn else "   ",
                     style="#d29922" if warn else "")
            if staged:
                if restart is not None:
                    t.append("☑ ", style="bold #3fb950")
                    t.append(f"{name:<22}", style="bold #c9d1d9")
                    t.append(f"{size_s}  ", style="#6e7681")
                    t.append(f"restart: {restart}", style="#58a6ff")
                    if after:
                        # Hint with the predecessor's status: greyed if absent
                        # from init, orange if predecessor is restart:always
                        # (never exits → dependency degenerate).
                        pred_state = self._state.get(after, {}).get(col)
                        pred_color = "#d29922"
                        if pred_state and pred_state[1]:
                            pred_color = (
                                "#d29922" if pred_state[1] == "always"
                                else "#8b949e"
                            )
                        t.append(f"  after: {after}", style=pred_color)
                else:
                    t.append("☑ ", style="bold #3fb950")
                    t.append(f"{name:<22}", style="#c9d1d9")
                    t.append(f"{size_s}", style="#6e7681")
            else:
                t.append("☐ ", style="#6e7681")
                t.append(f"{name:<22}", style="#6e7681")
                t.append(f"{size_s}", style="#4d5560")
            opts.append(Option(t, id=name))
        return opts

    def _col_staged_bytes(self, col: str) -> int:
        return sum(self._sizes.get(name, 0)
                   for name, _ in self._apps
                   if self._state[name][col][0])

    def _flash_bar(self) -> str:
        """Render the /flash column header with a live size bar."""
        used  = self._col_staged_bytes("flash")
        if self._flash_max <= 0:
            return f"  /flash  ({used/1024:.1f} KB staged)  "
        frac = max(0.0, min(1.0, used / self._flash_max))
        filled = int(frac * 16)
        bar    = "█" * filled + "░" * (16 - filled)
        pct    = int(frac * 100)
        status = "✗ OVERFLOW" if used > self._flash_max else f"{pct}%"
        return f"  /flash  [{bar}] {used/1024:.1f}/{self._flash_max/1024:.0f} KB  {status}  "

    def _sd_bar(self) -> str:
        used = self._col_staged_bytes("sd")
        n    = sum(1 for name, _ in self._apps if self._state[name]["sd"][0])
        return f"  /sd  ({n} apps, {used/1024:.1f} KB total — informational)  "

    def _refresh(self) -> None:
        for col, list_id, panel_id, title_fn in (
            ("flash", "#flashlist", "#flashcol", self._flash_bar),
            ("sd",    "#sdlist",    "#sdcol",    self._sd_bar),
        ):
            ol = self.query_one(list_id, OptionList)
            cur = self._cursor[col]
            ol.clear_options()
            for opt in self._make_opts(col):
                ol.add_option(opt)
            try:
                ol.highlighted = cur
            except Exception:
                pass
            try:
                self.query_one(panel_id, Vertical).border_title = title_fn()
            except Exception:
                pass

    @on(OptionList.OptionHighlighted, "#flashlist")
    def _hi_flash(self, ev: OptionList.OptionHighlighted) -> None:
        self._cursor["flash"] = ev.option_index
        self._col = "flash"

    @on(OptionList.OptionHighlighted, "#sdlist")
    def _hi_sd(self, ev: OptionList.OptionHighlighted) -> None:
        self._cursor["sd"] = ev.option_index
        self._col = "sd"

    def action_focus_col(self, which: str) -> None:
        self._col = which
        self.query_one("#flashlist" if which == "flash" else "#sdlist", OptionList).focus()

    def action_switch_col(self) -> None:
        self.action_focus_col("sd" if self._col == "flash" else "flash")

    def action_cycle_state(self) -> None:
        idx = self._cursor[self._col]
        if not (0 <= idx < len(self._apps)):
            return
        name = self._apps[idx][0]
        staged, restart, after = self._state[name][self._col]
        # □ → ☑(no init) → ☑(init+always) → □
        if not staged:
            self._state[name][self._col] = (True, None, "")
        elif restart is None:
            self._state[name][self._col] = (True, "always", after)
        else:
            # Removing from init also drops any `after:` reference.
            self._state[name][self._col] = (False, None, "")
        self._refresh()

    def action_cycle_restart(self) -> None:
        idx = self._cursor[self._col]
        if not (0 <= idx < len(self._apps)):
            return
        name = self._apps[idx][0]
        staged, restart, after = self._state[name][self._col]
        if not staged or restart is None:
            return  # only when already in init
        idx_r = _RESTART_CYCLE.index(restart) if restart in _RESTART_CYCLE else -1
        self._state[name][self._col] = (
            True,
            _RESTART_CYCLE[(idx_r + 1) % len(_RESTART_CYCLE)],
            after,
        )
        self._refresh()

    def action_cycle_after(self) -> None:
        """Cycle the highlighted init entry's `after:` through other init apps.

        Only meaningful when the app is in init (☑+restart). Predecessors are
        OTHER apps that are also in init in the same column. Cycle order:
            "" (none) → cand[0] → cand[1] → … → "" → …

        A degenerate dep on a `restart: always` predecessor is allowed (the
        kernel logs a warning and launches immediately) — it's the same fail-
        safe behaviour we get from a missing predecessor.
        """
        idx = self._cursor[self._col]
        if not (0 <= idx < len(self._apps)):
            return
        name = self._apps[idx][0]
        staged, restart, after = self._state[name][self._col]
        if not staged or restart is None:
            return  # only when already in init
        cands = [""] + self._init_predecessors(self._col, name)
        try:
            pos = cands.index(after)
        except ValueError:
            pos = 0
        next_after = cands[(pos + 1) % len(cands)]
        self._state[name][self._col] = (True, restart, next_after)
        self._refresh()

    def _build_profile_dict(self) -> dict:
        """Render the in-memory state as the dict we'll dump to profile.yaml.

        Shared by action_save (writes the file) and action_preview (shows it
        in a modal). Keeping this single-sourced means the preview always
        matches what will actually be saved.
        """
        flash_apps = [n for n, _ in self._apps if self._state[n]["flash"][0]]
        sd_apps    = [n for n, _ in self._apps if self._state[n]["sd"][0]]

        init_flash = []
        for n, _ in self._apps:
            staged, restart, after = self._state[n]["flash"]
            if staged and restart is not None:
                entry = {"path": f"/flash/bin/{n}.dap", "restart": restart}
                if after:
                    entry["after"] = after
                init_flash.append(entry)

        init_sd = []
        is_bin = {n: b for n, b in self._apps}
        for n, _ in self._apps:
            staged, restart, after = self._state[n]["sd"]
            if staged and restart is not None:
                base = "/sd/bin" if is_bin[n] else "/sd/apps"
                entry = {"path": f"{base}/{n}.dap", "restart": restart}
                if after:
                    entry["after"] = after
                init_sd.append(entry)

        return {
            "name":        self._profile.get("name", self._name),
            "board":       self._board,
            "description": self._profile.get("description", ""),
            "apps_flash":  flash_apps,
            "init_flash":  init_flash,
            "apps_sd":     sd_apps,
            "init_sd":     init_sd,
        }

    def action_preview(self) -> None:
        """Show the YAML that would be saved, without saving."""
        try:
            import yaml as _yaml
        except ImportError:
            self.app.push_screen(ErrorModal(
                "PyYAML missing",
                "pip install pyyaml to enable preview."))
            return
        yaml_text = _yaml.safe_dump(self._build_profile_dict(),
                                    sort_keys=False, default_flow_style=False)
        self.app.push_screen(ProfilePreviewModal(self._name, yaml_text))

    def action_save(self) -> None:
        """Re-render profiles/<name>/profile.yaml from the in-memory state."""
        from .system import PROFILES_DIR
        try:
            import yaml as _yaml
        except ImportError:
            self.dismiss(False)
            return
        new_profile = self._build_profile_dict()
        out = PROFILES_DIR / self._name / "profile.yaml"
        out.write_text(_yaml.safe_dump(new_profile, sort_keys=False, default_flow_style=False))
        self.dismiss(True)

    def action_discard(self) -> None:
        self.dismiss(False)


# ---------------------------------------------------------------------------
# Main TUI
# ---------------------------------------------------------------------------

_MENU: list[tuple[str, str, str] | None] = [
    # -- Image composition (Phase 25 profile-driven workflow) ----
    ("profile-edit", "e", "Edit Profile…"),
    ("profile-pick", "P", "Switch Profile…"),
    ("system-check", "k", "Check Profile"),
    None,
    # -- Flash actions (the device) ----
    ("flash-kernel", "f", "Flash Kernel"),
    ("flash-sysbin", "s", "Flash Sysbin (/flash)"),
    ("monitor",      "m", "Monitor"),
    None,
    # -- SD actions ----
    ("flash-sd",     "d", "Deploy to SD…"),
    None,
    # -- Build (independent) ----
    ("build-all",    "b", "Build All"),
    ("build-app",    "a", "Build App…"),
    None,
    # -- Advanced ----
    ("bspgen",       "g", "BSP Gen…"),
    None,
    # -- Settings ----
    ("board",        "c", "Board…"),
    ("port",         "p", "Port…"),
    ("quit",         "q", "Quit"),
]


class DbtApp(App):
    TITLE      = "dbt"
    DARK       = True
    CSS        = CSS
    ANIMATIONS = False

    BINDINGS = [
        Binding("e", "do('profile-edit')", "Edit Profile"),
        Binding("P", "do('profile-pick')", "Switch Profile"),
        Binding("k", "do('system-check')", "Check Profile"),
        Binding("f", "do('flash-kernel')", "Flash Kernel"),
        Binding("s", "do('flash-sysbin')", "Flash Sysbin"),
        Binding("m", "do('monitor')",      "Monitor"),
        Binding("d", "do('flash-sd')",     "Deploy SD"),
        Binding("b", "do('build-all')",    "Build All"),
        Binding("a", "do('build-app')",    "Build App"),
        Binding("g", "do('bspgen')",       "BSP Gen"),
        Binding("c", "do('board')",        "Board"),
        Binding("p", "do('port')",         "Port"),
        Binding("q", "quit",               "Quit"),
    ]

    # -- Compose / mount ─────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Static(self._status_text(), id="status")
        with Horizontal(id="body"):
            with Vertical(id="nav") as v:
                v.border_title = "  ACTIONS  "
                opts = []
                for item in _MENU:
                    if item is None:
                        opts.append(None)   # None = separator in textual 8.x
                    else:
                        action_id, key, label = item
                        opts.append(_opt(key, label, action_id))
                yield OptionList(*opts, id="menu")
            with Vertical(id="output") as v:
                v.border_title = "  OUTPUT  "
                yield RichLog(id="log", highlight=True, markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        # Splash for ~1 s; any keypress dismisses early. Suppress with
        # DUNEOS_TUI_NO_SPLASH=1 in CI / scripted runs.
        if not os.environ.get("DUNEOS_TUI_NO_SPLASH"):
            self.push_screen(SplashScreen(hold_s=1.0))
        self.query_one("#menu", OptionList).focus()
        log = self.query_one("#log", RichLog)
        b = _board(); p = _port(); idf = find_idf_root()
        log.write(
            f"[#3fb950]✓[/#3fb950]  board  [bold]{b}[/bold]" if b
            else "[#d29922]![/#d29922]  board not configured — press [bold]c[/bold]"
        )
        log.write(
            f"[#3fb950]✓[/#3fb950]  port   [bold]{p}[/bold]" if p
            else "[#d29922]![/#d29922]  port not configured  — press [bold]c[/bold]"
        )
        log.write(
            f"[#3fb950]✓[/#3fb950]  idf    [dim]{idf}[/dim]" if idf
            else "[#f85149]✗[/#f85149]  ESP-IDF v6.0.x not found — press [bold]c[/bold]"
        )

    # -- Routing ─────────────────────────────────────────────────────────────

    @on(OptionList.OptionSelected, "#menu")
    def _menu_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_do(event.option_id or "")

    def action_do(self, action_id: str) -> None:
        dispatch = {
            "profile-edit": self._run_profile_edit,
            "profile-pick": self._run_profile_pick,
            "system-check": self._run_system_check,
            "flash-kernel": self._run_flash_kernel,
            "flash-sysbin": self._run_flash_sysbin,
            "monitor":      self._run_monitor,
            "build-all":    self._run_build_all,
            "build-app":    self._pick_and_build,
            "flash-sd":     self._run_flash_sd,
            "bspgen":       self._run_bspgen,
            "board":        self._run_board_pick,
            "port":         self._run_port_pick,
            "quit":         self.action_quit,
        }
        fn = dispatch.get(action_id)
        if fn:
            fn()

    # -- Utilities ───────────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        self.query_one("#log", RichLog).write(msg)

    def _log_ansi(self, line: str) -> None:
        self.query_one("#log", RichLog).write(Text.from_ansi(line))

    def _err(self, title: str, body: str) -> None:
        self.push_screen(ErrorModal(title, body))

    def _set_busy(self, busy: bool) -> None:
        self.query_one("#menu", OptionList).disabled = busy

    def _status_text(self) -> str:
        b   = _board() or "[dim]no board[/dim]"
        p   = _port()  or "[dim]no port[/dim]"
        idf = find_idf_root()
        iv  = "[#3fb950]IDF v6[/#3fb950]" if idf else "[#f85149]IDF missing[/#f85149]"
        return (
            f"[bold #58a6ff]DuneOS dbt[/bold #58a6ff]"
            f"  │  board [bold]{b}[/bold]"
            f"  │  port [bold]{p}[/bold]"
            f"  │  {iv}"
        )

    def _refresh_status(self) -> None:
        self.query_one("#status", Static).update(self._status_text())

    def _idf_env(self) -> dict:
        idf_root = find_idf_root()
        if not idf_root:
            return os.environ.copy()
        from .setup import build_idf_env
        return build_idf_env(idf_root)

    def _idf_cmd(self, *idf_args: str) -> list[str]:
        import shlex
        flat = []
        for a in idf_args:
            flat.extend(shlex.split(a))

        idf_root = find_idf_root()
        if idf_root:
            from .setup import idf_python as _idf_python
            python = _idf_python(idf_root)
            idf_py = idf_root / "tools" / "idf.py"
            if python and idf_py.exists():
                return [str(python), str(idf_py)] + flat

        export   = _idf_export()
        args_str = " ".join(shlex.quote(a) for a in flat)
        return ["bash", "-c",
                f'source "{export}" > /dev/null 2>&1 && idf.py {args_str}']

    def _stream(self, cmd: list[str],
                cwd: Path | None = None,
                env: dict | None = None) -> int:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, cwd=cwd or DUNEOS_ROOT, env=env,
        )
        for line in proc.stdout:
            self.call_from_thread(self._log_ansi, line.rstrip())
        proc.wait()
        return proc.returncode

    def _guard(self, need_board=True, need_port=True, need_idf=True) -> bool:
        """Check config; post error modal and return False if anything is missing."""
        if need_board and not _board():
            self.call_from_thread(
                self._err, "Board not configured", "Press c to open Setup.")
            return False
        if need_port and not _port():
            self.call_from_thread(
                self._err, "Port not configured", "Press c to open Setup.")
            return False
        if need_idf and not find_idf_root():
            self.call_from_thread(
                self._err, "ESP-IDF v6.0.x not found",
                "Press c — run ./install.sh in the IDF directory to create the Python env.")
            return False
        return True

    # -- Profile (Phase 25) ──────────────────────────────────────────────────

    def _run_profile_pick(self) -> None:
        """Switch active profile (writes .duneos_profile + aligns .duneos_board)."""
        def _done(name: str | None) -> None:
            if name:
                self._log(f"[#3fb950]✓[/#3fb950]  active profile: [bold]{name}[/bold]")
            else:
                self._log("[dim]  profile pick: cancelled[/dim]")
        self.push_screen(ProfilePickScreen(), _done)

    def _run_profile_edit(self) -> None:
        """Edit the active profile's apps_flash / apps_sd / init_flash / init_sd."""
        from .system import active_profile_name
        name = active_profile_name()
        if not name:
            def _picked(picked: str | None) -> None:
                if picked:
                    self._open_profile_editor(picked)
            self._log("[#d29922]![/#d29922]  no active profile — pick one first")
            self.push_screen(ProfilePickScreen(), _picked)
            return
        self._open_profile_editor(name)

    def _open_profile_editor(self, name: str) -> None:
        def _done(saved: bool | None) -> None:
            if saved:
                self._log(f"[#3fb950]✓[/#3fb950]  profiles/{name}/profile.yaml saved")
            else:
                self._log("[dim]  profile edit: discarded[/dim]")
        self.push_screen(ProfileEditorScreen(name), _done)

    def _run_system_check(self) -> None:
        """Run `dbt system check` on the active profile, log results."""
        from .system import resolve_profile, check_profile
        try:
            name, profile = resolve_profile(None)
        except SystemExit as e:
            self._err("No active profile", str(e))
            return
        self._log(f"[bold]Profile:[/bold] {name}")
        # Capture stdout from check_profile.
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = check_profile(profile)
        for line in buf.getvalue().splitlines():
            self._log(f"  {line}")
        self._log(f"  [{'#3fb950' if rc == 0 else '#f85149'}]→ exit {rc}[/]")

    # -- Flash Kernel ────────────────────────────────────────────────────────

    def _run_flash_kernel(self) -> None:
        self._worker_flash_kernel()

    @work(thread=True, exclusive=True)
    def _worker_flash_kernel(self) -> None:
        self.call_from_thread(self._set_busy, True)
        try:
            if not self._guard():
                return
            board = _board(); port = _port()
            self.call_from_thread(
                self._log,
                f"\n[bold]── Flash Kernel ──[/bold]  board={board}  port={port}")

            yaml   = DUNEOS_ROOT / "boards" / board / "board.yaml"
            bspgen = DUNEOS_ROOT / "tools" / "duneos-bspgen.py"
            rc = self._stream([sys.executable, str(bspgen), str(yaml)])
            if rc != 0:
                self.call_from_thread(
                    self._err, "BSP generation failed",
                    f"boards/{board}/board.yaml — exit {rc}")
                return
            self.call_from_thread(self._log, "[#3fb950]✓[/#3fb950]  BSP generated")

            env = self._idf_env()
            self.call_from_thread(self._log, "  idf.py build …")
            rc = self._stream(self._idf_cmd("build"), env=env)
            if rc != 0:
                self.call_from_thread(
                    self._err, "Build failed", f"idf.py build exited {rc}")
                return
            self.call_from_thread(self._log, "[#3fb950]✓[/#3fb950]  Build OK")

            self.call_from_thread(self._log, f"  idf.py flash → {port} …")
            rc = self._stream(self._idf_cmd(f"-p {port}", "flash"), env=env)
            if rc != 0:
                self.call_from_thread(
                    self._err, "Flash failed",
                    f"exit {rc} — is {port} free?")
                return
            self.call_from_thread(self._log,
                "[bold #3fb950]✓  Kernel flashed![/bold #3fb950]")
        finally:
            self.call_from_thread(self._set_busy, False)

    # -- Flash Sysbin ────────────────────────────────────────────────────────

    def _run_flash_sysbin(self) -> None:
        self._worker_flash_sysbin()

    @work(thread=True, exclusive=True)
    def _worker_flash_sysbin(self) -> None:
        import shutil as _sh
        self.call_from_thread(self._set_busy, True)
        try:
            if not self._guard(need_idf=False):
                return
            self.call_from_thread(self._log, "\n[bold]── Flash Sysbin ──[/bold]")

            dbt = DUNEOS_ROOT / "tools" / "dbt.py"
            rc  = self._stream([sys.executable, str(dbt), "buildall"])
            if rc != 0:
                self.call_from_thread(
                    self._err, "Build errors", f"buildall exited {rc}")
                return

            staging = DUNEOS_ROOT / "build" / "sysbin_staging"
            if staging.exists():
                _sh.rmtree(staging)
            n = _stage(staging, _get_board_name())
            self.call_from_thread(self._log, f"  {n} app(s) staged")

            out = DUNEOS_ROOT / "build" / "sysbin.bin"
            _create_image(staging, out)
            self.call_from_thread(self._log,
                f"  image  [dim]{out.stat().st_size // 1024} KB[/dim]")

            esptool = _find_esptool()
            if not esptool:
                self.call_from_thread(self._err, "esptool not found", "")
                return
            port   = _port()
            offset = _get_sysbin_offset(_get_board_name())
            self.call_from_thread(self._log,
                f"  flashing @ {hex(offset)} → {port} …")
            rc = self._stream([esptool, "--port", port, "--baud", "460800",
                               "write_flash", hex(offset), str(out)])
            if rc != 0:
                self.call_from_thread(
                    self._err, "Flash failed",
                    f"exit {rc} — is {port} free?")
                return
            self.call_from_thread(self._log,
                "[bold #3fb950]✓  Sysbin flashed![/bold #3fb950]")
        finally:
            self.call_from_thread(self._set_busy, False)

    # -- Monitor ─────────────────────────────────────────────────────────────

    def _run_monitor(self) -> None:
        port = _port()
        if not port:
            self._err("Port not configured", "Press c to open Setup.")
            return
        idf_root = find_idf_root()
        if not idf_root:
            self._err("ESP-IDF not found", "Press c to open Setup.")
            return
        from .setup import build_idf_env, idf_python as _idf_python
        env    = build_idf_env(idf_root)
        python = _idf_python(idf_root)
        idf_py = idf_root / "tools" / "idf.py"
        with self.suspend():
            subprocess.run(
                [str(python), str(idf_py), "-p", port, "monitor"],
                env=env, cwd=DUNEOS_ROOT,
            )

    # -- Build All ───────────────────────────────────────────────────────────

    def _run_build_all(self) -> None:
        self._worker_build_all()

    @work(thread=True, exclusive=True)
    def _worker_build_all(self) -> None:
        self.call_from_thread(self._set_busy, True)
        try:
            self.call_from_thread(self._log, "\n[bold]── Build All ──[/bold]")
            dbt = DUNEOS_ROOT / "tools" / "dbt.py"
            rc  = self._stream([sys.executable, str(dbt), "buildall"])
            if rc != 0:
                self.call_from_thread(
                    self._err, "Build errors", f"buildall exited {rc}")
            else:
                self.call_from_thread(self._log,
                    "[bold #3fb950]✓  All apps built.[/bold #3fb950]")
        finally:
            self.call_from_thread(self._set_busy, False)

    # -- Build single app ────────────────────────────────────────────────────

    def _pick_and_build(self) -> None:
        def _on_result(result: tuple[Path, bool] | None) -> None:
            if result:
                app_dir, _ = result
                self._worker_build_single(app_dir)

        self.push_screen(AppSelectScreen("Build App"), _on_result)

    @work(thread=True, exclusive=True)
    def _worker_build_single(self, app_dir: Path) -> None:
        self.call_from_thread(self._set_busy, True)
        try:
            self.call_from_thread(self._log,
                f"\n[bold]── Build {app_dir.name} ──[/bold]")
            dbt = DUNEOS_ROOT / "tools" / "dbt.py"
            rc  = self._stream([sys.executable, str(dbt), "build"], cwd=app_dir)
            if rc != 0:
                self.call_from_thread(
                    self._err, "Build failed", "Check the log for compiler errors.")
            else:
                self.call_from_thread(self._log,
                    f"[bold #3fb950]✓  {app_dir.name} built.[/bold #3fb950]")
        finally:
            self.call_from_thread(self._set_busy, False)

    # -- Flash SD ────────────────────────────────────────────────────────────

    def _run_flash_sd(self) -> None:
        def _got_path(sd_str: str | None) -> None:
            if sd_str:
                _SD_FILE.write_text(sd_str + "\n")
                self._worker_flash_sd(Path(sd_str))
        self.push_screen(SdPathModal(), _got_path)

    @work(thread=True, exclusive=True)
    def _worker_flash_sd(self, sd: Path) -> None:
        self.call_from_thread(self._set_busy, True)
        try:
            if not sd.exists():
                self.call_from_thread(
                    self._err, "SD path not found", str(sd))
                return
            self.call_from_thread(self._log,
                f"\n[bold]── Flash SD → {sd} ──[/bold]")
            dbt = DUNEOS_ROOT / "tools" / "dbt.py"
            rc  = self._stream([sys.executable, str(dbt), "flash", "sd", str(sd)])
            if rc != 0:
                self.call_from_thread(
                    self._err, "Flash SD failed", f"exit {rc}")
            else:
                self.call_from_thread(self._log,
                    f"[bold #3fb950]✓  Deployed to {sd}[/bold #3fb950]")
        finally:
            self.call_from_thread(self._set_busy, False)

    # -- BSP Gen ─────────────────────────────────────────────────────────────

    def _run_bspgen(self) -> None:
        boards   = bspgen_list_boards()
        cur      = _board()
        # Build option list: active board first, then the rest, then "All boards"
        ordered  = ([cur] if cur and cur in boards else []) + \
                   [b for b in boards if b != cur]
        opts     = []
        for b in ordered:
            t = Text()
            t.append(f" {b}", style="bold #c9d1d9")
            if b == cur:
                t.append("  [active]", style="#3fb950")
            opts.append(Option(t, id=b))
        opts.append(Option(Text(" ── All boards ──", style="#58a6ff"), id="__all__"))

        class BspPickScreen(Screen):
            BINDINGS = [Binding("escape", "dismiss(None)", "Back")]

            def compose(self_) -> ComposeResult:
                with Vertical(id="apppanel") as v:
                    v.border_title = "  BSP Gen — select board  "
                    yield OptionList(*opts, id="applist")
                yield Static(
                    "  ↑↓ navigate   Enter select   Esc back",
                    id="apphint",
                )

            @on(OptionList.OptionSelected, "#applist")
            def _pick(self_, event: OptionList.OptionSelected) -> None:
                self_.dismiss(event.option_id)

        def _on_pick(choice: str | None) -> None:
            if choice == "__all__":
                self._worker_bspgen_all()
            elif choice:
                self._worker_bspgen(choice)

        self.push_screen(BspPickScreen(), _on_pick)

    @work(thread=True, exclusive=True)
    def _worker_bspgen(self, board: str) -> None:
        self.call_from_thread(self._set_busy, True)
        try:
            self.call_from_thread(self._log,
                f"\n[bold]── BSP Gen: {board} ──[/bold]")
            dbt = DUNEOS_ROOT / "tools" / "dbt.py"
            rc  = self._stream([sys.executable, str(dbt), "bspgen", board])
            if rc != 0:
                self.call_from_thread(self._err, "BSP Gen failed",
                                      f"boards/{board}/board.yaml — exit {rc}")
            else:
                self.call_from_thread(self._log,
                    f"[bold #3fb950]✓  BSP generated for {board}[/bold #3fb950]")
        finally:
            self.call_from_thread(self._set_busy, False)

    @work(thread=True, exclusive=True)
    def _worker_bspgen_all(self) -> None:
        self.call_from_thread(self._set_busy, True)
        try:
            self.call_from_thread(self._log, "\n[bold]── BSP Gen: all boards ──[/bold]")
            dbt = DUNEOS_ROOT / "tools" / "dbt.py"
            rc  = self._stream([sys.executable, str(dbt), "bspgen", "--all"])
            if rc != 0:
                self.call_from_thread(self._err, "BSP Gen failed",
                                      f"exit {rc} — see log")
            else:
                self.call_from_thread(self._log,
                    "[bold #3fb950]✓  All BSPs generated.[/bold #3fb950]")
        finally:
            self.call_from_thread(self._set_busy, False)

    # -- Board & Port pickers ────────────────────────────────────────────────

    def _run_board_pick(self) -> None:
        def _done(board: str | None) -> None:
            if board:
                _BOARD_FILE.write_text(board + "\n")
                self._refresh_status()
                self._log(f"[#3fb950]✓[/#3fb950]  board → {board}")
            else:
                self._log("[dim]  board: cancelled[/dim]")
        self.push_screen(BoardPickScreen(), _done)

    def _run_port_pick(self) -> None:
        def _got(choice: str | None) -> None:
            if choice == "__manual__":
                self.push_screen(PortInputModal(), _save)
            elif choice:
                _save(choice)
            else:
                self._log("[dim]  port: cancelled[/dim]")

        def _save(port: str | None) -> None:
            if port:
                _PORT_FILE.write_text(port + "\n")
                self._refresh_status()
                self._log(f"[#3fb950]✓[/#3fb950]  port → {port}")

        self.push_screen(PortPickScreen(), _got)

    # -- Setup (kept for programmatic use) ───────────────────────────────────

    def _open_setup(self) -> None:
        def _done(_) -> None:
            self._refresh_status()
        self.push_screen(SetupScreen(), _done)

    # -- Quit ────────────────────────────────────────────────────────────────

    def action_quit(self) -> None:
        self.exit()
