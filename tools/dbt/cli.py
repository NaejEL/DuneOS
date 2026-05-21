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
        sys.exit("ERROR: no apps found under apps/system or apps/user")

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
        sys.exit("ERROR: no apps found under apps/system or apps/user")

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
# Phase 25 — `dbt system <verb>` (image recipes)
# ---------------------------------------------------------------------------

def cmd_system_list(args) -> None:
    from .system import list_profiles, load_profile, active_profile_name
    active = active_profile_name()
    profiles = list_profiles()
    if not profiles:
        print("No profiles found. Create profiles/<name>/profile.yaml to get started.")
        return
    print(f"Profiles in {Path('profiles').as_posix()}/:")
    for p in profiles:
        name = p.parent.name
        try:
            cfg  = load_profile(name)
        except SystemExit as e:
            print(f"  ✗ {name:<30}  {e}")
            continue
        marker = "*" if active == name else " "
        desc   = cfg.get("description", "")
        board  = cfg.get("board", "?")
        print(f"  {marker} {name:<30}  board={board:<24}  {desc}")
    if active:
        print(f"\nActive profile: {active}  (`.duneos_profile`)")
    else:
        print("\nNo active profile set. Run `dbt system use <name>` or pass `--profile`.")


def cmd_system_use(args) -> None:
    from .system import load_profile, ACTIVE_PROFILE_FILE
    # Validate the profile exists before pinning it.
    load_profile(args.name)
    ACTIVE_PROFILE_FILE.write_text(args.name + "\n")
    # Also align .duneos_board so the rest of dbt sees the right board.
    cfg = load_profile(args.name)
    board_file = DUNEOS_ROOT / ".duneos_board"
    if not board_file.exists() or board_file.read_text().strip() != cfg["board"]:
        board_file.write_text(cfg["board"] + "\n")
        print(f"  .duneos_board updated to '{cfg['board']}'")
    print(f"Active profile: {args.name}  (board: {cfg['board']})")


def cmd_system_check(args) -> None:
    from .system import resolve_profile, check_profile
    _, profile = resolve_profile(getattr(args, "profile", None))
    rc = check_profile(profile)
    sys.exit(rc)


def cmd_system_build(args) -> None:
    from .system import resolve_profile, build_profile
    name, profile = resolve_profile(getattr(args, "profile", None))
    # Sync .duneos_board with the profile so existing build helpers pick up
    # the right toolchain.
    board_file = DUNEOS_ROOT / ".duneos_board"
    if not board_file.exists() or board_file.read_text().strip() != profile["board"]:
        sys.exit(
            f"ERROR: profile '{name}' targets board '{profile['board']}' but "
            f".duneos_board is '{board_file.read_text().strip() if board_file.exists() else '(unset)'}'.\n"
            f"  Run `dbt system use {name}` first."
        )
    plugin, arch, cpu, board_cfg = get_board_plugin()
    tc = plugin.find_compiler(arch, cpu)
    rc = build_profile(profile, plugin, arch, cpu, board_cfg, tc)
    sys.exit(rc)


def cmd_system_flash(args) -> None:
    """Stage profile.apps_flash + profile.init_flash and flash the sysbin partition."""
    from .system import resolve_profile
    from .flashimg import cmd_flashimg
    name, profile = resolve_profile(getattr(args, "profile", None))
    board_file = DUNEOS_ROOT / ".duneos_board"
    if not board_file.exists() or board_file.read_text().strip() != profile["board"]:
        sys.exit(
            f"ERROR: profile '{name}' targets '{profile['board']}' but "
            f".duneos_board is '{board_file.read_text().strip() if board_file.exists() else '(unset)'}'.\n"
            f"  Run `dbt system use {name}` first."
        )
    print(f"`dbt system flash` — profile '{name}' (board: {profile['board']})\n")
    # Delegate to cmd_flashimg with the profile attached.
    class _A: pass
    a = _A()
    a.build   = False
    a.port    = getattr(args, "port", None)
    a.baud    = getattr(args, "baud", 460800)
    a.safe    = False
    a.profile = profile
    cmd_flashimg(a)


def cmd_system_size(args) -> None:
    from .system import resolve_profile, report_sizes
    _, profile = resolve_profile(getattr(args, "profile", None))
    rc = report_sizes(profile)
    sys.exit(1 if rc > 0 else 0)


def cmd_system_diff(args) -> None:
    from .system import resolve_profile, load_profile, report_diff
    _, active = resolve_profile(getattr(args, "profile", None))
    other = load_profile(args.other)
    report_diff(active, other)


def cmd_system_deploy(args) -> None:
    """Build (if needed) and deploy the profile.apps_sd onto an SD mount."""
    from .system import resolve_profile, _app_map
    name, profile = resolve_profile(getattr(args, "profile", None))
    sd_path = Path(args.sd_path)
    if not sd_path.exists():
        sys.exit(f"ERROR: SD path does not exist: {sd_path}")

    app_map = _app_map()
    targets = profile.get("apps_sd", [])
    if not targets:
        print(f"Profile '{name}' has no apps_sd — nothing to deploy.")
        return
    print(f"Deploying {len(targets)} app(s) from profile '{name}' → {sd_path}\n")
    n_ok, n_fail = 0, 0
    for app_name in targets:
        if app_name not in app_map:
            print(f"  ✗ '{app_name}' missing — skipping")
            n_fail += 1
            continue
        app_dir = app_map[app_name]
        is_bin  = _is_bin_app(app_dir)
        if not deploy_single(app_dir, sd_path, is_bin):
            n_fail += 1
        else:
            n_ok += 1
    print(f"\n{n_ok}/{len(targets)} deployed" + (f"  {n_fail} FAILED" if n_fail else ""))
    sys.exit(1 if n_fail else 0)


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
    p_flash_sysbin.add_argument(
        "--safe", action="store_true",
        help="Replace init.yaml with usb_shell-only (recovery mode)")
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
    p_flashimg.add_argument("--safe", action="store_true",
                            help="Replace init.yaml with usb_shell-only (recovery mode)")
    p_flashimg.set_defaults(func=cmd_flashimg)

    # --- system <verb> (Phase 25 — declarative image recipes) ---
    p_system = sub.add_parser(
        "system",
        help="Compose, validate and flash an image from a profile recipe",
    )
    sys_sub = p_system.add_subparsers(dest="system_cmd", required=True)

    p_sys_list = sys_sub.add_parser("list", help="List available profiles in profiles/")
    p_sys_list.set_defaults(func=cmd_system_list)

    p_sys_use = sys_sub.add_parser("use", help="Set the active profile (writes .duneos_profile)")
    p_sys_use.add_argument("name", help="Profile name (directory under profiles/)")
    p_sys_use.set_defaults(func=cmd_system_use)

    p_sys_check = sys_sub.add_parser("check", help="Validate profile vs board + app permissions")
    p_sys_check.add_argument("--profile", help="Profile name (default: active from .duneos_profile)")
    p_sys_check.set_defaults(func=cmd_system_check)

    p_sys_build = sys_sub.add_parser("build", help="Build every app declared in the profile")
    p_sys_build.add_argument("--profile", help="Profile name (default: active)")
    p_sys_build.set_defaults(func=cmd_system_build)

    p_sys_flash = sys_sub.add_parser(
        "flash",
        help="Stage the profile's apps_flash + init_flash and flash the sysbin partition",
    )
    p_sys_flash.add_argument("--profile", help="Profile name (default: active)")
    p_sys_flash.add_argument("--port", help="Serial port (overrides .duneos_port)")
    p_sys_flash.add_argument("--baud", type=int, default=460800)
    p_sys_flash.set_defaults(func=cmd_system_flash)

    p_sys_deploy = sys_sub.add_parser(
        "deploy",
        help="Copy the profile's apps_sd onto the SD card mount point",
    )
    p_sys_deploy.add_argument("sd_path", help="SD card mount point (e.g. /run/media/.../SD)")
    p_sys_deploy.add_argument("--profile", help="Profile name (default: active)")
    p_sys_deploy.set_defaults(func=cmd_system_deploy)

    p_sys_size = sys_sub.add_parser(
        "size",
        help="Report kernel + sysbin + SD size projections vs partition limits",
    )
    p_sys_size.add_argument("--profile", help="Profile name (default: active)")
    p_sys_size.set_defaults(func=cmd_system_size)

    p_sys_diff = sys_sub.add_parser(
        "diff",
        help="Compare the active profile against another profile",
    )
    p_sys_diff.add_argument("other", help="Other profile name to compare against")
    p_sys_diff.add_argument("--profile", help="Active profile to compare from (default: active)")
    p_sys_diff.set_defaults(func=cmd_system_diff)

    args = parser.parse_args()
    args.func(args)
