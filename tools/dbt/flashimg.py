"""
flashimg — DuneOS sysbin LittleFS image builder and flasher
============================================================

Creates the LittleFS image for the board's 'sysbin' partition, then optionally
flashes it to the device. Always driven by a profile: `dbt system flash` and
`dbt qemu` are the two entry points.

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

_LFS_BLOCK_SIZE = 4096
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


def _sysbin_row(board_name: str | None) -> tuple[int, int]:
    """(offset, size) of the board's sysbin partition, from partitions.csv."""
    if not board_name:
        sys.exit("ERROR: no board selected — write one to .duneos_board "
                 "(`dbt system use <profile>`).")
    csv_path = DUNEOS_ROOT / "boards" / board_name / "partitions.csv"
    row = None
    if csv_path.exists():
        for line in csv_path.read_text().splitlines():
            parts = [x.strip() for x in line.strip().split(",")]
            if len(parts) >= 5 and parts[0] == "sysbin":
                row = parts
                break
    if row is None:
        sys.exit(
            f"ERROR: board '{board_name}' declares no 'sysbin' partition.\n"
            f"  Looked in {csv_path}.\n"
            f"  Run `python tools/duneos-bspgen.py boards/{board_name}/board.yaml`."
        )
    from .system import parse_csv_int, parse_partition_sizes
    size = parse_partition_sizes(board_name).get("sysbin", 0)
    if size <= 0:
        sys.exit(f"ERROR: board '{board_name}': unreadable sysbin size "
                 f"{row[4]!r} in {csv_path}.")
    offset = parse_csv_int(row[3])
    if offset is None:
        sys.exit(f"ERROR: board '{board_name}': unreadable sysbin offset "
                 f"{row[3]!r} in {csv_path}.")
    return offset, size


def _get_sysbin_offset(board_name: str | None) -> int:
    return _sysbin_row(board_name)[0]


def _get_sysbin_size(board_name: str | None) -> int:
    return _sysbin_row(board_name)[1]


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


def _no_fit_message(board_name: str, profile_name: str, staged: int,
                    capacity: int, metadata: bool = False) -> str:
    why = ("the raw bytes fit but the filesystem metadata did not"
           if metadata else "the staged bytes exceed the partition")
    return (
        f"ERROR: the image does not fit the sysbin partition — {why}.\n"
        f"  partition: {capacity} bytes ({capacity / 1024:.1f} KiB)\n"
        f"  staged:    {staged} bytes ({staged / 1024:.1f} KiB)\n"
        f"  profile:   {profile_name}\n"
        f"  board:     {board_name}\n"
        f"  Drop apps from profiles/{profile_name}/profile.yaml — "
        f"`dbt system size --profile {profile_name}` lists the biggest — or "
        f"grow the sysbin partition in boards/{board_name}/board.yaml."
    )


def _staged_bytes(staging_dir: Path) -> int:
    return sum(p.stat().st_size for p in staging_dir.rglob("*") if p.is_file())


def _create_image(staging_dir: Path, out_path: Path, capacity: int,
                  board_name: str, profile_name: str) -> None:
    """Pack staging_dir into a LittleFS image of `capacity` bytes at out_path."""
    try:
        from littlefs import LittleFS, LittleFSError  # type: ignore
    except ImportError:
        sys.exit(
            "ERROR: littlefs-python is not installed.\n"
            "  pip install -c tools/constraints.txt littlefs-python\n"
            "Then re-run:  python dbt.py system flash"
        )

    lfs = LittleFS(
        block_size=_LFS_BLOCK_SIZE,
        block_count=capacity // _LFS_BLOCK_SIZE,
        read_size=_LFS_READ_SIZE,
        prog_size=_LFS_PROG_SIZE,
    )

    try:
        for root, _dirs, files in os.walk(staging_dir):
            rel_root = Path(root).relative_to(staging_dir)
            lfs_dir = "/" + str(rel_root).replace("\\", "/") if str(rel_root) != "." else ""
            if lfs_dir:
                try:
                    lfs.mkdir(lfs_dir)
                except LittleFSError as exc:
                    if exc.code != LittleFSError.Error.LFS_ERR_EXIST:
                        raise
            for fname in files:
                src = Path(root) / fname
                lfs_path = (lfs_dir + "/" + fname) if lfs_dir else ("/" + fname)
                data = src.read_bytes()
                with lfs.open(lfs_path, "wb") as lf:
                    lf.write(data)
    except LittleFSError as exc:
        if exc.code != LittleFSError.Error.LFS_ERR_NOSPC:
            raise
        sys.exit(_no_fit_message(board_name, profile_name,
                                 _staged_bytes(staging_dir), capacity,
                                 metadata=True))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(lfs.context.buffer))


# ---------------------------------------------------------------------------
# Stage: collect .dap files + write a default init.yaml
# ---------------------------------------------------------------------------

_SAFE_INIT_YAML = """\
# DuneOS /init.yaml.safe — SAFE MODE recovery image.
#
# Boots only usb_shell so the device always comes back to a usable CDC
# console, no matter how broken the regular init.yaml is. The kernel
# consults this file at boot only when the board's recovery_pin is held
# (Phase 24.7) — otherwise the normal /init.yaml + /sd/init.yaml
# chain is used.
#
# To boot this set permanently, flash a recovery profile instead:
# `dbt system flash --profile <board>-recovery`.
services:
  - path: /bin/usb_shell.dap
    restart: always
"""


def _stage(staging_dir: Path, board_name: str, profile: dict,
           no_init: bool = False) -> int:
    """Copy the profile's built .dap files to staging_dir/bin/, render init.yaml.

    /init.yaml.safe is staged unconditionally for Phase 24.7 hold-key recovery.
    """
    if profile is None:
        raise ValueError(
            "_stage requires a profile — `dbt system flash --profile <name>` "
            "supplies one; there is no stage-everything mode.")

    bin_dir = staging_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    apps = find_apps()
    staged: list[str] = []
    from .manifest import load_manifest

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
        if app_name not in flash_set:
            continue
        dest = bin_dir / f"{app_name}.dap"
        shutil.copy2(elf, dest)
        print(f"  staged → /bin/{app_name}.dap")
        staged.append(app_name)

        # Install the app's icon into the shared theme dir (ADR 023):
        # /flash/share/icons/<icon-name>.dr, resolved by name in the launcher.
        # Prefer a hand-authored icon.dr, else the build-time one from icon.png.
        icon_name = manifest.get("icon")
        if icon_name:
            icon_src = app_dir / "icon.dr"
            if not icon_src.exists():
                icon_src = app_dir / "build" / "icon.dr"
            if icon_src.exists():
                icons_dir = staging_dir / "share" / "icons"
                icons_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(icon_src, icons_dir / f"{icon_name}.dr")
                print(f"  staged → /share/icons/{icon_name}.dr")

    dest_init = staging_dir / "init.yaml"
    if no_init:
        # Leaving /flash without an init.yaml is what makes the kernel take the
        # legacy autoboot path (scan /bin → select → launch). The QEMU smoke
        # bench needs exactly that path, since it is the one that exercises the
        # loader's scan of /flash/bin.
        print("  [no-init] /init.yaml omitted — kernel will autoboot from /bin")
    else:
        lines = [
            f"# DuneOS /init.yaml — generated by `dbt system flash` from",
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
        _convert_etc_pngs(dst_etc, _board_display_size(board_name))
        etc_files = sum(1 for _ in dst_etc.rglob("*") if _.is_file())
        print(f"  staged → /etc/  ({etc_files} file(s) from boards/{board_name}/etc/)")
    else:
        print(f"  [info] no boards/{board_name}/etc/ — /etc/ left empty")

    _install_default_icons(staging_dir)

    return len(staged)


def _board_display_size(board_name: str | None) -> tuple[int, int]:
    """The board's panel resolution from board_config.h, for sizing /etc art.
    Falls back to a generous default if the macros aren't found."""
    if board_name:
        cfg = DUNEOS_ROOT / "boards" / board_name / "board_config.h"
        if cfg.exists():
            import re
            txt = cfg.read_text(encoding="utf-8", errors="ignore")
            mw = re.search(r"DUNEOS_DISPLAY_WIDTH\s+(\d+)", txt)
            mh = re.search(r"DUNEOS_DISPLAY_HEIGHT\s+(\d+)", txt)
            if mw and mh:
                return (int(mw.group(1)), int(mh.group(1)))
    return (320, 240)


def _convert_etc_pngs(etc_root: Path, max_wh: tuple[int, int]) -> None:
    """Render any PNG dropped under /etc to a sibling .dr at flash time, so a
    non-developer can swap e.g. the splash logo (etc/splash/logo.png) without
    running `dbt img convert`. The PNG is not shipped — only the .dr apps read.
    Aspect-preserving downscale so it fits the board's panel (max_wh); a
    full-screen logo is streamed by the splash, not heap-loaded. Non-fatal if
    Pillow is absent."""
    pngs = list(etc_root.rglob("*.png"))
    if not pngs:
        return
    try:
        from PIL import Image
    except ImportError:
        print("  [warn] Pillow missing — /etc PNGs not converted to .dr")
        return
    from . import img
    for png in pngs:
        w, h = Image.open(png).size
        scale = min(max_wh[0] / w, max_wh[1] / h, 1.0)
        size = (round(w * scale), round(h * scale)) if scale < 1.0 else None
        img.convert(png, png.with_suffix(".dr"), resize=size)
        png.unlink()
        print(f"  /etc: {png.name} -> {png.with_suffix('.dr').name}")


def _install_default_icons(staging_dir: Path) -> None:
    """Install the OS generic-icon fallback set into /share/icons/ (ADR 023, the
    freedesktop `hicolor` role): assets/icons/*.png → <staging>/share/icons/*.dr.
    The launcher falls back to `application.dr` for icon-less apps. Non-fatal;
    never overwrites a per-app icon of the same name already staged."""
    src = DUNEOS_ROOT / "assets" / "icons"
    if not src.is_dir():
        return
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("  [warn] Pillow missing — default icons not installed")
        return
    from . import img
    from .builder import ICON_SIZE
    icons_dir = staging_dir / "share" / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)
    for png in sorted(src.glob("*.png")):
        dst = icons_dir / f"{png.stem}.dr"
        if dst.exists():
            continue                      # a per-app icon of this name wins
        img.convert(png, dst, resize=ICON_SIZE)
        print(f"  staged → /share/icons/{dst.name}  (default)")


# ---------------------------------------------------------------------------
# Public command
# ---------------------------------------------------------------------------

def cmd_flashimg(args) -> None:
    """Build (and optionally flash) the sysbin image for `args.profile`.

    Reached through `dbt system flash` and `dbt qemu` only; both supply a
    profile.
    """
    board_name = getattr(args, "board", None) or _get_board_name()
    profile = getattr(args, "profile", None)
    if profile is None:
        sys.exit("ERROR: the sysbin image is built from a profile. "
                 "Run `dbt system flash --profile <name>`.")
    capacity = _get_sysbin_size(board_name)

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

    out_dir = Path(getattr(args, "out_dir", None) or (DUNEOS_ROOT / "build"))
    staging_dir = out_dir / "sysbin_staging"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)

    print("Staging apps…")
    print(f"  Profile-driven staging: '{profile['name']}'")
    n = _stage(staging_dir, board_name, profile,
               no_init=getattr(args, "no_init", False))
    print(f"  {n} app(s) staged\n")

    staged = _staged_bytes(staging_dir)
    if staged > capacity:
        sys.exit(_no_fit_message(board_name, profile["name"], staged, capacity))

    out_path = out_dir / "sysbin.bin"
    print(f"Creating LittleFS image → {out_path}")
    _create_image(staging_dir, out_path, capacity, board_name, profile["name"])
    size_kb = out_path.stat().st_size // 1024
    print(f"  {size_kb} KB  ({capacity // 1024} KB partition)\n")

    # image_only stops before any serial resolution: `dbt qemu` must never
    # read .duneos_port, let alone touch a physical device.
    if getattr(args, "image_only", False):
        return

    port = _find_port(getattr(args, "port", None))

    if not port:
        print("Image ready. To flash:")
        print(f"  echo <PORT> > .duneos_port   # save for future use")
        print(f"  python dbt.py system flash --port <PORT>")
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
