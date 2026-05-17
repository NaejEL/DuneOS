"""
bspgen — dbt integration layer for duneos-bspgen.py.

Delegates all generation to the standalone duneos-bspgen.py script (no
code duplication). Adds multi-board orchestration, listing, and a board
picker used by both the CLI and TUI.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .constants import DUNEOS_ROOT
from .setup import _BOARD_FILE

_BSPGEN = DUNEOS_ROOT / "tools" / "duneos-bspgen.py"
_BOARDS_DIR = DUNEOS_ROOT / "boards"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def list_boards() -> list[str]:
    """Return sorted list of board names that have a board.yaml."""
    return sorted(
        d.name for d in _BOARDS_DIR.iterdir()
        if d.is_dir() and (d / "board.yaml").exists()
    )


def active_board() -> str | None:
    return _BOARD_FILE.read_text().strip() if _BOARD_FILE.exists() else None


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def generate_for_board(board: str, dry_run: bool = False) -> bool:
    """Generate BSP files for a board by name. Returns True on success."""
    yaml_path = _BOARDS_DIR / board / "board.yaml"
    if not yaml_path.exists():
        print(f"  ERROR: boards/{board}/board.yaml not found", file=sys.stderr)
        return False
    return _run_bspgen(yaml_path, dry_run=dry_run)


def _run_bspgen(yaml_path: Path, dry_run: bool = False) -> bool:
    cmd = [sys.executable, str(_BSPGEN), str(yaml_path)]
    if dry_run:
        cmd.append("--dry-run")
    return subprocess.run(cmd).returncode == 0


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

def cmd_bspgen(args) -> None:
    dry_run  = getattr(args, "dry_run", False)
    gen_all  = getattr(args, "all", False)
    do_list  = getattr(args, "list", False)
    board    = getattr(args, "board", None)

    if do_list:
        boards  = list_boards()
        current = active_board() or ""
        print(f"Available boards ({len(boards)}):")
        for b in boards:
            marker = " *" if b == current else "  "
            print(f" {marker} {b}")
        return

    if gen_all:
        boards = list_boards()
        if not boards:
            sys.exit("ERROR: no boards found under boards/")
        ok = fail = 0
        for b in boards:
            print(f"\n{'='*52}", flush=True)
            print(f"  {b}", flush=True)
            print(f"{'='*52}", flush=True)
            if generate_for_board(b, dry_run=dry_run):
                ok += 1
            else:
                fail += 1
        print(f"\n{ok}/{ok+fail} board(s) generated")
        if fail:
            sys.exit(1)
        return

    if board:
        if not generate_for_board(board, dry_run=dry_run):
            sys.exit(1)
        return

    # No args — use active board; if none set, list and prompt.
    cur = active_board()
    if not cur:
        boards = list_boards()
        print("Available boards:")
        for b in boards:
            print(f"  {b}")
        sys.exit(
            "\nERROR: no board configured.\n"
            "Run:  dbt bspgen <board>\n"
            " or:  echo <name> > .duneos_board && dbt bspgen"
        )

    print(f"Generating BSP for: {cur}", flush=True)
    if not generate_for_board(cur, dry_run=dry_run):
        sys.exit(1)
