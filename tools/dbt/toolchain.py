import os
import platform
import shutil
import sys
from pathlib import Path

from .constants import DUNEOS_ROOT, XTENSA_CPUS, RISCV_CPUS, CFLAGS_COMMON, CFLAGS_XTENSA, CFLAGS_RISCV

try:
    import yaml as _yaml
    _HAVE_YAML = True
except ImportError:
    _HAVE_YAML = False


def get_board_cpu() -> tuple[str, str]:
    """Return (arch, cpu_variant) for the active board."""
    board_file = DUNEOS_ROOT / ".duneos_board"
    if not board_file.exists():
        return ("xtensa", "esp32s3")

    board_name = board_file.read_text().strip().splitlines()[0].strip()
    yaml_path  = DUNEOS_ROOT / "boards" / f"{board_name}.yaml"

    cpu = None
    if yaml_path.exists() and _HAVE_YAML:
        with open(yaml_path) as f:
            doc = _yaml.safe_load(f)
        cpu = doc.get("board", {}).get("cpu")
    elif yaml_path.exists():
        for line in yaml_path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("cpu:"):
                cpu = stripped.split(":", 1)[1].strip().strip('"\'')
                break

    if not cpu:
        cfg_h = DUNEOS_ROOT / "boards" / board_name / "board_config.h"
        if cfg_h.exists():
            for line in cfg_h.read_text().splitlines():
                if "DUNEOS_CPU_ARCH" in line and '"riscv"' in line:
                    return ("riscv", board_name)
        return ("xtensa", "esp32s3")

    if cpu in RISCV_CPUS:
        return ("riscv", cpu)
    return ("xtensa", cpu)


def find_picolibc_include(tc: dict) -> Path | None:
    """Return the PicoLibc include dir bundled with the toolchain, if present.

    ESP-IDF v6 switched from Newlib to PicoLibc.  The two libraries use
    different O_* / fcntl flag values (e.g. O_CREAT: newlib=0x200, picolibc=0x40).
    Apps compiled with newlib headers would pass the wrong bits to the kernel
    VFS, causing open() with O_CREAT to silently fail with ENOENT.
    Adding -isystem <picolibc_include> ensures apps see the PicoLibc values.
    """
    cc = tc.get("cc")
    if cc is None:
        return None
    # cc = .../xtensa-esp-elf/bin/xtensa-esp32s3-elf-gcc[.exe]
    # picolibc include = .../xtensa-esp-elf/picolibc/include/
    candidate = Path(cc).parent.parent / "picolibc" / "include"
    return candidate if candidate.is_dir() else None


def build_cflags(arch: str, cpu: str, tc: dict | None = None) -> list[str]:
    flags = CFLAGS_COMMON + (CFLAGS_RISCV + [f"-mcpu={cpu}"] if arch == "riscv"
                              else CFLAGS_XTENSA)
    if tc:
        picolibc = find_picolibc_include(tc)
        if picolibc:
            flags = [f"-isystem{picolibc}"] + flags
    return flags


def find_toolchain(arch: str = "xtensa", cpu: str = "esp32s3") -> dict[str, Path]:
    """Locate the cross-compiler for the given architecture and CPU variant."""
    is_win = platform.system() == "Windows"
    exe    = ".exe" if is_win else ""

    if arch == "riscv":
        prefixes = ["riscv32-esp-elf-", "riscv32-unknown-elf-"]
    else:
        prefixes = [f"xtensa-{cpu}-elf-", "xtensa-esp-elf-"]

    tools = ["gcc", "ld", "objcopy", "readelf", "nm"]
    keys  = ["cc",  "ld", "objcopy", "readelf", "nm"]

    def make_result(bin_dir: Path, prefix: str) -> dict[str, Path]:
        return {k: bin_dir / (prefix + t + exe) for k, t in zip(keys, tools)}

    def try_bin_dir(bin_dir: Path, prefix: str) -> dict[str, Path] | None:
        if (bin_dir / (prefix + "gcc" + exe)).exists():
            return make_result(bin_dir, prefix)
        return None

    # 1. PATH
    for prefix in prefixes:
        found = shutil.which(prefix + "gcc")
        if found:
            return make_result(Path(found).parent, prefix)

    # 2. Derive from IDF_PATH
    idf_path = os.environ.get("IDF_PATH")
    if idf_path:
        tools_root = Path(idf_path).parent.parent.parent / "tools" / "xtensa-esp-elf"
        if not tools_root.exists():
            tools_root = Path(idf_path).parent / "tools" / "xtensa-esp-elf"
        for version_dir in sorted(tools_root.glob("*"), reverse=True):
            bin_dir = version_dir / "xtensa-esp-elf" / "bin"
            for prefix in prefixes:
                result = try_bin_dir(bin_dir, prefix)
                if result:
                    return result

    # 3. Common install locations
    if is_win:
        candidates = [
            Path("C:/Espressif/tools/xtensa-esp-elf"),
            Path(os.environ.get("USERPROFILE", "C:/Users")) / ".espressif" / "tools" / "xtensa-esp-elf",
        ]
    else:
        candidates = [
            Path.home() / ".espressif" / "tools" / "xtensa-esp-elf",
            Path("/opt/espressif/tools/xtensa-esp-elf"),
        ]

    for base in candidates:
        if not base.exists():
            continue
        for version_dir in sorted(base.glob("*"), reverse=True):
            bin_dir = version_dir / "xtensa-esp-elf" / "bin"
            for prefix in prefixes:
                result = try_bin_dir(bin_dir, prefix)
                if result:
                    print(f"  [toolchain] {bin_dir} (prefix: {prefix})")
                    return result

    sys.exit(
        f"ERROR: {arch.upper()} cross-compiler not found.\n"
        f"Tried prefixes: {prefixes}\n"
        "Options:\n"
        "  1. Add the toolchain bin directory to PATH\n"
        "  2. Set IDF_PATH to your ESP-IDF installation\n"
        "  3. Install ESP-IDF (https://docs.espressif.com/)"
    )
