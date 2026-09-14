"""LEG-31, the general case: what the fragment enables, the header must define.

The per-board check is only worth its runtime if it can fail, so the negative
control lives beside it: the board LEG-31 describes — one I2C bus numbered 1 —
is built here in the test body, pushed past validate(), and must come back
naming the DUNEOS_I2C0_* macros the kernel would not have found.

Nothing here reads a generated file from the tree: every header and fragment is
produced into tmp_path from the tracked board.yaml.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from . import kernel_macros as km

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_bspgen():
    spec = importlib.util.spec_from_file_location(
        "duneos_bspgen", REPO_ROOT / "tools" / "duneos-bspgen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BSPGEN = _load_bspgen()


def _tracked_boards():
    return sorted((REPO_ROOT / "boards").glob("*/board.yaml"))


@pytest.mark.parametrize("yaml_path", _tracked_boards(), ids=lambda p: p.parent.name)
def test_every_macro_the_enabled_drivers_reference_is_defined(yaml_path, tmp_path):
    board = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))["board"]
    out = tmp_path / yaml_path.parent.name
    out.mkdir()
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "duneos-bspgen.py"),
         str(yaml_path), "--out", str(out / "board_config.h")],
        capture_output=True)
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")

    missing = km.missing_macros(board["cpu"],
                               (out / "sdkconfig.board").read_text(encoding="utf-8"),
                               out / "board_config.h")
    assert missing == set(), (
        f"{yaml_path.parent.name} enables drivers referencing undefined board "
        f"macros: {sorted(missing)}")


def test_the_check_fails_on_a_bus_numbered_one(tmp_path):
    """Negative control. Without this the test above is a green light with no
    lamp behind it — an over-broad subtraction would pass all eight boards."""
    board = {"name": "synthetic-i2c1", "cpu": "esp32s3", "flash_size_mb": 8,
             "i2c": [{"id": 1, "sda_pin": 2, "scl_pin": 1, "freq_hz": 400000}]}
    with pytest.raises(SystemExit):
        BSPGEN.validate(board, Path("boards/synthetic-i2c1/board.yaml"))

    header = tmp_path / "board_config.h"
    header.write_text(BSPGEN.generate(board), encoding="utf-8")
    fragment = BSPGEN.generate_sdkconfig_board(board)
    assert "CONFIG_DUNEOS_DRV_I2C=y" in fragment

    missing = km.missing_macros("esp32s3", fragment, header)
    assert {"DUNEOS_I2C0_SDA_PIN", "DUNEOS_I2C0_SCL_PIN",
            "DUNEOS_I2C0_FREQ_HZ"} <= missing


def test_the_check_fails_on_a_board_missing_its_ethernet_pins(tmp_path):
    """Second control, on a different board, driver and CMake file: kincony-A16
    is the only board enabling ETH, and hal_eth.c is reached only through a
    multi-line append in arch/xtensa_esp32/arch.cmake. A parse that reads the
    opening line alone passes this board while defining none of its macros."""
    yaml_path = REPO_ROOT / "boards" / "kincony-A16" / "board.yaml"
    out = tmp_path / "kincony"
    out.mkdir()
    subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "duneos-bspgen.py"),
         str(yaml_path), "--out", str(out / "board_config.h")],
        capture_output=True, check=True)
    fragment = (out / "sdkconfig.board").read_text(encoding="utf-8")
    assert "CONFIG_DUNEOS_DRV_ETH=y" in fragment

    header = out / "board_config.h"
    header.write_text("\n".join(
        line for line in header.read_text(encoding="utf-8").splitlines()
        if not line.startswith("#define DUNEOS_ETH_")), encoding="utf-8")

    missing = km.missing_macros("esp32", fragment, header)
    assert {"DUNEOS_ETH_MDC_PIN", "DUNEOS_ETH_MDIO_PIN", "DUNEOS_ETH_PHY_ADDR",
            "DUNEOS_ETH_CLK_MODE", "DUNEOS_ETH_CLK_GPIO"} <= missing


# --- the parse itself ---------------------------------------------------------
#
# Every assertion above is only as good as the source list behind it, and a
# source that silently fails to parse looks exactly like a source with nothing
# to demand. These three are the lamp.

@pytest.mark.parametrize("cpu", sorted(km.ARCH_DIRS_BY_CPU))
def test_the_cmake_parse_drops_no_source(cpu):
    """Counted against the file's own text, so a source can leave the parse
    only by leaving the file — never by being reformatted."""
    for cmake_path in km.cmake_files(cpu):
        parsed = [t for t, _, _ in km.cmake_sources(cmake_path) if t.endswith(".c")]
        assert parsed == km.quoted_c_tokens(cmake_path), cmake_path


@pytest.mark.parametrize("cpu", sorted(km.ARCH_DIRS_BY_CPU))
def test_every_parsed_source_resolves_to_a_file(cpu):
    for cmake_path in km.cmake_files(cpu):
        for token, path, _gate in km.cmake_sources(cmake_path):
            if path is None:
                assert token in km.UNRESOLVABLE, f"{cmake_path}: {token}"
            else:
                assert path.exists(), f"{cmake_path}: {token} -> {path}"


def test_the_scan_reaches_the_core_and_the_guarded_drivers():
    unconditional = {p.name for p, gates in km.kernel_sources("esp32s3").items()
                     if frozenset() in gates}
    assert {"vfs.c", "vfs_dev.c", "vfs_tmp.c", "supervisor.c", "init.c",
            "task.c", "klog.c", "api.c", "symbols.c"} <= unconditional

    gated = {p.name for p, gates in km.kernel_sources("esp32").items()
             if all(g for g in gates)}
    assert {"drv_i2c.c", "i2c_bus.c", "drv_logic.c", "hal_logic.c",
            "drv_eth.c", "hal_eth.c", "hal_phy.c"} <= gated


# The one direction _eval_cond must never take. A CONFIG_ symbol outside
# CONFIG_DUNEOS_ lives in ESP-IDF's sdkconfig, which this module cannot see;
# reading it as undefined classes the branch dead and stops demanding the macros
# inside it, which is LEG-31 blindness reintroduced through the condition walk
# rather than through the source scan.
def test_an_idf_config_symbol_leaves_its_branch_live():
    defines = {"DUNEOS_X": "1"}
    assert km._eval_cond("defined(CONFIG_SPIRAM)", defines, set()) is None
    assert km._eval_cond("defined(CONFIG_SPIRAM) && DUNEOS_X", defines, set()) is None
    # Ours stay evaluable, in both directions.
    assert km._eval_cond("defined(CONFIG_DUNEOS_DRV_I2C)", defines, set()) == 0
    assert km._eval_cond(
        "defined(CONFIG_DUNEOS_DRV_I2C)", defines, {"CONFIG_DUNEOS_DRV_I2C"}) == 1


# UNRESOLVABLE is an allow-list, and an allow-list nobody rereads is how a check
# stops checking. Pin it to the tree the way GUARDED_EXCEPTIONS is pinned.
def test_the_unresolvable_allowlist_still_names_something_real():
    assert km.UNRESOLVABLE == {"${BLOBS_GEN_C}"}
    seen = {token
            for cpu in km.ARCH_DIRS_BY_CPU
            for cmake_path in km.cmake_files(cpu)
            for token, path, _gate in km.cmake_sources(cmake_path)
            if path is None}
    assert seen == km.UNRESOLVABLE, \
        "UNRESOLVABLE no longer matches the tree; a stale entry is an " \
        "allow-list nobody rereads"
