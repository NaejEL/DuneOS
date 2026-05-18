import json
import sys
from pathlib import Path

from .constants import DUNEOS_ROOT, MANIFEST_YAML_FILE, MANIFEST_JSON_FILE

try:
    import yaml as _yaml
    _HAVE_YAML = True
except ImportError:
    _HAVE_YAML = False


def load_manifest(app_dir: Path) -> dict:
    """
    Load the app manifest. Tries duneos.yaml first, falls back to manifest.json.
    manifest.json support is deprecated and will be removed in a future phase.
    """
    yaml_path = app_dir / MANIFEST_YAML_FILE
    json_path = app_dir / MANIFEST_JSON_FILE

    if yaml_path.exists():
        if not _HAVE_YAML:
            sys.exit(
                "ERROR: duneos.yaml found but PyYAML is not installed.\n"
                "  pip install pyyaml"
            )
        with open(yaml_path) as f:
            return _yaml.safe_load(f)

    if json_path.exists():
        print(
            f"  WARNING: {json_path.name} is deprecated — "
            f"migrate to {MANIFEST_YAML_FILE}",
            file=sys.stderr,
        )
        with open(json_path) as f:
            return json.load(f)

    sys.exit(
        f"ERROR: no manifest found in {app_dir}\n"
        f"  Expected {MANIFEST_YAML_FILE} (or legacy {MANIFEST_JSON_FILE})"
    )


def validate_manifest(m: dict) -> None:
    required = ["name", "version", "required_abi_version"]
    missing  = [k for k in required if k not in m]
    if missing:
        sys.exit(f"ERROR: manifest missing fields: {', '.join(missing)}")


def _is_bin_app(app_dir: Path) -> bool:
    bin_root = DUNEOS_ROOT / "system" / "bin"
    try:
        app_dir.relative_to(bin_root)
        return True
    except ValueError:
        return False


def find_apps(include_examples: bool = False) -> list[tuple[Path, bool]]:
    """
    Return [(app_dir, is_bin), ...] for all apps with a manifest.
    Searches system/, apps/, and optionally examples/.
    """
    search_roots = [DUNEOS_ROOT / "system", DUNEOS_ROOT / "apps"]
    if include_examples:
        search_roots.append(DUNEOS_ROOT / "examples")

    results = []
    for base in search_roots:
        if not base.exists():
            continue
        # Accept either duneos.yaml or manifest.json
        seen = set()
        for pattern in (MANIFEST_YAML_FILE, MANIFEST_JSON_FILE):
            for mpath in sorted(base.rglob(pattern)):
                app_dir = mpath.parent
                if app_dir not in seen:
                    seen.add(app_dir)
                    results.append((app_dir, _is_bin_app(app_dir)))
    return results
