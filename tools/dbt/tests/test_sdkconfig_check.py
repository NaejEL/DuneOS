"""Tests for the stale-sdkconfig guard (SPEC-leg-37).

ESP-IDF applies a defaults file only to symbols absent from a build directory's
cached sdkconfig, so a stale build dir silently keeps — and flashes — the values
the change was meant to replace. The decisive properties are that a genuine
contradiction is refused by name, that a symbol Kconfig legitimately dropped is
not mistaken for one, and that a clean or first-time build dir passes.
"""

import pytest

from dbt import sdkconfig_check as sc

SYMBOL = "CONFIG_ESP_MAIN_TASK_STACK_SIZE"
WATCHPOINT = "CONFIG_FREERTOS_WATCHPOINT_END_OF_STACK"


# --- parsing -----------------------------------------------------------------

def test_parses_values_and_unset_symbols():
    parsed = sc.parse_sdkconfig(
        "CONFIG_A=y\n"
        "# CONFIG_B is not set\n"
        'CONFIG_C="8MB"\n'
        f"{SYMBOL}=4608\n"
    )
    assert parsed == {"CONFIG_A": "y", "CONFIG_B": "n",
                      "CONFIG_C": '"8MB"', SYMBOL: "4608"}


def test_n_and_not_set_are_the_same_statement():
    """bspgen emits `CONFIG_SPIRAM=n`; the generated sdkconfig writes
    `# CONFIG_SPIRAM is not set`. Treating those as different would make every
    board permanently 'stale'."""
    declared = sc.parse_sdkconfig("CONFIG_SPIRAM=n\n")
    effective = sc.parse_sdkconfig("# CONFIG_SPIRAM is not set\n")
    assert sc.find_conflicts(declared, effective) == []


def test_comments_and_blank_lines_are_ignored():
    parsed = sc.parse_sdkconfig("\n# Board: x\n#\n\nCONFIG_A=y\n# a note\n")
    assert parsed == {"CONFIG_A": "y"}


def test_whitespace_around_a_declaration_does_not_change_the_symbol():
    assert sc.parse_sdkconfig(f"  {SYMBOL} = 4608  \n") == {SYMBOL: "4608"}


# --- conflict detection ------------------------------------------------------

def test_a_changed_value_is_a_conflict():
    conflicts = sc.find_conflicts({SYMBOL: "4608"}, {SYMBOL: "3584"})
    assert conflicts == [(SYMBOL, "4608", "3584")]


def test_a_policy_symbol_turned_off_in_the_build_is_a_conflict():
    """The watchpoint half of SPEC-leg-37 drops just as silently as the stack."""
    conflicts = sc.find_conflicts({WATCHPOINT: "y"}, {WATCHPOINT: "n"})
    assert conflicts == [(WATCHPOINT, "y", "n")]


def test_an_agreeing_build_is_not_a_conflict():
    assert sc.find_conflicts({SYMBOL: "4608"}, {SYMBOL: "4608", "CONFIG_X": "y"}) == []


def test_a_symbol_absent_from_the_build_is_not_a_conflict():
    """Kconfig drops a symbol whose dependencies are unmet. Refusing that would
    block the build rather than protect the board."""
    assert sc.find_conflicts({"CONFIG_DEPENDENT": "y"}, {"CONFIG_OTHER": "y"}) == []


def test_conflicts_are_reported_in_a_stable_order():
    declared = {"CONFIG_B": "y", "CONFIG_A": "y"}
    effective = {"CONFIG_B": "n", "CONFIG_A": "n"}
    assert [c[0] for c in sc.find_conflicts(declared, effective)] == [
        "CONFIG_A", "CONFIG_B"]


# --- layering: the fragment overrides the root defaults ----------------------

def test_the_board_fragment_wins_over_the_root_defaults(tmp_path):
    """CMakeLists.txt lists sdkconfig.defaults first, then the board fragment,
    and the last file wins. The guard must compare against the same winner."""
    root = tmp_path / "sdkconfig.defaults"
    root.write_text(f"{SYMBOL}=3584\n")
    frag = tmp_path / "sdkconfig.board"
    frag.write_text(f"{SYMBOL}=4608\n")
    assert sc.declared_config([root, frag])[SYMBOL] == "4608"


def test_a_missing_source_file_is_skipped(tmp_path):
    root = tmp_path / "sdkconfig.defaults"
    root.write_text(f"{WATCHPOINT}=y\n")
    merged = sc.declared_config([root, tmp_path / "absent.board"])
    assert merged == {WATCHPOINT: "y"}


def test_both_layers_are_checked_for_the_active_board():
    """The root policy file is as droppable as the board fragment, so the guard
    must cover both — a build dir with the watchpoint off is the case that was
    armed across every build directory in the tree."""
    sources = sc.default_sources("m5stack-cardputer")
    assert [p.name for p in sources] == ["sdkconfig.defaults", "sdkconfig.board"]
    declared = sc.declared_config(sources)
    assert declared.get(SYMBOL) == "4608"
    assert declared.get(WATCHPOINT) == "y"


# --- end to end on a build directory -----------------------------------------

def test_a_stale_build_directory_is_refused(tmp_path):
    stale = tmp_path / "sdkconfig"
    stale.write_text(f"{SYMBOL}=3584\n# {WATCHPOINT} is not set\n")
    conflicts = sc.check_build_sdkconfig("m5stack-cardputer", stale)
    assert (SYMBOL, "4608", "3584") in conflicts
    assert (WATCHPOINT, "y", "n") in conflicts


def test_a_matching_build_directory_passes(tmp_path):
    clean = tmp_path / "sdkconfig"
    declared = sc.declared_config(sc.default_sources("m5stack-cardputer"))
    clean.write_text("".join(f"{k}={v}\n" for k, v in declared.items()))
    assert sc.check_build_sdkconfig("m5stack-cardputer", clean) == []


def test_an_unconfigured_build_directory_passes(tmp_path):
    """A first build has no cached sdkconfig and nothing to contradict."""
    assert sc.check_build_sdkconfig("m5stack-cardputer",
                                    tmp_path / "sdkconfig") == []


def test_enforce_exits_and_names_symbol_stale_value_and_remedy(tmp_path):
    stale = tmp_path / "sdkconfig"
    stale.write_text(f"{SYMBOL}=3584\n")
    printed = []
    with pytest.raises(SystemExit) as exc:
        sc.enforce("m5stack-cardputer", stale, printer=printed.append)
    assert exc.value.code == 1
    msg = "\n".join(printed)
    assert SYMBOL in msg
    assert "3584" in msg
    assert "4608" in msg
    assert "fullclean" in msg
    assert str(stale) in msg


def test_enforce_is_silent_when_the_build_agrees(tmp_path):
    clean = tmp_path / "sdkconfig"
    declared = sc.declared_config(sc.default_sources("m5stack-cardputer"))
    clean.write_text("".join(f"{k}={v}\n" for k, v in declared.items()))
    printed = []
    sc.enforce("m5stack-cardputer", clean, printer=printed.append)
    assert printed == []


def test_the_qemu_boards_are_not_caught_by_their_own_fragment(tmp_path):
    """Neither QEMU board declares main_task_stack, so a build dir carrying
    ESP-IDF's own default must stay clean (criterion 7)."""
    for board in ("esp32s3-qemu", "esp32s3-qemu-psram"):
        declared = sc.declared_config(sc.default_sources(board))
        assert SYMBOL not in declared
        effective = dict(declared)
        effective[SYMBOL] = "3584"
        assert sc.find_conflicts(declared, effective) == []
