"""The two shared-bus flags must agree, because one bus cannot be both.

A display and an SD card can sit on the same SPI host. When they do, the SD
mount owns spi_bus_initialize() and the display merely attaches, so vfs.c must
not free the bus when the mount fails — an absent card would otherwise take the
panel down with it. Two macros carry that fact: DUNEOS_SD_BUS_SHARED, read by
vfs.c, and DUNEOS_DISPLAY_BUS_SHARED, read by st7789_hw.c.

They used to be computed independently, and only the display side understood
the legacy `display.spi_host` declaration form. A legacy-style board therefore
got DISPLAY_BUS_SHARED=1 (attach, don't init) together with SD_BUS_SHARED=0
(free the bus on mount failure) — the display attaching to a bus that was about
to be freed under it. The t-embed escaped it only by accident, through a
separate `role: raw` entry on the same host.

Both now come from one helper, and these tests pin that: the flags agree, and
the legacy form alone is enough to raise them.
"""

import importlib.util
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_bspgen():
    spec = importlib.util.spec_from_file_location(
        "duneos_bspgen", REPO_ROOT / "tools" / "duneos-bspgen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BSPGEN = _load_bspgen()

BASE = {"name": "testboard", "cpu": "esp32s3", "flash_size_mb": 8}

# An SD card on host 2, declared the way every board declares one.
SD_ON_2 = {"spi_id": 2, "cs_pin": 13, "freq_khz": 40000}
SPI2 = {"id": 2, "role": "master", "mosi_pin": 9, "miso_pin": 10,
        "clk_pin": 11, "cs_pin": 13}

# The same display, declared the two ways bspgen accepts.
DISP_LEGACY = {"driver": "st7789", "width": 320, "height": 170,
               "spi_host": "SPI2_HOST", "mosi_pin": 9, "clk_pin": 11,
               "cs_pin": 41, "dc_pin": 16, "rst_pin": 40}
DISP_MODERN = {"driver": "st7789", "width": 320, "height": 170,
               "spi_id": 2, "cs_pin": 41, "dc_pin": 16, "rst_pin": 40}


def _board(display, *, sd=True, spi_role="master"):
    b = dict(BASE)
    b["spi"] = [dict(SPI2, role=spi_role)]
    if sd:
        b["sd_card"] = dict(SD_ON_2)
    if display:
        b["display"] = dict(display)
    return b


def _flags(board):
    """(sd_bus_shared, display_bus_shared) as generate() actually emits them."""
    text = BSPGEN.generate(board)
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "#define" and parts[1] in (
                "DUNEOS_SD_BUS_SHARED", "DUNEOS_DISPLAY_BUS_SHARED"):
            out[parts[1]] = int(parts[2])
    return out.get("DUNEOS_SD_BUS_SHARED"), out.get("DUNEOS_DISPLAY_BUS_SHARED")


# ----- the helper is the single answer ------------------------------------

def test_both_declaration_styles_resolve_to_the_same_host():
    assert BSPGEN._display_spi_id(_board(DISP_LEGACY)) == 2
    assert BSPGEN._display_spi_id(_board(DISP_MODERN)) == 2


def test_no_display_resolves_to_none():
    assert BSPGEN._display_spi_id(_board(None)) is None


def test_a_display_without_an_explicit_host_defaults_to_three():
    disp = {k: v for k, v in DISP_LEGACY.items() if k != "spi_host"}
    assert BSPGEN._display_spi_id(_board(disp)) == 3


# ----- the regression this file exists for --------------------------------

def test_a_legacy_display_alone_on_the_sd_host_shares_the_bus():
    """The case that was silently wrong.

    No `role: raw` anywhere — the display is the only other tenant, and it is
    declared the legacy way. Before the two sites were unified this emitted
    SD_BUS_SHARED=0, so vfs.c freed the bus on a missing card while
    st7789_hw.c had only attached to it.
    """
    sd_shared, disp_shared = _flags(_board(DISP_LEGACY, spi_role="master"))
    assert sd_shared == 1
    assert disp_shared == 1


def test_the_modern_form_behaves_identically():
    assert _flags(_board(DISP_MODERN, spi_role="master")) == (1, 1)


def test_a_display_on_another_host_does_not_share():
    disp = dict(DISP_LEGACY, spi_host="SPI3_HOST")
    sd_shared, disp_shared = _flags(_board(disp))
    assert sd_shared == 0
    assert disp_shared == 0


def test_a_raw_tenant_still_shares_without_a_display():
    """The other reason a bus is shared must keep working on its own."""
    sd_shared, _ = _flags(_board(None, spi_role="raw"))
    assert sd_shared == 1


# ----- every real board, from tracked YAML only ---------------------------

def test_no_real_board_has_the_two_flags_disagreeing():
    """Completeness over the tree. Reads board.yaml, never a generated file."""
    checked = 0
    for yml in sorted((REPO_ROOT / "boards").glob("*/board.yaml")):
        board = yaml.safe_load(yml.read_text(encoding="utf-8"))["board"]
        if not board.get("display") or not board.get("sd_card"):
            continue
        sd_shared, disp_shared = _flags(board)
        assert sd_shared == disp_shared, (
            f"{yml.parent.name}: SD_BUS_SHARED={sd_shared} but "
            f"DISPLAY_BUS_SHARED={disp_shared} for one and the same bus")
        checked += 1
    assert checked >= 2, (
        f"only {checked} boards carry both a display and an SD card; this test "
        "stops proving anything below two")
