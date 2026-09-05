import os
import tempfile
from pathlib import Path

DUNEOS_ROOT       = Path(__file__).resolve().parent.parent.parent
SDK_DIR           = DUNEOS_ROOT / "sdk"
SDK_INCLUDE       = DUNEOS_ROOT / "kernel" / "duneos_kernel" / "include"
LIBDUNE_DIR       = DUNEOS_ROOT / "libdune"

BOARD_FILE        = DUNEOS_ROOT / ".duneos_board"


def write_board_file(board: str, path: "Path | None" = None) -> None:
    """Set the active board, atomically.

    CMakeLists.txt reads .duneos_board on every configure and falls back to a
    hardcoded default when the file is ABSENT, then aborts with FATAL_ERROR if
    that default differs from the board the build dir was configured for. A
    plain truncate-and-write (or a delete-and-recreate) opens a window where a
    concurrent configure sees no file and takes the fallback — which surfaces
    as "Board changed: 'X' → 'esp32s3-devkitc'" and reads exactly like a bench
    defect. os.replace() is atomic on POSIX and Windows, so a reader gets
    either the old content or the new one.
    """
    target = Path(path) if path is not None else BOARD_FILE
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".duneos_board.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(board + "\n")
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

MANIFEST_YAML_FILE = "duneos.yaml"
MANIFEST_JSON_FILE = "manifest.json"   # deprecated — read-only compat
APP_BUILD_DIR      = "build"
APP_ELF_NAME       = "app.elf"
MANIFEST_SECTION   = ".duneos_manifest"

XTENSA_CPUS = {"esp32", "esp32s2", "esp32s3"}
RISCV_CPUS  = {"esp32c2", "esp32c3", "esp32c5", "esp32c6", "esp32h2", "esp32p4"}

CFLAGS_COMMON = [
    # NOT -ffunction-sections / -fdata-sections: the .dap is ET_REL (ld -r), and
    # `ld -r` does no gc-sections, so per-function sections eliminate nothing —
    # they just explode the section count (~528 for the launcher), which inflates
    # the loader's per-load transient buffers (shdrs/shstrtab/symtab/strtab ≈ 60 K
    # read from SD + malloc'd on the general heap). One section per .o instead
    # keeps the same code but shrinks that transient ~5x — and unblocks the app
    # arena (which needs the general heap to keep a big contiguous block free).
    # See docs/audit-ram-2026-06.md P0 and ADR 022.
    "-fno-builtin",
    "-fno-common",
    "-ffreestanding",
    "-nostdlib",
    "-Os",
    "-std=c17",
    # -std=c17 sets __STRICT_ANSI__, which prevents PicoLibc from exposing POSIX
    # functions (clock_gettime, etc.) by default.  Explicitly request POSIX.1-2008.
    "-D_POSIX_C_SOURCE=200809L",
    # PicoLibc uses a TLS variable for errno by default.  Route it through
    # __errno() instead — the same function the kernel exports — so apps
    # generate a reference to __errno rather than an unresolvable TLS symbol.
    "-D__PICOLIBC_ERRNO_FUNCTION=__errno",
    "-Wall",
    "-Wextra",
    "-Werror=implicit-function-declaration",
]

# Xtensa: long calls required when code may run from PSRAM
CFLAGS_XTENSA = ["-mlongcalls"]

# RISC-V: ilp32 ABI; -mno-relax keeps ET_REL output predictable
CFLAGS_RISCV = ["-mabi=ilp32", "-mno-relax"]

LDFLAGS = [
    "-r",          # ET_REL output
    "-nostdlib",
]
