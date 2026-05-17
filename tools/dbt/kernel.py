"""
kernel — Build and flash the DuneOS kernel.

Dispatches to the active board's toolchain plugin so that a future RP2040
board (pico-sdk plugin) works without changing this file.
"""
import sys

from .constants import DUNEOS_ROOT
from .setup import ensure_board, ensure_port, run_bspgen, _IDF_FILE, _msg
from .toolchain import get_board_plugin


def cmd_flash_kernel(args) -> None:
    """Build and/or flash the DuneOS kernel."""
    try:
        from rich.console import Console
        console = Console()
    except ImportError:
        console = None

    build_only = getattr(args, "build_only", False)
    flash_only = getattr(args, "flash_only", False)
    do_monitor = getattr(args, "monitor", False)

    # 1. Ensure board configured (auto-wizard if missing)
    board = ensure_board(console)

    # 2. Regenerate BSP headers for the active board
    _msg(console, f"\n[bold]Generating BSP for [cyan]{board}[/cyan]…[/bold]",
                  f"\nGenerating BSP for {board}…")
    if not run_bspgen(board, console):
        sys.exit("ERROR: bspgen failed — check boards/{board}/board.yaml")

    # 3. Load toolchain plugin for this board
    plugin, arch, cpu, board_cfg = get_board_plugin()

    idf_root = plugin.find_toolchain_root()
    if not idf_root:
        _msg(
            console,
            "[red]Toolchain root not found.[/red]\n"
            "Run [cyan]python tools/dbt.py setup[/cyan] for installation instructions.",
            "ERROR: Toolchain root not found.\nRun: python tools/dbt.py setup",
        )
        sys.exit(1)
    _msg(console, f"[dim]Toolchain: {idf_root}[/dim]", f"Toolchain: {idf_root}")
    # Cache discovered path so setup.py finds it next time
    if not _IDF_FILE.exists():
        _IDF_FILE.write_text(str(idf_root) + "\n")

    board_dir = DUNEOS_ROOT / "boards" / board
    build_dir = DUNEOS_ROOT / "build"

    # 4. Build
    if not flash_only:
        _msg(console, "\n[bold]Building kernel…[/bold]", "\nBuilding kernel…")
        rc = plugin.build_kernel(board_dir, build_dir, None)
        if rc != 0:
            sys.exit(f"ERROR: kernel build failed (exit {rc})")
        _msg(console, "[green]✓ Build OK[/green]", "✓ Build OK")

    # 5. Flash
    if not build_only:
        port = ensure_port(console)
        _msg(console, f"\n[bold]Flashing kernel to [cyan]{port}[/cyan]…[/bold]",
                      f"\nFlashing kernel to {port}…")
        rc = plugin.flash_kernel(build_dir, port, 460800)
        if rc != 0:
            sys.exit(f"ERROR: kernel flash failed (exit {rc})")
        _msg(console, "[bold green]✓ Kernel flashed![/bold green]",
                      "✓ Kernel flashed!")

    # 6. Optional monitor
    if do_monitor and not build_only:
        port = ensure_port(console)
        _msg(console, f"\n[bold]Opening monitor on {port}…[/bold]",
                      f"\nOpening monitor on {port}…")
        plugin.monitor(port)
    elif not build_only and not do_monitor:
        _msg(console,
             "\n[dim]Tip: add [cyan]--monitor[/cyan] to open the serial console immediately.[/dim]",
             "\nTip: add --monitor to open the serial console immediately.")
