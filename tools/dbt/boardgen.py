"""
boardgen — generate per-app `_board.h` from the active board.yaml + the app's
declared capabilities.

The header lands in `<app_build_dir>/_board.h` and is exposed to the app via
`-I<app_build_dir>` plus the alias header `kernel/duneos_kernel/include/duneos/
board.h` which simply does `#include "_board.h"`. App code writes:

    #include <duneos/board.h>
    int fd = open(DUNEOS_DISPLAY_DEV, O_WRONLY);

Moving the chip to a different bus (e.g. exposing SPI3 as /dev/spi-2 once
Phase 24.6 lands) means `DUNEOS_DISPLAY_DEV` changes value at build time; the
app source and SDK libs (libdisp.c, libgfx.c, …) keep working untouched.

This is ADR 015 Pattern 2.
"""

from __future__ import annotations

from pathlib import Path


def _flatten(board_cfg: dict) -> dict:
    """Return the board YAML's payload — either the top dict, or its `board:`
    sub-section if the file uses that nesting."""
    if "board" in board_cfg and isinstance(board_cfg["board"], dict):
        merged = {**board_cfg.get("board", {})}
        for k, v in board_cfg.items():
            if k != "board" and k not in merged:
                merged[k] = v
        return merged
    return board_cfg


def _display_block(board: dict, lines: list[str]) -> None:
    """Emit display-related defines for the app.

    Two paths:
      * PSRAM board → kernel /dev/fb0 (libgfx Tier B opens it directly).
        Emits DUNEOS_DISPLAY_DEV "/dev/fb0".
      * Non-PSRAM board with display on a raw SPI bus → userspace libst7789
        opens /dev/spi-<N>. Emits DUNEOS_DISPLAY_DEV_INDEX = N (so libst7789
        builds "/dev/spi-<N>") plus the device pins (CS/DC/RST/BL), MADCTL,
        rotation, and CASET/RASET offsets needed by the userspace init
        sequence.
    """
    disp = board.get("display")
    if not isinstance(disp, dict):
        return
    psram = int(board.get("psram_size_mb", 0) or 0)
    lines.append("/* ----- display ----- */")
    if "driver" in disp:
        lines.append(f'#define DUNEOS_DISPLAY_DRIVER    "{disp["driver"]}"')
    if "width" in disp:
        lines.append(f"#define DUNEOS_DISPLAY_WIDTH     {int(disp['width'])}")
    if "height" in disp:
        lines.append(f"#define DUNEOS_DISPLAY_HEIGHT    {int(disp['height'])}")
    if psram > 0:
        lines.append('#define DUNEOS_DISPLAY_DEV       "/dev/fb0"')
    else:
        # Resolve which /dev/spi-N the display lives on, if any.
        spi_id_ref = disp.get("spi_id")
        raw_buses = [s for s in board.get("spi", []) if s.get("role") == "raw"]
        idx = None
        for i, b in enumerate(raw_buses, start=1):
            if b.get("id") == spi_id_ref:
                idx = i; break
        if idx is not None:
            lines.append(f"#define DUNEOS_DISPLAY_DEV_INDEX {idx}")
        # Device pins for libst7789
        for k in ("cs_pin", "dc_pin", "rst_pin", "bl_pin"):
            if k in disp:
                lines.append(f"#define DUNEOS_DISPLAY_{k.upper()}     {int(disp[k])}")
        if "freq_hz" in disp:
            lines.append(f"#define DUNEOS_DISPLAY_FREQ_HZ   {int(disp['freq_hz'])}")
        rotation = int(disp.get("rotation", 0))
        _madctl_table = {0: 0x00, 1: 0x60, 2: 0xC0, 3: 0xA0}
        madctl   = int(disp.get("madctl", _madctl_table.get(rotation, 0x00)))
        swap_xy  = 1 if (madctl & 0x20) else 0
        lines.append(f"#define DUNEOS_DISPLAY_ROTATION    {rotation}")
        lines.append(f"#define DUNEOS_DISPLAY_MADCTL      {hex(madctl)}")
        lines.append(f"#define DUNEOS_DISPLAY_SWAP_XY     {swap_xy}")
        lines.append(f"#define DUNEOS_DISPLAY_COL_OFFSET  {int(disp.get('col_offset', 0))}")
        lines.append(f"#define DUNEOS_DISPLAY_ROW_OFFSET  {int(disp.get('row_offset', 0))}")
    lines.append("")


def _input_block(board: dict, lines: list[str]) -> None:
    if board.get("keyboard_matrix") or board.get("buttons") or board.get("encoder"):
        lines.append("/* ----- input ----- */")
        lines.append('#define DUNEOS_INPUT_DEV         "/dev/input/event0"')
        lines.append("")


def _i2c_block(board: dict, lines: list[str]) -> None:
    i2c = board.get("i2c")
    if not i2c:
        return
    lines.append("/* ----- i2c ----- */")
    # board.yaml schema: i2c: [{id: 0, ...}, ...] OR i2c: {id: 0, ...}
    if isinstance(i2c, list):
        for bus in i2c:
            if isinstance(bus, dict) and "id" in bus:
                lines.append(f'#define DUNEOS_I2C{bus["id"]}_DEV         "/dev/i2c-{bus["id"]}"')
    elif isinstance(i2c, dict) and "id" in i2c:
        lines.append(f'#define DUNEOS_I2C{i2c["id"]}_DEV         "/dev/i2c-{i2c["id"]}"')
    # Convenience: alias I2C0 if a bus 0 exists (most common case).
    lines.append("")


# Battery chip types that the kernel serves directly via /dev/battery0
# (no userspace lib exists for them). Mirrored in capabilities.py
# CAPABILITY_MAP["battery"]["kernel_served"]. Keep both in sync.
KERNEL_SERVED_BATTERY = {"adc_simple"}


def _battery_block(board: dict, lines: list[str]) -> None:
    """Emit battery defines for the userspace battery_daemon + libbattery.

    Emitted for any I2C/SPI gauge chip declared in board.yaml `battery:`
    (bq27220, max17043, ip5306, …). ADC-backed batteries (CardPuter
    `adc_simple`) stay kernel-served via /dev/battery0 and need no
    userspace defines.

    Adding a new gauge chip = ship sdk/sensor/lib<chip>.c that exports
    `duneos_battery_ops`, declare it in board.yaml as
    `battery: {type: <chip>, i2c_id: N, gauge_addr: 0xNN}`, and the
    capability resolver pulls the right backend.
    """
    bat = board.get("battery")
    if not isinstance(bat, dict):
        return
    btype = bat.get("type")
    if not btype or btype in KERNEL_SERVED_BATTERY:
        return
    lines.append(f"/* ----- battery ({btype}, userspace daemon) ----- */")
    lines.append(f'#define DUNEOS_BATTERY_DRIVER        "{btype}"')
    lines.append(f'#define DUNEOS_BATTERY_I2C_DEV       "/dev/i2c-{int(bat.get("i2c_id", 0))}"')
    lines.append(f"#define DUNEOS_BATTERY_GAUGE_ADDR    {hex(int(bat.get('gauge_addr', 0x55)))}")
    lines.append('#define DUNEOS_BATTERY_TMPFS_PATH    "/tmp/battery"')
    lines.append("")


def _storage_block(board: dict, lines: list[str]) -> None:
    has_sd = bool(board.get("sd_card"))
    lines.append("/* ----- storage ----- */")
    lines.append(f"#define DUNEOS_HAS_SD            {1 if has_sd else 0}")
    lines.append('#define DUNEOS_FLASH_MOUNT       "/flash"')
    lines.append('#define DUNEOS_SD_MOUNT          "/sd"')
    lines.append("")


def generate(board_cfg: dict, board_name: str, capabilities: list[str]) -> str:
    """Render the `_board.h` content for an app on `board_name`.

    Currently capabilities don't influence the emitted defines — every section
    is always written. Once the file grows, we can prune sections based on
    declared capabilities to keep the header small.
    """
    _ = capabilities   # reserved for future filtering
    b = _flatten(board_cfg)
    lines: list[str] = [
        "/* DuneOS — auto-generated by dbt for app build. DO NOT EDIT. */",
        f"/* Board: {board_name} */",
        "",
        "#pragma once",
        "",
        f'#define DUNEOS_BOARD_NAME        "{board_name}"',
        "",
    ]
    _storage_block(b, lines)
    _display_block(b, lines)
    _input_block(b, lines)
    _i2c_block(b, lines)
    _battery_block(b, lines)
    return "\n".join(lines)


def write_to(build_dir: Path, board_cfg: dict, board_name: str,
             capabilities: list[str]) -> Path:
    build_dir.mkdir(parents=True, exist_ok=True)
    out = build_dir / "_board.h"
    out.write_text(generate(board_cfg, board_name, capabilities))
    return out
