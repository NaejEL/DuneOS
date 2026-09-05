"""Tests for the bspgen emulator-target flag (SPEC-leg-29).

`qemu: true` in a board.yaml is the sole source of CONFIG_DUNEOS_TARGET_QEMU,
which selects the loader's emulator-only exec write path. That path is wrong on
silicon, so the decisive property is not that QEMU boards get the symbol but
that every other board provably does not.
"""

import importlib.util
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_bspgen():
    spec = importlib.util.spec_from_file_location(
        "duneos_bspgen", REPO_ROOT / "tools" / "duneos-bspgen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BSPGEN = _load_bspgen()

SYMBOL = "CONFIG_DUNEOS_TARGET_QEMU=y"

BASE_BOARD = {
    "name": "testboard",
    "cpu": "esp32s3",
    "flash_size_mb": 8,
    "uart": [{"id": 0, "tx_pin": 43, "rx_pin": 44, "default_baud": 115200}],
}

QEMU_BOARDS = ("esp32s3-qemu", "esp32s3-qemu-psram")


def _board_yaml(name):
    with open(REPO_ROOT / "boards" / name / "board.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["board"]


def _all_board_names():
    return sorted(p.parent.name for p in (REPO_ROOT / "boards").glob("*/board.yaml"))


def test_qemu_true_emits_the_symbol():
    board = dict(BASE_BOARD)
    board["qemu"] = True
    assert SYMBOL in BSPGEN.generate_sdkconfig_board(board)


def test_no_qemu_key_emits_no_symbol():
    assert "DUNEOS_TARGET_QEMU" not in BSPGEN.generate_sdkconfig_board(dict(BASE_BOARD))


def test_qemu_false_emits_no_symbol():
    board = dict(BASE_BOARD)
    board["qemu"] = False
    assert "DUNEOS_TARGET_QEMU" not in BSPGEN.generate_sdkconfig_board(board)


def test_the_flag_changes_nothing_else_in_the_fragment():
    """The guard must be additive: turning it on may add its own lines and
    nothing more, or a hardware board could drift through an unrelated key."""
    plain = BSPGEN.generate_sdkconfig_board(dict(BASE_BOARD))
    board = dict(BASE_BOARD)
    board["qemu"] = True
    emulated = BSPGEN.generate_sdkconfig_board(board)

    added = [l for l in emulated.splitlines() if l not in plain.splitlines()]
    assert added == ["# Emulator target (no IRAM/DRAM SRAM alias)", SYMBOL]
    for line in plain.splitlines():
        assert line in emulated.splitlines()


def test_only_the_qemu_boards_declare_the_flag():
    declaring = [n for n in _all_board_names() if _board_yaml(n).get("qemu")]
    assert declaring == sorted(QEMU_BOARDS)


def test_no_hardware_board_generates_the_symbol():
    """The criterion that matters: the emulator write path must be absent from
    every board that physically exists."""
    for name in _all_board_names():
        frag = BSPGEN.generate_sdkconfig_board(_board_yaml(name))
        if name in QEMU_BOARDS:
            assert SYMBOL in frag, name
        else:
            assert "DUNEOS_TARGET_QEMU" not in frag, name


def test_generated_sdkconfig_board_files_match_the_yaml():
    """Guards against a stale checked-out artefact: the committed generated
    file must agree with what bspgen produces from the YAML today."""
    for name in _all_board_names():
        generated = (REPO_ROOT / "boards" / name / "sdkconfig.board")
        if not generated.exists():
            continue
        text = generated.read_text(encoding="utf-8")
        assert (SYMBOL in text) == (name in QEMU_BOARDS), name


def test_the_kconfig_declares_the_symbol():
    """bspgen must not emit a symbol Kconfig has never heard of, or the build
    silently ignores it and the loader keeps the hardware path."""
    kconfig = (REPO_ROOT / "kernel" / "duneos_loader" / "Kconfig").read_text(
        encoding="utf-8")
    assert "config DUNEOS_TARGET_QEMU" in kconfig


def test_the_loader_guards_the_write_path_on_the_symbol():
    src = (REPO_ROOT / "kernel" / "duneos_loader" / "src" / "loader.c").read_text(
        encoding="utf-8")
    assert "CONFIG_DUNEOS_TARGET_QEMU" in src
    assert "iram_word_dram_alias" in src


def test_qemu_as_a_yaml_string_is_rejected():
    """`qemu: "false"` parses to a truthy Python string; truthiness alone would
    arm the emulator write path on a board that meant the opposite."""
    import pytest
    for value in ("false", "true", "yes", 1):
        board = dict(BASE_BOARD)
        board["qemu"] = value
        with pytest.raises(SystemExit):
            BSPGEN.generate_sdkconfig_board(board)


def test_qemu_on_a_riscv_board_is_rejected():
    """Kconfig declares DUNEOS_TARGET_QEMU `depends on IDF_TARGET_ARCH_XTENSA`,
    so emitting it for a RISC-V board would be silently dropped with no
    diagnostic — the board would run the hardware write path under emulation."""
    import pytest
    board = dict(BASE_BOARD)
    board["cpu"] = "esp32c3"
    board["qemu"] = True
    with pytest.raises(SystemExit):
        BSPGEN.generate_sdkconfig_board(board)


def test_the_kconfig_dependency_is_the_one_bspgen_gates_on():
    """If the Kconfig dependency is ever widened, the bspgen gate must follow;
    this test is what makes that coupling visible."""
    kconfig = (REPO_ROOT / "kernel" / "duneos_loader" / "Kconfig").read_text(
        encoding="utf-8")
    block = kconfig.split("config DUNEOS_TARGET_QEMU", 1)[1].split("\n\n", 1)[0]
    assert "depends on IDF_TARGET_ARCH_XTENSA" in block
    assert BSPGEN.XTENSA_CPUS == {"esp32", "esp32s2", "esp32s3"}


def test_every_xtensa_cpu_accepts_the_flag():
    for cpu in sorted(BSPGEN.XTENSA_CPUS):
        board = dict(BASE_BOARD)
        board["cpu"] = cpu
        board["qemu"] = True
        assert SYMBOL in BSPGEN.generate_sdkconfig_board(board)
