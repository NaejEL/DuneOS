"""SPEC-leg-10 — the CI matrix is derived from the tree, not listed in ci.yml.

Everything here reads tracked files only: board.yaml, profile.yaml,
arch/*/arch.cmake, ci.yml. The generated per-board files are gitignored and the
root conftest read guard would fail any test that opened one (LEG-38).
"""

import shutil
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from dbt.ci_matrix import discover  # noqa: E402

CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

EXPECTED = {
    "kernel_boards": ["esp32s3-devkitc", "kincony-A16",
                      "lilygo-t-embed-cc1101", "m5stack-cardputer"],
    "qemu_boards": ["esp32s3-qemu", "esp32s3-qemu-psram"],
    "check_profiles": ["cardputer-contest", "cardputer-default", "cardputer-net",
                       "cardputer-recovery", "esp32s3-devkitc-default",
                       "kincony-a16-default", "t-embed-default", "t-embed-recovery"],
    "build_profiles": ["cardputer-contest", "kincony-a16-default", "t-embed-default"],
    "excluded": ["esp32c3-devkitc", "esp32p4-devkitm"],
}


def clone(root: Path, dest: Path) -> Path:
    """The tracked inputs of the derivation, copied to a writable tree.

    File by file rather than copytree: boards/ also holds the gitignored bspgen
    output, and copying it would trip the LEG-38 read guard.
    """
    for pattern in ("boards/*/board.yaml", "profiles/*/profile.yaml"):
        for src in root.glob(pattern):
            target = dest / src.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
    for cmake in (root / "arch").glob("*/arch.cmake"):
        target = dest / "arch" / cmake.parent.name / "arch.cmake"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("")
    return dest


def write_board(root: Path, name: str, **board) -> None:
    path = root / "boards" / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "board.yaml").write_text(yaml.safe_dump({"board": {"name": name, **board}}))


def write_profile(root: Path, name: str, board: str, apps: list) -> None:
    path = root / "profiles" / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "profile.yaml").write_text(
        yaml.safe_dump({"name": name, "board": board, "apps_flash": apps}))


def assert_complete(matrix: dict, root: Path) -> None:
    """Every board lands in exactly one bucket, every profile in one or the other."""
    buckets = (set(matrix["kernel_boards"]), set(matrix["qemu_boards"]),
               set(matrix["excluded"]))
    for path in (root / "boards").glob("*/board.yaml"):
        hits = [b for b in buckets if path.parent.name in b]
        assert len(hits) == 1, f"{path.parent.name} is in {len(hits)} buckets"
    buildable = buckets[0] | buckets[1]
    for path in (root / "profiles").glob("*/profile.yaml"):
        name = path.parent.name
        board = yaml.safe_load(path.read_text()).get("board")
        if board in buildable:
            assert name in matrix["check_profiles"]
        else:
            assert name not in matrix["check_profiles"]


# --- Criterion 1 ---------------------------------------------------------

@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_real_tree_matrix(key):
    matrix = discover(REPO_ROOT)
    value = matrix[key]
    assert sorted(value) == EXPECTED[key]


def test_real_tree_is_complete():
    assert_complete(discover(REPO_ROOT), REPO_ROOT)


# --- Rule tests on synthetic trees ---------------------------------------

def test_board_without_arch_cmake_is_excluded(tmp_path):
    write_board(tmp_path, "synth", cpu="esp32c3")
    matrix = discover(tmp_path)
    assert matrix["kernel_boards"] == []
    assert "riscv32" in matrix["excluded"]["synth"]


def test_board_with_arch_cmake_is_a_kernel_board(tmp_path):
    write_board(tmp_path, "synth", cpu="esp32s3")
    (tmp_path / "arch" / "xtensa_esp32s3").mkdir(parents=True)
    (tmp_path / "arch" / "xtensa_esp32s3" / "arch.cmake").write_text("")
    assert discover(tmp_path)["kernel_boards"] == ["synth"]


def test_qemu_board_is_not_a_kernel_board(tmp_path):
    write_board(tmp_path, "synth", cpu="esp32s3", qemu=True)
    (tmp_path / "arch" / "xtensa_esp32s3").mkdir(parents=True)
    (tmp_path / "arch" / "xtensa_esp32s3" / "arch.cmake").write_text("")
    matrix = discover(tmp_path)
    assert matrix["kernel_boards"] == []
    assert matrix["qemu_boards"] == ["synth"]


def test_profile_with_no_app_is_checked_but_not_built(tmp_path):
    write_board(tmp_path, "synth", cpu="esp32s3")
    (tmp_path / "arch" / "xtensa_esp32s3").mkdir(parents=True)
    (tmp_path / "arch" / "xtensa_esp32s3" / "arch.cmake").write_text("")
    write_profile(tmp_path, "empty", "synth", [])
    matrix = discover(tmp_path)
    assert matrix["check_profiles"] == ["empty"]
    assert matrix["build_profiles"] == []


def test_build_profile_is_the_one_with_most_apps(tmp_path):
    write_board(tmp_path, "synth", cpu="esp32s3")
    (tmp_path / "arch" / "xtensa_esp32s3").mkdir(parents=True)
    (tmp_path / "arch" / "xtensa_esp32s3" / "arch.cmake").write_text("")
    write_profile(tmp_path, "small", "synth", ["a"])
    write_profile(tmp_path, "big", "synth", ["a", "b", "c"])
    assert discover(tmp_path)["build_profiles"] == ["big"]


def test_derivation_keys_on_cpu_not_arch(tmp_path):
    """boards/esp32p4-devkitm/board.yaml declares no `arch:`, and esp_idf.py
    defaults the missing key to xtensa-esp32s3. A board whose `arch:` lies must
    still follow its cpu."""
    write_board(tmp_path, "liar", cpu="esp32p4", arch="xtensa-esp32s3")
    (tmp_path / "arch" / "xtensa_esp32s3").mkdir(parents=True)
    (tmp_path / "arch" / "xtensa_esp32s3" / "arch.cmake").write_text("")
    assert discover(tmp_path)["kernel_boards"] == []
    assert "liar" in discover(tmp_path)["excluded"]


# --- Criterion 2: adding the arch is the only thing CI needs -------------

def test_adding_riscv_arch_cmake_pulls_both_boards_in(tmp_path):
    root = clone(REPO_ROOT, tmp_path / "tree")
    before = discover(root)
    assert sorted(before["excluded"]) == EXPECTED["excluded"]

    (root / "arch" / "riscv32").mkdir(parents=True, exist_ok=True)
    (root / "arch" / "riscv32" / "arch.cmake").write_text("")

    after = discover(root)
    assert after["excluded"] == {}
    assert set(after["kernel_boards"]) == set(EXPECTED["kernel_boards"]) | {
        "esp32c3-devkitc", "esp32p4-devkitm"}
    assert set(after["check_profiles"]) == set(EXPECTED["check_profiles"]) | {
        "esp32c3-devkitc-default", "esp32p4-devkitm-default"}
    assert_complete(after, root)


# --- Criterion 3: an unlisted board reddens the completeness test --------

def test_a_new_board_fails_the_expected_membership(tmp_path):
    root = clone(REPO_ROOT, tmp_path / "tree")
    write_board(root, "synth-s3", cpu="esp32s3")
    matrix = discover(root)
    assert_complete(matrix, root)
    with pytest.raises(AssertionError):
        assert sorted(matrix["kernel_boards"]) == EXPECTED["kernel_boards"]


def test_a_profile_on_an_unlisted_board_fails_completeness(tmp_path):
    root = clone(REPO_ROOT, tmp_path / "tree")
    write_profile(root, "orphan", "m5stack-cardputer", ["cat"])
    with pytest.raises(AssertionError):
        assert sorted(discover(root)["check_profiles"]) == EXPECTED["check_profiles"]


# --- Criterion 4: no literal board or profile name in a matrix -----------

def matrix_values(workflow: dict) -> list:
    out = []
    for job in workflow.get("jobs", {}).values():
        matrix = (job.get("strategy") or {}).get("matrix") or {}
        for value in matrix.values():
            out.append(value)
    return out


def test_every_matrix_is_a_fromjson_of_a_discover_output():
    workflow = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    values = matrix_values(workflow)
    assert values, "no strategy.matrix found — the wiring test would be vacuous"
    for value in values:
        assert isinstance(value, str), f"literal matrix list in ci.yml: {value!r}"
        assert value.startswith("${{ fromJSON(needs.discover.outputs."), value


def test_no_board_or_profile_name_appears_as_a_matrix_value():
    workflow = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    names = {p.parent.name for p in (REPO_ROOT / "boards").glob("*/board.yaml")}
    names |= {p.parent.name for p in (REPO_ROOT / "profiles").glob("*/profile.yaml")}
    for value in matrix_values(workflow):
        rendered = value if isinstance(value, str) else " ".join(map(str, value))
        for name in names:
            assert name not in rendered, f"'{name}' is a literal matrix value"


def test_the_wiring_test_bites_on_an_injected_literal(tmp_path):
    copy = tmp_path / "ci.yml"
    text = CI_YML.read_text(encoding="utf-8")
    copy.write_text(text.replace(
        "        board: ${{ fromJSON(needs.discover.outputs.kernel_boards) }}",
        "        board: [m5stack-cardputer]"), encoding="utf-8")
    workflow = yaml.safe_load(copy.read_text(encoding="utf-8"))
    values = matrix_values(workflow)
    assert ["m5stack-cardputer"] in values, "the injection did not take"
    with pytest.raises(AssertionError):
        for value in values:
            assert isinstance(value, str), f"literal matrix list in ci.yml: {value!r}"
