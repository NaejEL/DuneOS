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


def _input_dev_block(board: dict, lines: list[str]) -> None:
    """Emit only the /dev/input/event0 path — universally useful and cheap.
    Consumers (g_shell, any app reading events) just open this. Always
    emitted regardless of declared capabilities."""
    if board.get("keyboard_matrix") or board.get("buttons") or board.get("encoder"):
        lines.append("/* ----- input device path ----- */")
        lines.append('#define DUNEOS_INPUT_DEV         "/dev/input/event0"')
        lines.append("")


def _input_block(board: dict, lines: list[str]) -> None:
    """Emit the producer-side input defines for kb_iomatrix + btn_gpio daemons.

    Gated by `capabilities: [input]` in the app manifest — only the daemons
    that actually scan hardware need DUNEOS_KB_* + DUNEOS_BTN_GPIO_*. Apps
    that just consume events (g_shell …) don't declare `input` and won't
    see these defines, keeping their <duneos/board.h> small.

    Encoder pins stay in the kernel's board_config.h (the encoder backend
    lives in the kernel — see ADR 010 / Phase 24 hal_encoder).
    """
    kb   = board.get("keyboard_matrix")
    btns = board.get("buttons")
    if not (kb or btns):
        return

    lines.append("/* ----- input producer (kb_iomatrix/btn_gpio daemons) ----- */")

    if isinstance(kb, dict):
        lines.append("/* keyboard_matrix → apps/system/kb_iomatrix daemon */")
        row_pins = kb.get("row_select_pins", [])
        col_pins = kb.get("col_pins", [])
        # 74HC138-style: exactly 3 row select pins for 8 outputs (3-bit address).
        if len(row_pins) >= 3:
            lines.append(f"#define DUNEOS_KB_ROW_A0_PIN     {int(row_pins[0])}")
            lines.append(f"#define DUNEOS_KB_ROW_A1_PIN     {int(row_pins[1])}")
            lines.append(f"#define DUNEOS_KB_ROW_A2_PIN     {int(row_pins[2])}")
        lines.append(f"#define DUNEOS_KB_NUM_COLS       {len(col_pins)}")
        if col_pins:
            cols_init = "{ " + ", ".join(str(int(p)) for p in col_pins) + " }"
            lines.append(f"#define DUNEOS_KB_COL_PINS       {cols_init}")
        lines.append(f"#define DUNEOS_KB_MATRIX_ROWS    {int(kb.get('matrix_rows', 4))}")
        lines.append(f"#define DUNEOS_KB_MATRIX_COLS    {int(kb.get('matrix_cols', 14))}")

    if isinstance(btns, list) and btns:
        lines.append("/* buttons → apps/system/btn_gpio daemon */")
        lines.append(f"#define DUNEOS_BTN_GPIO_COUNT    {len(btns)}")
        pins_init  = "{ " + ", ".join(str(int(b["pin"])) for b in btns) + " }"
        # `code` values come from <duneos/input_ioctl.h> (KEY_*) — emit as
        # bare identifiers so the C preprocessor substitutes them.
        codes_init = "{ " + ", ".join(str(b["code"]) for b in btns) + " }"
        lines.append(f"#define DUNEOS_BTN_GPIO_PINS     {pins_init}")
        lines.append(f"#define DUNEOS_BTN_GPIO_CODES    {codes_init}")

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
    # Optional: the pack's nominal capacity. A gauge that computes state of
    # charge from a design capacity it was never told is confidently wrong, and
    # the figure is a property of the cell soldered to the board, so it belongs
    # in board.yaml. Emitted only when declared — a backend guards on the macro
    # and does nothing without it, which is what keeps every other board's
    # behaviour unchanged.
    # The charger is a second chip on the same bus, and the questions people
    # actually ask ("why does it stop at 96%") are answered by reading both it
    if bat.get("design_capacity_mah") is not None:
        cap = int(bat["design_capacity_mah"])
        if not 1 <= cap <= 32767:
            raise SystemExit(
                f"ERROR: battery.design_capacity_mah={cap} out of range; the "
                "gauge field is a signed 16-bit mAh value (1..32767)")
        lines.append(f"#define DUNEOS_BATTERY_DESIGN_CAPACITY_MAH  {cap}")
    # The current below which the gauge calls the charge finished. Must sit
    # above the charger's cutoff — see _check_taper_agrees_with_charger.
    if bat.get("taper_current_ma") is not None:
        taper = int(bat["taper_current_ma"])
        if not 1 <= taper <= 1000:
            raise SystemExit(
                f"ERROR: battery.taper_current_ma={taper} out of range; the "
                "gauge field accepts 1..1000 mA")
        lines.append(f"#define DUNEOS_BATTERY_TAPER_CURRENT_MA     {taper}")
    lines.append("")


def _charger_block(board: dict, lines: list[str]) -> None:
    """Emit charger defines for libcharger + the chip backend.

    A charger left on its power-on defaults fills whatever cell is attached
    with whatever current the silicon guessed, and stops at whatever taper the
    silicon guessed. On the t-embed those defaults were a 3000 mAh assumption
    and a 256 mA cutoff against a 1300 mAh cell. All three numbers below are
    properties of the board, so they live in board.yaml.
    """
    chg = board.get("charger")
    if not isinstance(chg, dict):
        return
    ctype = chg.get("type")
    if not ctype:
        return

    lines.append(f"/* ----- charger ({ctype}) ----- */")
    lines.append(f'#define DUNEOS_CHARGER_DRIVER        "{ctype}"')
    lines.append(f'#define DUNEOS_CHARGER_I2C_DEV       "/dev/i2c-'
                 f'{int(chg.get("i2c_id", 0))}"')
    lines.append(f"#define DUNEOS_CHARGER_ADDR          "
                 f"{hex(int(chg.get('addr', 0x6B)))}")

    for key, macro, lo, hi, unit in (
        ("charge_voltage_mv",      "DUNEOS_CHARGER_VOLTAGE_MV",      3840, 4608, "mV"),
        ("charge_current_ma",      "DUNEOS_CHARGER_CURRENT_MA",         0, 8128, "mA"),
        ("termination_current_ma", "DUNEOS_CHARGER_TERM_CURRENT_MA",   64, 1024, "mA"),
    ):
        if chg.get(key) is None:
            continue
        v = int(chg[key])
        if not lo <= v <= hi:
            raise SystemExit(
                f"ERROR: charger.{key}={v} {unit} is outside the chip's "
                f"range {lo}..{hi} {unit}")
        lines.append(f"#define {macro:<28} {v}")
    lines.append("")


def _check_taper_agrees_with_charger(board: dict) -> None:
    """The gauge and the charger must agree on what "finished" means.

    The gauge declares charge termination only after seeing the current fall
    below its own taper threshold *while still charging* (SLUUBD4A 4.4.1: two
    40 s windows under Taper Current, with capacity still accumulating). If
    the charger cuts off ABOVE that threshold, the current jumps from the
    cutoff straight to zero, the crossing never happens, the gauge's FC flag
    never sets, and RemainingCapacity is never synced to FullChargeCapacity —
    so the indicator stops short of 100% forever on a battery the charger
    considers full. Measured on the t-embed: charger 256 mA, gauge 100 mA,
    stuck at 96%.

    Two numbers in two different chips, consistent only by agreement. So the
    agreement is checked here rather than left to whoever reads both
    datasheets next.
    """
    bat = board.get("battery") or {}
    chg = board.get("charger") or {}
    taper = bat.get("taper_current_ma")
    term  = chg.get("termination_current_ma")
    if taper is None or term is None:
        return
    if int(taper) <= int(term):
        raise SystemExit(
            f"ERROR: battery.taper_current_ma={taper} must be GREATER than "
            f"charger.termination_current_ma={term}.\n"
            f"  The charger stops at {term} mA, so the current never falls "
            f"below {taper} mA while charging;\n"
            f"  the gauge therefore never detects charge termination and the "
            f"reported charge stops short of 100%.")


def _storage_block(board: dict, lines: list[str]) -> None:
    has_sd = bool(board.get("sd_card"))
    lines.append("/* ----- storage ----- */")
    lines.append(f"#define DUNEOS_HAS_SD            {1 if has_sd else 0}")
    lines.append('#define DUNEOS_FLASH_MOUNT       "/"')
    lines.append('#define DUNEOS_SD_MOUNT          "/sd"')
    lines.append("")


"""
Capability-gated blocks. An app that declares `capabilities: [X, Y, ...]`
in its duneos.yaml gets only the blocks listed here for X, Y, etc., plus
the always-on storage/INPUT_DEV/i2c paths. Apps without any `capabilities:`
field get every block emitted (legacy back-compat — keeps existing apps
building without manifest edits).
"""
CAPABILITY_TO_BLOCKS = {
    "display": (_display_block,),
    "input":   (_input_block,),
    "battery": (_battery_block,),
    "charger": (_charger_block,),
}


def generate(board_cfg: dict, board_name: str, capabilities: list[str]) -> str:
    """Render the `_board.h` content for an app on `board_name`.

    Phase 24.8: apps that declare `capabilities:` in their manifest get a
    pruned header containing only the blocks they need. Apps without any
    capabilities get every block (legacy behavior, unchanged).
    """
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
    # Always-on blocks: paths/identity that any app may legitimately need.
    _check_taper_agrees_with_charger(b)
    _storage_block(b, lines)
    _input_dev_block(b, lines)
    _i2c_block(b, lines)

    # Capability-gated blocks.
    if capabilities:
        wanted = {fn for cap in capabilities
                     for fn in CAPABILITY_TO_BLOCKS.get(cap, ())}
    else:
        # Legacy: app declares no capabilities → emit everything.
        wanted = {fn for fns in CAPABILITY_TO_BLOCKS.values() for fn in fns}

    # Driven by CAPABILITY_TO_BLOCKS, not by a second hand-written list.
    # There used to be a literal tuple here, and it was already one entry
    # behind the map: adding "charger" to the map emitted nothing, silently,
    # because the loop still named three functions. A capability registered in
    # one place and forgotten in the other is the same half-finished pair this
    # repo keeps finding. Dict order is insertion order, so the emitted
    # sequence stays stable and reviewable.
    for fns in CAPABILITY_TO_BLOCKS.values():
        for fn in fns:
            if fn in wanted:
                fn(b, lines)

    return "\n".join(lines)


def write_to(build_dir: Path, board_cfg: dict, board_name: str,
             capabilities: list[str]) -> Path:
    build_dir.mkdir(parents=True, exist_ok=True)
    out = build_dir / "_board.h"
    # encoding="utf-8": the generated header carries non-ASCII glyphs (→) in
    # comments; Windows' default cp1252 codec can't encode them.
    out.write_text(generate(board_cfg, board_name, capabilities), encoding="utf-8")
    return out
