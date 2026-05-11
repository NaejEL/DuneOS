#!/usr/bin/env python3
"""
dbt — DuneBuild Tool
====================
Build tool for DuneOS applications, inspired by ufbt (Flipper Zero).

Usage:
    python dbt.py new <app_name>   Create a new app from template
    python dbt.py build            Build the app in the current directory
    python dbt.py info             Show ELF sections, symbols, relocations
    python dbt.py deploy <path>    Copy built ELF to <path> (SD card mount point)
    python dbt.py clean            Remove build artefacts

The tool produces a single ET_REL ELF suitable for loading by duneos_loader.
No CMake, no ESP-IDF build system — just the Xtensa cross-compiler.

Requirements:
    - Xtensa ESP32-S3 cross-compiler in PATH or pointed to by IDF_PATH
    - Python 3.8+
"""

import argparse
import json
import os
import platform
import shutil
import struct
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DUNEOS_ROOT = Path(__file__).resolve().parent.parent
SDK_INCLUDE  = DUNEOS_ROOT / "components" / "duneos_kernel" / "include"

APP_MANIFEST_FILE  = "manifest.json"
APP_BUILD_DIR      = "build"
APP_ELF_NAME       = "app.elf"
MANIFEST_SECTION   = ".duneos_manifest"

# Compiler flags for relocatable Xtensa apps.
# -fno-builtin: prevents the compiler from inlining libc calls — calls like
#   write() and malloc() must remain as relocatable references so the loader
#   can resolve them against the kernel export table.
CFLAGS = [
    "-mlongcalls",          # Xtensa: enable long calls (required for PSRAM code)
    "-ffunction-sections",  # one ELF section per function → dead-code elimination
    "-fdata-sections",      # one ELF section per data object
    "-fno-builtin",         # keep libc calls as external references
    "-fno-common",          # no common BSS symbols (use explicit definitions)
    "-ffreestanding",       # freestanding environment, no hosted startup
    "-nostdlib",            # don't link any standard library
    "-Os",                  # optimise for size — PSRAM is shared
    "-std=c17",
    "-Wall",
    "-Wextra",
    "-Werror=implicit-function-declaration",
]

# Flags for the partial link step (ld -r → ET_REL output).
LDFLAGS = [
    "-r",                   # produce relocatable output (ET_REL)
    "-nostdlib",            # no startup files or standard libs
    # --gc-sections omitted: incompatible with -r (no entry point defined)
]

# ---------------------------------------------------------------------------
# Toolchain discovery
# ---------------------------------------------------------------------------

def find_toolchain(target: str = "esp32s3") -> dict[str, Path]:
    """
    Locate the Xtensa cross-compiler for the given target.

    ESP-IDF v5 ships a unified toolchain (xtensa-esp-elf-*) where the target
    CPU is selected via -mcpu=. ESP-IDF v4 used target-specific prefixes
    (xtensa-esp32s3-elf-*). We try both, unified first.

    Search order:
      1. Compiler already in PATH
      2. IDF_PATH environment variable → tools directory
      3. Common Espressif install locations (Windows: C:/Espressif)
    """
    is_win = platform.system() == "Windows"
    exe    = ".exe" if is_win else ""

    # Target-specific prefix first (correctly configured for LE ESP32-S3),
    # then unified xtensa-esp-elf which defaults to big-endian without -mcpu.
    prefixes = [f"xtensa-{target}-elf-", "xtensa-esp-elf-"]
    tools    = ["gcc", "ld", "objcopy", "readelf", "nm"]
    keys     = ["cc", "ld", "objcopy", "readelf", "nm"]

    def make_result(bin_dir: Path, prefix: str) -> dict[str, Path]:
        return {k: bin_dir / (prefix + t + exe) for k, t in zip(keys, tools)}

    def try_bin_dir(bin_dir: Path, prefix: str) -> dict[str, Path] | None:
        cc = bin_dir / (prefix + "gcc" + exe)
        if cc.exists():
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
            # Alternative: IDF_PATH/../../tools (when IDF is in esp-idf subdir)
            tools_root = Path(idf_path).parent / "tools" / "xtensa-esp-elf"
        for version_dir in sorted(tools_root.glob("*"), reverse=True):
            bin_dir = version_dir / "xtensa-esp-elf" / "bin"
            for prefix in prefixes:
                result = try_bin_dir(bin_dir, prefix)
                if result:
                    return result

    # 3. Common install locations
    candidates = []
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
        f"ERROR: Xtensa cross-compiler not found.\n"
        f"Tried prefixes: {prefixes}\n"
        "Options:\n"
        "  1. Add the toolchain bin directory to PATH\n"
        "  2. Set IDF_PATH to your ESP-IDF installation\n"
        "  3. Install ESP-IDF (https://docs.espressif.com/projects/esp-idf/en/latest/esp32/get-started/)"
    )


# ---------------------------------------------------------------------------
# App manifest helpers
# ---------------------------------------------------------------------------

def load_manifest(app_dir: Path) -> dict:
    path = app_dir / APP_MANIFEST_FILE
    if not path.exists():
        sys.exit(f"ERROR: {path} not found — is this a DuneOS app directory?")
    with open(path) as f:
        return json.load(f)


def validate_manifest(m: dict) -> None:
    required = ["name", "version", "required_abi_version"]
    missing  = [k for k in required if k not in m]
    if missing:
        sys.exit(f"ERROR: manifest.json missing fields: {', '.join(missing)}")


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def cmd_build(args) -> None:
    app_dir   = Path(".").resolve()
    build_dir = app_dir / APP_BUILD_DIR
    build_dir.mkdir(exist_ok=True)

    manifest = load_manifest(app_dir)
    validate_manifest(manifest)

    tc = find_toolchain()
    cc = tc["cc"]

    # Gather sources
    sources = sorted(app_dir.glob("*.c")) + sorted(app_dir.glob("src/*.c"))
    if not sources:
        sys.exit("ERROR: no .c files found in current directory or src/")

    # Generate a C file containing the manifest, embedded in the right section
    manifest_c = build_dir / "_manifest.c"
    manifest_json = json.dumps(manifest, separators=(",", ":"))
    manifest_c.write_text(
        f'__attribute__((section("{MANIFEST_SECTION}"), used))\n'
        f'const char _duneos_manifest[] = {json.dumps(manifest_json)};\n'
    )
    sources = [manifest_c] + list(sources)

    includes = [
        f"-I{SDK_INCLUDE}",
        f"-I{app_dir}",
    ]

    # Compile each source to a .o
    objects = []
    for src in sources:
        obj = build_dir / (src.stem + ".o")
        compile_cmd = (
            [str(cc)] +
            CFLAGS +
            includes +
            ["-c", str(src), "-o", str(obj)]
        )
        print(f"  CC  {src.name}")
        run(compile_cmd)
        objects.append(obj)

    # Partial link → single ET_REL ELF
    # Use gcc as driver so it sets the correct little-endian Xtensa emulation for ld.
    elf = build_dir / APP_ELF_NAME
    link_cmd = (
        [str(cc)] +
        CFLAGS +
        LDFLAGS +
        [str(o) for o in objects] +
        ["-o", str(elf)]
    )
    print(f"  LD  {elf.name}")
    run(link_cmd)

    size = elf.stat().st_size
    print(f"\nBuild OK → {elf}  ({size} bytes)")
    print(f"Deploy:  python dbt.py deploy <sd_mount_point>")
    print(f"Inspect: python dbt.py info")


# ---------------------------------------------------------------------------
# Info (readelf wrapper)
# ---------------------------------------------------------------------------

def cmd_info(args) -> None:
    app_dir = Path(".").resolve()
    elf     = app_dir / APP_BUILD_DIR / APP_ELF_NAME
    if not elf.exists():
        sys.exit("ERROR: app.elf not found — run 'dbt build' first")

    manifest = load_manifest(app_dir)
    tc = find_toolchain()

    # --- Manifest ---
    print("=== Manifest ===")
    for k, v in manifest.items():
        print(f"  {k}: {v}")

    # --- Memory footprint from section headers ---
    print("\n=== Memory footprint ===")
    re_out = run([str(tc["readelf"]), "-S", "--wide", str(elf)], capture=True)
    text_sz = data_sz = rodata_sz = bss_sz = 0
    for line in re_out.splitlines():
        parts = line.split()
        if len(parts) < 7:
            continue
        # readelf wide output: [ N] Name Type Addr Off Size ES Flg ...
        # Find the Size column (6th zero-padded hex field)
        try:
            sec_name = parts[1] if parts[0].startswith('[') else None
            if sec_name is None:
                continue
            size_hex = parts[5]
            size = int(size_hex, 16)
            if size == 0:
                continue
            if sec_name.startswith(".text") or sec_name.startswith(".literal"):
                text_sz += size
            elif sec_name.startswith(".data"):
                data_sz += size
            elif sec_name.startswith(".rodata"):
                rodata_sz += size
            elif sec_name.startswith(".bss"):
                bss_sz += size
        except (ValueError, IndexError):
            continue

    total_flash = text_sz + data_sz + rodata_sz
    total_ram   = data_sz + bss_sz
    print(f"  .text + .literal : {text_sz:>8} bytes  (IRAM/flash)")
    print(f"  .rodata          : {rodata_sz:>8} bytes  (flash)")
    print(f"  .data            : {data_sz:>8} bytes  (RAM, init from flash)")
    print(f"  .bss             : {bss_sz:>8} bytes  (RAM, zero-init)")
    print(f"  ─────────────────────────────────────")
    print(f"  Total flash      : {total_flash:>8} bytes")
    print(f"  Total RAM        : {total_ram:>8} bytes")
    print(f"  ELF file size    : {elf.stat().st_size:>8} bytes")

    print("\n=== Undefined symbols (kernel must export these) ===")
    nm_out = run([str(tc["nm"]), "-u", str(elf)], capture=True)
    for line in nm_out.splitlines():
        if line.strip():
            print(" ", line.strip())

    print("\n=== Relocations ===")
    run([str(tc["readelf"]), "-r", "--wide", str(elf)], capture=False)


# ---------------------------------------------------------------------------
# Deploy
# ---------------------------------------------------------------------------

def cmd_deploy(args) -> None:
    app_dir = Path(".").resolve()
    elf     = app_dir / APP_BUILD_DIR / APP_ELF_NAME
    if not elf.exists():
        sys.exit("ERROR: app.elf not found — run 'dbt build' first")

    manifest = load_manifest(app_dir)
    app_name = manifest["name"]

    dest_dir = Path(args.path) / "apps"
    dest_dir.mkdir(parents=True, exist_ok=True)

    ext      = ".dap" if not getattr(args, "elf", False) else ".elf"
    dest_elf = dest_dir / f"{app_name}{ext}"
    shutil.copy2(elf, dest_elf)
    print(f"Deployed → {dest_elf}")


# ---------------------------------------------------------------------------
# New (app template)
# ---------------------------------------------------------------------------

APP_TEMPLATE_C = """\
#include <unistd.h>
#include <string.h>

/* DuneOS app entry point. The kernel calls this after loading the ELF. */
void app_main(void)
{{
    const char msg[] = "Hello from {name}!\\n";
    write(STDOUT_FILENO, msg, strlen(msg));

    /* duneos_exit() is provided by the kernel — do not call exit() directly */
    extern void duneos_exit(int code);
    duneos_exit(0);
}}
"""

APP_TEMPLATE_MANIFEST = """\
{{
  "name": "{name}",
  "version": "0.1.0",
  "required_abi_version": 1,
  "permissions": ["uart"]
}}
"""

def cmd_new(args) -> None:
    name    = args.name
    app_dir = Path(name)

    if app_dir.exists():
        sys.exit(f"ERROR: directory '{name}' already exists")

    app_dir.mkdir()
    (app_dir / f"{name}.c").write_text(APP_TEMPLATE_C.format(name=name))
    (app_dir / APP_MANIFEST_FILE).write_text(
        APP_TEMPLATE_MANIFEST.format(name=name)
    )

    print(f"Created app '{name}' in ./{name}/")
    print(f"  {name}/{name}.c")
    print(f"  {name}/manifest.json")
    print(f"\nBuild with:")
    print(f"  cd {name} && python {Path(__file__).relative_to(Path('.').resolve())} build")


# ---------------------------------------------------------------------------
# Clean
# ---------------------------------------------------------------------------

def cmd_clean(args) -> None:
    build_dir = Path(".") / APP_BUILD_DIR
    if build_dir.exists():
        shutil.rmtree(build_dir)
        print(f"Removed {build_dir}")
    else:
        print("Nothing to clean")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(cmd: list, capture: bool = False) -> str:
    cmd = [str(c) for c in cmd]
    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=capture,
            text=True,
        )
        return result.stdout if capture else ""
    except subprocess.CalledProcessError as e:
        if capture and e.stderr:
            print(e.stderr, file=sys.stderr)
        sys.exit(f"ERROR: command failed: {' '.join(cmd)}")
    except FileNotFoundError:
        sys.exit(f"ERROR: executable not found: {cmd[0]}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="dbt",
        description="DuneBuild Tool — build DuneOS applications",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_new = sub.add_parser("new", help="Create a new app from template")
    p_new.add_argument("name", help="App name (also used as directory name)")
    p_new.set_defaults(func=cmd_new)

    p_build = sub.add_parser("build", help="Build the app in the current directory")
    p_build.set_defaults(func=cmd_build)

    p_info = sub.add_parser("info", help="Show ELF sections, symbols and relocations")
    p_info.set_defaults(func=cmd_info)

    p_deploy = sub.add_parser("deploy", help="Copy built app to SD card mount point")
    p_deploy.add_argument("path", help="SD card mount point (e.g. E:\\ or /mnt/sd)")
    p_deploy.add_argument("--elf", action="store_true",
                          help="Use .elf extension instead of .dap")
    p_deploy.set_defaults(func=cmd_deploy)

    p_clean = sub.add_parser("clean", help="Remove build artefacts")
    p_clean.set_defaults(func=cmd_clean)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
