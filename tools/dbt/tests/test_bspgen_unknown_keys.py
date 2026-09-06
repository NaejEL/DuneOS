"""Tests for board.yaml top-level key validation (SPEC-leg-37).

An unknown key used to generate cleanly and do nothing. That was survivable
while every key was a peripheral block whose absence is visible at runtime; it
stopped being survivable with `main_task_stack`, where `main_task_stac: 4608`
exits 0, emits no symbol, and leaves the board on the SDK default that bricks it.
Unknown keys are therefore refused, not ignored.
"""

import importlib.util
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_bspgen():
    spec = importlib.util.spec_from_file_location(
        "duneos_bspgen", REPO_ROOT / "tools" / "duneos-bspgen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BSPGEN = _load_bspgen()

BASE_BOARD = {"name": "testboard", "cpu": "esp32s3", "flash_size_mb": 8}
FAKE_YAML = Path("boards/testboard/board.yaml")


def _all_board_names():
    return sorted(p.parent.name for p in (REPO_ROOT / "boards").glob("*/board.yaml"))


def test_a_known_board_validates():
    BSPGEN.validate(dict(BASE_BOARD), FAKE_YAML)


def test_an_unknown_key_is_refused():
    board = dict(BASE_BOARD)
    board["not_a_real_key"] = 1
    with pytest.raises(SystemExit):
        BSPGEN.validate(board, FAKE_YAML)


def test_a_typo_of_the_stack_key_is_refused_and_suggests_the_real_one(capsys):
    """The motivating case: silently generating without the symbol leaves the
    board on 3584 B, which is the overflow SPEC-leg-37 exists to close."""
    board = dict(BASE_BOARD)
    board["main_task_stac"] = 4608
    with pytest.raises(SystemExit):
        BSPGEN.validate(board, FAKE_YAML)
    err = capsys.readouterr().err
    assert "main_task_stac" in err
    assert "main_task_stack" in err


def test_the_rejection_names_the_file_and_lists_the_valid_keys(capsys):
    board = dict(BASE_BOARD)
    board["zzz_unknown"] = 1
    with pytest.raises(SystemExit):
        BSPGEN.validate(board, FAKE_YAML)
    err = capsys.readouterr().err
    assert str(FAKE_YAML) in err
    assert "main_task_stack" in err


def test_every_key_used_by_every_board_in_the_tree_is_known():
    """A whitelist that rejects a shipped board is a broken build, so this is the
    check that keeps the rule honest: `lora`/`rfid` on lilygo are declared
    hardware no generator models yet, and are known keys on purpose."""
    unknown = {}
    for name in _all_board_names():
        with open(REPO_ROOT / "boards" / name / "board.yaml", encoding="utf-8") as f:
            board = yaml.safe_load(f)["board"]
        extra = sorted(k for k in board if k not in BSPGEN.KNOWN_BOARD_KEYS)
        if extra:
            unknown[name] = extra
    assert unknown == {}


def test_all_eight_boards_still_validate():
    for name in _all_board_names():
        path = REPO_ROOT / "boards" / name / "board.yaml"
        with open(path, encoding="utf-8") as f:
            board = yaml.safe_load(f)["board"]
        BSPGEN.validate(board, path)
