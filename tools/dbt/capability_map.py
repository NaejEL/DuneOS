"""
capability_map — bridge between app PERM bits and kernel CONFIG flags.

An app declares `permissions: <bitmask>` in its duneos.yaml. Each bit is a
DUNEOS_PERM_* constant (kernel/duneos_kernel/include/duneos/abi.h). For the
app to run, the kernel built for this board MUST have the corresponding
CONFIG_DUNEOS_DRV_* compiled in — otherwise the syscall succeeds but the
device isn't there.

`dbt system check` reads each app's permission bitmask, looks up the
required kernel CONFIGs in this map, and validates them against the
active board's sdkconfig.board. Failures abort the build BEFORE
spending time compiling.

Permissions that don't require a kernel-side driver (FS_READ, FS_WRITE
— served by VFS core which is always built) map to an empty list.

Keep in sync with DUNEOS_PERM_* in abi.h.
"""

from __future__ import annotations

# Bit value → human name (for error messages). Order matches abi.h.
PERM_NAMES = {
    1 << 0:  "DUNEOS_PERM_GPIO",
    1 << 1:  "DUNEOS_PERM_UART",
    1 << 2:  "DUNEOS_PERM_SPI",
    1 << 3:  "DUNEOS_PERM_I2C",
    1 << 4:  "DUNEOS_PERM_NET",
    1 << 5:  "DUNEOS_PERM_FS_READ",
    1 << 6:  "DUNEOS_PERM_FS_WRITE",
    1 << 7:  "DUNEOS_PERM_BATTERY",
    1 << 8:  "DUNEOS_PERM_INPUT",
    1 << 9:  "DUNEOS_PERM_FB",
    1 << 10: "DUNEOS_PERM_NET_RAW",
}

# Bit value → list of CONFIG_DUNEOS_DRV_* that MUST be =y for the
# permission to be functional. Empty list = served by VFS core only.
PERM_TO_CONFIG = {
    1 << 0:  ["CONFIG_DUNEOS_DRV_GPIO"],
    1 << 1:  ["CONFIG_DUNEOS_DRV_UART"],
    1 << 2:  ["CONFIG_DUNEOS_DRV_SPI"],
    1 << 3:  ["CONFIG_DUNEOS_DRV_I2C"],
    1 << 4:  ["CONFIG_DUNEOS_DRV_WIFI"],
    1 << 5:  [],   # FS_READ — VFS core, always available
    1 << 6:  [],   # FS_WRITE — same
    1 << 7:  [],   # BATTERY — either kernel ADC driver OR userspace daemon writes /tmp/battery
                   # We don't fail-check here; the userspace path doesn't need any CONFIG.
    1 << 8:  ["CONFIG_DUNEOS_DRV_INPUT"],
    1 << 9:  ["CONFIG_DUNEOS_DRV_FB"],
    1 << 10: ["CONFIG_DUNEOS_DRV_RAW80211"],
}


def decode_perms(mask: int) -> list[str]:
    """Return the human-readable list of permissions in `mask`."""
    return [name for bit, name in PERM_NAMES.items() if mask & bit]


def required_configs(mask: int) -> set[str]:
    """Return the set of CONFIG_DUNEOS_DRV_* required by the permission mask."""
    out: set[str] = set()
    for bit, cfgs in PERM_TO_CONFIG.items():
        if mask & bit:
            out.update(cfgs)
    return out


def check_app(app_name: str, mask: int, sdkconfig_lines: list[str]) -> list[str]:
    """
    Check that the kernel sdkconfig.board enables the CONFIGs required by
    the app's permission mask. Returns a list of human-readable error
    strings (empty if all good).
    """
    enabled = {ln.split("=", 1)[0]
               for ln in sdkconfig_lines
               if "=y" in ln and not ln.lstrip().startswith("#")}
    missing = required_configs(mask) - enabled
    if not missing:
        return []
    perms = decode_perms(mask)
    return [
        f"app '{app_name}' declares {p} but the board's kernel profile is "
        f"missing {' or '.join(cfg for cfg in sorted(required_configs(mask)) if cfg in missing)}"
        for p in perms
        if any(c in missing for c in PERM_TO_CONFIG.get(
            next(bit for bit, name in PERM_NAMES.items() if name == p), []))
    ]
