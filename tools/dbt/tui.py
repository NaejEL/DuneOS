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

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
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

    def compose(self) -> ComposeResult:
        lbl = f" App: {self._app_name}" if self._app_name else ""
        with Vertical(id="setupbox") as v:
            v.border_title = "  Flash SD  "
            if lbl:
                yield Label(f"[bold #c9d1d9]{lbl}[/bold #c9d1d9]")
            yield Label("  SD card mount point:")
            yield Input(
                placeholder="/mnt/sd  or  /media/user/SD",
                id="sd-inp",
            )
            yield Label("  [dim]Enter: confirm   Esc: cancel[/dim]")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)


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
        self._apps = find_apps(include_examples=True)
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
# Main TUI
# ---------------------------------------------------------------------------

_MENU: list[tuple[str, str, str] | None] = [
    ("flash-kernel", "f", "Flash Kernel"),
    ("flash-sysbin", "s", "Flash Sysbin"),
    ("monitor",      "m", "Monitor"),
    None,
    ("build-all",    "b", "Build All"),
    ("build-app",    "a", "Build App…"),
    ("flash-sd",     "d", "Flash SD…"),
    None,
    ("bspgen",       "g", "BSP Gen…"),
    ("setup",        "c", "Setup"),
    ("quit",         "q", "Quit"),
]


class DbtApp(App):
    TITLE      = "dbt"
    DARK       = True
    CSS        = CSS
    ANIMATIONS = False

    BINDINGS = [
        Binding("f", "do('flash-kernel')", "Flash Kernel"),
        Binding("s", "do('flash-sysbin')", "Flash Sysbin"),
        Binding("m", "do('monitor')",      "Monitor"),
        Binding("b", "do('build-all')",    "Build All"),
        Binding("a", "do('build-app')",    "Build App"),
        Binding("d", "do('flash-sd')",     "Flash SD"),
        Binding("g", "do('bspgen')",       "BSP Gen"),
        Binding("c", "do('setup')",        "Setup"),
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
            "flash-kernel": self._run_flash_kernel,
            "flash-sysbin": self._run_flash_sysbin,
            "monitor":      self._run_monitor,
            "build-all":    self._run_build_all,
            "build-app":    self._pick_and_build,
            "flash-sd":     self._run_flash_sd,
            "bspgen":       self._run_bspgen,
            "setup":        self._open_setup,
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
            n = _stage(staging)
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

    # -- Setup ───────────────────────────────────────────────────────────────

    def _open_setup(self) -> None:
        def _done(_) -> None:
            # This callback runs on the main UI thread — call directly.
            self._refresh_status()

        self.push_screen(SetupScreen(), _done)

    # -- Quit ────────────────────────────────────────────────────────────────

    def action_quit(self) -> None:
        self.exit()
