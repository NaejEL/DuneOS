"""
Capability resolver — maps app-declared capabilities to concrete source files.

An app declares its capabilities in duneos.yaml:

    capabilities:
      - display

dbt looks up the capability in CAPABILITY_MAP, queries the active board's
board.yaml for the chip variant, and substitutes the chip name into the
template source paths.

For "display":
  - capability_map["display"] points at board.yaml's display.driver
  - sources template expands to:
      $SDK/display/libdisp.c        — chip-agnostic dispatch layer
      $SDK/display/lib{driver}.c    — chip-specific backend (libst7789.c, …)

This keeps app manifests portable: they declare INTENT ("I need a display"),
the build system resolves IMPLEMENTATION at compile time based on the
active board. Moving an app from a CardPuter (ST7789) to a board with an
ILI9341 requires zero edit to the app's manifest — the board's yaml does
the work.

ADR 014 documents this design. Extending the system to a new capability =
adding one entry to CAPABILITY_MAP and shipping the corresponding
$SDK/<capability>/lib*.c files.
"""

from __future__ import annotations

import sys
from pathlib import Path


CapabilitySpec = dict
"""
Each capability is described by a dict with these keys:

  board_key: list[str]
      Path into board.yaml to find the chip/driver name.
      e.g. ["display", "driver"] reads board_cfg["display"]["driver"].

  sources: list[str]
      Source path templates relative to the repo root. Tokens:
        {sdk}     — replaced by str(DUNEOS_ROOT/"sdk")
        {driver}  — replaced by the board.yaml driver value

  description: str
      One-line human-readable, shown in error messages.
"""


CAPABILITY_MAP: dict[str, CapabilitySpec] = {
    "display": {
        "board_key": ["display", "driver"],
        "sources": [
            "{sdk}/display/libdisp.c",
            "{sdk}/display/lib{driver}.c",
        ],
        "description": "Graphical display (chip-specific backend selected per board)",
    },
    # Future capabilities (planned, not yet wired):
    #
    #   "input": {
    #       "board_key": ["input", "kind"],
    #       "sources": [
    #           "{sdk}/input/libinput.c",
    #           "{sdk}/input/lib{driver}.c",
    #       ],
    #       "description": "User input device (keyboard, buttons, encoder)",
    #   },
    #
    #   "sensor:battery": {
    #       "board_key": ["battery", "chip"],
    #       "sources": [
    #           "{sdk}/sensor/libbq{driver}.c",
    #       ],
    #       "description": "Battery fuel gauge sensor",
    #   },
    #
    # Pattern: same nesting; the board's yaml declares what chip is present,
    # the app declares it needs that capability, dbt links the right backend.
}


def _nested_get(d: dict, keys: list[str]):
    """Walk a nested dict via key list. Returns None if any link is missing."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def resolve(capabilities: list[str],
            board_cfg: dict,
            sdk_dir: Path) -> list[Path]:
    """
    Translate an app's declared capabilities into concrete source file paths.

    On unknown capability or missing board.yaml field: prints a clear error
    and exits non-zero (per ADR 014 — build-time, not runtime, failure).

    Args:
        capabilities: list of capability names from app's duneos.yaml.
        board_cfg:    the parsed board.yaml dict (NB: nested under "board").
        sdk_dir:      absolute path to the repo's sdk/ directory.

    Returns: list of resolved source file paths (Path objects, absolute).
    """
    if not capabilities:
        return []

    # The toolchain plugin stores the board YAML under a "board" key plus
    # top-level peripheral sections (display, spi, ...). We look in both.
    def _board_key_get(keys):
        # Try top-level first (e.g. board_cfg["display"]["driver"])
        v = _nested_get(board_cfg, keys)
        if v is not None:
            return v
        # Then under "board": (legacy fallback)
        v = _nested_get(board_cfg.get("board", {}), keys)
        return v

    resolved: list[Path] = []
    for cap in capabilities:
        # Future: parse "display:secondary" → name="display", param="secondary"
        name = cap.strip()
        spec = CAPABILITY_MAP.get(name)
        if spec is None:
            sys.exit(
                f"ERROR: unknown capability '{cap}' in app manifest.\n"
                f"  Known capabilities: {', '.join(sorted(CAPABILITY_MAP))}\n"
                f"  See docs/adr/014-capability-resolution.md for how to add one."
            )

        driver = _board_key_get(spec["board_key"])
        if not driver:
            board_path = ".".join(spec["board_key"])
            sys.exit(
                f"ERROR: app requires capability '{cap}' but the active board\n"
                f"  does not declare it. Expected board.yaml to set:\n"
                f"      {board_path}: <driver-name>\n"
                f"  Capability description: {spec['description']}"
            )

        for tmpl in spec["sources"]:
            path = Path(tmpl.format(sdk=str(sdk_dir), driver=driver))
            if not path.exists():
                sys.exit(
                    f"ERROR: capability '{cap}' wants source {path}\n"
                    f"  but the file does not exist. Either the board declares\n"
                    f"  an unsupported driver ('{driver}') or the SDK is missing\n"
                    f"  the matching lib{driver}.c — add it to {path.parent}/."
                )
            resolved.append(path)

    return resolved
