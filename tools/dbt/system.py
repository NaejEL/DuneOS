"""
dbt system — image recipe orchestration (Phase 25).

A *profile* is a declarative image recipe living in profiles/<name>/profile.yaml.
It picks one board and lists which apps go to /bin/ (sysbin LittleFS image,
mounted at root "/") vs /sd/ (deploy-time copy), plus the contents of
/init.yaml and /sd/init.yaml.

The active profile is selected by `.duneos_profile` at repo root (gitignored,
like .duneos_board). `dbt system use <name>` writes that file; explicit
`--profile <name>` overrides for one command.

Schema (validated by `dbt system check`):

    name: cardputer-default            # informational, must match dir name
    board: m5stack-cardputer           # boards/<this> must exist
    description: "..."                 # one-line summary for `dbt system list`

    apps_flash:                        # apps staged into the sysbin image
      - usb_shell
      - kb_iomatrix

    init_flash:                        # /init.yaml content
      - { path: /bin/usb_shell.dap, restart: always }

    apps_sd:                           # apps deployed to /sd/apps/ or /sd/bin/
      - snake

    init_sd: []                        # /sd/init.yaml content (often empty)
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from .constants import DUNEOS_ROOT
from .capability_map import check_app, decode_perms, required_configs
from .manifest import find_apps, load_manifest, validate_manifest

try:
    import yaml as _yaml
except ImportError:
    sys.exit("ERROR: PyYAML required for `dbt system`. `pip install pyyaml`.")


PROFILES_DIR        = DUNEOS_ROOT / "profiles"
ACTIVE_PROFILE_FILE = DUNEOS_ROOT / ".duneos_profile"

REQUIRED_KEYS = {"name", "board"}
OPTIONAL_KEYS = {"description", "apps_flash", "init_flash", "apps_sd", "init_sd"}


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def list_profiles() -> list[Path]:
    """Return paths to every profiles/<name>/profile.yaml, sorted by name."""
    if not PROFILES_DIR.exists():
        return []
    return sorted(PROFILES_DIR.glob("*/profile.yaml"))


def active_profile_name() -> str | None:
    """Read .duneos_profile if present, return the name (or None)."""
    if ACTIVE_PROFILE_FILE.exists():
        name = ACTIVE_PROFILE_FILE.read_text().strip()
        if name:
            return name
    return None


def load_profile(name: str) -> dict:
    """Load profiles/<name>/profile.yaml. Validates required fields, returns dict."""
    p = PROFILES_DIR / name / "profile.yaml"
    if not p.exists():
        sys.exit(
            f"ERROR: profile '{name}' not found.\n"
            f"  Expected: {p}\n"
            f"  `dbt system list` to see available profiles."
        )
    cfg = _yaml.safe_load(p.read_text()) or {}
    missing = REQUIRED_KEYS - cfg.keys()
    if missing:
        sys.exit(f"ERROR: profile '{name}' missing required keys: {', '.join(sorted(missing))}")
    if cfg["name"] != name:
        print(f"  [warn] profile '{name}': field 'name' is '{cfg['name']}' "
              f"(directory name wins)", file=sys.stderr)
    # Normalise list fields
    for k in ("apps_flash", "apps_sd"):
        cfg.setdefault(k, [])
        if not isinstance(cfg[k], list):
            sys.exit(f"ERROR: profile '{name}': '{k}' must be a list")
    for k in ("init_flash", "init_sd"):
        cfg.setdefault(k, [])
        if not isinstance(cfg[k], list):
            sys.exit(f"ERROR: profile '{name}': '{k}' must be a list")
    return cfg


def resolve_profile(explicit: str | None) -> tuple[str, dict]:
    """Return (name, parsed_profile) using explicit > active > error."""
    name = explicit or active_profile_name()
    if not name:
        avail = [p.parent.name for p in list_profiles()]
        sys.exit(
            "ERROR: no active profile. Either:\n"
            "  - pass `--profile <name>`\n"
            "  - run `dbt system use <name>` to set .duneos_profile\n"
            f"  Available: {', '.join(avail) if avail else '(none — create profiles/<name>/profile.yaml)'}"
        )
    return name, load_profile(name)


# ---------------------------------------------------------------------------
# App discovery — map name → directory
# ---------------------------------------------------------------------------

def _app_map() -> dict[str, Path]:
    """Build name → app_dir from all apps in apps/system + apps/user."""
    out: dict[str, Path] = {}
    for app_dir, _is_bin in find_apps():
        try:
            m = load_manifest(app_dir)
        except SystemExit:
            continue
        name = m.get("name")
        if name:
            out[name] = app_dir
    return out


# ---------------------------------------------------------------------------
# check — validate profile against board + app perms
# ---------------------------------------------------------------------------

def _read_sdkconfig(board: str) -> list[str]:
    sdk = DUNEOS_ROOT / "boards" / board / "sdkconfig.board"
    if not sdk.exists():
        sys.exit(
            f"ERROR: boards/{board}/sdkconfig.board not found.\n"
            f"  Run `python tools/duneos-bspgen.py boards/{board}/board.yaml`."
        )
    return sdk.read_text().splitlines()


def check_profile(profile: dict) -> int:
    """Validate the profile. Returns 0 if all good, 1 on errors. Prints diagnostics."""
    name  = profile["name"]
    board = profile["board"]
    print(f"Profile '{name}' → board '{board}'")

    board_dir = DUNEOS_ROOT / "boards" / board
    if not board_dir.exists():
        print(f"  ✗ board directory missing: {board_dir}")
        return 1

    sdk_lines = _read_sdkconfig(board)
    app_map = _app_map()
    all_apps_in_profile = list(profile.get("apps_flash", [])) + list(profile.get("apps_sd", []))

    errors   = 0   # hard errors (block build): missing apps, broken init refs
    warnings = 0   # soft mismatches: app over-grants perms, or kernel under-equips

    # Hard error: apps listed in profile that don't exist
    missing_apps = [a for a in all_apps_in_profile if a not in app_map]
    if missing_apps:
        for a in missing_apps:
            print(f"  ✗ app '{a}' listed in profile but no manifest found in apps/")
        errors += len(missing_apps)

    # Hard error: init_flash references must be in apps_flash; same for init_sd.
    for entry in profile.get("init_flash", []):
        path = entry.get("path", "")
        if path.startswith("/bin/") and path.endswith(".dap"):
            app = path[len("/bin/"):-len(".dap")]
            if app not in profile.get("apps_flash", []):
                print(f"  ✗ init_flash references '{app}' but it's not in apps_flash")
                errors += 1
    for entry in profile.get("init_sd", []):
        path = entry.get("path", "")
        if path.startswith("/sd/") and path.endswith(".dap"):
            base = path.rsplit("/", 1)[-1][:-len(".dap")]
            if base not in profile.get("apps_sd", []):
                print(f"  ✗ init_sd references '{base}' but it's not in apps_sd")
                errors += 1

    # Soft warning: app declares a permission whose kernel driver isn't enabled
    # on this board. Most apps over-grant permissions (`permissions: 255` is a
    # common "all bits set" pattern); the kernel just declines symbol lookup
    # for unauthorised exports. Useful diagnostic, not a build blocker.
    for app_name in all_apps_in_profile:
        if app_name not in app_map:
            continue
        try:
            m = load_manifest(app_map[app_name])
            validate_manifest(m)
        except SystemExit as e:
            print(f"  ✗ app '{app_name}': {e}")
            errors += 1
            continue
        mask = int(m.get("permissions", 0))
        problems = check_app(app_name, mask, sdk_lines)
        for prob in problems:
            print(f"  ⚠ {prob}")
            warnings += 1

    n_flash = len(profile.get("apps_flash", []))
    n_sd    = len(profile.get("apps_sd", []))
    if errors == 0:
        suffix = f", {warnings} warning(s) — perms vs kernel config" if warnings else ""
        print(f"  ✓ profile OK ({n_flash} apps in /bin, {n_sd} on /sd){suffix}")
        return 0
    print(f"  {errors} error(s), {warnings} warning(s) — fix the errors before build")
    return 1


# ---------------------------------------------------------------------------
# build — buildall (only the apps in the profile)
# ---------------------------------------------------------------------------

def build_profile(profile: dict, plugin, arch, cpu, board_cfg, tc) -> int:
    """Compile every app listed in apps_flash + apps_sd. Returns 0/1."""
    from .builder import build_single, clean_single
    app_map = _app_map()
    targets = list(dict.fromkeys(
        list(profile.get("apps_flash", [])) + list(profile.get("apps_sd", []))
    ))
    if not targets:
        print("  (profile lists no apps — nothing to build)")
        return 0

    n_ok, n_fail = 0, 0
    for name in targets:
        if name not in app_map:
            print(f"  ✗ app '{name}' missing — skipping")
            n_fail += 1
            continue
        rel = app_map[name].relative_to(DUNEOS_ROOT)
        print(f"{'='*60}\n  {rel}\n{'='*60}")
        if build_single(app_map[name], plugin, arch, cpu, board_cfg, tc):
            n_ok += 1
        else:
            n_fail += 1
        print()
    print(f"  Build: {n_ok}/{len(targets)} OK" + (f"  {n_fail} FAILED" if n_fail else ""))
    return 0 if n_fail == 0 else 1


# ---------------------------------------------------------------------------
# flash — kernel + sysbin (with profile's apps_flash subset)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Size accounting (Phase 25.3)
# ---------------------------------------------------------------------------

def parse_partition_sizes(board: str) -> dict[str, int]:
    """Parse boards/<board>/partitions.csv → {name: size_bytes}."""
    p = DUNEOS_ROOT / "boards" / board / "partitions.csv"
    out: dict[str, int] = {}
    if not p.exists():
        return out
    for line in p.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        cols = [c.strip() for c in s.split(",")]
        if len(cols) < 5:
            continue
        name, _typ, _sub, _off, size = cols[:5]
        try:
            out[name] = int(size, 0)   # accepts "0x180000"
        except ValueError:
            # ESP-IDF also accepts "1M" / "256K" shorthand.
            sz = size.upper().rstrip("B")
            mult = 1
            if sz.endswith("K"): mult, sz = 1024,        sz[:-1]
            elif sz.endswith("M"): mult, sz = 1024*1024, sz[:-1]
            try:
                out[name] = int(sz) * mult
            except ValueError:
                pass
    return out


def app_elf_size(app_name: str) -> int:
    """Return size in bytes of an app's built ELF, or 0 if not built / not found."""
    for app_dir, _ in find_apps():
        try:
            m = load_manifest(app_dir)
        except SystemExit:
            continue
        if m.get("name") == app_name:
            elf = app_dir / "build" / "app.elf"
            return elf.stat().st_size if elf.exists() else 0
    return 0


def kernel_image_size() -> int:
    """Return the built kernel ELF/binary size in bytes (0 if missing)."""
    for candidate in (DUNEOS_ROOT / "build" / "duneos.bin",
                      DUNEOS_ROOT / "build" / "duneos.elf"):
        if candidate.exists():
            return candidate.stat().st_size
    return 0


def compute_image_sizes(profile: dict) -> dict:
    """Aggregate the projected image size from a profile.

    Returns a dict with:
      flash_total:      sum of apps_flash ELF sizes (bytes)
      flash_max:        sysbin partition size (bytes, from partitions.csv)
      sd_total:         sum of apps_sd ELF sizes (informational)
      kernel_size:      built kernel image size (or 0 if not built)
      kernel_max:       factory partition size
      per_app_flash:    [(name, size), …] sorted desc — for detail listings
      per_app_sd:       [(name, size), …] sorted desc
    """
    parts = parse_partition_sizes(profile["board"])
    flash_apps = profile.get("apps_flash", [])
    sd_apps    = profile.get("apps_sd",    [])

    per_app_flash = sorted(((n, app_elf_size(n)) for n in flash_apps),
                           key=lambda t: -t[1])
    per_app_sd    = sorted(((n, app_elf_size(n)) for n in sd_apps),
                           key=lambda t: -t[1])

    return {
        "flash_total":   sum(s for _, s in per_app_flash),
        "flash_max":     parts.get("sysbin", 0),
        "sd_total":      sum(s for _, s in per_app_sd),
        "kernel_size":   kernel_image_size(),
        "kernel_max":    parts.get("factory", 0),
        "per_app_flash": per_app_flash,
        "per_app_sd":    per_app_sd,
    }


def _fmt_kb(n: int) -> str:
    """Format byte count as 'NN.N KB' (matches kernel's klog convention)."""
    return f"{n/1024:6.1f} KB"


def _bar(used: int, total: int, width: int = 32) -> str:
    """Render a progress bar string `[████░░░] used/total KB (pp%)`."""
    if total <= 0:
        return _fmt_kb(used)
    frac = max(0.0, min(1.0, used / total))
    filled = int(frac * width)
    bar = "█" * filled + "░" * (width - filled)
    pct = int(frac * 100)
    return f"[{bar}] {_fmt_kb(used)} / {_fmt_kb(total)} ({pct}%)"


def report_sizes(profile: dict) -> int:
    """Print the size report for a profile. Returns non-zero if overflow detected."""
    info = compute_image_sizes(profile)
    name = profile["name"]
    print(f"Image size — profile '{name}' (board: {profile['board']})\n")

    over = 0

    # Kernel (factory partition)
    print("  Kernel    " + _bar(info["kernel_size"], info["kernel_max"]))
    if info["kernel_size"] == 0:
        print("            [hint] kernel not built — `dbt flash kernel --build-only`")
    elif info["kernel_max"] and info["kernel_size"] > info["kernel_max"]:
        print("            ✗ OVERFLOW — kernel binary exceeds factory partition")
        over += 1

    # Sysbin (apps_flash)
    print()
    print("  sysbin /  " + _bar(info["flash_total"], info["flash_max"]))
    if info["flash_max"] and info["flash_total"] > info["flash_max"]:
        print("            ✗ OVERFLOW — staged apps exceed sysbin partition")
        print("            [hint] LittleFS overhead is on top; budget ~5% headroom")
        over += 1
    if info["per_app_flash"]:
        print("            top apps:")
        for n, s in info["per_app_flash"][:5]:
            print(f"              {n:<24}  {_fmt_kb(s)}")

    # SD (informational only — depends on the actual SD card)
    print()
    print(f"  /sd       {_fmt_kb(info['sd_total'])} total ({len(info['per_app_sd'])} apps, informational)")
    if info["per_app_sd"]:
        for n, s in info["per_app_sd"][:5]:
            print(f"              {n:<24}  {_fmt_kb(s)}")

    return over


# ---------------------------------------------------------------------------
# Diff (Phase 25.3)
# ---------------------------------------------------------------------------

def report_diff(active: dict, other: dict) -> None:
    """Print a unified-diff-style listing of two profiles' apps lists + init."""
    print(f"Diff: '{active['name']}' (active)  vs  '{other['name']}'\n")

    if active["board"] != other["board"]:
        print(f"  ⚠ different boards: {active['board']}  vs  {other['board']}\n")

    for key in ("apps_flash", "apps_sd"):
        a = set(active.get(key, []))
        b = set(other.get(key, []))
        only_a = sorted(a - b)
        only_b = sorted(b - a)
        if only_a or only_b:
            print(f"  {key}:")
            for n in only_a:
                print(f"    - {n}    (in {active['name']} only)")
            for n in only_b:
                print(f"    + {n}    (in {other['name']} only)")
        else:
            print(f"  {key}: identical ({len(a)} apps)")

    for key in ("init_flash", "init_sd"):
        # Normalise to (path, restart) tuples for set comparison
        a = {(e.get("path",""), e.get("restart","")) for e in active.get(key, [])}
        b = {(e.get("path",""), e.get("restart","")) for e in other.get(key, [])}
        if a != b:
            print(f"  {key}:")
            for p, r in sorted(a - b):
                print(f"    - {p}  restart: {r}    (in {active['name']} only)")
            for p, r in sorted(b - a):
                print(f"    + {p}  restart: {r}    (in {other['name']} only)")
        else:
            print(f"  {key}: identical ({len(a)} entries)")


def write_flash_init_yaml(profile: dict, out: Path) -> None:
    """Render profile['init_flash'] to a YAML file readable by init.c parser."""
    lines = ["# DuneOS /init.yaml — generated by `dbt system flash` from",
             f"# profile '{profile['name']}' (board: {profile['board']}).",
             "# Do not hand-edit — re-run dbt system flash after editing the profile.",
             "services:"]
    for entry in profile.get("init_flash", []):
        path    = entry.get("path", "")
        restart = entry.get("restart", "no")
        lines.append(f"  - path: {path}")
        lines.append(f"    restart: {restart}")
    out.write_text("\n".join(lines) + "\n")
