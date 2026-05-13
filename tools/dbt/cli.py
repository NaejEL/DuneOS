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
    python dbt.py buildall [path]           Build all system apps; deploy if path given
    python dbt.py buildall [path] --clean   Clean first, then build + deploy
    python dbt.py buildall [path] --examples  Also build examples/

App category is inferred from directory:
    system/bin/*   → deployed to <sd>/bin/
    everything else → deployed to <sd>/apps/
"""

import argparse
import sys
from pathlib import Path

from .constants import APP_BUILD_DIR, APP_ELF_NAME, MANIFEST_YAML_FILE, DUNEOS_ROOT
from .manifest import load_manifest, find_apps, _is_bin_app
from .toolchain import get_board_cpu, find_toolchain
from .builder import build_single, clean_single, run
from .deploy import deploy_single

# ---------------------------------------------------------------------------
# App template — generates duneos.yaml
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


def cmd_info(args) -> None:
    app_dir  = Path(".").resolve()
    elf      = app_dir / APP_BUILD_DIR / APP_ELF_NAME
    if not elf.exists():
        sys.exit("ERROR: app.elf not found — run 'dbt build' first")

    manifest  = load_manifest(app_dir)
    arch, cpu = get_board_cpu()
    tc        = find_toolchain(arch, cpu)

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
    sd_path  = Path(args.path) if args.path else None
    do_clean = getattr(args, "clean", False)

    apps = find_apps(include_examples=True)
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
    apps = find_apps(include_examples=True)
    if not apps:
        print("No apps found.")
        return
    for app_dir, _ in apps:
        clean_single(app_dir)
    print(f"Cleaned {len(apps)} app(s).")


def main() -> None:
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
                          help="Deploy to <sd>/bin/ instead of <sd>/apps/")
    p_deploy.set_defaults(func=cmd_deploy)

    p_clean = sub.add_parser("clean", help="Remove build artefacts")
    p_clean.set_defaults(func=cmd_clean)

    p_buildall = sub.add_parser("buildall",
                                help="Build all system apps (and optionally deploy + examples)")
    p_buildall.add_argument("path", nargs="?", default=None,
                            help="SD card mount point — if given, deploy after building")
    p_buildall.add_argument("--clean", action="store_true",
                            help="Clean each app's build directory before building")
    p_buildall.set_defaults(func=cmd_buildall)

    p_cleanall = sub.add_parser("cleanall", help="Remove build artefacts for all apps")
    p_cleanall.set_defaults(func=cmd_cleanall)

    args = parser.parse_args()
    args.func(args)
