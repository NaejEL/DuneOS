import shutil
import sys
from pathlib import Path

from .constants import APP_BUILD_DIR, APP_ELF_NAME
from .manifest import load_manifest


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

    return True
