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


_KNOWN_FIELDS = {
    # Identity
    "name", "version", "required_abi_version",
    # Runtime
    "permissions", "stack_size", "heap_size", "wdt_timeout_ms",
    # Build
    "sources", "capabilities",
    # Phase 25.4 — UI metadata, consumed by the future app launcher.
    # Free-form short string: builtin name (e.g. "app", "term", "gear")
    # or a path to an RGB565 asset (e.g. "/flash/icons/clock.rgb").
    # Embedded into the manifest blob verbatim; not interpreted by the kernel.
    "icon",
}

_ICON_MAX_LEN = 64


def validate_manifest(m: dict) -> None:
    required = ["name", "version", "required_abi_version"]
    missing  = [k for k in required if k not in m]
    if missing:
        sys.exit(f"ERROR: manifest missing fields: {', '.join(missing)}")

    icon = m.get("icon")
    if icon is not None:
        if not isinstance(icon, str):
            sys.exit("ERROR: manifest 'icon' must be a string")
        if len(icon) > _ICON_MAX_LEN:
            sys.exit(
                f"ERROR: manifest 'icon' too long "
                f"({len(icon)} > {_ICON_MAX_LEN} chars)"
            )

    unknown = sorted(set(m.keys()) - _KNOWN_FIELDS)
    if unknown:
        # Don't fail the build — apps in the wild may have extra keys —
        # but surface them so typos are visible (e.g. "stak_size:").
        print(
            f"  WARNING: manifest has unrecognised field(s): {', '.join(unknown)}",
            file=sys.stderr,
        )


def _is_bin_app(app_dir: Path) -> bool:
    bin_root = DUNEOS_ROOT / "apps" / "system" / "bin"
    try:
        app_dir.relative_to(bin_root)
        return True
    except ValueError:
        return False


def find_apps() -> list[tuple[Path, bool]]:
    """
    Return [(app_dir, is_bin), ...] for all apps with a manifest.
    Searches apps/system (DuneOS-maintained) and apps/user (third-party / demos).
    """
    search_roots = [DUNEOS_ROOT / "apps" / "system", DUNEOS_ROOT / "apps" / "user"]

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
