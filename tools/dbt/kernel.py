"""
kernel — Build and flash the DuneOS kernel via idf.py.
"""
import platform
import subprocess
import sys
from pathlib import Path

from .constants import DUNEOS_ROOT
from .setup import (
    ensure_board, ensure_port,
    find_idf_root, run_bspgen,
    build_idf_env, idf_python,
    _IDF_FILE, _msg,
)


def _run_idf(idf_root: Path, idf_args: list[str]) -> int:
    """Run idf.py with the given args using the IDF Python venv directly."""
    is_win = platform.system() == "Windows"
    if is_win:
        export   = idf_root / "export.bat"
        args_str = " ".join(idf_args)
        cmd      = ["cmd", "/c", f'call "{export}" && idf.py {args_str}']
        result   = subprocess.run(cmd, cwd=DUNEOS_ROOT)
    else:
        env    = build_idf_env(idf_root)
        python = idf_python(idf_root)
        idf_py = idf_root / "tools" / "idf.py"
        if python and idf_py.exists():
            cmd = [str(python), str(idf_py)] + idf_args
        else:
            import shlex
            args_str = " ".join(shlex.quote(a) for a in idf_args)
            cmd      = ["bash", "-c",
                        f'source "{idf_root / "export.sh"}" && idf.py {args_str}']
            env      = None
        result = subprocess.run(cmd, cwd=DUNEOS_ROOT, env=env)

    return result.returncode


def _resolve_idf(console) -> Path:
    """Find ESP-IDF root or exit with a helpful message."""
    idf_root = find_idf_root()
    if idf_root:
        _msg(console, f"[dim]ESP-IDF: {idf_root}[/dim]", f"ESP-IDF: {idf_root}")
        # Cache the discovered path for next run
        if not _IDF_FILE.exists():
            _IDF_FILE.write_text(str(idf_root) + "\n")
        return idf_root

    _msg(
        console,
        "[red]ESP-IDF v6.0.x not found.[/red]\n"
        "Run [cyan]python tools/dbt.py setup[/cyan] for installation instructions.",
        "ERROR: ESP-IDF v6.0.x not found.\n"
        "Run: python tools/dbt.py setup",
    )
    sys.exit(1)


def cmd_flash_kernel(args) -> None:
    """Build and/or flash the DuneOS kernel."""
    try:
        from rich.console import Console
        console = Console()
    except ImportError:
        console = None

    build_only = getattr(args, "build_only", False)
    flash_only = getattr(args, "flash_only", False)
    monitor    = getattr(args, "monitor", False)

    # 1. Ensure board configured (auto-wizard if missing)
    board = ensure_board(console)

    # 2. Regenerate BSP headers for the active board
    _msg(console, f"\n[bold]Generating BSP for [cyan]{board}[/cyan]…[/bold]",
                  f"\nGenerating BSP for {board}…")
    if not run_bspgen(board, console):
        sys.exit("ERROR: bspgen failed — check boards/{board}/board.yaml")

    # 3. Locate ESP-IDF
    idf_root = _resolve_idf(console)

    # 4. Build
    if not flash_only:
        _msg(console, "\n[bold]Building kernel…[/bold]", "\nBuilding kernel…")
        rc = _run_idf(idf_root, ["build"])
        if rc != 0:
            sys.exit(f"ERROR: idf.py build failed (exit {rc})")
        _msg(console, "[green]✓ Build OK[/green]", "✓ Build OK")

    # 5. Flash
    if not build_only:
        port = ensure_port(console)
        _msg(console, f"\n[bold]Flashing kernel to [cyan]{port}[/cyan]…[/bold]",
                      f"\nFlashing kernel to {port}…")
        rc = _run_idf(idf_root, ["-p", port, "flash"])
        if rc != 0:
            sys.exit(f"ERROR: idf.py flash failed (exit {rc})")
        _msg(console, "[bold green]✓ Kernel flashed![/bold green]",
                      "✓ Kernel flashed!")

    # 6. Optional monitor
    if monitor and not build_only:
        port = ensure_port(console)
        _msg(console, f"\n[bold]Opening monitor on {port}…[/bold]",
                      f"\nOpening monitor on {port}…")
        _run_idf(idf_root, ["-p", port, "monitor"])
    elif not build_only and not monitor:
        _msg(console,
             "\n[dim]Tip: add [cyan]--monitor[/cyan] to open the serial console immediately.[/dim]",
             "\nTip: add --monitor to open the serial console immediately.")
