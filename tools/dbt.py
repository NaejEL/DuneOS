#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dbt — DuneBuild Tool
====================
Build tool for DuneOS applications, inspired by ufbt (Flipper Zero).

Usage (single app, run from app directory):
    python dbt.py new <app_name>   Create a new app from template
    python dbt.py build            Build the app in the current directory
    python dbt.py info             Show ELF sections, symbols, relocations
    python dbt.py deploy <path>    Copy built ELF to <path> (SD card mount point)
    python dbt.py clean            Remove build artefacts

Usage (all apps, run from repo root):
    python dbt.py buildall [path]          Build all system apps; deploy if path given
    python dbt.py buildall [path] --clean  Clean first, then build + deploy
    python dbt.py buildall [path] --examples  Also build examples/

App category is inferred from directory:
    system/bin/*   → deployed to <sd>/bin/
    everything else → deployed to <sd>/apps/

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

try:
    import yaml as _yaml
    _HAVE_YAML = True
except ImportError:
    _HAVE_YAML = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DUNEOS_ROOT = Path(__file__).resolve().parent.parent
SDK_INCLUDE  = DUNEOS_ROOT / "components" / "duneos_kernel" / "include"

APP_MANIFEST_FILE  = "manifest.json"
APP_BUILD_DIR      = "build"
APP_ELF_NAME       = "app.elf"
MANIFEST_SECTION   = ".duneos_manifest"

# CPU sets — determines toolchain prefix and CFLAGS.
XTENSA_CPUS = {"esp32", "esp32s2", "esp32s3"}
RISCV_CPUS  = {"esp32c2", "esp32c3", "esp32c5", "esp32c6", "esp32h2", "esp32p4"}

# Flags common to all architectures.
# -fno-builtin keeps libc calls as external references so the loader resolves
# them against the kernel export table rather than inlining them.
CFLAGS_COMMON = [
    "-ffunction-sections",  # one ELF section per function → dead-code elimination
    "-fdata-sections",      # one ELF section per data object
    "-fno-builtin",         # keep libc calls as external references
    "-fno-common",          # no common BSS symbols (use explicit definitions)
    "-ffreestanding",       # freestanding environment, no hosted startup
    "-nostdlib",            # don't link any standard library
    "-Os",                  # optimise for size
    "-std=c17",
    "-Wall",
    "-Wextra",
    "-Werror=implicit-function-declaration",
]

# Xtensa-specific: long calls are required when code may run from PSRAM
# (IRAM alias is > 1 MB away from DRAM).
CFLAGS_XTENSA = ["-mlongcalls"]

# RISC-V: ilp32 ABI (all 32-bit ESP32 RISC-V chips).
# -mno-relax suppresses R_RISCV_RELAX hints — the DuneOS loader ignores them
# anyway, but keeping them out makes the ET_REL output more predictable.
CFLAGS_RISCV = ["-mabi=ilp32", "-mno-relax"]

# Flags for the partial link step (ld -r → ET_REL output).
LDFLAGS = [
    "-r",                   # produce relocatable output (ET_REL)
    "-nostdlib",            # no startup files or standard libs
    # --gc-sections omitted: incompatible with -r (no entry point defined)
]

# ---------------------------------------------------------------------------
# Toolchain discovery
# ---------------------------------------------------------------------------

def get_board_cpu() -> tuple[str, str]:
    """
    Return (arch, cpu_variant) for the active board.

    Resolution order:
      1. DUNEOS_ROOT/.duneos_board  → board name
      2. DUNEOS_ROOT/boards/<name>.yaml  → board.cpu
      3. Falls back to ("xtensa", "esp32s3") if nothing is found.

    Returns e.g. ("xtensa", "esp32s3") or ("riscv", "esp32c3").
    """
    board_file = DUNEOS_ROOT / ".duneos_board"
    if not board_file.exists():
        return ("xtensa", "esp32s3")  # safe default

    board_name = board_file.read_text().strip().splitlines()[0].strip()
    yaml_path  = DUNEOS_ROOT / "boards" / f"{board_name}.yaml"

    cpu = None
    if yaml_path.exists() and _HAVE_YAML:
        with open(yaml_path) as f:
            doc = _yaml.safe_load(f)
        cpu = doc.get("board", {}).get("cpu")
    elif yaml_path.exists():
        # Minimal fallback: grep 'cpu:' without yaml library
        for line in yaml_path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("cpu:"):
                cpu = stripped.split(":", 1)[1].strip().strip('"\'')
                break

    if not cpu:
        # Last resort: parse board_config.h for DUNEOS_CPU_ARCH
        cfg_h = DUNEOS_ROOT / "boards" / board_name / "board_config.h"
        if cfg_h.exists():
            for line in cfg_h.read_text().splitlines():
                if "DUNEOS_CPU_ARCH" in line and '"riscv"' in line:
                    return ("riscv", board_name)
        return ("xtensa", "esp32s3")

    if cpu in RISCV_CPUS:
        return ("riscv", cpu)
    return ("xtensa", cpu)


def build_cflags(arch: str, cpu: str) -> list[str]:
    """Return the complete CFLAGS list for the given architecture."""
    if arch == "riscv":
        return CFLAGS_COMMON + CFLAGS_RISCV + [f"-mcpu={cpu}"]
    else:
        return CFLAGS_COMMON + CFLAGS_XTENSA


def find_toolchain(arch: str = "xtensa", cpu: str = "esp32s3") -> dict[str, Path]:
    """
    Locate the cross-compiler for the given architecture and CPU variant.

    For Xtensa (esp32, esp32s2, esp32s3):
      - Tries target-specific prefix xtensa-<cpu>-elf- first (correctly tuned).
      - Falls back to unified xtensa-esp-elf- (requires -mcpu=<cpu>).

    For RISC-V (esp32c3, esp32c6, esp32h2, esp32p4, …):
      - Tries ESP-IDF v5 unified riscv32-esp-elf-.
      - Falls back to generic riscv32-unknown-elf- (e.g. Ubuntu packages).

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

    if arch == "riscv":
        prefixes = ["riscv32-esp-elf-", "riscv32-unknown-elf-"]
    else:
        prefixes = [f"xtensa-{cpu}-elf-", "xtensa-esp-elf-"]

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
        f"ERROR: {arch.upper()} cross-compiler not found.\n"
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
# App discovery (for buildall)
# ---------------------------------------------------------------------------

def _is_bin_app(app_dir: Path) -> bool:
    """True if app lives under system/bin/ — deploys to <sd>/bin/."""
    bin_root = DUNEOS_ROOT / "system" / "bin"
    try:
        app_dir.relative_to(bin_root)
        return True
    except ValueError:
        return False


def find_apps(include_examples: bool = False) -> list[tuple[Path, bool]]:
    """
    Return [(app_dir, is_bin), ...] for all apps that have a manifest.json.
    Searches system/ and optionally examples/.
    is_bin drives the deploy destination (bin/ vs apps/).
    """
    search_roots = [DUNEOS_ROOT / "system"]
    if include_examples:
        search_roots.append(DUNEOS_ROOT / "examples")

    results = []
    for base in search_roots:
        if not base.exists():
            continue
        for manifest_path in sorted(base.rglob(APP_MANIFEST_FILE)):
            app_dir = manifest_path.parent
            results.append((app_dir, _is_bin_app(app_dir)))
    return results


# ---------------------------------------------------------------------------
# Core build / clean / deploy — operate on an explicit app_dir
# ---------------------------------------------------------------------------

def build_single(app_dir: Path, arch: str, cpu: str, tc: dict) -> bool:
    """
    Build the app at app_dir.  Returns True on success, False on failure.
    Prints progress to stdout; errors go to stderr.
    """
    build_dir = app_dir / APP_BUILD_DIR
    build_dir.mkdir(exist_ok=True)

    try:
        manifest = load_manifest(app_dir)
        validate_manifest(manifest)
    except SystemExit as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        return False

    cc     = tc["cc"]
    cflags = build_cflags(arch, cpu)

    sources = sorted(app_dir.glob("*.c")) + sorted(app_dir.glob("src/*.c"))
    if not sources:
        print(f"  ERROR: no .c files in {app_dir}", file=sys.stderr)
        return False

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

    objects = []
    for src in sources:
        obj = build_dir / (src.stem + ".o")
        compile_cmd = (
            [str(cc)] + cflags + includes + ["-c", str(src), "-o", str(obj)]
        )
        print(f"  CC  {src.name}")
        try:
            run(compile_cmd)
        except SystemExit:
            return False
        objects.append(obj)

    elf = build_dir / APP_ELF_NAME
    link_cmd = (
        [str(cc)] + cflags + LDFLAGS + [str(o) for o in objects] + ["-o", str(elf)]
    )
    print(f"  LD  {elf.name}")
    try:
        run(link_cmd)
    except SystemExit:
        return False

    print(f"  OK  {elf.stat().st_size} bytes")
    return True


def clean_single(app_dir: Path) -> None:
    build_dir = app_dir / APP_BUILD_DIR
    if build_dir.exists():
        shutil.rmtree(build_dir)
        print(f"  cleaned  {app_dir.relative_to(DUNEOS_ROOT)}")


def deploy_single(app_dir: Path, sd_path: Path, is_bin: bool) -> bool:
    elf = app_dir / APP_BUILD_DIR / APP_ELF_NAME
    if not elf.exists():
        print(f"  ERROR: {elf} not found — was build successful?", file=sys.stderr)
        return False

    try:
        manifest = load_manifest(app_dir)
    except SystemExit as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        return False

    subdir   = "bin" if is_bin else "apps"
    dest_dir = sd_path / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest     = dest_dir / f"{manifest['name']}.dap"
    shutil.copy2(elf, dest)
    print(f"  ->  {dest}")
    return True


# ---------------------------------------------------------------------------
# Build (single app, run from app directory)
# ---------------------------------------------------------------------------

def cmd_build(args) -> None:
    app_dir  = Path(".").resolve()
    arch, cpu = get_board_cpu()
    tc        = find_toolchain(arch, cpu)
    print(f"  [arch] {arch} / {cpu}")

    ok = build_single(app_dir, arch, cpu, tc)
    if ok:
        elf = app_dir / APP_BUILD_DIR / APP_ELF_NAME
        print(f"\nBuild OK → {elf}")
        print(f"Deploy:  python dbt.py deploy <sd_mount_point>")
        print(f"Inspect: python dbt.py info")
    else:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Info (readelf wrapper)
# ---------------------------------------------------------------------------

def cmd_info(args) -> None:
    app_dir = Path(".").resolve()
    elf     = app_dir / APP_BUILD_DIR / APP_ELF_NAME
    if not elf.exists():
        sys.exit("ERROR: app.elf not found — run 'dbt build' first")

    manifest = load_manifest(app_dir)
    arch, cpu = get_board_cpu()
    tc        = find_toolchain(arch, cpu)

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
# Deploy (single app, run from app directory)
# ---------------------------------------------------------------------------

def cmd_deploy(args) -> None:
    app_dir  = Path(".").resolve()
    is_bin   = getattr(args, "bin", False) or _is_bin_app(app_dir)
    sd_path  = Path(args.path)

    elf = app_dir / APP_BUILD_DIR / APP_ELF_NAME
    if not elf.exists():
        sys.exit("ERROR: app.elf not found — run 'dbt build' first")

    manifest = load_manifest(app_dir)
    app_name = manifest["name"]

    subdir   = "bin" if is_bin else "apps"
    # --bin flag overrides auto-detection; --elf overrides extension
    if getattr(args, "bin", False):
        subdir = "bin"
    dest_dir = sd_path / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext      = ".dap" if not getattr(args, "elf", False) else ".elf"
    dest_elf = dest_dir / f"{app_name}{ext}"
    shutil.copy2(elf, dest_elf)
    print(f"Deployed → {dest_elf}")


# ---------------------------------------------------------------------------
# Clean (single app, run from app directory)
# ---------------------------------------------------------------------------

def cmd_clean(args) -> None:
    build_dir = Path(".") / APP_BUILD_DIR
    if build_dir.exists():
        shutil.rmtree(build_dir)
        print(f"Removed {build_dir}")
    else:
        print("Nothing to clean")


# ---------------------------------------------------------------------------
# Build-all (run from repo root or anywhere)
# ---------------------------------------------------------------------------

def cmd_buildall(args) -> None:
    sd_path  = Path(args.path) if args.path else None
    do_clean = getattr(args, "clean", False)

    apps = find_apps(include_examples=getattr(args, "examples", False))
    if not apps:
        sys.exit("ERROR: no apps found under system/ (or examples/ with --examples)")

    arch, cpu = get_board_cpu()
    tc        = find_toolchain(arch, cpu)
    print(f"Board: {arch} / {cpu}  |  {len(apps)} apps found\n")

    ok_list   = []
    fail_list = []

    for app_dir, is_bin in apps:
        rel = app_dir.relative_to(DUNEOS_ROOT)
        print(f"{'='*60}")
        print(f"  {rel}")
        print(f"{'='*60}")

        if do_clean:
            clean_single(app_dir)

        if not build_single(app_dir, arch, cpu, tc):
            fail_list.append(rel)
            print()
            continue

        if sd_path:
            if not deploy_single(app_dir, sd_path, is_bin):
                fail_list.append(rel)
                print()
                continue

        ok_list.append(rel)
        print()

    # Summary
    total = len(apps)
    n_ok  = len(ok_list)
    n_fail = len(fail_list)
    print(f"{'='*60}")
    print(f"  Results: {n_ok}/{total} OK" + (f"  {n_fail} FAILED" if n_fail else ""))
    if fail_list:
        print("\n  Failed apps:")
        for p in fail_list:
            print(f"    ✗  {p}")
    if sd_path and ok_list:
        print(f"\n  Deployed {n_ok} app(s) to {sd_path}")
    print(f"{'='*60}")

    if fail_list:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Clean-all (run from repo root or anywhere)
# ---------------------------------------------------------------------------

def cmd_cleanall(args) -> None:
    apps = find_apps(include_examples=True)
    if not apps:
        print("No apps found.")
        return
    for app_dir, _ in apps:
        clean_single(app_dir)
    print(f"Cleaned {len(apps)} app(s).")


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
  "permissions": 0
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
    # Ensure stdout/stderr can emit UTF-8 on Windows (CP1252 by default).
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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
    p_deploy.add_argument("--bin", action="store_true",
                          help="Deploy to <sd>/bin/ instead of <sd>/apps/ (system utilities)")
    p_deploy.set_defaults(func=cmd_deploy)

    p_clean = sub.add_parser("clean", help="Remove build artefacts")
    p_clean.set_defaults(func=cmd_clean)

    p_buildall = sub.add_parser(
        "buildall",
        help="Build all system apps (and optionally deploy + examples)",
    )
    p_buildall.add_argument(
        "path", nargs="?", default=None,
        help="SD card mount point — if given, deploy after building",
    )
    p_buildall.add_argument(
        "--clean", action="store_true",
        help="Clean each app's build directory before building",
    )
    p_buildall.add_argument(
        "--examples", action="store_true",
        help="Also build apps under examples/",
    )
    p_buildall.set_defaults(func=cmd_buildall)

    p_cleanall = sub.add_parser("cleanall", help="Remove build artefacts for all apps")
    p_cleanall.set_defaults(func=cmd_cleanall)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
