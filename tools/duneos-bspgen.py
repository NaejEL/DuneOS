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
      adc_unit: 1             (adc_simple only — 1=ADC1, 2=ADC2; default 1)
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
                            CONFIG_DUNEOS_DRV_BATTERY_ADC_SIMPLE=y
                            # etc.
                          (BQ27220 is served by apps/system/battery_daemon now,
                          not by a kernel driver — see 24-debt #1.)
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
# board.yaml spi_id (2=SPI2, 3=SPI3) → spi_host_device_t enum value (SPI2_HOST=1, SPI3_HOST=2)
SPI_HOST_ENUM  = {1: 0, 2: 1, 3: 2}
# board.yaml adc_unit (1=ADC1, 2=ADC2) → ESP-IDF adc_unit_t enum value (0-based)
ADC_UNIT_MAP   = {"ADC_UNIT_1": 1, "ADC_UNIT_2": 2}  # accept legacy string values from old YAMLs


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
            freq_khz  = spi_bus.get("max_freq_hz", 20_000_000) // 1000
            lines += [
                "/* ---------- SPI SD card ---------- */",
                _define("DUNEOS_SD_SPI_HOST",  SPI_HOST_ENUM.get(spi_id, spi_id - 1)),
                _define("DUNEOS_SD_MOSI_PIN",  spi_bus["mosi_pin"]),
                _define("DUNEOS_SD_MISO_PIN",  spi_bus["miso_pin"]),
                _define("DUNEOS_SD_CLK_PIN",   spi_bus["clk_pin"]),
                _define("DUNEOS_SD_CS_PIN",    spi_bus["cs_pin"]),
                _define("DUNEOS_SD_FREQ_KHZ",  freq_khz),
                _define("DUNEOS_SD_CD_PIN",    cd_pin),
                "",
            ]

    # ----- Raw SPI buses (/dev/spi-1, /dev/spi-2, ...) -----
    # Every spi entry with role: raw becomes a user-accessible bus, numbered
    # in declaration order starting at 1. The yaml's `id` is the ESP-IDF host
    # number (2 for SPI2_HOST, 3 for SPI3_HOST) — kept distinct from the
    # /dev/spi-<n> userspace index to allow non-monotonic host assignments
    # (a board can expose SPI3 as /dev/spi-1 without exposing SPI2).
    raw_buses = [s for s in board.get("spi", []) if s.get("role") == "raw"]
    if raw_buses:
        sd_spi_id = board.get("sd_card", {}).get("spi_id")
        lines += [
            "/* ---------- SPI raw buses (userspace via /dev/spi-N) ---------- */",
            _define("DUNEOS_HAVE_SPI",            1),
            _define("DUNEOS_NUM_RAW_SPI_BUSES",   len(raw_buses)),
            "",
        ]
        for idx, bus in enumerate(raw_buses, start=1):
            spi_id = bus["id"]
            shared = (spi_id == sd_spi_id)
            lines += [
                f"/* /dev/spi-{idx} ← board.yaml spi[id={spi_id}] */",
                _define(f"DUNEOS_SPI{idx}_HOST",         SPI_HOST_ENUM.get(spi_id, spi_id - 1)),
                _define(f"DUNEOS_SPI{idx}_MOSI_PIN",     bus["mosi_pin"]),
                _define(f"DUNEOS_SPI{idx}_MISO_PIN",     bus.get("miso_pin", -1)),
                _define(f"DUNEOS_SPI{idx}_CLK_PIN",      bus["clk_pin"]),
                _define(f"DUNEOS_SPI{idx}_MAX_FREQ_HZ",  bus.get("max_freq_hz", 10_000_000)),
            ]
            if shared:
                # Bus already initialised by vfs.c SD mount — drv_spi attaches instead of init.
                lines.append(_define(f"DUNEOS_SPI{idx}_BUS_SHARED", 1))
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
    #
    # Kernel only carries the ADC-divider backend (drv_battery_adc_simple).
    # I2C/SPI gauge chips (bq27220, max17043, ip5306, …) are served by the
    # userspace `apps/system/battery_daemon` + `sdk/sensor/lib<chip>.c`
    # (ADR 009 boundary). For those chips, bspgen emits nothing here — the
    # per-app <duneos/board.h> generated by tools/dbt/boardgen.py carries
    # DUNEOS_BATTERY_I2C_DEV / _GAUGE_ADDR / _TMPFS_PATH for the daemon.
    batt = board.get("battery")
    if batt:
        btype = batt.get("type", "adc_simple")
        lines += [f"/* ---------- Battery ({btype}) ---------- */"]
        lines += [_define("DUNEOS_HAVE_BATTERY", 1)]
        if btype == "adc_simple":
            lines += [
                _define("DUNEOS_BATTERY_TYPE_ADC_SIMPLE", 1),
                _define("DUNEOS_BATTERY_ADC_UNIT",        ADC_UNIT_MAP.get(batt.get("adc_unit"), batt.get("adc_unit", 1))),
                _define("DUNEOS_BATTERY_ADC_CHANNEL",     batt.get("adc_channel", 0)),
                _define("DUNEOS_BATTERY_VDIV_FACTOR",     batt.get("vdiv_factor", 2)),
                _define("DUNEOS_BATTERY_FULL_MV",         batt.get("full_mv", 4200)),
                _define("DUNEOS_BATTERY_EMPTY_MV",        batt.get("empty_mv", 3300)),
                _define("DUNEOS_BATTERY_CHRG_GPIO",       batt.get("chrg_gpio", -1)),
            ]
        # All other types (bq27220, max17043, ip5306, ...) are userspace —
        # see boardgen.py for the defines exposed to the daemon.
        lines.append("")

    # ----- Display -----
    disp = board.get("display")
    if disp:
        # SPI bus resolution.
        #   Modern style: display.spi_id points at a spi[] entry; mosi/clk
        #     come from that entry. Used by CardPuter (Phase 24-debt #6).
        #   Legacy style: display.spi_host + display.mosi_pin/clk_pin inline.
        #     Used by T-Embed (display on the SD-shared SPI2). Kept working
        #     so existing boards don't need migrating until they need to.
        if "spi_id" in disp:
            spi_id_ref = disp["spi_id"]
            matching = next((s for s in board.get("spi", []) if s.get("id") == spi_id_ref), None)
            if not matching:
                raise SystemExit(
                    f"ERROR: board.yaml display.spi_id={spi_id_ref} "
                    f"does not match any spi: entry"
                )
            display_spi_id = spi_id_ref
            disp_mosi = matching["mosi_pin"]
            disp_clk  = matching["clk_pin"]
        else:
            host_name      = disp.get("spi_host", "SPI3_HOST")
            display_spi_id = SPI_HOST_IDS.get(host_name, 3)
            disp_mosi      = disp["mosi_pin"]
            disp_clk       = disp["clk_pin"]

        sd_spi_id    = board.get("sd_card", {}).get("spi_id", -1)
        bus_shared   = (display_spi_id == sd_spi_id)
        rotation     = disp.get("rotation", 0)
        # ST7789 MADCTL byte: rotation 0=0x00, 1=0x60(MX+MV), 2=0xC0(MY+MX), 3=0xA0(MY+MV)
        _madctl_table = {0: 0x00, 1: 0x60, 2: 0xC0, 3: 0xA0}
        madctl        = disp.get("madctl", _madctl_table.get(rotation, 0x00))
        # MV bit (0x20) means row/column exchange → CASET/RASET axes are swapped vs portrait
        swap_xy       = 1 if (madctl & 0x20) else 0

        # If the display's SPI host is one of the raw buses we exposed
        # earlier, compute its /dev/spi-<N> index so libst7789 (userspace)
        # can open the right device. Otherwise (no raw bus matches, e.g.
        # T-Embed where the display SPI is the SD-shared bus consumed by
        # the kernel), no DEV_INDEX is emitted and the kernel framebuffer
        # path takes over.
        raw_dev_index = None
        for idx, b in enumerate(raw_buses, start=1):
            if b.get("id") == display_spi_id:
                raw_dev_index = idx
                break

        lines += [
            "/* ---------- Display ---------- */",
            _define("DUNEOS_HAVE_DISPLAY",       1),
            _define("DUNEOS_DISPLAY_WIDTH",      disp["width"]),
            _define("DUNEOS_DISPLAY_HEIGHT",     disp["height"]),
            _define("DUNEOS_DISPLAY_SPI_HOST",   SPI_HOST_ENUM.get(display_spi_id, display_spi_id - 1)),
            _define("DUNEOS_DISPLAY_MOSI_PIN",   disp_mosi),
            _define("DUNEOS_DISPLAY_CLK_PIN",    disp_clk),
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
        ]
        if raw_dev_index is not None:
            lines.append(_define("DUNEOS_DISPLAY_DEV_INDEX", raw_dev_index))
        lines.append("")

    # ----- Keyboard matrix / GPIO buttons -----
    # The kernel no longer scans either (24-debt #5 moved kb_iomatrix.c and
    # btn_gpio.c to userspace daemons). We emit only a presence flag so other
    # bspgen code paths can branch on it if needed; the actual pin/keymap
    # defines for the userspace daemons live in the per-app <duneos/board.h>
    # generated by tools/dbt/boardgen.py.
    if board.get("keyboard_matrix"):
        lines += [_define("DUNEOS_HAVE_KEYBOARD_MATRIX", 1), ""]
    if board.get("buttons"):
        lines += [_define("DUNEOS_HAVE_BTN_GPIO", 1), ""]

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

    # ----- Recovery pin (Phase 24.7 safe-boot) -----
    # If declared, the kernel checks this pin at boot and uses
    # /flash/init.yaml.safe (usb_shell only) when it's held — escape hatch
    # from a bricked init.yaml without re-flashing. The pin may overlap with
    # a regular `buttons:` entry; the check happens before any userspace
    # daemon configures the pin, and is non-destructive (input + pull-up).
    rec = board.get("recovery_pin")
    if rec is not None:
        # Accept both shorthand `recovery_pin: 6` and dict
        # `recovery_pin: {pin: 6, active_low: true}`.
        if isinstance(rec, int):
            rec_pin, rec_active_low = rec, True
        else:
            rec_pin        = int(rec["pin"])
            rec_active_low = bool(rec.get("active_low", True))
        lines += [
            "/* ---------- Recovery / safe-boot pin ---------- */",
            _define("DUNEOS_HAVE_RECOVERY_PIN",       1),
            _define("DUNEOS_RECOVERY_PIN",            rec_pin),
            _define("DUNEOS_RECOVERY_PIN_ACTIVE_LOW", 1 if rec_active_low else 0),
            "",
        ]

    # ----- GPIO expanders -----
    # Schema: gpio_expanders: [{type, i2c_addr, i2c_id?, pins?, direction?}, ...]
    # Emitted as an X-macro table (DUNEOS_GPIOCHIP_LIST) so each chip-type driver
    # iterates it — no per-chip #ifdef, scales to any count across any I2C bus.
    expanders = board.get("gpio_expanders", [])
    if expanders:
        lines += [
            "/* ---------- GPIO expanders ---------- */",
            _define("DUNEOS_GPIO_DIR_INPUT",  0),
            _define("DUNEOS_GPIO_DIR_OUTPUT", 1),
            "",
            "/* X(id, type, i2c_bus, i2c_addr, pins, direction) — iterated in-driver. */",
            "#define DUNEOS_GPIOCHIP_LIST(X) \\",
        ]
        rows = []
        for idx, exp in enumerate(expanders):
            chip_id  = idx + 1   # gpiochip0 is the native chip
            addr     = exp["i2c_addr"]
            addr_str = hex(addr) if isinstance(addr, int) else addr
            bus      = exp.get("i2c_id", 0)
            pins     = exp.get("pins", 16)
            direction = str(exp.get("direction", "input")).strip().lower()
            if direction not in ("input", "output"):
                raise SystemExit(
                    f"board.yaml: gpio_expander '{exp.get('type')}' @ {addr_str}: "
                    f"direction must be 'input' or 'output', got {exp.get('direction')!r}")
            dir_macro = "DUNEOS_GPIO_DIR_INPUT" if direction == "input" else "DUNEOS_GPIO_DIR_OUTPUT"
            rows.append(f'    X({chip_id}, "{exp["type"]}", {bus}, {addr_str}, {pins}, {dir_macro})')
        for i, row in enumerate(rows):
            lines.append(row + (" \\" if i < len(rows) - 1 else ""))
        lines.append("")


    # ----- Network interfaces -----
    # Currently supports: interface=rmii (ESP32 RMII MAC, hal_eth.c backend).
    # SPI Ethernet (interface=spi, e.g. W5500) will add a second type here.
    _PHY_TYPE_MAP = {
        "lan8720": "DUNEOS_ETH_PHY_LAN8720",
        "ksz8081": "DUNEOS_ETH_PHY_KSZ8081",
        "rtl8201": "DUNEOS_ETH_PHY_RTL8201",
        "ip101":   "DUNEOS_ETH_PHY_IP101",
    }
    _CLK_MODE_DEFAULTS = {
        "rmii": "ETH_CLOCK_GPIO0_IN",
    }
    for net in board.get("network", []):
        iface = net.get("interface", "rmii").lower()
        if iface != "rmii":
            continue
        phy_type = _PHY_TYPE_MAP.get(net.get("type", "").lower())
        if not phy_type:
            continue
        clk_raw = net.get("clk_mode", _CLK_MODE_DEFAULTS.get(iface, "ETH_CLOCK_GPIO0_IN"))
        # Translate legacy ETH_CLOCK_GPIO*_OUT/IN to IDF v6 EMAC_ enum names.
        clk_mode_v6 = "EMAC_CLK_OUT" if "_OUT" in clk_raw else "EMAC_CLK_EXT_IN"
        try:
            clk_gpio_num = int(clk_raw.replace("ETH_CLOCK_GPIO", "").split("_")[0])
        except (ValueError, IndexError):
            clk_gpio_num = 0
        lines += [
            "/* ---------- Ethernet RMII ---------- */",
            _define("DUNEOS_HAVE_ETHERNET",    1),
            _define("DUNEOS_ETH_PHY_TYPE",     phy_type),
            _define("DUNEOS_ETH_PHY_ADDR",     net.get("phy_addr", 0)),
            _define("DUNEOS_ETH_MDC_PIN",      net["phy_mdc_pin"]),
            _define("DUNEOS_ETH_MDIO_PIN",     net["phy_mdio_pin"]),
            _define("DUNEOS_ETH_CLK_MODE",     clk_mode_v6),
            _define("DUNEOS_ETH_CLK_GPIO",     clk_gpio_num),
            "",
        ]
        break  # only one RMII interface per board

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
              "CONFIG_DUNEOS_DRV_GPIO=y",
              "CONFIG_DUNEOS_DRV_LOGIC=y", ""]

    if board.get("i2c"):
        lines += ["CONFIG_DUNEOS_DRV_I2C=y", ""]

    raw_buses = [s for s in board.get("spi", []) if s.get("role") == "raw"]
    if raw_buses:
        lines += ["CONFIG_DUNEOS_DRV_SPI=y", ""]

    batt = board.get("battery")
    if batt:
        btype = batt.get("type", "adc_simple")
        if btype == "adc_simple":
            lines.append("CONFIG_DUNEOS_DRV_BATTERY_ADC_SIMPLE=y")
            lines.append("")
        # Any other type (bq27220, max17043, ip5306, …) is userspace-served
        # by apps/system/battery_daemon; nothing to enable kernel-side.

    disp = board.get("display")
    if disp:
        # Display backend selection:
        #   PSRAM board → kernel /dev/fb0 (drv_fb_st7789).
        #   No PSRAM, display SPI = one of the raw buses → userspace libst7789
        #     opens /dev/spi-N. No kernel display driver needed.
        # The drv_disp_st7789.c (/dev/disp0) path was retired in 24-debt #6+#2.
        if psram_mb > 0:
            lines.append("CONFIG_DUNEOS_DRV_FB=y")
        lines.append("")

    # Keyboard-matrix and GPIO-button polling backends live in userspace
    # since 24-debt #5 (apps/system/kb_iomatrix.dap, apps/system/btn_gpio.dap).
    # The kernel keeps only /dev/input/event0 + encoder (HW counter/ISR).
    has_input = board.get("keyboard_matrix") or board.get("buttons") or board.get("encoder")
    if has_input:
        lines.append("CONFIG_DUNEOS_DRV_INPUT=y")
        if board.get("encoder"):
            lines.append("CONFIG_DUNEOS_DRV_INPUT_ENCODER=y")
        lines.append("")

    for exp in board.get("gpio_expanders", []):
        etype = exp.get("type", "").lower().replace("-", "_")
        config_key = f"CONFIG_DUNEOS_DRV_GPIOCHIP_{etype.upper()}=y"
        if config_key not in lines:
            lines.append(config_key)
    if board.get("gpio_expanders"):
        lines.append("")


    for net in board.get("network", []):
        if net.get("interface", "").lower() == "rmii":
            lines += [
                "# Ethernet RMII",
                "CONFIG_DUNEOS_DRV_ETH=y",
                "CONFIG_ETH_ENABLED=y",
                "CONFIG_ETH_USE_ESP32_EMAC=y",
            ]
            phy = net.get("type", "").lower()
            # Keep in sync with _PHY_TYPE_MAP (board_config.h side) so every PHY
            # the kernel can be compiled for also gets its IDF Kconfig enabled.
            _phy_kconfig = {
                "lan8720": "CONFIG_ETH_PHY_LAN8720=y",
                "ksz8081": "CONFIG_ETH_PHY_KSZ8081=y",
                "rtl8201": "CONFIG_ETH_PHY_RTL8201=y",
                "ip101":   "CONFIG_ETH_PHY_IP101=y",
            }
            if phy in _phy_kconfig:
                lines.append(_phy_kconfig[phy])
            # Generic ETH_CLOCK_GPIO<N>_<IN|OUT> parsing — mirrors the
            # board_config.h DUNEOS_ETH_CLK_GPIO emission so the two stay in
            # sync for any pin (GPIO0 in, GPIO0/16/17 out on ESP32).
            clk_mode = net.get("clk_mode", "ETH_CLOCK_GPIO0_IN")
            try:
                clk_gpio = int(clk_mode.replace("ETH_CLOCK_GPIO", "").split("_")[0])
            except (ValueError, IndexError):
                clk_gpio = 0
            if "_OUT" in clk_mode:
                lines += ["CONFIG_ETH_RMII_CLK_OUTPUT=y",
                          f"CONFIG_ETH_RMII_CLK_OUT_GPIO={clk_gpio}"]
            else:
                lines.append("CONFIG_ETH_RMII_CLK_INPUT=y")
            lines.append("")
            break

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

    # ---- Raw Kconfig overrides ----
    # board.yaml kconfig: list is emitted verbatim — use for board-specific
    # tuning that bspgen doesn't model structurally (e.g. stack sizes).
    for kv in board.get("kconfig", []):
        lines.append(str(kv))
    if board.get("kconfig"):
        lines.append("")

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
