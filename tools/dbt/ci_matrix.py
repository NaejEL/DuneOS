"""Derive the CI board and profile matrix from the tree.

A board is in the matrix when the architecture its `cpu` maps to has an
`arch/<dir>/arch.cmake`. Keyed on `cpu` rather than `arch:` because
`boards/esp32p4-devkitm/board.yaml` declares no `arch:` key at all.

Importable (`from dbt.ci_matrix import discover`) and runnable as a script:

    python tools/dbt/ci_matrix.py --github-output "$GITHUB_OUTPUT"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("ERROR: PyYAML required for dbt.ci_matrix. `pip install pyyaml`.")


REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Kept identical to duneos-bspgen.py's VALID_CPUS / XTENSA_CPUS. A cpu outside
# VALID_CPUS is a typo, not a future architecture: classifying it as riscv32
# would hide it in `excluded` today and silently promote it to a build the day
# arch/riscv32/arch.cmake lands, where it would fail in bspgen instead.
VALID_CPUS = {"esp32", "esp32s2", "esp32s3",
              "esp32c2", "esp32c3", "esp32c5", "esp32c6",
              "esp32h2", "esp32p4"}
XTENSA_CPUS = {"esp32", "esp32s2", "esp32s3"}

OUTPUT_KEYS = ("kernel_boards", "qemu_boards", "check_profiles",
               "build_profiles", "excluded")

# An empty matrix is valid YAML that expands to zero jobs and reports success,
# so these three must never be empty on a tree that has any board at all.
NON_EMPTY_KEYS = ("kernel_boards", "qemu_boards", "build_profiles")


class MatrixError(Exception):
    pass


def arch_dir_for_cpu(cpu: str) -> str:
    return f"xtensa_{cpu}" if cpu in XTENSA_CPUS else "riscv32"


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _boards(root: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted((root / "boards").glob("*/board.yaml")):
        name = path.parent.name
        board = _load(path).get("board")
        if not isinstance(board, dict):
            out[name] = {"cpu": None, "qemu": False, "arch_dir": None,
                         "reason": "board.yaml has no `board:` mapping"}
            continue
        cpu = board.get("cpu")
        if not cpu or not isinstance(cpu, str):
            out[name] = {"cpu": None, "qemu": False, "arch_dir": None,
                         "reason": "board.cpu is absent or not a string"}
            continue
        if cpu not in VALID_CPUS:
            out[name] = {"cpu": cpu, "qemu": False, "arch_dir": None,
                         "reason": f"cpu {cpu!r} is not one bspgen accepts"}
            continue
        arch_dir = arch_dir_for_cpu(cpu)
        buildable = (root / "arch" / arch_dir / "arch.cmake").is_file()
        out[name] = {
            "cpu": cpu,
            # bspgen refuses a non-boolean `qemu:`, so anything else here is a
            # board.yaml that will not generate. Demoting it silently would drop
            # its boot gate with nothing red, so it is an error, not a default.
            "qemu": _qemu_flag(name, board),
            "arch_dir": arch_dir,
            "reason": None if buildable
                      else f"cpu {cpu}: arch/{arch_dir}/arch.cmake absent",
        }
    return out


def _qemu_flag(board_name: str, board: dict) -> bool:
    qemu = board.get("qemu", False)
    if not isinstance(qemu, bool):
        raise MatrixError(
            f"boards/{board_name}/board.yaml: board.qemu must be a boolean, got "
            f"{type(qemu).__name__} ({qemu!r}) — write `qemu: true` or omit it")
    return qemu


def _app_list(value) -> list:
    return list(value) if isinstance(value, list) else []


def _profiles(root: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted((root / "profiles").glob("*/profile.yaml")):
        profile = _load(path)
        # list() on a bare string yields its characters, so `apps_flash: abc`
        # would score 3 and could win the build_profiles election.
        apps = _app_list(profile.get("apps_flash")) + _app_list(profile.get("apps_sd"))
        out[path.parent.name] = {
            "board": profile.get("board"),
            "n_apps": len(set(apps)),
        }
    return out


def discover(root: Path = REPO_ROOT) -> dict:
    boards = _boards(root)
    profiles = _profiles(root)

    buildable = {n for n, b in boards.items() if b["reason"] is None}

    kernel_boards = sorted(n for n in buildable if not boards[n]["qemu"])
    qemu_boards = sorted(n for n in buildable if boards[n]["qemu"])
    excluded = {n: boards[n]["reason"] for n in sorted(boards) if boards[n]["reason"]}

    check_profiles = sorted(n for n, p in profiles.items() if p["board"] in buildable)

    # A profile whose board does not exist belongs to no bucket: it would be
    # neither checked nor reported, and no completeness assertion over the
    # boards can see it. Named here so it is a failure rather than a silence.
    orphans = sorted(n for n, p in profiles.items()
                     if p["board"] not in boards)

    build_profiles = []
    for board in sorted(buildable):
        # Most apps wins; ties go to the alphabetically first name so the
        # emitted matrix is stable across runs and machines, which a set or a
        # dict-order pick would not be.
        candidates = sorted((n for n, p in profiles.items()
                             if p["board"] == board and p["n_apps"] >= 1),
                            key=lambda n: (-profiles[n]["n_apps"], n))
        if candidates:
            build_profiles.append(candidates[0])
    build_profiles.sort()

    return {
        "kernel_boards": kernel_boards,
        "qemu_boards": qemu_boards,
        "check_profiles": check_profiles,
        "build_profiles": build_profiles,
        "excluded": excluded,
        "orphan_profiles": orphans,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--github-output", type=Path, default=None,
                        help="File to append 'key=<json>' lines to")
    args = parser.parse_args(argv)

    try:
        matrix = discover(args.root)
    except MatrixError as exc:
        print(f"ci_matrix: {exc}", file=sys.stderr)
        return 1

    lines = [f"{key}={json.dumps(matrix[key], separators=(',', ':'))}"
             for key in OUTPUT_KEYS]

    # Printed unconditionally: a malformed line surfaces in GitHub only as
    # "matrix must be an array", with nothing to read it against.
    for line in lines:
        print(line)
    for board, reason in matrix["excluded"].items():
        print(f"excluded: {board} — {reason}", file=sys.stderr)

    empty = [key for key in NON_EMPTY_KEYS if not matrix[key]]
    if empty:
        print(f"ci_matrix: {', '.join(empty)} empty — the matching jobs would "
              f"expand to zero entries and report success", file=sys.stderr)
    if matrix["orphan_profiles"]:
        print(f"ci_matrix: profiles naming no existing board: "
              f"{', '.join(matrix['orphan_profiles'])}", file=sys.stderr)
    if empty or matrix["orphan_profiles"]:
        return 1

    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
