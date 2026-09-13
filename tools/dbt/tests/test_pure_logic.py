"""SPEC-leg-17/18/19/21 criterion 8 — the dbt logic that decides what gets
built, and rejects what must not.

`validate_manifest` (manifest.py), `decode_perms` (capability_map.py) and
`resolve` (capabilities.py) had no test of their own; each of them silently
shapes an app build.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from dbt import capabilities  # noqa: E402
from dbt.capability_map import (PERM_NAMES, decode_perms,  # noqa: E402
                                required_configs)
from dbt.manifest import validate_manifest  # noqa: E402


# --- validate_manifest ---------------------------------------------------


def minimal():
    return {"name": "demo", "version": "0.1.0", "required_abi_version": 1}


def test_a_minimal_manifest_is_accepted():
    validate_manifest(minimal())


@pytest.mark.parametrize("field", ["name", "version", "required_abi_version"])
def test_a_missing_required_field_aborts_the_build(field):
    m = minimal()
    del m[field]
    with pytest.raises(SystemExit) as exc:
        validate_manifest(m)
    assert field in str(exc.value)


def test_every_missing_field_is_named_at_once():
    with pytest.raises(SystemExit) as exc:
        validate_manifest({})
    message = str(exc.value)
    for field in ("name", "version", "required_abi_version"):
        assert field in message


def test_a_non_string_icon_is_rejected():
    m = minimal() | {"icon": 42}
    with pytest.raises(SystemExit) as exc:
        validate_manifest(m)
    assert "icon" in str(exc.value)


def test_an_over_long_icon_is_rejected():
    m = minimal() | {"icon": "x" * 65}
    with pytest.raises(SystemExit) as exc:
        validate_manifest(m)
    assert "too long" in str(exc.value)


def test_an_icon_at_the_bound_is_accepted():
    validate_manifest(minimal() | {"icon": "x" * 64})


def test_an_unknown_field_warns_but_does_not_abort(capsys):
    validate_manifest(minimal() | {"stak_size": 4096})
    assert "stak_size" in capsys.readouterr().err


# --- decode_perms --------------------------------------------------------


def test_no_permission_decodes_to_nothing():
    assert decode_perms(0) == []


def test_a_single_bit_names_its_permission():
    assert decode_perms(1 << 4) == ["DUNEOS_PERM_NET"]


def test_a_mask_names_every_bit_it_carries():
    # 112 = NET | FS_READ | FS_WRITE — wifi_daemon's declared mask.
    assert set(decode_perms(112)) == {"DUNEOS_PERM_NET", "DUNEOS_PERM_FS_READ",
                                      "DUNEOS_PERM_FS_WRITE"}


def test_an_undefined_bit_is_not_invented():
    highest = max(PERM_NAMES)
    assert decode_perms(highest << 1) == []


def test_the_names_and_the_config_map_cover_the_same_bits():
    from dbt.capability_map import PERM_TO_CONFIG
    assert set(PERM_NAMES) == set(PERM_TO_CONFIG)


def test_vfs_served_permissions_require_no_driver():
    assert required_configs((1 << 5) | (1 << 6)) == set()


def test_a_driver_backed_permission_names_its_config():
    assert required_configs(1 << 3) == {"CONFIG_DUNEOS_DRV_I2C"}


# --- capabilities.resolve ------------------------------------------------


SDK = REPO_ROOT / "sdk"


def test_no_capability_resolves_to_no_source():
    assert capabilities.resolve([], {}, SDK) == []


def test_display_resolves_to_the_board_s_backend():
    paths = capabilities.resolve(["display"], {"display": {"driver": "st7789"}}, SDK)
    names = [p.name for p in paths]
    assert "libdisp.c" in names
    assert "libst7789.c" in names
    assert all(p.is_absolute() for p in paths)


def test_an_unknown_capability_aborts_the_build():
    with pytest.raises(SystemExit) as exc:
        capabilities.resolve(["telepathy"], {}, SDK)
    assert "telepathy" in str(exc.value)


def test_a_board_without_the_hardware_raises_capability_missing():
    with pytest.raises(capabilities.CapabilityMissing) as exc:
        capabilities.resolve(["display"], {}, SDK)
    assert "display" in str(exc.value)
    # builder.py catches the base class; a narrower rescue would miss this.
    assert isinstance(exc.value, capabilities.CapabilityNotApplicable)


def test_a_kernel_served_driver_is_a_skip_not_a_failure(monkeypatch):
    monkeypatch.setitem(capabilities.CAPABILITY_MAP, "display",
                        {"board_key": ["display", "driver"],
                         "sources": ["{sdk}/display/lib{driver}.c"],
                         "kernel_served": {"builtin"}})
    with pytest.raises(capabilities.CapabilityNotApplicable) as exc:
        capabilities.resolve(["display"], {"display": {"driver": "builtin"}}, SDK)
    assert not isinstance(exc.value, capabilities.CapabilityMissing)
    assert "kernel-served" in str(exc.value)


def test_a_marker_only_capability_pulls_no_source(monkeypatch):
    monkeypatch.setitem(capabilities.CAPABILITY_MAP, "marker", {"sources": []})
    assert capabilities.resolve(["marker"], {}, SDK) == []


def test_a_declared_backend_that_does_not_exist_aborts(monkeypatch):
    monkeypatch.setitem(capabilities.CAPABILITY_MAP, "display",
                        {"board_key": ["display", "driver"],
                         "sources": ["{sdk}/display/lib{driver}.c"]})
    with pytest.raises(SystemExit) as exc:
        capabilities.resolve(["display"], {"display": {"driver": "nosuchchip"}}, SDK)
    assert "libnosuchchip.c" in str(exc.value)


def test_the_board_key_is_also_looked_up_under_board():
    paths = capabilities.resolve(["display"],
                                 {"board": {"display": {"driver": "st7789"}}}, SDK)
    assert [p.name for p in paths]
