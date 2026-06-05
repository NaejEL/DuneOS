"""ESP-IDF toolchain plugin for dbt.

Handles:
  sdk=esp-idf, arch=xtensa-esp32s3  (ESP32-S3, ESP32-S2)
  sdk=esp-idf, arch=xtensa-esp32    (plain ESP32 — integrated RMII Ethernet MAC)
  sdk=esp-idf, arch=riscv32          (ESP32-C2/C3/C5/C6/H2/P4)
"""

SDK  = "esp-idf"
ARCH = ["xtensa-esp32s3", "xtensa-esp32s2", "xtensa-esp32", "riscv32"]

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from ..constants import (
    DUNEOS_ROOT, CFLAGS_COMMON, CFLAGS_XTENSA, CFLAGS_RISCV, LDFLAGS,
    RISCV_CPUS,
)


# ---------------------------------------------------------------------------
# Toolchain root
# ---------------------------------------------------------------------------

def find_toolchain_root() -> Path | None:
    """Locate the ESP-IDF v6.0.x installation root."""
    from ..setup import find_idf_root
    return find_idf_root()


# ---------------------------------------------------------------------------
# Compiler discovery
# ---------------------------------------------------------------------------

def find_compiler(arch: str, cpu: str) -> dict[str, Path]:
    """Locate the cross-compiler; return {cc, ld, objcopy, readelf, nm}."""
    is_win = platform.system() == "Windows"
    exe    = ".exe" if is_win else ""

    if arch == "riscv32" or cpu in RISCV_CPUS:
        prefixes = ["riscv32-esp-elf-", "riscv32-unknown-elf-"]
    else:
        prefixes = [f"xtensa-{cpu}-elf-", "xtensa-esp-elf-"]

    tool_names = ["gcc", "ld", "objcopy", "readelf", "nm", "ar"]
    keys       = ["cc",  "ld", "objcopy", "readelf", "nm", "ar"]

    def make_result(bin_dir: Path, prefix: str) -> dict[str, Path]:
        return {k: bin_dir / (prefix + t + exe) for k, t in zip(keys, tool_names)}

    def try_bin_dir(bin_dir: Path, prefix: str) -> dict[str, Path] | None:
        if (bin_dir / (prefix + "gcc" + exe)).exists():
            return make_result(bin_dir, prefix)
        return None

    # 1. PATH
    for prefix in prefixes:
        found = shutil.which(prefix + "gcc")
        if found:
            return make_result(Path(found).parent, prefix)

    # 2. Derive from IDF_PATH env var
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


# ---------------------------------------------------------------------------
# Compile / link flags
# ---------------------------------------------------------------------------

def _find_picolibc_include(cc: Path) -> Path | None:
    """Return the PicoLibc include dir bundled with the toolchain, if present.

    ESP-IDF v6 switched from Newlib to PicoLibc. The two libraries use
    different O_* / fcntl flag values (e.g. O_CREAT: newlib=0x200, picolibc=0x40).
    Adding -isystem <picolibc_include> ensures apps see the PicoLibc values.
    """
    candidate = cc.parent.parent / "picolibc" / "include"
    return candidate if candidate.is_dir() else None


def cflags(board_cfg: dict, tc: dict | None = None) -> list[str]:
    """Return compile flags for the given board configuration."""
    b    = board_cfg.get("board", {})
    arch = b.get("arch", "xtensa-esp32s3")
    cpu  = b.get("cpu", "esp32s3")

    flags = CFLAGS_COMMON + (
        CFLAGS_RISCV + [f"-mcpu={cpu}"] if arch == "riscv32"
        else CFLAGS_XTENSA
    )
    if tc:
        picolibc = _find_picolibc_include(Path(str(tc["cc"])))
        if picolibc:
            flags = [f"-isystem{picolibc}"] + flags

    return flags


def ldflags(board_cfg: dict) -> list[str]:
    """Return linker flags for app builds (ET_REL output)."""
    return LDFLAGS


def linker_script(board_dir: Path) -> Path | None:
    """App builds use -r (ET_REL) — no linker script needed."""
    return None


# ---------------------------------------------------------------------------
# Kernel build / flash / monitor
# ---------------------------------------------------------------------------

def _bat_arg(a: str) -> str:
    """Quote one argument for a generated cmd.exe .bat line.

    Without this, paths with spaces (e.g. C:\\Program Files\\...) split into
    multiple args and cmd.exe metacharacters are interpreted unexpectedly.
    """
    if a and not any(c in a for c in ' \t"&|<>^()%!'):
        return a
    return '"' + a.replace('"', '""') + '"'


def _run_idf(idf_root: Path, idf_args: list[str]) -> int:
    """Invoke idf.py with the given arguments from the DuneOS repo root."""
    from ..setup import build_idf_env, idf_python
    is_win = platform.system() == "Windows"
    if is_win:
        import tempfile
        export   = idf_root / "export.bat"
        args_str = " ".join(_bat_arg(a) for a in idf_args)
        # list2cmdline escapes inner quotes with \" which cmd.exe does not
        # understand. Write a temp .bat instead.
        # export.bat changes cwd to IDF_PATH — cd /d back to DUNEOS_ROOT before
        # calling idf.py, otherwise CMake looks for CMakeLists.txt in the wrong dir.
        bat = (
            f'@call "{export}"\r\n'
            f'cd /d "{DUNEOS_ROOT}"\r\n'
            f'idf.py {args_str}\r\n'
            f'exit /b %ERRORLEVEL%\r\n'
        )
        fd, bat_path = tempfile.mkstemp(suffix='.bat')
        try:
            # newline='' so the explicit \r\n in `bat` isn't re-translated to
            # \r\r\n by Windows text-mode newline handling.
            with os.fdopen(fd, 'w', newline='') as f:
                f.write(bat)
            result = subprocess.run(['cmd', '/c', bat_path])
        finally:
            try:
                os.unlink(bat_path)
            except OSError:
                pass
    else:
        env    = build_idf_env(idf_root)
        python = idf_python(idf_root)
        idf_py = idf_root / "tools" / "idf.py"
        if python and idf_py.exists():
            cmd = [str(python), str(idf_py)] + idf_args
        else:
            import shlex
            args_str = " ".join(shlex.quote(a) for a in idf_args)
            cmd      = ["bash", "-c",
                        f'source "{idf_root / "export.sh"}" && idf.py {args_str}']
            env      = None
        result = subprocess.run(cmd, cwd=DUNEOS_ROOT, env=env)

    return result.returncode


def build_kernel(board_dir: Path, build_dir: Path, port: str | None) -> int:
    """Build the DuneOS kernel via idf.py. Returns the exit code."""
    idf_root = find_toolchain_root()
    if not idf_root:
        sys.exit("ERROR: ESP-IDF not found. Run 'dbt setup'.")
    return _run_idf(idf_root, ["build"])


def flash_kernel(build_dir: Path, port: str, baud: int) -> int:
    """Flash the DuneOS kernel via idf.py. Returns the exit code."""
    idf_root = find_toolchain_root()
    if not idf_root:
        sys.exit("ERROR: ESP-IDF not found. Run 'dbt setup'.")
    return _run_idf(idf_root, ["-p", port, "flash"])


def monitor(port: str) -> None:
    """Open the ESP-IDF serial monitor."""
    idf_root = find_toolchain_root()
    if not idf_root:
        sys.exit("ERROR: ESP-IDF not found. Run 'dbt setup'.")
    _run_idf(idf_root, ["-p", port, "monitor"])
