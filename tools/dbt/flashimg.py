"""
flashimg — DuneOS sysbin LittleFS image builder and flasher
============================================================

Creates a 1 MB LittleFS image suitable for flashing to the 'sysbin' partition,
then optionally flashes it directly to the device.

Port is read from (in priority order):
  1. --port CLI argument
  2. .duneos_port file at the repo root  (echo COM13 > .duneos_port)
  3. DUNEOS_PORT environment variable

Requires:
  pip install littlefs-python
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .constants import DUNEOS_ROOT
from .manifest import find_apps
from .toolchain import get_board_plugin
from .builder import build_single
from .deploy import deploy_single

_SYSBIN_SIZE   = 0x100000   # 1 MB — must match partitions.csv
_LFS_BLOCK_SIZE = 4096
_LFS_BLOCK_COUNT = _SYSBIN_SIZE // _LFS_BLOCK_SIZE   # 256
_LFS_READ_SIZE  = 256
_LFS_PROG_SIZE  = 256

_PORT_FILE = DUNEOS_ROOT / ".duneos_port"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_port(cli_port: str | None) -> str | None:
    """Resolve serial port: CLI arg > .duneos_port > DUNEOS_PORT env."""
    if cli_port:
        return cli_port
    if _PORT_FILE.exists():
        p = _PORT_FILE.read_text().strip()
        if p:
            return p
    return os.environ.get("DUNEOS_PORT")


def _get_sysbin_offset(board_name: str | None) -> int:
    """Read sysbin partition offset from the board's (or root) partitions.csv."""
    candidates = []
    if board_name:
        candidates.append(DUNEOS_ROOT / "boards" / board_name / "partitions.csv")
    candidates.append(DUNEOS_ROOT / "partitions.csv")

    for csv_path in candidates:
        if not csv_path.exists():
            continue
        for line in csv_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or not line:
                continue
            parts = [x.strip() for x in line.split(",")]
            if len(parts) >= 4 and parts[0] == "sysbin":
                try:
                    return int(parts[3], 16)
                except ValueError:
                    pass

    # Fallback to the hardcoded default (8 MB board layout)
    return 0x190000


def _get_board_name() -> str | None:
    board_file = DUNEOS_ROOT / ".duneos_board"
    if board_file.exists():
        return board_file.read_text().strip() or None
    return None


def _find_esptool() -> str | None:
    """Locate esptool via DUNEOS_ESPTOOL, the current venv, or PATH."""
    override = os.environ.get("DUNEOS_ESPTOOL")
    if override:
        return override
    # When running inside the dbt venv, esptool lives next to the Python binary.
    bin_dir = Path(sys.executable).parent
    for name in ("esptool.exe", "esptool.py", "esptool"):
        candidate = bin_dir / name
        if candidate.exists():
            return str(candidate)
    for name in ("esptool.py", "esptool"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _create_image(staging_dir: Path, out_path: Path) -> None:
    """Pack staging_dir into a LittleFS image at out_path."""
    try:
        from littlefs import LittleFS  # type: ignore
    except ImportError:
        sys.exit(
            "ERROR: littlefs-python is not installed.\n"
            "  pip install littlefs-python\n"
            "Then re-run:  python dbt.py flashimg"
        )

    lfs = LittleFS(
        block_size=_LFS_BLOCK_SIZE,
        block_count=_LFS_BLOCK_COUNT,
        read_size=_LFS_READ_SIZE,
        prog_size=_LFS_PROG_SIZE,
    )

    for root, _dirs, files in os.walk(staging_dir):
        rel_root = Path(root).relative_to(staging_dir)
        lfs_dir = "/" + str(rel_root).replace("\\", "/") if str(rel_root) != "." else ""
        if lfs_dir:
            try:
                lfs.mkdir(lfs_dir)
            except Exception:
                pass
        for fname in files:
            src = Path(root) / fname
            lfs_path = (lfs_dir + "/" + fname) if lfs_dir else ("/" + fname)
            data = src.read_bytes()
            with lfs.open(lfs_path, "wb") as lf:
                lf.write(data)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(lfs.context.buffer))


# ---------------------------------------------------------------------------
# Stage: collect .dap files + write a default init.yaml
# ---------------------------------------------------------------------------

_SAFE_INIT_YAML = """\
# DuneOS /flash/init.yaml.safe — SAFE MODE recovery image.
#
# Boots only usb_shell so the device always comes back to a usable CDC
# console, no matter how broken the regular init.yaml is. The kernel
# consults this file at boot only when the board's recovery_pin is held
# (Phase 24.7) — otherwise the normal /flash/init.yaml + /sd/init.yaml
# chain is used.
#
# `dbt flashimg --safe` flashes this content as /flash/init.yaml instead
# of the regular one, locking the device into safe mode permanently
# until a normal flashimg is run.
services:
  - path: /flash/bin/usb_shell.dap
    restart: always
"""


def _stage(staging_dir: Path, board_name: str, safe_mode: bool = False,
           profile: dict | None = None) -> int:
    """
    Copy built .dap files to staging_dir/bin/ and stage init.yaml.

    Two modes:
      * profile=None (legacy / `dbt flashimg`): stage ALL system apps, copy
        boards/<board>/init.yaml verbatim.
      * profile=<dict> (`dbt system flash`): stage ONLY the apps listed in
        profile.apps_flash, render init.yaml from profile.init_flash.

    safe_mode overrides both: writes the minimal usb_shell-only init.yaml.
    /flash/init.yaml.safe is always staged for Phase 24.7 hold-key recovery.
    """
    bin_dir = staging_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    apps = find_apps()
    staged: list[str] = []
    from .manifest import load_manifest

    # In profile mode, build a filter set of app names.
    flash_set: set[str] | None = None
    if profile is not None:
        flash_set = set(profile.get("apps_flash", []))

    for app_dir, is_bin in apps:
        elf = app_dir / "build" / "app.elf"
        if not elf.exists():
            print(f"  [skip] {app_dir.name} — not built")
            continue
        try:
            manifest = load_manifest(app_dir)
        except SystemExit:
            continue
        app_name = manifest["name"]
        # Profile filter: skip apps not listed in apps_flash.
        if flash_set is not None and app_name not in flash_set:
            continue
        dest = bin_dir / f"{app_name}.dap"
        shutil.copy2(elf, dest)
        print(f"  staged → /bin/{app_name}.dap")
        staged.append(app_name)

    # Stage init.yaml — three sources:
    dest_init = staging_dir / "init.yaml"
    if safe_mode:
        dest_init.write_text(_SAFE_INIT_YAML)
        print(f"  staged → /init.yaml  (SAFE MODE — usb_shell only)")
    elif profile is not None:
        # Render from profile.init_flash
        lines = [
            f"# DuneOS /flash/init.yaml — generated by `dbt system flash` from",
            f"# profile '{profile['name']}' (board: {profile['board']}).",
            f"# Edit profiles/{profile['name']}/profile.yaml and re-run.",
            "services:",
        ]
        for entry in profile.get("init_flash", []):
            lines.append(f"  - path: {entry.get('path','')}")
            lines.append(f"    restart: {entry.get('restart','no')}")
            # Phase 25.4: propagate `after:` so the kernel observer sees the
            # dependency. Without this the editor-set value silently dropped.
            after = entry.get("after")
            if after:
                lines.append(f"    after: {after}")
        dest_init.write_text("\n".join(lines) + "\n")
        print(f"  staged → /init.yaml  (from profile '{profile['name']}')")
    else:
        board_init = DUNEOS_ROOT / "boards" / board_name / "init.yaml"
        if board_init.exists():
            shutil.copy2(board_init, dest_init)
            print(f"  staged → /init.yaml  (from boards/{board_name}/init.yaml)")
        else:
            dest_init.write_text(
                f"# DuneOS /flash/init.yaml for {board_name}\n"
                f"# Create boards/{board_name}/init.yaml or a profile to populate.\n"
                f"services:\n"
            )
            print(f"  [warn] boards/{board_name}/init.yaml not found — staged empty stub")

    # Always stage init.yaml.safe (Phase 24.7 hold-key recovery).
    safe_dest = staging_dir / "init.yaml.safe"
    safe_dest.write_text(_SAFE_INIT_YAML)
    print(f"  staged → /init.yaml.safe  (recovery image — used when recovery_pin held)")

    # Phase 25.5: per-board /etc tree. Copied verbatim to the LittleFS root
    # so apps can read /etc/<app>/config.yaml at runtime. Keeping the source
    # of truth under boards/<board>/etc makes per-board overrides natural
    # (each board declares its own splash logo path, wifi creds, etc.).
    src_etc = DUNEOS_ROOT / "boards" / board_name / "etc"
    if src_etc.is_dir():
        dst_etc = staging_dir / "etc"
        # Skip *.example/*.template — those are git-only docs/templates,
        # never runtime files. Everything else is copied verbatim.
        def _ignore(_src, names):
            return [n for n in names
                    if n.endswith(".example") or n.endswith(".template")]
        shutil.copytree(src_etc, dst_etc, dirs_exist_ok=True, ignore=_ignore)
        etc_files = sum(1 for _ in dst_etc.rglob("*") if _.is_file())
        print(f"  staged → /etc/  ({etc_files} file(s) from boards/{board_name}/etc/)")
    else:
        print(f"  [info] no boards/{board_name}/etc/ — /etc/ left empty")

    return len(staged)


# ---------------------------------------------------------------------------
# Public command
# ---------------------------------------------------------------------------

def cmd_flashimg(args) -> None:
    board_name = _get_board_name()

    if getattr(args, "build", False):
        print("Building system apps…")
        plugin, arch, cpu, board_cfg = get_board_plugin()
        tc = plugin.find_compiler(arch, cpu)
        apps = find_apps()
        for app_dir, is_bin in apps:
            if not is_bin:
                continue
            if not build_single(app_dir, plugin, arch, cpu, board_cfg, tc):
                print(f"  [FAIL] {app_dir.name}")
        print()

    staging_dir = DUNEOS_ROOT / "build" / "sysbin_staging"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)

    print("Staging apps…")
    safe = getattr(args, "safe", False)
    profile = getattr(args, "profile", None)
    if safe:
        print("  [SAFE MODE] init.yaml replaced with usb_shell-only.")
    elif profile is not None:
        print(f"  Profile-driven staging: '{profile['name']}'")
    n = _stage(staging_dir, board_name, safe_mode=safe, profile=profile)
    print(f"  {n} app(s) staged\n")

    out_path = DUNEOS_ROOT / "build" / "sysbin.bin"
    print(f"Creating LittleFS image → {out_path.relative_to(DUNEOS_ROOT)}")
    _create_image(staging_dir, out_path)
    size_kb = out_path.stat().st_size // 1024
    print(f"  {size_kb} KB  ({_SYSBIN_SIZE // 1024} KB partition)\n")

    port = _find_port(getattr(args, "port", None))

    if not port:
        print("Image ready. To flash:")
        print(f"  echo <PORT> > .duneos_port   # save for future use")
        print(f"  python dbt.py flashimg --port <PORT>")
        return

    esptool = _find_esptool()
    if not esptool:
        print(f"Image ready at {out_path}")
        print("ERROR: esptool not found in PATH.")
        print("  Set DUNEOS_ESPTOOL=/path/to/esptool.py  or install it:")
        print("  pip install esptool")
        sys.exit(1)

    offset = _get_sysbin_offset(board_name)
    baud   = getattr(args, "baud", 460800)

    cmd = [
        esptool,
        "--port", port,
        "--baud", str(baud),
        "write_flash",
        hex(offset),
        str(out_path),
    ]
    print(f"Flashing sysbin @ {hex(offset)} via {port}…")
    print("  " + " ".join(cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(f"esptool exited with code {result.returncode}")
    print("\nFlash complete.")
