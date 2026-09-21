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

XTENSA_CPUS = {"esp32", "esp32s2", "esp32s3"}

OUTPUT_KEYS = ("kernel_boards", "qemu_boards", "check_profiles",
               "build_profiles", "excluded")


def arch_dir_for_cpu(cpu: str) -> str:
    return f"xtensa_{cpu}" if cpu in XTENSA_CPUS else "riscv32"


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _boards(root: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted((root / "boards").glob("*/board.yaml")):
        name = path.parent.name
        board = _load(path).get("board") or {}
        cpu = board.get("cpu")
        if not cpu:
            out[name] = {"cpu": None, "qemu": False, "arch_dir": None,
                         "reason": "board.cpu is absent"}
            continue
        arch_dir = arch_dir_for_cpu(cpu)
        buildable = (root / "arch" / arch_dir / "arch.cmake").is_file()
        out[name] = {
            "cpu": cpu,
            "qemu": board.get("qemu") is True,
            "arch_dir": arch_dir,
            "reason": None if buildable
                      else f"cpu {cpu}: arch/{arch_dir}/arch.cmake absent",
        }
    return out


def _profiles(root: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted((root / "profiles").glob("*/profile.yaml")):
        profile = _load(path)
        apps = list(profile.get("apps_flash") or []) + list(profile.get("apps_sd") or [])
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

    build_profiles = []
    for board in sorted(buildable):
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
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--github-output", type=Path, default=None,
                        help="File to append 'key=<json>' lines to")
    args = parser.parse_args(argv)

    matrix = discover(args.root)
    lines = [f"{key}={json.dumps(matrix[key], separators=(',', ':'))}"
             for key in OUTPUT_KEYS]

    # Printed unconditionally: a malformed line surfaces in GitHub only as
    # "matrix must be an array", with nothing to read it against.
    for line in lines:
        print(line)
    for board, reason in matrix["excluded"].items():
        print(f"excluded: {board} — {reason}", file=sys.stderr)

    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
