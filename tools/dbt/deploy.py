import glob
import shutil
import sys
from pathlib import Path

from .constants import APP_BUILD_DIR, APP_ELF_NAME
from .manifest import load_manifest


def _deploy_data(app_dir: Path, sd_path: Path, manifest: dict) -> None:
    """Copy an app's `data:` entries onto the SD card, creating folders. Each
    entry is { src, dst }: src is a file, directory, or glob relative to the app
    dir; dst is a directory relative to the SD root (leading '/' and a 'sd/'
    prefix are tolerated). Lets an app ship runtime data — e.g. i2cscope's
    scenarios → /sd/i2cscope/ — so users don't create folders by hand."""
    for entry in manifest.get("data", []):
        if not isinstance(entry, dict):
            continue
        src = str(entry.get("src", "")).strip()
        dst = str(entry.get("dst", "")).strip()
        if not src:
            continue

        rel = dst.lstrip("/")
        if rel.startswith("sd/"):
            rel = rel[3:]
        dst_dir = (sd_path / rel) if rel else sd_path
        dst_dir.mkdir(parents=True, exist_ok=True)

        src_path = app_dir / src
        files: list[Path] = []
        if src_path.is_dir():
            files = [f for f in sorted(src_path.iterdir()) if f.is_file()]
        elif src_path.is_file():
            files = [src_path]
        else:
            files = [Path(m) for m in sorted(glob.glob(str(app_dir / src))) if Path(m).is_file()]

        for f in files:
            shutil.copy2(f, dst_dir / f.name)
        if files:
            print(f"  ->  {dst_dir}  ({len(files)} file(s) from {src})")
        else:
            print(f"  [warn] data: no files matched '{src}'")


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
    dest = dest_dir / f"{manifest['name']}.dap"
    shutil.copy2(elf, dest)
    print(f"  ->  {dest}")

    # Ship the icon next to the .dap (ADR 023): the launcher looks for
    # <name>.dr adjacent to a side-loaded app. Keeps the .dap itself lean.
    icon_src = app_dir / "icon.dr"
    if not icon_src.exists():
        icon_src = app_dir / APP_BUILD_DIR / "icon.dr"
    if icon_src.exists():
        icon_dest = dest_dir / f"{manifest['name']}.dr"
        shutil.copy2(icon_src, icon_dest)
        print(f"  ->  {icon_dest}")

    _deploy_data(app_dir, sd_path, manifest)

    return True
