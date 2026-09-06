"""
sdkconfig_check — refuse a build whose cached sdkconfig contradicts the sources.

ESP-IDF applies an `SDKCONFIG_DEFAULTS` file only to symbols **not already
present** in a build directory's `sdkconfig`. A build dir configured before one
of those files changed therefore keeps its old values, reconfigures with exit 0
and prints "Defaults policy: sdkconfig" — the new declaration is read and
discarded in silence.

That is not cosmetic drift. SPEC-leg-37 raised the CardPuter's main-task stack
from ESP-IDF's 3584 B default to a measured 4608 B because 3584 B overflowed into
the heap and bricked the board, and turned on the stack watchpoint so the next
overflow is named. A build dir predating either change flashes 3584 B with the
watchpoint off — the exact configuration the change closes, with nothing on
stderr. So the declared sources are the truth and a disagreement is refused here
rather than found on hardware.

Both layers CMakeLists.txt passes as SDKCONFIG_DEFAULTS are checked, in the same
order it lists them (the board fragment overrides the root defaults):

    sdkconfig.defaults                    project-wide POLICY   (ADR 040)
    boards/<board>/sdkconfig.board        per-board MEASUREMENT (ADR 040)

Only genuine contradictions are reported. A symbol a source declares but the
build sdkconfig does not carry at all is left alone: Kconfig legitimately drops a
symbol whose dependencies are unmet, and turning that into a refusal would block
the build instead of protecting the board.
"""
from pathlib import Path

from .constants import DUNEOS_ROOT

# "n" and an absent-because-unset symbol are the same statement in Kconfig, and
# a source fragment writes the first form while a generated sdkconfig writes the
# second. Normalise, or every `CONFIG_SPIRAM=n` reads as a conflict.
NOT_SET = "n"


def parse_sdkconfig(text: str) -> dict:
    """Symbol -> value for one Kconfig file (source fragment or built sdkconfig)."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            body = line.lstrip("#").strip()
            if body.startswith("CONFIG_") and body.endswith("is not set"):
                out[body[: -len("is not set")].strip()] = NOT_SET
            continue
        if not line.startswith("CONFIG_") or "=" not in line:
            continue
        sym, _, val = line.partition("=")
        val = val.strip()
        out[sym.strip()] = NOT_SET if val == "n" else val
    return out


def find_conflicts(declared: dict, effective: dict) -> list:
    """(symbol, declared, effective) triples the build dir would silently keep."""
    conflicts = []
    for sym, want in declared.items():
        if sym not in effective:
            continue
        got = effective[sym]
        if got != want:
            conflicts.append((sym, want, got))
    return sorted(conflicts)


def declared_config(sources) -> dict:
    """Merge SDKCONFIG_DEFAULTS sources in CMake order — later wins."""
    merged: dict[str, str] = {}
    for path in sources:
        path = Path(path)
        if path.exists():
            merged.update(parse_sdkconfig(path.read_text(encoding="utf-8")))
    return merged


def default_sources(board: str) -> list:
    """The SDKCONFIG_DEFAULTS list CMakeLists.txt builds, in its order."""
    return [DUNEOS_ROOT / "sdkconfig.defaults",
            DUNEOS_ROOT / "boards" / board / "sdkconfig.board"]


def format_conflicts(conflicts: list, sdkconfig: Path, sources) -> str:
    named = "\n".join(f"    {Path(s)}" for s in sources)
    lines = [
        f"ERROR: the cached Kconfig in {sdkconfig} contradicts what this board declares.",
        "",
        "Declared by:",
        named,
        "",
        "ESP-IDF only applies a default for a symbol that is not already present in",
        "the build directory's sdkconfig, so these values would be kept as they are",
        "and built or flashed, with no warning:",
        "",
    ]
    for sym, want, got in conflicts:
        lines.append(f"  {sym}")
        lines.append(f"      declared: {want}")
        lines.append(f"      stale in the build directory: {got}")
    lines += [
        "",
        "Discard the cached config, then build again:",
        f"    rm {sdkconfig}",
        "  or clear the whole build directory:",
        "    idf.py fullclean",
    ]
    return "\n".join(lines)


def check_build_sdkconfig(board: str, sdkconfig) -> list:
    """Conflicts between what the board declares and a build dir's sdkconfig.

    Returns [] when the build dir is clean or not yet configured — a first build
    has no cached sdkconfig and nothing to contradict.
    """
    sdkconfig = Path(sdkconfig)
    if not sdkconfig.exists():
        return []
    declared = declared_config(default_sources(board))
    if not declared:
        return []
    effective = parse_sdkconfig(sdkconfig.read_text(encoding="utf-8"))
    return find_conflicts(declared, effective)


def enforce(board: str, sdkconfig, printer=print) -> None:
    """Abort the run when the build dir would silently keep a stale value."""
    conflicts = check_build_sdkconfig(board, sdkconfig)
    if not conflicts:
        return
    printer(format_conflicts(conflicts, Path(sdkconfig), default_sources(board)))
    raise SystemExit(1)
