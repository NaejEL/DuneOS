"""Toolchain plugin system for dbt.

Each SDK family is a plugin module in this directory:
  esp_idf.py   → sdk: esp-idf  (Xtensa + RISC-V ESP32 families)
  pico_sdk.py  → sdk: pico-sdk (RP2040, Phase 23)
  ...

Usage:
    from .toolchain import get_board_plugin
    plugin, arch, cpu, board_cfg = get_board_plugin()
    tc    = plugin.find_compiler(arch, cpu)
    flags = plugin.cflags(board_cfg, tc)

Plugin interface (each plugin module must export):
    SDK: str                                   matched against board.yaml sdk:
    ARCH: list[str]                            supported arch values
    find_toolchain_root() -> Path | None
    find_compiler(arch, cpu) -> dict           {cc, ld, objcopy, readelf, nm}
    cflags(board_cfg, tc=None) -> list[str]
    ldflags(board_cfg) -> list[str]
    linker_script(board_dir) -> Path | None
    build_kernel(board_dir, build_dir, port, sdkconfig=None) -> int
    flash_kernel(build_dir, port, baud) -> int
    monitor(port) -> None
    run_qemu(build_dir, flash_image, timeout_s, consume, sdkconfig=None,
             psram_mb=0) -> (status, returncode)   # ADR 039 — emulator
"""

from importlib import import_module

try:
    import yaml as _yaml
    _HAVE_YAML = True
except ImportError:
    _HAVE_YAML = False


def _read_board_yaml(board_name: str) -> dict:
    from ..constants import DUNEOS_ROOT
    # Support both flat (boards/<name>.yaml) and nested (boards/<name>/board.yaml) layouts
    for yaml_path in (
        DUNEOS_ROOT / "boards" / board_name / "board.yaml",
        DUNEOS_ROOT / "boards" / f"{board_name}.yaml",
    ):
        if yaml_path.exists():
            break
    else:
        return {}

    if _HAVE_YAML:
        # Board YAMLs contain UTF-8 comments — don't depend on the Windows
        # locale codepage (cp1252 rejects some UTF-8 continuation bytes).
        with open(yaml_path, encoding="utf-8") as f:
            return _yaml.safe_load(f) or {}

    # Fallback without PyYAML: parse only the fields we need
    board: dict = {}
    for line in yaml_path.read_text().splitlines():
        s = line.strip()
        for key in ("arch", "sdk", "cpu", "name"):
            if s.startswith(f"{key}:") and key not in board:
                board[key] = s.split(":", 1)[1].strip().strip('"\'')
    return {"board": board}


def load_plugin(sdk: str):
    """Return the plugin module for a given sdk name.

    sdk="esp-idf" → loads toolchain/esp_idf.py
    sdk="pico-sdk" → loads toolchain/pico_sdk.py
    """
    module_name = sdk.replace("-", "_")
    try:
        return import_module(f".{module_name}", package=__name__)
    except ModuleNotFoundError:
        raise SystemExit(
            f"ERROR: no toolchain plugin for sdk={sdk!r}.\n"
            f"Expected: tools/dbt/toolchain/{module_name}.py\n"
            "To add a new SDK family, create that file implementing the plugin interface."
        )


def get_board_plugin():
    """Return (plugin, arch, cpu, board_cfg) for the active board.

    Reads .duneos_board → boards/<board>/board.yaml → dispatches to plugin.
    Falls back to sdk=esp-idf / arch=xtensa-esp32s3 for boards without those fields.
    """
    from ..constants import DUNEOS_ROOT, RISCV_CPUS

    board_file = DUNEOS_ROOT / ".duneos_board"
    board_name = (
        board_file.read_text().strip().splitlines()[0].strip()
        if board_file.exists() else ""
    )

    board_cfg = _read_board_yaml(board_name) if board_name else {}
    b = board_cfg.get("board", {})

    cpu  = b.get("cpu", "esp32s3")
    sdk  = b.get("sdk", "esp-idf")

    _XTENSA_CPU_ARCH = {
        "esp32s3": "xtensa-esp32s3",
        "esp32s2": "xtensa-esp32s2",
        "esp32":   "xtensa-esp32",
    }
    arch = b.get("arch") or (
        "riscv32" if cpu in RISCV_CPUS
        else _XTENSA_CPU_ARCH.get(cpu, "xtensa-esp32s3")
    )

    # Ensure arch/cpu are present in board_cfg so plugins can rely on them
    board_cfg.setdefault("board", {})
    board_cfg["board"].setdefault("arch", arch)
    board_cfg["board"].setdefault("cpu",  cpu)

    return load_plugin(sdk), arch, cpu, board_cfg
