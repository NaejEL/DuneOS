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

  board_key: list[str]   (optional — omit for marker-only capabilities)
      Path into board.yaml to find the chip/driver name.
      e.g. ["display", "driver"] reads board_cfg["display"]["driver"].
      When absent (or empty), the capability acts as a pure marker:
      no driver dispatch happens, no sources are pulled. Used by
      Phase 24.8 to gate <duneos/board.h> emission in boardgen.py
      for capabilities that don't need a chip-specific backend
      (today: "input" — the daemons ARE the implementation).

  sources: list[str]
      Source path templates relative to the repo root. Tokens:
        {sdk}     — replaced by str(DUNEOS_ROOT/"sdk")
        {driver}  — replaced by the board.yaml driver value
      May be empty for marker-only capabilities.

  kernel_served: set[str]   (optional)
      Driver values that the kernel already serves directly (no userspace
      lib exists for them). When the board declares such a driver,
      resolve() raises CapabilityNotApplicable so the caller can skip
      building this app instead of failing — typical for daemons that are
      only relevant on boards whose chip needs userspace dispatch.

  description: str
      One-line human-readable, shown in error messages.
"""


class CapabilityNotApplicable(Exception):
    """Raised when a capability is technically declared by the active board
    but is served by the kernel (no userspace backend file exists). The caller
    (typically dbt builder) should treat this as a benign skip rather than a
    build failure.
    """


CAPABILITY_MAP: dict[str, CapabilitySpec] = {
    "display": {
        "board_key": ["display", "driver"],
        # libdisp.c is the chip-agnostic dispatcher; it forwards every call
        # to `duneos_disp_ops`, defined by the chip backend (libst7789.c,
        # libssd1306.c, …). Adding a new display chip = ship lib<chip>.c
        # exporting duneos_disp_ops, and the board.yaml's display.driver
        # field selects which one this app links against.
        "sources": [
            "{sdk}/display/libdisp.c",
            "{sdk}/display/lib{driver}.c",
        ],
        "description": "Graphical display via chip-specific userspace backend",
    },
    "input": {
        # Marker-only: no driver dispatch, no SDK lib to pull. The daemons
        # (apps/system/kb_iomatrix.dap, apps/system/btn_gpio.dap) ARE the
        # implementation; this capability exists so boardgen.py can gate
        # the kb/btn pin defines in <duneos/board.h> to apps that ask
        # for them. Apps that only READ from /dev/input/event0 (g_shell,
        # …) get DUNEOS_INPUT_DEV unconditionally — declaring this
        # capability is for the producer side.
        "sources": [],
        "description": "Hardware input source (keyboard matrix, GPIO buttons) — producer side",
    },
    "battery": {
        "board_key": ["battery", "type"],
        # libbattery.c forwards every battery_open/_read/_close call through
        # `duneos_battery_ops`, defined by the chip backend (libbq27220.c,
        # libmax17043.c, libip5306.c, …). Adding a new gauge chip = ship
        # sdk/sensor/lib<chip>.c that exports duneos_battery_ops, and the
        # board.yaml's battery.type field selects which one is linked here.
        "sources": [
            "{sdk}/sensor/libbattery.c",
            "{sdk}/sensor/lib{driver}.c",
        ],
        # ADC voltage-divider batteries are served by the kernel directly
        # (/dev/battery0 via drv_battery_adc_simple.c) — there is no
        # userspace backend to pull in. Apps that declare `capabilities:
        # [battery]` and end up on such a board are skipped gracefully.
        "kernel_served": {"adc_simple"},
        "description": "Battery monitor — userspace backend for I2C/SPI gas gauges",
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

        # Marker-only capability (no board_key) → no sources, no checks.
        # Used to gate boardgen.py block emission without dispatching a
        # chip-specific source file.
        if not spec.get("board_key"):
            continue

        driver = _board_key_get(spec["board_key"])
        if not driver:
            board_path = ".".join(spec["board_key"])
            sys.exit(
                f"ERROR: app requires capability '{cap}' but the active board\n"
                f"  does not declare it. Expected board.yaml to set:\n"
                f"      {board_path}: <driver-name>\n"
                f"  Capability description: {spec['description']}"
            )

        if driver in spec.get("kernel_served", set()):
            raise CapabilityNotApplicable(
                f"capability '{cap}' is kernel-served on this board "
                f"(driver={driver}); no userspace backend to link"
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
