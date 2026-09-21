"""SPEC-leg-10 — the CI matrix is derived from the tree, not listed in ci.yml.

Everything here reads tracked files only: board.yaml, profile.yaml,
arch/*/arch.cmake, ci.yml. The generated per-board files are gitignored and the
root conftest read guard would fail any test that opened one (LEG-38).

The negative-proof tests call the same helper as the positive ones. Re-inlining
an assertion to prove it bites proves only that the copy bites.
"""

import shutil
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from dbt.ci_matrix import (MatrixError, NON_EMPTY_KEYS, OUTPUT_KEYS,  # noqa: E402
                           discover, main)

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

XTENSA_ARCH = ("arch", "xtensa_esp32s3", "arch.cmake")


# --- Tree helpers --------------------------------------------------------

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


def write_arch(root: Path, *parts: str) -> None:
    path = root.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")


def write_board(root: Path, name: str, **board) -> None:
    path = root / "boards" / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "board.yaml").write_text(yaml.safe_dump({"board": {"name": name, **board}}))


def write_raw_board(root: Path, name: str, text: str) -> None:
    path = root / "boards" / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "board.yaml").write_text(text)


def write_profile(root: Path, name: str, board: str, apps: list) -> None:
    path = root / "profiles" / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "profile.yaml").write_text(
        yaml.safe_dump({"name": name, "board": board, "apps_flash": apps}))


def xtensa_board(root: Path, name: str = "synth", **board) -> None:
    board.setdefault("cpu", "esp32s3")
    write_board(root, name, **board)
    write_arch(root, *XTENSA_ARCH)


# --- The assertions under test, named once -------------------------------

def assert_expected_membership(matrix: dict) -> None:
    """The real tree's exact buckets. Criterion 1, and what criterion 3 breaks."""
    for key, expected in EXPECTED.items():
        assert sorted(matrix[key]) == expected, key


def assert_complete(matrix: dict, root: Path) -> None:
    """Every board in exactly one bucket; every profile accounted for."""
    buckets = (set(matrix["kernel_boards"]), set(matrix["qemu_boards"]),
               set(matrix["excluded"]))
    for path in (root / "boards").glob("*/board.yaml"):
        hits = [b for b in buckets if path.parent.name in b]
        assert len(hits) == 1, f"{path.parent.name} is in {len(hits)} buckets"
    assert matrix["orphan_profiles"] == [], (
        "profiles naming no existing board: " + ", ".join(matrix["orphan_profiles"]))
    buildable = buckets[0] | buckets[1]
    for path in (root / "profiles").glob("*/profile.yaml"):
        name = path.parent.name
        board = yaml.safe_load(path.read_text()).get("board")
        # No `else: assert name not in check_profiles` — that is vacuously true
        # for a profile the module dropped, which is the one failure it has.
        if board in buildable:
            assert name in matrix["check_profiles"], name
        else:
            assert board in matrix["excluded"], f"{name} names unknown board {board!r}"


def matrix_values(workflow: dict) -> list:
    out = []
    for job in workflow.get("jobs", {}).values():
        matrix = (job.get("strategy") or {}).get("matrix") or {}
        for key, value in matrix.items():
            if key in ("include", "exclude"):
                for entry in value if isinstance(value, list) else []:
                    out.extend((entry or {}).values())
                continue
            out.append(value)
    return out


def assert_matrix_is_derived(workflow: dict) -> None:
    """Criterion 3's wiring rule, and what criterion 4 injects against."""
    values = matrix_values(workflow)
    assert values, "no strategy.matrix found — this assertion would be vacuous"
    prefix = "${{ fromJSON(needs.discover.outputs."
    for value in values:
        assert isinstance(value, str), f"literal matrix list in ci.yml: {value!r}"
        assert value.startswith(prefix), f"matrix value is not derived: {value!r}"
        emitted = value[len(prefix):].split(")", 1)[0]
        assert emitted in OUTPUT_KEYS, (
            f"matrix reads needs.discover.outputs.{emitted}, which discover "
            f"does not emit; it emits {', '.join(OUTPUT_KEYS)}")


def workflow_from(text: str) -> dict:
    return yaml.safe_load(text)


# --- Criterion 1 ---------------------------------------------------------

def test_real_tree_matrix():
    assert_expected_membership(discover(REPO_ROOT))


def test_real_tree_is_complete():
    assert_complete(discover(REPO_ROOT), REPO_ROOT)


# --- Rule tests on synthetic trees ---------------------------------------

def test_board_without_arch_cmake_is_excluded(tmp_path):
    write_board(tmp_path, "synth", cpu="esp32c3")
    matrix = discover(tmp_path)
    assert matrix["kernel_boards"] == []
    assert "riscv32" in matrix["excluded"]["synth"]


def test_board_with_arch_cmake_is_a_kernel_board(tmp_path):
    xtensa_board(tmp_path)
    assert discover(tmp_path)["kernel_boards"] == ["synth"]


def test_qemu_board_is_not_a_kernel_board(tmp_path):
    xtensa_board(tmp_path, qemu=True)
    matrix = discover(tmp_path)
    assert matrix["kernel_boards"] == []
    assert matrix["qemu_boards"] == ["synth"]


@pytest.mark.parametrize("value", ["true", 1, "yes"])
def test_non_boolean_qemu_is_an_error_not_a_demotion(tmp_path, value):
    xtensa_board(tmp_path, qemu=value)
    with pytest.raises(MatrixError):
        discover(tmp_path)


def test_profile_with_no_app_is_checked_but_not_built(tmp_path):
    xtensa_board(tmp_path)
    write_profile(tmp_path, "empty", "synth", [])
    matrix = discover(tmp_path)
    assert matrix["check_profiles"] == ["empty"]
    assert matrix["build_profiles"] == []


def test_build_profile_is_the_one_with_most_apps(tmp_path):
    xtensa_board(tmp_path)
    write_profile(tmp_path, "small", "synth", ["a"])
    write_profile(tmp_path, "big", "synth", ["a", "b", "c"])
    assert discover(tmp_path)["build_profiles"] == ["big"]


def test_build_profile_tie_breaks_alphabetically(tmp_path):
    xtensa_board(tmp_path)
    write_profile(tmp_path, "zeta", "synth", ["a", "b"])
    write_profile(tmp_path, "alpha", "synth", ["c", "d"])
    assert discover(tmp_path)["build_profiles"] == ["alpha"]


def test_derivation_keys_on_cpu_not_arch(tmp_path):
    """boards/esp32p4-devkitm/board.yaml declares no `arch:`, and esp_idf.py
    defaults the missing key to xtensa-esp32s3. A board whose `arch:` lies must
    still follow its cpu."""
    write_board(tmp_path, "liar", cpu="esp32p4", arch="xtensa-esp32s3")
    write_arch(tmp_path, *XTENSA_ARCH)
    matrix = discover(tmp_path)
    assert matrix["kernel_boards"] == []
    assert "liar" in matrix["excluded"]


def test_unknown_cpu_is_not_silently_called_riscv(tmp_path):
    """A typo must not park in `excluded` with a riscv32 reason and then enter
    kernel_boards the day arch/riscv32/arch.cmake lands."""
    write_board(tmp_path, "typo", cpu="esp32s33")
    write_arch(tmp_path, "arch", "riscv32", "arch.cmake")
    matrix = discover(tmp_path)
    assert matrix["kernel_boards"] == []
    assert "not one bspgen accepts" in matrix["excluded"]["typo"]


def test_board_yaml_without_a_board_mapping_is_named(tmp_path):
    write_raw_board(tmp_path, "broken", "board: hello\n")
    assert "no `board:` mapping" in discover(tmp_path)["excluded"]["broken"]


def test_profile_naming_an_unknown_board_is_reported(tmp_path):
    xtensa_board(tmp_path)
    write_profile(tmp_path, "orphan", "no-such-board", ["a"])
    matrix = discover(tmp_path)
    assert matrix["orphan_profiles"] == ["orphan"]
    assert "orphan" not in matrix["check_profiles"]
    with pytest.raises(AssertionError):
        assert_complete(matrix, tmp_path)


# --- Criterion 2: adding the arch is the only thing CI needs -------------

def test_adding_riscv_arch_cmake_pulls_both_boards_in(tmp_path):
    root = clone(REPO_ROOT, tmp_path / "tree")
    before = discover(root)
    assert sorted(before["excluded"]) == EXPECTED["excluded"]

    write_arch(root, "arch", "riscv32", "arch.cmake")

    after = discover(root)
    assert after["excluded"] == {}
    assert set(after["kernel_boards"]) == set(EXPECTED["kernel_boards"]) | {
        "esp32c3-devkitc", "esp32p4-devkitm"}
    assert set(after["check_profiles"]) == set(EXPECTED["check_profiles"]) | {
        "esp32c3-devkitc-default", "esp32p4-devkitm-default"}
    assert_complete(after, root)
    # The workflow that consumes this is unchanged and still derived.
    assert_matrix_is_derived(workflow_from(CI_YML.read_text(encoding="utf-8")))


# --- Criterion 3: an unlisted board reddens the completeness test --------

def test_a_new_board_fails_the_expected_membership(tmp_path):
    root = clone(REPO_ROOT, tmp_path / "tree")
    write_board(root, "synth-s3", cpu="esp32s3")
    matrix = discover(root)
    assert_complete(matrix, root)
    with pytest.raises(AssertionError):
        assert_expected_membership(matrix)


def test_a_profile_on_an_unlisted_board_fails_completeness(tmp_path):
    root = clone(REPO_ROOT, tmp_path / "tree")
    write_profile(root, "extra", "m5stack-cardputer", ["cat"])
    with pytest.raises(AssertionError):
        assert_expected_membership(discover(root))


# --- Criterion 4: no literal board or profile name in a matrix -----------

def test_every_matrix_is_a_fromjson_of_a_discover_output():
    assert_matrix_is_derived(workflow_from(CI_YML.read_text(encoding="utf-8")))


def tracked_names() -> set[str]:
    names = {p.parent.name for p in (REPO_ROOT / "boards").glob("*/board.yaml")}
    return names | {p.parent.name
                    for p in (REPO_ROOT / "profiles").glob("*/profile.yaml")}


def test_no_board_or_profile_name_appears_as_a_matrix_value():
    workflow = workflow_from(CI_YML.read_text(encoding="utf-8"))
    names = tracked_names()
    for value in matrix_values(workflow):
        rendered = value if isinstance(value, str) else " ".join(map(str, value))
        for name in names:
            assert name not in rendered, f"'{name}' is a literal matrix value"


# The matrix is only half of it. `echo m5stack-cardputer > .duneos_board` in a
# step body is what this cycle removed, and it is not a matrix value — a literal
# reintroduced there would pin one board again with every wiring test green.
def test_no_board_or_profile_name_appears_in_a_step_body():
    text = CI_YML.read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))
    for name in tracked_names():
        assert name not in body, (
            f"'{name}' is hardcoded in ci.yml; the matrix must come from "
            f"discover, and so must anything derived from it")


INJECTIONS = [
    "        board: [m5stack-cardputer]\n",
    "        board: [m5stack-cardputer, kincony-A16]\n",
    "        board:\n          - m5stack-cardputer\n",
    "        board: ${{ fromJSON(needs.discover.outputs.nonexistent) }}\n",
    "        board: ${{ fromJSON(env.BOARDS) }}\n",
]


@pytest.mark.parametrize("injected", INJECTIONS)
def test_the_wiring_assertion_bites_on_an_injected_literal(tmp_path, injected):
    original = "        board: ${{ fromJSON(needs.discover.outputs.kernel_boards) }}\n"
    text = CI_YML.read_text(encoding="utf-8")
    assert original in text
    copy = tmp_path / "ci.yml"
    copy.write_text(text.replace(original, injected), encoding="utf-8")
    with pytest.raises(AssertionError):
        assert_matrix_is_derived(workflow_from(copy.read_text(encoding="utf-8")))


# --- main(): the entry point ci.yml actually calls ------------------------
#
# In-process, never through tools/dbt.py: a test that shells out to the CLI
# proves the shell works, not that the module does.

def test_main_writes_the_github_output_lines(tmp_path):
    out = tmp_path / "gh_output"
    assert main(["--root", str(REPO_ROOT), "--github-output", str(out)]) == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    assert [line.split("=", 1)[0] for line in lines] == list(OUTPUT_KEYS)
    import json
    emitted = {k: json.loads(v) for k, v in (l.split("=", 1) for l in lines)}
    assert emitted["kernel_boards"] == EXPECTED["kernel_boards"]
    assert sorted(emitted["excluded"]) == EXPECTED["excluded"]


def test_main_appends_rather_than_truncating(tmp_path):
    out = tmp_path / "gh_output"
    out.write_text("PRIOR=1\n", encoding="utf-8")
    assert main(["--root", str(REPO_ROOT), "--github-output", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("PRIOR=1\n")


@pytest.mark.parametrize("key", NON_EMPTY_KEYS)
def test_main_refuses_an_empty_matrix(tmp_path, key, capsys):
    """An empty array expands to zero jobs and reports success."""
    root = clone(REPO_ROOT, tmp_path / "tree")
    if key == "kernel_boards":
        for path in (root / "boards").glob("*/board.yaml"):
            if yaml.safe_load(path.read_text())["board"].get("qemu") is not True:
                shutil.rmtree(path.parent)
    elif key == "qemu_boards":
        for path in (root / "boards").glob("*/board.yaml"):
            if yaml.safe_load(path.read_text())["board"].get("qemu") is True:
                shutil.rmtree(path.parent)
    else:
        shutil.rmtree(root / "profiles")
        (root / "profiles").mkdir()

    out = tmp_path / "gh_output"
    assert main(["--root", str(root), "--github-output", str(out)]) == 1
    assert key in capsys.readouterr().err
    assert not out.exists(), "a refused matrix must not be written to $GITHUB_OUTPUT"


def test_main_refuses_an_orphan_profile(tmp_path, capsys):
    root = clone(REPO_ROOT, tmp_path / "tree")
    write_profile(root, "orphan", "no-such-board", ["a"])
    out = tmp_path / "gh_output"
    assert main(["--root", str(root), "--github-output", str(out)]) == 1
    assert "orphan" in capsys.readouterr().err
    assert not out.exists()


def test_main_refuses_a_non_boolean_qemu(tmp_path, capsys):
    root = clone(REPO_ROOT, tmp_path / "tree")
    write_board(root, "bad", cpu="esp32s3", qemu="true")
    assert main(["--root", str(root)]) == 1
    assert "must be a boolean" in capsys.readouterr().err


# Malformed YAML must reach the named-reason path, not a TypeError traceback:
# main() catches MatrixError only, so an unhashable cpu would kill discover
# with a stack trace where every other bad shape gets a sentence.
@pytest.mark.parametrize("cpu", [["esp32s3"], {"a": 1}, 32, None])
def test_a_non_string_cpu_gets_a_named_reason(tmp_path, cpu):
    root = clone(REPO_ROOT, tmp_path / "tree")
    (root / "boards" / "odd").mkdir()
    (root / "boards" / "odd" / "board.yaml").write_text(
        yaml.safe_dump({"board": {"name": "odd", "cpu": cpu}}), encoding="utf-8")
    result = discover(root)
    assert "odd" in result["excluded"]
    assert "cpu" in result["excluded"]["odd"]


# 67 distinct characters — above cardputer-contest's 44 distinct apps, so a
# scalar read as characters would take its place in the election.
SCALAR_LONG = ("abcdefghijklmnopqrstuvwxyz"
               "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
               "0123456789-.+_/")


def test_a_scalar_apps_flash_does_not_win_the_election(tmp_path):
    root = clone(REPO_ROOT, tmp_path / "tree")
    (root / "profiles" / "zz-scalar").mkdir()
    (root / "profiles" / "zz-scalar" / "profile.yaml").write_text(
        yaml.safe_dump({"board": "m5stack-cardputer",
                        "apps_flash": SCALAR_LONG}),
        encoding="utf-8")
    # One app if read as a name; 60 distinct characters if read as a sequence.
    assert discover(root)["build_profiles"] == ["cardputer-contest",
                                                "kincony-a16-default",
                                                "t-embed-default"]
