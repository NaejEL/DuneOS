"""`system flash` writes only the image, so it must say when the kernel is old.

This cost a whole debugging session. The launcher appeared to run and draw
nothing: it had opened a display whose SPI bus the kernel had freed on a failed
SD mount. The guard against that free had been merged and never flashed,
because `dbt system flash` writes the sysbin partition and nothing else — by
design, and with no sign anywhere that the other half of the fix was absent.
The only hint on the console was one log line from a debug probe that had
already been deleted from the tree, and noticing it was luck.

So the versions are compared before every image flash. These tests cover the
parsing, which is where a silent wrong answer would hide: a guard that cannot
read a version returns None and says nothing, and "says nothing" is exactly
what the old behaviour was.
"""

import struct
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from dbt import flashimg as FLASHIMG   # noqa: E402  (after the path insert)


# esp_app_desc_t, the first 0x30 bytes: magic, secure_version, reserv1[2],
# version[32]. Built here rather than copied from a real image so the test
# constructs what it asserts against.
MAGIC = 0xABCD5432


def _descriptor(version: bytes, magic: int = MAGIC) -> bytes:
    return (struct.pack("<I", magic)        # magic_word
            + struct.pack("<I", 0)          # secure_version
            + b"\x00" * 8                   # reserv1[2]
            + version.ljust(32, b"\x00"))   # version[32]


def test_a_version_string_is_read_back():
    assert FLASHIMG._app_version_from_bytes(_descriptor(b"e6a2aa2")) == "e6a2aa2"


def test_a_dirty_describe_survives_intact():
    """`git describe` output is what lands here, hyphens and all."""
    v = b"2.1.3-4-gdeadbee-dirty"
    assert FLASHIMG._app_version_from_bytes(_descriptor(v)) == v.decode()


def test_a_full_width_version_is_not_truncated():
    v = b"x" * 31          # 32 bytes with the NUL
    assert FLASHIMG._app_version_from_bytes(_descriptor(v)) == v.decode()


def test_a_wrong_magic_is_refused():
    """Reading the wrong flash offset must not yield a plausible string."""
    assert FLASHIMG._app_version_from_bytes(_descriptor(b"e6a2aa2", magic=0)) is None


def test_a_short_read_is_refused():
    assert FLASHIMG._app_version_from_bytes(_descriptor(b"e6a2aa2")[:0x20]) is None


def test_an_empty_version_is_none_not_empty_string():
    """So the caller's `if not local: return` cannot be fooled into comparing
    an empty string against a real one and reporting a false mismatch."""
    assert FLASHIMG._app_version_from_bytes(_descriptor(b"")) is None


# ----- the comparison, with both reads stubbed ---------------------------

def _run_guard(monkeypatch, local, device, capsys):
    monkeypatch.setattr(FLASHIMG, "_local_kernel_version", lambda: local)
    monkeypatch.setattr(FLASHIMG, "_device_kernel_version",
                        lambda *a, **k: device)
    FLASHIMG.warn_if_kernel_is_stale("esptool", "/dev/null", 460800, "board")
    return capsys.readouterr().out


def test_a_mismatch_warns_and_names_both_versions(monkeypatch, capsys):
    out = _run_guard(monkeypatch, "deadbee", "e6a2aa2", capsys)
    assert "deadbee" in out and "e6a2aa2" in out
    assert "flash kernel" in out


def test_a_match_says_nothing(monkeypatch, capsys):
    assert _run_guard(monkeypatch, "e6a2aa2", "e6a2aa2", capsys) == ""


@pytest.mark.parametrize("local,device", [
    (None, "e6a2aa2"),   # nothing built locally to compare against
    ("e6a2aa2", None),   # device unreadable, or board has no app partition
    (None, None),
])
def test_an_unknown_version_is_silent_rather_than_wrong(monkeypatch, capsys,
                                                        local, device):
    """A guard that cannot tell must not claim the kernel is stale: a false
    warning on every flash is how a real one stops being read."""
    assert _run_guard(monkeypatch, local, device, capsys) == ""


# ----- the partition lookup, on a tree this test builds ------------------
#
# partitions.csv is bspgen output and gitignored, so it is constructed here
# rather than read from boards/ — the repo's own conftest refuses a test that
# opens a generated file, having been burned by exactly that four times.

PARTITIONS = """\
# Name,   Type, SubType, Offset,  Size
nvs,      data, nvs,     0x9000,  0x6000
phy_init, data, phy,     0xf000,  0x1000
factory,  app,  factory, 0x10000, 0x1E0000
sysbin,   data, spiffs,  0x190000,0x100000
"""


def _tree(tmp_path, board="testboard", csv_text=PARTITIONS):
    d = tmp_path / "boards" / board
    d.mkdir(parents=True)
    (d / "partitions.csv").write_text(csv_text, encoding="utf-8")
    return board


def test_the_app_partition_offset_is_found(monkeypatch, tmp_path):
    board = _tree(tmp_path)
    monkeypatch.setattr(FLASHIMG, "DUNEOS_ROOT", tmp_path)
    assert FLASHIMG._app_offset(board) == 0x10000


def test_an_ota_subtype_is_accepted_not_just_factory(monkeypatch, tmp_path):
    """The lookup keys on type=app, so an OTA layout still resolves."""
    csv = PARTITIONS.replace("factory,  app,  factory, 0x10000",
                             "ota_0,    app,  ota_0,   0x20000")
    board = _tree(tmp_path, csv_text=csv)
    monkeypatch.setattr(FLASHIMG, "DUNEOS_ROOT", tmp_path)
    assert FLASHIMG._app_offset(board) == 0x20000


def test_a_table_with_no_app_row_yields_none(monkeypatch, tmp_path):
    csv = "\n".join(
        l for l in PARTITIONS.splitlines()
        if len(l.split(",")) < 2 or l.split(",")[1].strip() != "app")
    board = _tree(tmp_path, csv_text=csv)
    monkeypatch.setattr(FLASHIMG, "DUNEOS_ROOT", tmp_path)
    assert FLASHIMG._app_offset(board) is None


def test_an_unknown_board_yields_none_not_an_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(FLASHIMG, "DUNEOS_ROOT", tmp_path)
    assert FLASHIMG._app_offset("no-such-board") is None
    assert FLASHIMG._app_offset(None) is None
