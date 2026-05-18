"""
dbt — DuneBuild Tool
====================
Build tool for DuneOS applications.

Run without arguments → full-screen TUI (textual).

Direct CLI commands:
    dbt setup                   Wizard: board, port, ESP-IDF
    dbt flash kernel            Build + flash the DuneOS kernel
    dbt flash sysbin            Build apps + flash LittleFS partition
    dbt flash sd <path>         Build all apps + deploy to SD card
    dbt build                   Build the app in the current directory
    dbt deploy <path>           Copy built app to SD card mount point
    dbt info                    Show ELF sections, symbols, relocations
    dbt new <name>              Create a new app from template
    dbt buildall [path]         Build all system apps (+ deploy if path given)
    dbt clean / cleanall        Remove build artefacts
"""

import argparse
import sys
from pathlib import Path

from .constants import APP_BUILD_DIR, APP_ELF_NAME, MANIFEST_YAML_FILE, DUNEOS_ROOT
from .manifest import load_manifest, find_apps, _is_bin_app
from .toolchain import get_board_plugin
from .builder import build_single, clean_single, run
from .deploy import deploy_single
from .flashimg import cmd_flashimg
from .setup import cmd_setup
from .kernel import cmd_flash_kernel
from .bspgen import cmd_bspgen


# ---------------------------------------------------------------------------
# App template
# ---------------------------------------------------------------------------

_APP_TEMPLATE_C = """\
#include <unistd.h>
#include <string.h>

/* DuneOS app entry point. The kernel calls this after loading the ELF. */
void app_main(void)
{{
    const char msg[] = "Hello from {name}!\\n";
    write(STDOUT_FILENO, msg, strlen(msg));

    extern void duneos_exit(int code);
    duneos_exit(0);
}}
"""

_APP_TEMPLATE_YAML = """\
name: {name}
version: "0.1.0"
required_abi_version: 1
permissions: 0
stack_size: 4096
"""


# ---------------------------------------------------------------------------
# Existing commands (unchanged)
# ---------------------------------------------------------------------------

def cmd_new(args) -> None:
    name    = args.name
    app_dir = Path(name)

    if app_dir.exists():
        sys.exit(f"ERROR: directory '{name}' already exists")

    app_dir.mkdir()
    (app_dir / f"{name}.c").write_text(_APP_TEMPLATE_C.format(name=name))
    (app_dir / MANIFEST_YAML_FILE).write_text(_APP_TEMPLATE_YAML.format(name=name))

    print(f"Created app '{name}' in ./{name}/")
    print(f"  {name}/{name}.c")
    print(f"  {name}/{MANIFEST_YAML_FILE}")
    print(f"\nBuild with:")
    print(f"  cd {name} && python {Path(__file__).parent.parent / 'dbt.py'} build")


def cmd_build(args) -> None:
    app_dir  = Path(".").resolve()
    plugin, arch, cpu, board_cfg = get_board_plugin()
    tc = plugin.find_compiler(arch, cpu)
    print(f"  [arch] {arch} / {cpu}")

    ok = build_single(app_dir, plugin, arch, cpu, board_cfg, tc)
    if ok:
        elf = app_dir / APP_BUILD_DIR / APP_ELF_NAME
        print(f"\nBuild OK → {elf}")
        print(f"Deploy:  python dbt.py deploy <sd_mount_point>")
        print(f"Inspect: python dbt.py info")
    else:
        sys.exit(1)


def cmd_info(args) -> None:
    app_dir  = Path(".").resolve()
    elf      = app_dir / APP_BUILD_DIR / APP_ELF_NAME
    if not elf.exists():
        sys.exit("ERROR: app.elf not found — run 'dbt build' first")

    manifest = load_manifest(app_dir)
    plugin, arch, cpu, board_cfg = get_board_plugin()
    tc = plugin.find_compiler(arch, cpu)

    print("=== Manifest ===")
    for k, v in manifest.items():
        print(f"  {k}: {v}")

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
            size = int(parts[5], 16)
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
    print(f"  {'─'*37}")
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


def cmd_deploy(args) -> None:
    app_dir = Path(".").resolve()
    is_bin  = getattr(args, "bin", False) or _is_bin_app(app_dir)
    sd_path = Path(args.path)

    elf = app_dir / APP_BUILD_DIR / APP_ELF_NAME
    if not elf.exists():
        sys.exit("ERROR: app.elf not found — run 'dbt build' first")

    manifest = load_manifest(app_dir)
    app_name = manifest["name"]
    subdir   = "bin" if is_bin else "apps"
    if getattr(args, "bin", False):
        subdir = "bin"

    dest_dir = sd_path / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext      = ".dap" if not getattr(args, "elf", False) else ".elf"
    import shutil
    dest_elf = dest_dir / f"{app_name}{ext}"
    shutil.copy2(elf, dest_elf)
    print(f"Deployed → {dest_elf}")


def cmd_clean(args) -> None:
    build_dir = Path(".") / APP_BUILD_DIR
    import shutil
    if build_dir.exists():
        shutil.rmtree(build_dir)
        print(f"Removed {build_dir}")
    else:
        print("Nothing to clean")


def cmd_buildall(args) -> None:
    sd_path  = Path(args.path) if getattr(args, "path", None) else None
    do_clean = getattr(args, "clean", False)

    apps = find_apps()
    if not apps:
        sys.exit("ERROR: no apps found under system/ or apps/")

    plugin, arch, cpu, board_cfg = get_board_plugin()
    tc = plugin.find_compiler(arch, cpu)
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

        if not build_single(app_dir, plugin, arch, cpu, board_cfg, tc):
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

    total  = len(apps)
    n_ok   = len(ok_list)
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


def cmd_cleanall(args) -> None:
    apps = find_apps()
    if not apps:
        print("No apps found.")
        return
    for app_dir, _ in apps:
        clean_single(app_dir)
    print(f"Cleaned {len(apps)} app(s).")


# ---------------------------------------------------------------------------
# flash sd — build all + deploy to SD card
# ---------------------------------------------------------------------------

def cmd_flash_sd(args) -> None:
    """Build all system apps and deploy them to an SD card mount point."""
    sd_path = Path(args.path)
    if not sd_path.exists():
        sys.exit(f"ERROR: SD path does not exist: {sd_path}")

    apps = find_apps()
    if not apps:
        sys.exit("ERROR: no apps found under system/ or apps/")

    plugin, arch, cpu, board_cfg = get_board_plugin()
    tc = plugin.find_compiler(arch, cpu)
    print(f"Building {len(apps)} apps for {arch}/{cpu}…\n")

    ok_list   = []
    fail_list = []

    for app_dir, is_bin in apps:
        rel = app_dir.relative_to(DUNEOS_ROOT)
        if not build_single(app_dir, plugin, arch, cpu, board_cfg, tc):
            fail_list.append(rel)
            continue
        if not deploy_single(app_dir, sd_path, is_bin):
            fail_list.append(rel)
            continue
        ok_list.append(rel)

    print(f"\n{len(ok_list)}/{len(apps)} deployed to {sd_path}")
    if fail_list:
        for p in fail_list:
            print(f"  ✗  {p}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    # No arguments → full-screen TUI
    if len(sys.argv) == 1:
        from .tui import DbtApp
        DbtApp().run()
        return

    parser = argparse.ArgumentParser(
        prog="dbt",
        description="DuneBuild Tool — build and flash DuneOS",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- setup ---
    p_setup = sub.add_parser("setup", help="Interactive setup wizard (board, port, ESP-IDF)")
    p_setup.set_defaults(func=cmd_setup)

    # --- bspgen ---
    p_bspgen = sub.add_parser(
        "bspgen",
        help="Generate BSP files (board_config.h, sdkconfig.board, partitions.csv, idf_target.txt)",
    )
    p_bspgen.add_argument(
        "board", nargs="?", default=None,
        help="Board name (default: active board from .duneos_board)",
    )
    p_bspgen.add_argument(
        "--all", action="store_true",
        help="Generate BSP for every board that has a board.yaml",
    )
    p_bspgen.add_argument(
        "--list", action="store_true",
        help="List all boards with a board.yaml (active board marked with *)",
    )
    p_bspgen.add_argument(
        "--dry-run", action="store_true",
        help="Print generated files to stdout without writing them",
    )
    p_bspgen.set_defaults(func=cmd_bspgen)

    # --- flash (nested subcommands) ---
    p_flash = sub.add_parser("flash", help="Flash firmware to the device")
    flash_sub = p_flash.add_subparsers(dest="flash_target", required=True)

    p_flash_kernel = flash_sub.add_parser(
        "kernel", help="Build + flash the DuneOS kernel (wraps idf.py)")
    p_flash_kernel.add_argument(
        "--build-only", dest="build_only", action="store_true",
        help="Build only, do not flash")
    p_flash_kernel.add_argument(
        "--flash-only", dest="flash_only", action="store_true",
        help="Flash only, skip rebuild")
    p_flash_kernel.add_argument(
        "--monitor", action="store_true",
        help="Open serial monitor after flashing")
    p_flash_kernel.set_defaults(func=cmd_flash_kernel)

    p_flash_sysbin = flash_sub.add_parser(
        "sysbin", help="Build apps + flash LittleFS sysbin partition")
    p_flash_sysbin.add_argument(
        "--no-build", dest="build", action="store_false", default=True,
        help="Skip rebuilding apps before packaging")
    p_flash_sysbin.add_argument(
        "--port", help="Serial port (overrides .duneos_port)")
    p_flash_sysbin.add_argument(
        "--baud", type=int, default=460800,
        help="Flash baud rate (default: 460800)")
    p_flash_sysbin.set_defaults(func=cmd_flashimg)

    p_flash_sd = flash_sub.add_parser(
        "sd", help="Build all apps + deploy to SD card mount point")
    p_flash_sd.add_argument("path", help="SD card mount point (e.g. /mnt/sd or E:\\)")
    p_flash_sd.set_defaults(func=cmd_flash_sd)

    # --- new ---
    p_new = sub.add_parser("new", help="Create a new app from template")
    p_new.add_argument("name", help="App name (also used as directory name)")
    p_new.set_defaults(func=cmd_new)

    # --- build ---
    p_build = sub.add_parser("build", help="Build the app in the current directory")
    p_build.set_defaults(func=cmd_build)

    # --- info ---
    p_info = sub.add_parser("info", help="Show ELF sections, symbols and relocations")
    p_info.set_defaults(func=cmd_info)

    # --- deploy ---
    p_deploy = sub.add_parser("deploy", help="Copy built app to SD card mount point")
    p_deploy.add_argument("path", help="SD card mount point")
    p_deploy.add_argument("--elf", action="store_true",
                          help="Use .elf extension instead of .dap")
    p_deploy.add_argument("--bin", action="store_true",
                          help="Deploy to <sd>/bin/ instead of <sd>/apps/")
    p_deploy.set_defaults(func=cmd_deploy)

    # --- clean ---
    p_clean = sub.add_parser("clean", help="Remove build artefacts for current app")
    p_clean.set_defaults(func=cmd_clean)

    # --- buildall ---
    p_buildall = sub.add_parser("buildall",
                                help="Build all system apps (optionally deploy)")
    p_buildall.add_argument("path", nargs="?", default=None,
                            help="SD card mount point — deploy after build if given")
    p_buildall.add_argument("--clean", action="store_true",
                            help="Clean each app before building")
    p_buildall.set_defaults(func=cmd_buildall)

    # --- cleanall ---
    p_cleanall = sub.add_parser("cleanall", help="Remove build artefacts for all apps")
    p_cleanall.set_defaults(func=cmd_cleanall)

    # --- flashimg (legacy alias for flash sysbin) ---
    p_flashimg = sub.add_parser(
        "flashimg",
        help="[legacy] Build LittleFS sysbin image and flash — use 'flash sysbin' instead",
    )
    p_flashimg.add_argument("--build", action="store_true",
                            help="Build all system apps before packaging")
    p_flashimg.add_argument("--port",
                            help="Serial port (overrides .duneos_port)")
    p_flashimg.add_argument("--baud", type=int, default=460800,
                            help="Flash baud rate (default: 460800)")
    p_flashimg.set_defaults(func=cmd_flashimg)

    args = parser.parse_args()
    args.func(args)
