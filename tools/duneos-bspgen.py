#!/usr/bin/env python3
"""
duneos-bspgen — DuneOS Board Support Package Generator
=======================================================
Reads a board YAML descriptor and generates the board_config.h header that
DuneOS kernel components include to learn pin assignments and hardware options.

Usage:
    python duneos-bspgen.py boards/m5stack-cardputer/board.yaml
    python duneos-bspgen.py boards/esp32s3-devkitc/board.yaml

All outputs land in the same directory as board.yaml (boards/<name>/).
These files are gitignored — run bspgen once per board before building.

The output path defaults to  boards/<board_name>/board_config.h  next to the YAML.

YAML schema (all keys under the top-level 'board:' key):
  name          string   Board identifier (directory name, no spaces)
  cpu           string   esp32s3 | esp32 | esp32s2 | esp32c3
  flash_size_mb int
  psram_size_mb int      0 if no PSRAM
  psram_type    string   opi | qspi   (ignored if psram_size_mb == 0)
  uart:
    - id: N
      tx_pin: N
      rx_pin: N
      default_baud: N
  spi:
    - id: N             SPI host number (2=SPI2_HOST, 3=SPI3_HOST)
      mosi_pin: N
      miso_pin: N
      clk_pin: N
      cs_pin: N        (per-device CS; for SD the cs comes from sd_card section)
      max_freq_hz: N
  sd_card:
      interface: spi
      spi_id: N        references spi[].id
      cd_pin: N        -1 if not present
  i2c:
    - id: N
      sda_pin: N
      scl_pin: N
      freq_hz: N
  display:
      driver: st7789 | ili9341 | ...
      width: N
      height: N
      spi_host: SPI2_HOST | SPI3_HOST
      mosi_pin: N
      clk_pin: N
      cs_pin: N
      dc_pin: N
      rst_pin: N
      bl_pin: N
      freq_hz: N
  keyboard:
      i2c_id: N
      i2c_addr: 0xNN
  battery:
      type: adc_simple | ip5306 | max17043 | bq27220
      adc_unit: ADC_UNIT_1   (adc_simple only, default ADC_UNIT_1)
      adc_channel: N         (adc_simple only — GPIO-to-channel mapping is chip-specific)
      full_mv: N             (adc_simple only, default 4200)
      empty_mv: N            (adc_simple only, default 3300)
      chrg_gpio: N           (adc_simple only, -1 if not wired)
  leds:
    - id: status | ...
      pin: N
      active_high: true|false
      type: gpio | ws2812    (optional, default gpio)

Requirements:
    pip install pyyaml
The tool also generates two additional files alongside board_config.h:

  idf_target.txt       — single-line IDF_TARGET value read by CMakeLists.txt to
                          set the ESP-IDF target without hardcoding it in settings.

  sdkconfig.board      — Kconfig fragment appended to sdkconfig.defaults; selects
                          which DuneOS kernel drivers are compiled in for this board:
                            CONFIG_DUNEOS_DRV_NULL=y
                            CONFIG_DUNEOS_DRV_UART=y
                            CONFIG_DUNEOS_DRV_GPIO=y
                            CONFIG_DUNEOS_DRV_I2C=y
                            CONFIG_DUNEOS_DRV_BATTERY_BQ27220=y
                            # etc.
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime, timezone

try:
    import yaml
except ImportError:
    sys.exit(
        "ERROR: pyyaml not installed.\n"
        "Install it with:  pip install pyyaml\n"
        "Or in the ESP-IDF Python env:  python -m pip install pyyaml"
    )

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

REQUIRED_BOARD_KEYS = ["name", "cpu"]
VALID_CPUS = {"esp32", "esp32s2", "esp32s3",
              "esp32c2", "esp32c3", "esp32c5", "esp32c6",
              "esp32h2", "esp32p4"}

# Architecture classification (same sets used by dbt.py)
XTENSA_CPUS = {"esp32", "esp32s2", "esp32s3"}
RISCV_CPUS  = VALID_CPUS - XTENSA_CPUS


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def validate(board: dict, yaml_path: Path) -> None:
    for k in REQUIRED_BOARD_KEYS:
        if k not in board:
            die(f"'{yaml_path}' is missing required field: board.{k}")
    if board["cpu"] not in VALID_CPUS:
        die(f"Unknown cpu '{board['cpu']}'. Valid: {', '.join(sorted(VALID_CPUS))}")

    if "sd_card" in board:
        sd = board["sd_card"]
        spi_id = sd.get("spi_id")
        if spi_id is not None:
            spi_ids = [s.get("id") for s in board.get("spi", [])]
            if spi_id not in spi_ids:
                die(f"sd_card.spi_id={spi_id} references a SPI bus not defined in spi[]")


# ---------------------------------------------------------------------------
# Header generation
# ---------------------------------------------------------------------------

SPI_HOST_NAMES = {2: "SPI2_HOST", 3: "SPI3_HOST", 1: "SPI1_HOST"}
SPI_HOST_IDS   = {v: k for k, v in SPI_HOST_NAMES.items()}  # "SPI2_HOST" → 2


def _define(name: str, value) -> str:
    if isinstance(value, bool):
        return f"#define {name:<40} {1 if value else 0}"
    if isinstance(value, str):
        return f'#define {name:<40} {value}'
    return f"#define {name:<40} {value}"


def generate(board: dict) -> str:
    lines = []
    name = board["name"]
    cpu  = board["cpu"]

    lines += [
        "#pragma once",
        "",
        f"/*",
        f" * board_config.h for {name}",
        f" * Generated by duneos-bspgen on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f" * DO NOT EDIT — re-generate from boards/{name}/board.yaml",
        f" */",
        "",
    ]

    # ----- identity -----
    arch       = "xtensa" if cpu in XTENSA_CPUS else "riscv"
    em_machine = "EM_XTENSA" if arch == "xtensa" else "EM_RISCV"

    lines += [
        _define("DUNEOS_BOARD_NAME",         f'"{name}"'),
        _define(f"DUNEOS_CPU_{cpu.upper()}", 1),
        _define("DUNEOS_CPU_ARCH",           f'"{arch}"'),
        _define("DUNEOS_ELF_MACHINE",        em_machine),
        _define("DUNEOS_FLASH_SIZE_MB",      board.get("flash_size_mb", 4)),
        _define("DUNEOS_PSRAM_SIZE_MB",      board.get("psram_size_mb", 0)),
        "",
    ]

    # ----- UART -----
    uarts = board.get("uart", [])
    for u in uarts:
        uid = u["id"]
        pfx = f"DUNEOS_UART{uid}"
        lines += [
            f"/* ---------- UART{uid} ---------- */",
            _define(f"{pfx}_TX_PIN",  u["tx_pin"]),
            _define(f"{pfx}_RX_PIN",  u["rx_pin"]),
            _define(f"{pfx}_BAUD",    u.get("default_baud", 115200)),
            "",
        ]

    # ----- SD presence -----
    has_sd = board.get("has_sd", True)
    lines += [_define("DUNEOS_HAS_SD", 1 if has_sd else 0), ""]

    # ----- SD card -----
    sd = board.get("sd_card")
    if sd:
        spi_id  = sd.get("spi_id")
        cd_pin  = sd.get("cd_pin", -1)
        spi_bus = next((s for s in board.get("spi", []) if s.get("id") == spi_id), None)
        if spi_bus:
            host_name = SPI_HOST_NAMES.get(spi_id, f"SPI{spi_id}_HOST")
            freq_khz  = spi_bus.get("max_freq_hz", 20_000_000) // 1000
            lines += [
                "/* ---------- SPI SD card ---------- */",
                _define("DUNEOS_SD_SPI_HOST",  host_name),
                _define("DUNEOS_SD_MOSI_PIN",  spi_bus["mosi_pin"]),
                _define("DUNEOS_SD_MISO_PIN",  spi_bus["miso_pin"]),
                _define("DUNEOS_SD_CLK_PIN",   spi_bus["clk_pin"]),
                _define("DUNEOS_SD_CS_PIN",    spi_bus["cs_pin"]),
                _define("DUNEOS_SD_FREQ_KHZ",  freq_khz),
                _define("DUNEOS_SD_CD_PIN",    cd_pin),
                "",
            ]

    # ----- Raw SPI bus (/dev/spi-1) -----
    # Any spi entry with role: raw is exposed as the user-accessible SPI bus.
    raw_spi = next((s for s in board.get("spi", []) if s.get("role") == "raw"), None)
    if raw_spi:
        spi_id    = raw_spi["id"]
        sd_spi_id = board.get("sd_card", {}).get("spi_id")
        shared    = (spi_id == sd_spi_id)
        host_name = SPI_HOST_NAMES.get(spi_id, f"SPI{spi_id}_HOST")
        lines += [
            "/* ---------- SPI raw bus (/dev/spi-1) ---------- */",
            _define("DUNEOS_HAVE_SPI",          1),
            _define("DUNEOS_SPI1_HOST",         host_name),
            _define("DUNEOS_SPI1_MOSI_PIN",     raw_spi["mosi_pin"]),
            _define("DUNEOS_SPI1_MISO_PIN",     raw_spi.get("miso_pin", -1)),
            _define("DUNEOS_SPI1_CLK_PIN",      raw_spi["clk_pin"]),
            _define("DUNEOS_SPI1_MAX_FREQ_HZ",  raw_spi.get("max_freq_hz", 10_000_000)),
        ]
        if shared:
            # Bus already initialised by vfs.c SD mount — drv_spi skips spi_bus_initialize().
            lines.append(_define("DUNEOS_SPI1_BUS_SHARED", 1))
        lines.append("")

    # ----- I2C -----
    i2c_buses = board.get("i2c", [])
    if i2c_buses:
        lines += [_define("DUNEOS_HAVE_I2C", 1), ""]
    for i2c in i2c_buses:
        iid = i2c["id"]
        pfx = f"DUNEOS_I2C{iid}"
        lines += [
            f"/* ---------- I2C{iid} ---------- */",
            _define(f"{pfx}_SDA_PIN",  i2c["sda_pin"]),
            _define(f"{pfx}_SCL_PIN",  i2c["scl_pin"]),
            _define(f"{pfx}_FREQ_HZ",  i2c.get("freq_hz", 400_000)),
            "",
        ]

    # ----- Battery -----
    batt = board.get("battery")
    if batt:
        btype = batt.get("type", "adc_simple")
        lines += [f"/* ---------- Battery ({btype}) ---------- */"]
        lines += [_define("DUNEOS_HAVE_BATTERY", 1)]
        if btype == "adc_simple":
            lines += [
                _define("DUNEOS_BATTERY_TYPE_ADC_SIMPLE", 1),
                _define("DUNEOS_BATTERY_ADC_UNIT",        batt.get("adc_unit", "ADC_UNIT_1")),
                _define("DUNEOS_BATTERY_ADC_CHANNEL",     batt.get("adc_channel", 0)),
                _define("DUNEOS_BATTERY_VDIV_FACTOR",     batt.get("vdiv_factor", 2)),
                _define("DUNEOS_BATTERY_FULL_MV",         batt.get("full_mv", 4200)),
                _define("DUNEOS_BATTERY_EMPTY_MV",        batt.get("empty_mv", 3300)),
                _define("DUNEOS_BATTERY_CHRG_GPIO",       batt.get("chrg_gpio", -1)),
            ]
        elif btype == "ip5306":
            lines += [_define("DUNEOS_BATTERY_TYPE_IP5306", 1)]
        elif btype == "max17043":
            lines += [_define("DUNEOS_BATTERY_TYPE_MAX17043", 1)]
        elif btype == "bq27220":
            gauge_addr   = batt.get("gauge_addr", 0x55)
            charger_addr = batt.get("charger_addr")
            lines += [
                _define("DUNEOS_BATTERY_TYPE_BQ27220",  1),
                _define("DUNEOS_BATTERY_GAUGE_ADDR",
                        hex(gauge_addr) if isinstance(gauge_addr, int) else gauge_addr),
            ]
            if charger_addr is not None:
                lines += [_define("DUNEOS_BATTERY_CHARGER_ADDR",
                                  hex(charger_addr) if isinstance(charger_addr, int) else charger_addr)]
        lines.append("")

    # ----- Display -----
    disp = board.get("display")
    if disp:
        host_name       = disp.get("spi_host", "SPI3_HOST")
        display_spi_id  = SPI_HOST_IDS.get(host_name, 3)
        sd_spi_id       = board.get("sd_card", {}).get("spi_id", -1)
        bus_shared      = (display_spi_id == sd_spi_id)
        rotation        = disp.get("rotation", 0)
        # ST7789 MADCTL byte: rotation 0=0x00, 1=0x60(MX+MV), 2=0xC0(MY+MX), 3=0xA0(MY+MV)
        _madctl_table   = {0: 0x00, 1: 0x60, 2: 0xC0, 3: 0xA0}
        madctl          = disp.get("madctl", _madctl_table.get(rotation, 0x00))
        # MV bit (0x20) means row/column exchange → CASET/RASET axes are swapped vs portrait
        swap_xy         = 1 if (madctl & 0x20) else 0
        lines += [
            "/* ---------- Display ---------- */",
            _define("DUNEOS_HAVE_DISPLAY",       1),
            _define("DUNEOS_DISPLAY_WIDTH",      disp["width"]),
            _define("DUNEOS_DISPLAY_HEIGHT",     disp["height"]),
            _define("DUNEOS_DISPLAY_SPI_HOST",   host_name),
            _define("DUNEOS_DISPLAY_MOSI_PIN",   disp["mosi_pin"]),
            _define("DUNEOS_DISPLAY_CLK_PIN",    disp["clk_pin"]),
            _define("DUNEOS_DISPLAY_CS_PIN",     disp["cs_pin"]),
            _define("DUNEOS_DISPLAY_DC_PIN",     disp["dc_pin"]),
            _define("DUNEOS_DISPLAY_RST_PIN",    disp["rst_pin"]),
            _define("DUNEOS_DISPLAY_BL_PIN",     disp.get("bl_pin", -1)),
            _define("DUNEOS_DISPLAY_FREQ_HZ",    disp.get("freq_hz", 20_000_000)),
            _define("DUNEOS_DISPLAY_ROTATION",   rotation),
            _define("DUNEOS_DISPLAY_MADCTL",     hex(madctl)),
            _define("DUNEOS_DISPLAY_SWAP_XY",    swap_xy),
            _define("DUNEOS_DISPLAY_COL_OFFSET", disp.get("col_offset", 0)),
            _define("DUNEOS_DISPLAY_ROW_OFFSET", disp.get("row_offset", 0)),
            _define("DUNEOS_DISPLAY_BUS_SHARED", 1 if bus_shared else 0),
            "",
        ]

    # ----- Keyboard matrix (IOMatrix) -----
    kb_matrix = board.get("keyboard_matrix")
    if kb_matrix:
        row_pins = kb_matrix.get("row_select_pins", [])
        col_pins = kb_matrix.get("col_pins", [])
        col_pins_str = "{" + ", ".join(str(p) for p in col_pins) + "}"
        lines += [
            "/* ---------- Keyboard matrix (IOMatrix + 74HC138 row decoder) ---------- */",
            _define("DUNEOS_HAVE_KEYBOARD_MATRIX", 1),
            _define("DUNEOS_KB_ROW_A0_PIN",   row_pins[0] if len(row_pins) > 0 else -1),
            _define("DUNEOS_KB_ROW_A1_PIN",   row_pins[1] if len(row_pins) > 1 else -1),
            _define("DUNEOS_KB_ROW_A2_PIN",   row_pins[2] if len(row_pins) > 2 else -1),
            _define("DUNEOS_KB_COL_PINS",     col_pins_str),
            _define("DUNEOS_KB_NUM_COLS",     len(col_pins)),
            _define("DUNEOS_KB_MATRIX_ROWS",  kb_matrix.get("matrix_rows", 4)),
            _define("DUNEOS_KB_MATRIX_COLS",  kb_matrix.get("matrix_cols", 14)),
            "",
        ]

    # ----- GPIO buttons -----
    buttons = board.get("buttons", [])
    if buttons:
        pins_str  = "{" + ", ".join(str(b["pin"]) for b in buttons) + "}"
        KEY_CODES = {
            "KEY_ENTER": 0x0d, "KEY_ESC": 0x1b, "KEY_UP": 0x41,
            "KEY_DOWN": 0x42, "KEY_LEFT": 0x44, "KEY_RIGHT": 0x43,
        }
        codes_str = "{" + ", ".join(
            hex(KEY_CODES[b["code"]]) if b.get("code") in KEY_CODES else str(b.get("code", 0))
            for b in buttons
        ) + "}"
        lines += [
            "/* ---------- GPIO buttons ---------- */",
            _define("DUNEOS_HAVE_BTN_GPIO",    1),
            _define("DUNEOS_BTN_GPIO_COUNT",   len(buttons)),
            _define("DUNEOS_BTN_GPIO_PINS",    pins_str),
            _define("DUNEOS_BTN_GPIO_CODES",   codes_str),
            "",
        ]

    # ----- Rotary encoder -----
    enc = board.get("encoder")
    if enc:
        lines += [
            "/* ---------- Rotary encoder (quadrature) ---------- */",
            _define("DUNEOS_HAVE_ENCODER",    1),
            _define("DUNEOS_ENCODER_A_PIN",   enc["a_pin"]),
            _define("DUNEOS_ENCODER_B_PIN",   enc["b_pin"]),
            "",
        ]

    # ----- Keyboard (I2C expander, legacy key) -----
    kb = board.get("keyboard")
    if kb:
        lines += [
            "/* ---------- Keyboard ---------- */",
            _define("DUNEOS_KB_I2C_ADDR", hex(kb["i2c_addr"]) if isinstance(kb["i2c_addr"], int) else kb["i2c_addr"]),
            "",
        ]

    # ----- USB -----
    usb = board.get("usb")
    _usb_mode = (usb.get("mode", "") if usb else "").lower()
    if _usb_mode in ("otg", "device"):
        vid = usb.get("vid", 0x303A)
        pid = usb.get("pid", 0x4DAE)
        lines += [
            "/* ---------- USB OTG (TinyUSB) ---------- */",
            _define("DUNEOS_HAVE_USB_OTG", 1),
            _define("DUNEOS_USB_VID", hex(vid) if isinstance(vid, int) else vid),
            _define("DUNEOS_USB_PID", hex(pid) if isinstance(pid, int) else pid),
        ]
        if usb.get("msc"):
            lines.append(_define("DUNEOS_USB_MSC_BACKEND",
                                  f'"{usb["msc"].get("backend", "sd")}"'))
        if usb.get("cdc", {}).get("enabled"):
            lines.append(_define("DUNEOS_USB_CDC_ENABLED", 1))
        lines.append("")
    elif _usb_mode == "jtag":
        lines += ["/* ---------- USB JTAG/Serial (built-in) ---------- */",
                  _define("DUNEOS_HAVE_USB_JTAG", 1), ""]

    # ----- LEDs -----
    for led in board.get("leds", []):
        lid = led["id"].upper()
        lines += [
            f"/* ---------- LED {led['id']} ---------- */",
            _define(f"DUNEOS_LED_{lid}_PIN",         led["pin"]),
            _define(f"DUNEOS_LED_{lid}_ACTIVE_HIGH",  1 if led.get("active_high", True) else 0),
        ]
        if led.get("type") == "ws2812":
            lines.append(_define(f"DUNEOS_LED_{lid}_WS2812", 1))
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# partitions.csv (board-specific partition table)
# ---------------------------------------------------------------------------

# Fixed offsets (bytes)
_OVERHEAD_END   = 0x10000   # nvs + phy_init end; factory starts here
_KERNEL_SIZE    = 0x180000  # 1.5 MB factory (kernel)
_SYSBIN_SIZE    = 0x100000  # 1 MB LittleFS (/flash)
_MIN_FLASH_MB_FOR_SYSBIN = 4


def generate_partitions(board: dict) -> str:
    """Return the content of the board-specific partitions.csv."""
    name          = board["name"]
    flash_size_mb = board.get("flash_size_mb", 8)
    flash_bytes   = flash_size_mb * 1024 * 1024

    factory_offset = _OVERHEAD_END
    factory_end    = factory_offset + _KERNEL_SIZE

    lines = [
        f"# DuneOS partition table — {flash_size_mb}MB flash ({name})",
        "# Name,   Type, SubType,  Offset,    Size",
        f"nvs,      data, nvs,      0x9000,    0x6000",
        f"phy_init, data, phy,      0xf000,    0x1000",
        f"factory,  app,  factory,  0x{factory_offset:x},   0x{_KERNEL_SIZE:x}",
    ]

    if flash_size_mb >= _MIN_FLASH_MB_FOR_SYSBIN:
        sysbin_offset  = factory_end
        storage_offset = sysbin_offset + _SYSBIN_SIZE
        storage_size   = flash_bytes - storage_offset
        lines += [
            f"sysbin,   data, spiffs,   0x{sysbin_offset:x},  0x{_SYSBIN_SIZE:x}",
            f"storage,  data, fat,      0x{storage_offset:x},  0x{storage_size:x}",
        ]
    else:
        # Flash too small for a dedicated sysbin partition
        storage_offset = factory_end
        storage_size   = flash_bytes - storage_offset
        lines += [
            f"storage,  data, fat,      0x{storage_offset:x},  0x{storage_size:x}",
        ]

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# sdkconfig.board (Kconfig driver selection fragment)
# ---------------------------------------------------------------------------

# Maps CPU to IDF_TARGET string.
CPU_IDF_TARGET = {cpu: cpu for cpu in VALID_CPUS}  # esp32s3 → "esp32s3", etc.


def generate_sdkconfig_board(board: dict) -> str:
    """
    Return the full content of sdkconfig.board for this board.

    This file replaces the hand-written sdkconfig.defaults per board.
    It is generated from the board YAML and is gitignored.
    The root sdkconfig.defaults covers project-wide settings only.
    """
    name = board["name"]
    lines = [
        f"# Board: {name}",
        f"# Generated by duneos-bspgen -- do not edit manually.",
        "",
    ]

    # ---- Flash ----
    flash_mb = board.get("flash_size_mb", 4)
    lines += [
        "# Flash",
        f"CONFIG_ESPTOOLPY_FLASHSIZE_{flash_mb}MB=y",
        f'CONFIG_ESPTOOLPY_FLASHSIZE="{flash_mb}MB"',
        "",
    ]

    # ---- PSRAM ----
    psram_mb   = board.get("psram_size_mb", 0)
    psram_type = board.get("psram_type", "opi").lower()
    if psram_mb > 0:
        lines += ["# PSRAM", "CONFIG_SPIRAM=y"]
        if psram_type == "opi":
            lines += [
                "CONFIG_SPIRAM_MODE_OCT=y",
                "CONFIG_SPIRAM_SPEED_80M=y",
                "CONFIG_SPIRAM_FETCH_INSTRUCTIONS=y",
                "CONFIG_SPIRAM_RODATA=y",
            ]
        else:  # qspi
            lines += ["CONFIG_SPIRAM_MODE_QUAD=y"]
    else:
        lines += ["# PSRAM", "CONFIG_SPIRAM=n"]
    lines.append("")

    # ---- Console ----
    # "console" controls the ESP-IDF early-boot serial console (CONFIG_ESP_CONSOLE_*).
    # It is NOT the same as log output: DuneOS always redirects ESP_LOG* to whatever
    # output device makes sense for the board (USB CDC, UART, …) at runtime via
    # esp_log_set_vprintf — independently of this setting.
    #
    # Supported values:  uart | usb_jtag | usb_cdc | none
    #   uart     → CONFIG_ESP_CONSOLE_UART_DEFAULT  (UART0, GPIO43/44 on ESP32-S3)
    #   usb_jtag → CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG
    #   usb_cdc  → CONFIG_ESP_CONSOLE_USB_CDC  (conflicts with TinyUSB — avoid)
    #   none     → CONFIG_ESP_CONSOLE_NONE     (recommended when usb.mode=otg so
    #              that UART0 stays free for application use)
    #
    # When usb.mode=otg the default is "none" (TinyUSB owns GPIO19/20, so JTAG is
    # unavailable, and UART0 must not be claimed by the kernel for its own console).
    # When usb.mode=jtag the console is forced to "usb_jtag" regardless of this field.
    usb = board.get("usb")
    usb_mode = (usb.get("mode", "") if usb else "").lower()
    if usb_mode == "jtag":
        console = "usb_jtag"
    elif usb_mode in ("otg", "device"):
        console = board.get("console", "none").lower()
    else:
        console = board.get("console", "uart").lower()

    if console == "usb_jtag":
        lines += ["# Console", "CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y", ""]
    elif console == "usb_cdc":
        lines += ["# Console", "CONFIG_ESP_CONSOLE_USB_CDC=y", ""]
    elif console == "none":
        lines += ["# Console — none (klog redirected to USB CDC via esp_log_set_vprintf)",
                  "CONFIG_ESP_CONSOLE_NONE=y", ""]
    else:
        lines += ["# Console", "CONFIG_ESP_CONSOLE_UART_DEFAULT=y", ""]

    # ---- Memory protection (must be off for dynamic code loading) ----
    lines += ["# Dynamic app loading requires no MEMPROT",
              "CONFIG_ESP_SYSTEM_MEMPROT_FEATURE=n", ""]

    # ---- DuneOS drivers ----
    lines += ["# DuneOS kernel drivers", "CONFIG_DUNEOS_DRV_NULL=y",
              "CONFIG_DUNEOS_DRV_UART=y", "CONFIG_DUNEOS_DRV_KLOG=y",
              "CONFIG_DUNEOS_DRV_GPIO=y", ""]

    if board.get("i2c"):
        lines += ["CONFIG_DUNEOS_DRV_I2C=y", ""]

    raw_spi = next((s for s in board.get("spi", []) if s.get("role") == "raw"), None)
    if raw_spi:
        lines += ["CONFIG_DUNEOS_DRV_SPI=y", ""]

    batt = board.get("battery")
    if batt:
        btype = batt.get("type", "adc_simple")
        if btype == "adc_simple":
            lines.append("CONFIG_DUNEOS_DRV_BATTERY_ADC_SIMPLE=y")
        elif btype == "bq27220":
            lines.append("CONFIG_DUNEOS_DRV_BATTERY_BQ27220=y")
        lines.append("")

    disp = board.get("display")
    if disp:
        lines += ["CONFIG_DUNEOS_DRV_DISP=y"]
        if psram_mb > 0:
            lines.append("CONFIG_DUNEOS_DRV_FB=y")
        lines.append("")

    has_input = board.get("keyboard_matrix") or board.get("buttons") or board.get("encoder")
    if has_input:
        lines.append("CONFIG_DUNEOS_DRV_INPUT=y")
        if board.get("keyboard_matrix", {}).get("type") == "ioMatrix":
            lines.append("CONFIG_DUNEOS_DRV_INPUT_IOMATRIX=y")
        if board.get("buttons"):
            lines.append("CONFIG_DUNEOS_DRV_INPUT_BTNGPIO=y")
        if board.get("encoder"):
            lines.append("CONFIG_DUNEOS_DRV_INPUT_ENCODER=y")
        lines.append("")

    if board.get("wifi", True):
        lines += ["CONFIG_DUNEOS_DRV_WIFI=y", ""]

    # usb_mode already computed above for console selection
    if usb_mode in ("otg", "device"):  # "device" is the legacy name for "otg"
        lines += ["# USB OTG (TinyUSB — esp_tinyusb managed component)"]
        if usb.get("msc"):
            lines.append("CONFIG_DUNEOS_DRV_USB_MSC=y")
            lines.append("CONFIG_TINYUSB_MSC_ENABLED=y")
        if usb.get("cdc", {}).get("enabled"):
            lines.append("CONFIG_DUNEOS_DRV_USB_CDC=y")
            lines.append("CONFIG_TINYUSB_CDC_ENABLED=y")
        lines.append("")
    elif usb_mode == "jtag":
        lines += ["# USB JTAG/Serial (built-in — no TinyUSB)", ""]

    # ---- Partition table ----
    lines += [
        "# Partition table (layout generated from flash_size_mb)",
        "CONFIG_PARTITION_TABLE_CUSTOM=y",
        f'CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="boards/{name}/partitions.csv"',
        "",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="duneos-bspgen",
        description="Generate board_config.h (+ idf_target.txt + sdkconfig.board) "
                    "from a DuneOS board YAML descriptor",
    )
    parser.add_argument("yaml", help="Path to board YAML file")
    parser.add_argument("--out", help="Output path (default: boards/<name>/board_config.h)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print generated header to stdout, do not write files")
    args = parser.parse_args()

    yaml_path = Path(args.yaml)
    if not yaml_path.exists():
        die(f"File not found: {yaml_path}")

    with open(yaml_path) as f:
        doc = yaml.safe_load(f)

    if "board" not in doc:
        die(f"'{yaml_path}' has no top-level 'board:' key")

    board = doc["board"]
    validate(board, yaml_path)

    header         = generate(board)
    sdkconfig_frag = generate_sdkconfig_board(board)
    partitions     = generate_partitions(board)
    idf_target     = CPU_IDF_TARGET.get(board["cpu"], board["cpu"])

    if args.dry_run:
        print("=== board_config.h ===")
        print(header)
        print("\n=== sdkconfig.board ===")
        print(sdkconfig_frag)
        print("\n=== partitions.csv ===")
        print(partitions)
        print(f"\n=== idf_target.txt ===\n{idf_target}")
        return

    name = board["name"]
    if args.out:
        out_path = Path(args.out)
    else:
        # Output alongside board.yaml (boards/<name>/board_config.h)
        out_path = yaml_path.parent / "board_config.h"

    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path.write_text(header + "\n", encoding='utf-8')
    print(f"Generated: {out_path}")

    sdkconfig_path = out_dir / "sdkconfig.board"
    sdkconfig_path.write_text(sdkconfig_frag, encoding='utf-8')
    print(f"Generated: {sdkconfig_path}")

    partitions_path = out_dir / "partitions.csv"
    partitions_path.write_text(partitions, encoding='utf-8')
    print(f"Generated: {partitions_path}")

    idf_target_path = out_dir / "idf_target.txt"
    idf_target_path.write_text(idf_target + "\n", encoding='utf-8')
    print(f"Generated: {idf_target_path}")


if __name__ == "__main__":
    main()
