"""
setup — Interactive wizard for board, port and ESP-IDF configuration.
"""
import os
import platform
import subprocess
import sys
from pathlib import Path

from .constants import DUNEOS_ROOT

_BOARD_FILE = DUNEOS_ROOT / ".duneos_board"
_PORT_FILE  = DUNEOS_ROOT / ".duneos_port"
_IDF_FILE   = DUNEOS_ROOT / ".duneos_idf"   # user-saved IDF root (gitignored)


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def _list_boards() -> list[tuple[str, str]]:
    """Return [(board_dir_name, human_description)] from boards/*/board.yaml."""
    boards = []
    boards_dir = DUNEOS_ROOT / "boards"
    try:
        import yaml
        _have_yaml = True
    except ImportError:
        _have_yaml = False

    for yaml_path in sorted(boards_dir.glob("*/board.yaml")):
        name = yaml_path.parent.name
        desc = ""
        if _have_yaml:
            try:
                with open(yaml_path) as f:
                    doc = yaml.safe_load(f)
                board = doc.get("board", {})
                parts = []
                cpu = board.get("cpu", "")
                if cpu:
                    parts.append(cpu)
                flash = board.get("flash_size_mb")
                if flash:
                    parts.append(f"{flash}MB flash")
                psram = board.get("psram_size_mb", 0)
                if psram:
                    parts.append(f"{psram}MB PSRAM")
                desc = "  ".join(parts)
            except Exception:
                pass
        boards.append((name, desc))
    return boards


def _list_ports() -> list[str]:
    """List available serial ports via pyserial (bundled with esptool)."""
    try:
        from serial.tools.list_ports import comports
        return sorted(p.device for p in comports())
    except Exception:
        return []


_IDF_REQUIRED_VERSION = "v6.0"  # major.minor prefix — patch level doesn't matter


def _check_idf_version(idf_root: Path) -> bool:
    """Return True if the IDF at idf_root is v6.0.x (required version)."""
    # Primary: tools/cmake/version.cmake  — present since IDF v4
    vcmake = idf_root / "tools" / "cmake" / "version.cmake"
    if vcmake.exists():
        major = minor = None
        for line in vcmake.read_text().splitlines():
            if "IDF_VERSION_MAJOR" in line:
                try:
                    major = int(line.split()[-1].rstrip(")"))
                except (ValueError, IndexError):
                    pass
            elif "IDF_VERSION_MINOR" in line:
                try:
                    minor = int(line.split()[-1].rstrip(")"))
                except (ValueError, IndexError):
                    pass
        if major is not None and minor is not None:
            return major == 6 and minor == 0
    # Fallback: legacy version.txt
    version_file = idf_root / "version.txt"
    if version_file.exists():
        return version_file.read_text().strip().startswith(_IDF_REQUIRED_VERSION)
    # Cannot verify — let it through
    return True


def find_idf_root() -> Path | None:
    """
    Locate the ESP-IDF v6.0.x root directory by checking (in order):
      1. .duneos_idf file in the repo root
      2. IDF_PATH environment variable
      3. idf.py found in PATH (export.sh was sourced)
      4. Common install locations (~/.espressif, ~/esp, C:/Espressif …)
    Only returns paths that match ESP-IDF v6.0.x.
    """
    def _valid(p: Path) -> bool:
        return (p / "tools" / "idf.py").exists() and _check_idf_version(p)

    # 1. Saved path
    if _IDF_FILE.exists():
        p = Path(_IDF_FILE.read_text().strip())
        if _valid(p):
            return p

    # 2. IDF_PATH env
    idf_env = os.environ.get("IDF_PATH")
    if idf_env:
        p = Path(idf_env)
        if _valid(p):
            return p

    # 3. idf.py already on PATH (export.sh sourced in the shell)
    import shutil
    if shutil.which("idf.py") and idf_env:
        p = Path(idf_env)
        if _valid(p):
            return p

    # 4. Common install locations — check both direct and version-namespaced layouts:
    #    ~/esp/esp-idf              (direct clone)
    #    ~/esp/v6.0.1/esp-idf      (version-prefixed)
    #    ~/.espressif/v6.0.1/esp-idf   (vscode-idf installer layout)
    #    C:/Espressif/frameworks/esp-idf-v6.0.1  (Windows installer)
    is_win = platform.system() == "Windows"
    if is_win:
        search_bases = [
            Path("C:/Espressif/frameworks"),
            Path(os.environ.get("USERPROFILE", "C:/Users")) / ".espressif" / "frameworks",
            Path("C:/esp"),   # version-prefixed layout: C:/esp/v6.0.1/esp-idf
        ]
    else:
        home = Path.home()
        search_bases = [
            home / ".espressif",   # vscode-idf + idf_tools_path default
            home / "esp",          # manual clone convention
            Path("/opt/espressif"),
        ]

    for base in search_bases:
        if not base.exists():
            continue
        # Direct: base/esp-idf
        for name in ["esp-idf"] + [d.name for d in sorted(base.glob("esp-idf*"), reverse=True)]:
            candidate = base / name
            if candidate.is_dir() and _valid(candidate):
                return candidate
        # Version-prefixed: base/v*/esp-idf  (e.g. ~/.espressif/v6.0.1/esp-idf)
        for version_dir in sorted(base.glob("v*"), reverse=True):
            if not version_dir.is_dir():
                continue
            candidate = version_dir / "esp-idf"
            if _valid(candidate):
                return candidate

    return None


def find_idf_activate_script(idf_root: Path) -> Path | None:
    """Find the VSCode-idf activation script for idf_root.

    VSCode layout: ~/.espressif/v6.0.1/esp-idf → ~/.espressif/tools/activate_idf_v6.0.1.sh
    """
    version_dir = idf_root.parent
    tools_dir   = version_dir.parent / "tools"
    candidate   = tools_dir / f"activate_idf_{version_dir.name}.sh"
    if candidate.exists():
        return candidate
    hits = sorted(tools_dir.glob("activate_idf_v*.sh"), reverse=True)
    return hits[0] if hits else None


def build_idf_env(idf_root: Path) -> dict:
    """Build a complete env dict for running idf.py via the VSCode activation script.

    Parses KEY=VALUE output of activate_idf_*.sh -e so we get the full PATH
    (cmake, ninja, xtensa-esp-elf-gcc, venv/bin) without needing to source
    export.sh, which silently fails when ~/.espressif/python_env/ is absent.
    """
    script = find_idf_activate_script(idf_root)
    if not script:
        return os.environ.copy()

    try:
        result = subprocess.run(
            ["bash", str(script), "-e"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return os.environ.copy()
    except Exception:
        return os.environ.copy()

    env       = os.environ.copy()
    tool_path = ""
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip(); val = val.strip()
        if key == "PATH":
            tool_path = val
        elif key == "SYSTEM_PATH":
            pass  # we reconstruct PATH below
        else:
            env[key] = val

    if tool_path:
        env["PATH"] = tool_path + ":" + os.environ.get("PATH", "")

    return env


def idf_python(idf_root: Path) -> Path | None:
    """Return the path to the IDF Python venv executable for idf_root."""
    env    = build_idf_env(idf_root)
    py_env = env.get("IDF_PYTHON_ENV_PATH")
    if py_env:
        for name in ("python3", "python"):
            p = Path(py_env) / "bin" / name
            if p.exists():
                return p
    return None


def run_bspgen(board: str, console=None) -> bool:
    """Run duneos-bspgen.py for the given board. Returns True on success."""
    yaml_path = DUNEOS_ROOT / "boards" / board / "board.yaml"
    bspgen    = DUNEOS_ROOT / "tools" / "duneos-bspgen.py"

    if not yaml_path.exists():
        _msg(console, f"[red]Board YAML not found:[/red] {yaml_path}",
                      f"ERROR: Board YAML not found: {yaml_path}")
        return False
    if not bspgen.exists():
        _msg(console, f"[red]duneos-bspgen.py not found:[/red] {bspgen}",
                      f"ERROR: duneos-bspgen.py not found: {bspgen}")
        return False

    result = subprocess.run(
        [sys.executable, str(bspgen), str(yaml_path)],
        cwd=DUNEOS_ROOT,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Interactive prompts
# ---------------------------------------------------------------------------

def _prompt(text: str) -> str:
    """Read a line from stdin, handling Ctrl-C gracefully."""
    try:
        return input(text).strip()
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit("Aborted.")


def prompt_board(console=None) -> str:
    """Interactively select a board; returns its directory name."""
    boards = _list_boards()
    if not boards:
        sys.exit("ERROR: no boards found under boards/")

    if console:
        from rich.table import Table
        t = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
        t.add_column("#", style="dim", width=3)
        t.add_column("Board", style="bold")
        t.add_column("Specs", style="dim")
        for i, (name, desc) in enumerate(boards, 1):
            t.add_row(str(i), name, desc)
        console.print(t)
    else:
        for i, (name, desc) in enumerate(boards, 1):
            print(f"  [{i}] {name:<40} {desc}")

    current = _BOARD_FILE.read_text().strip() if _BOARD_FILE.exists() else ""
    suffix  = f" (current: {current}, Enter to keep)" if current else ""

    while True:
        raw = _prompt(f"Select board [1-{len(boards)}]{suffix}: ")
        if not raw and current:
            return current
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(boards):
                return boards[idx - 1][0]
        if raw in {n for n, _ in boards}:
            return raw
        print(f"  Enter 1–{len(boards)} or a board name.")


def prompt_port(console=None) -> str:
    """Interactively select or type a serial port; returns the port string."""
    ports = _list_ports()
    current = _PORT_FILE.read_text().strip() if _PORT_FILE.exists() else ""

    if ports:
        if console:
            console.print("\n[bold]Detected serial ports:[/bold]")
            for i, p in enumerate(ports, 1):
                console.print(f"  [cyan]{i}[/cyan]  {p}")
        else:
            print("\nDetected serial ports:")
            for i, p in enumerate(ports, 1):
                print(f"  [{i}] {p}")
        suffix = f"[1-{len(ports)}], path" + (f", Enter for {current}" if current else "")
    else:
        print("  (no serial ports detected)")
        suffix = "path" + (f", Enter for {current}" if current else "")

    while True:
        raw = _prompt(f"Select port ({suffix}): ")
        if not raw and current:
            return current
        if raw.isdigit() and ports:
            idx = int(raw)
            if 1 <= idx <= len(ports):
                return ports[idx - 1]
        if raw:
            return raw
        print("  Please enter a port.")


# ---------------------------------------------------------------------------
# Ensure helpers (called from other commands to auto-configure if missing)
# ---------------------------------------------------------------------------

def ensure_board(console=None) -> str:
    """Return active board name, prompting interactively if not configured."""
    if _BOARD_FILE.exists():
        b = _BOARD_FILE.read_text().strip()
        if b:
            return b

    _msg(console, "\n[yellow]Board not configured.[/yellow] Choose one:\n",
                  "\nBoard not configured. Choose one:\n")
    board = prompt_board(console)
    _BOARD_FILE.write_text(board + "\n")
    _msg(console, f"[green]✓ Board set:[/green] {board}", f"✓ Board set: {board}")
    return board


def ensure_port(console=None) -> str:
    """Return active port, prompting interactively if not configured."""
    if _PORT_FILE.exists():
        p = _PORT_FILE.read_text().strip()
        if p:
            return p

    _msg(console, "\n[yellow]Port not configured.[/yellow] Choose one:",
                  "\nPort not configured. Choose one:")
    port = prompt_port(console)
    _PORT_FILE.write_text(port + "\n")
    _msg(console, f"[green]✓ Port set:[/green] {port}", f"✓ Port set: {port}")
    return port


# ---------------------------------------------------------------------------
# Setup wizard command
# ---------------------------------------------------------------------------

def cmd_setup(args) -> None:
    """Full interactive setup wizard: board → port → ESP-IDF."""
    try:
        from rich.console import Console
        from rich.rule import Rule
        console = Console()
    except ImportError:
        console = None

    _msg(console, "\n[bold cyan]DuneOS Setup Wizard[/bold cyan]\n",
                  "\n=== DuneOS Setup Wizard ===\n")

    # --- Step 1: Board ---
    _msg(console, "[bold]Step 1/3 — Target board[/bold]\n",
                  "Step 1/3 — Target board\n")
    board = prompt_board(console)
    _BOARD_FILE.write_text(board + "\n")
    _msg(console, f"\n[green]✓[/green] Board: [bold]{board}[/bold]\n",
                  f"\n✓ Board: {board}\n")

    # --- Step 2: Port ---
    _msg(console, "[bold]Step 2/3 — Serial port[/bold]",
                  "Step 2/3 — Serial port")
    port = prompt_port(console)
    _PORT_FILE.write_text(port + "\n")
    _msg(console, f"\n[green]✓[/green] Port: [bold]{port}[/bold]\n",
                  f"\n✓ Port: {port}\n")

    # --- Step 3: ESP-IDF ---
    _msg(console, "[bold]Step 3/3 — ESP-IDF toolchain[/bold]\n",
                  "Step 3/3 — ESP-IDF toolchain\n")
    idf_root = find_idf_root()

    if idf_root:
        _IDF_FILE.write_text(str(idf_root) + "\n")
        _msg(console, f"[green]✓[/green] ESP-IDF found: [dim]{idf_root}[/dim]",
                      f"✓ ESP-IDF found: {idf_root}")

        # Run bspgen
        _msg(console, f"\nGenerating BSP headers for [bold]{board}[/bold]…",
                      f"\nGenerating BSP headers for {board}…")
        if not run_bspgen(board, console):
            _msg(console, "[red]✗ bspgen failed — check board YAML.[/red]",
                          "✗ bspgen failed — check board YAML.")
        else:
            _msg(console, "[green]✓[/green] BSP headers generated.",
                          "✓ BSP headers generated.")

        _msg(console,
             "\n[bold green]Setup complete![/bold green]\n\n"
             "Next:\n"
             "  [cyan]python tools/dbt.py flash kernel[/cyan]    ← build & flash kernel\n"
             "  [cyan]python tools/dbt.py flash sysbin[/cyan]    ← build & flash /flash partition\n",
             "\nSetup complete!\n"
             "  python tools/dbt.py flash kernel\n"
             "  python tools/dbt.py flash sysbin\n")
    else:
        _msg(console,
             "[red]✗ ESP-IDF v6.0.x not found.[/red]\n\n"
             "[bold]DuneOS requires ESP-IDF v6.0.1[/bold] (other versions are not supported).\n\n"
             "Install on Linux / macOS:\n\n"
             "  [cyan]mkdir -p ~/esp && cd ~/esp[/cyan]\n"
             "  [cyan]git clone --recursive https://github.com/espressif/esp-idf.git -b v6.0.1[/cyan]\n"
             "  [cyan]cd esp-idf && ./install.sh esp32s3[/cyan]\n"
             "  [cyan]source ~/esp/esp-idf/export.sh[/cyan]\n\n"
             "Then save the path so dbt finds it automatically:\n"
             "  [cyan]echo ~/esp/esp-idf > .duneos_idf[/cyan]\n\n"
             "[dim]Full guide: https://docs.espressif.com/projects/esp-idf/en/v6.0.1/esp32s3/get-started/[/dim]",
             "✗ ESP-IDF v6.0.x not found.\n\n"
             "DuneOS requires ESP-IDF v6.0.1 (other versions are not supported).\n\n"
             "Install:\n"
             "  mkdir -p ~/esp && cd ~/esp\n"
             "  git clone --recursive https://github.com/espressif/esp-idf.git -b v6.0.1\n"
             "  cd esp-idf && ./install.sh esp32s3\n"
             "  source ~/esp/esp-idf/export.sh\n\n"
             "Then save the path:\n"
             "  echo ~/esp/esp-idf > .duneos_idf\n")


# ---------------------------------------------------------------------------
# Shared output helper
# ---------------------------------------------------------------------------

def _msg(console, rich_text: str, plain_text: str) -> None:
    if console:
        console.print(rich_text)
    else:
        print(plain_text)
