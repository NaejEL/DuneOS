"""SPEC-leg-17/18/19/21 criteria 5 and 6 — `dbt test` tells apart a gate that
failed from one that never ran.

The gates are stubbed. What is under test is the verdict logic, not whether
pytest or make happen to be installed on this machine.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dbt import testing  # noqa: E402
from dbt.qemu import EXIT_CONFIG as QEMU_EXIT_CONFIG  # noqa: E402


def gate(name, *, reason=None, rc=0):
    return testing.Gate(name, name, lambda: reason, lambda: rc)


def run(gates, allow_missing=False):
    lines = []
    code = testing.run_gates(gates, allow_missing=allow_missing, echo=lines.append)
    return code, "\n".join(lines)


def test_every_gate_green_exits_zero():
    code, out = run([gate("pytest"), gate("host-c")])
    assert code == testing.EXIT_OK
    assert "OK" in out


def test_an_unavailable_gate_is_not_a_pass():
    code, out = run([gate("pytest"), gate("host-c", reason="no make on PATH")])
    assert code == testing.EXIT_UNAVAILABLE
    assert code != testing.EXIT_FAILED
    assert "host-c" in out and "no make on PATH" in out


def test_a_failing_gate_uses_a_different_code():
    code, out = run([gate("pytest", rc=1), gate("host-c")])
    assert code == testing.EXIT_FAILED
    assert code != testing.EXIT_UNAVAILABLE
    assert "pytest" in out


def test_a_failure_outranks_an_unavailability():
    code, _ = run([gate("pytest", rc=2), gate("qemu", reason="wrong board")])
    assert code == testing.EXIT_FAILED


def test_allow_missing_downgrades_to_zero_but_still_says_so():
    code, out = run([gate("host-c", reason="no make on PATH")], allow_missing=True)
    assert code == testing.EXIT_OK
    assert "WARNING" in out and "host-c" in out


def test_allow_missing_does_not_forgive_a_failure():
    code, _ = run([gate("host-c", rc=1)], allow_missing=True)
    assert code == testing.EXIT_FAILED


def test_a_gate_that_probes_unavailable_is_never_run():
    ran = []

    def boom():
        ran.append("qemu")
        return 0

    code, _ = run([testing.Gate("qemu", "q", lambda: "wrong board", boom)])
    assert ran == []
    assert code == testing.EXIT_UNAVAILABLE


def test_a_qemu_config_exit_is_reported_as_unavailable():
    """`dbt qemu` exits 6 on a board mismatch. That is a configuration answer,
    not a firmware verdict, and must not read as a failure."""
    code, out = run([testing.Gate("qemu", "q", lambda: None,
                                  lambda: testing.UNAVAILABLE_RC)])
    assert code == testing.EXIT_UNAVAILABLE
    assert "unavailable" in out.lower()


def test_the_qemu_gate_maps_exit_six_to_unavailable(monkeypatch):
    monkeypatch.setattr(testing, "_active_board", lambda: "esp32s3-qemu")
    monkeypatch.setattr(testing, "_run", lambda argv: QEMU_EXIT_CONFIG)
    assert testing._run_qemu() == testing.UNAVAILABLE_RC


def test_the_qemu_gate_passes_other_exits_through(monkeypatch):
    monkeypatch.setattr(testing, "_active_board", lambda: "esp32s3-qemu")
    monkeypatch.setattr(testing, "_run", lambda argv: 3)
    assert testing._run_qemu() == 3


# --- Criterion 6 ---------------------------------------------------------


def test_qemu_is_unavailable_on_a_cardputer_checkout(tmp_path, monkeypatch):
    board_file = tmp_path / ".duneos_board"
    board_file.write_text("m5stack-cardputer\n", encoding="utf-8")
    before = board_file.read_bytes()
    monkeypatch.setattr(testing, "BOARD_FILE", board_file)

    reason = testing._probe_qemu()
    assert reason is not None
    assert "m5stack-cardputer" in reason

    gates = [g for g in testing.build_gates(qemu=True) if g.name == "qemu"]
    assert len(gates) == 1
    code, out = run(gates)
    assert code == testing.EXIT_UNAVAILABLE
    assert board_file.read_bytes() == before


def test_an_unset_board_file_is_unavailable_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(testing, "BOARD_FILE", tmp_path / "absent")
    assert testing._probe_qemu() is not None


def test_qemu_and_fuzz_are_opt_in():
    assert [g.name for g in testing.build_gates()] == ["pytest", "host-c"]
    assert "qemu" in [g.name for g in testing.build_gates(qemu=True)]
    assert "fuzz" in [g.name for g in testing.build_gates(fuzz=True)]


def test_the_cheap_gates_come_first():
    names = [g.name for g in testing.build_gates(fuzz=True, qemu=True)]
    assert names.index("pytest") < names.index("host-c") < names.index("qemu")
    assert names.index("host-c") < names.index("fuzz")


# --- The pytest gate counts what RAN ------------------------------------
#
# `pytest -q` exits 0 for a run that skipped every test. Trusting that return
# code is the failure mode this whole verb exists to end, and it shipped inside
# the verb once already.


def verdict(rc, counts, floor=None):
    lines = []
    code = testing.pytest_verdict(rc, counts, floor, echo=lines.append)
    return code, "\n".join(lines)


def test_a_run_where_every_test_skipped_is_not_a_pass():
    code, out = verdict(0, (366, 366))
    assert code != 0
    assert "verified nothing" in out


def test_a_normal_run_passes():
    assert verdict(0, (366, 0))[0] == 0


def test_a_run_below_the_floor_is_not_a_pass():
    code, out = verdict(0, (366, 100), floor=359)
    assert code != 0
    assert "floor is 359" in out


def test_a_run_at_the_floor_passes():
    assert verdict(0, (366, 7), floor=359)[0] == 0


def test_a_few_skips_above_the_floor_still_pass():
    assert verdict(0, (366, 3), floor=359)[0] == 0


def test_an_empty_collection_is_not_a_pass():
    code, out = verdict(5, None)
    assert code != 0
    assert "NO TEST RAN" in out


def test_a_collection_error_is_not_a_pass():
    code, out = verdict(3, None)
    assert code != 0
    assert "NO TEST RAN" in out


def test_an_unreadable_report_is_not_a_pass():
    code, out = verdict(0, None)
    assert code != 0
    assert "not a pass" in out


def test_a_real_failure_still_reports_as_a_failure():
    assert verdict(1, (366, 0), floor=359)[0] == 1


def test_the_floor_is_read_from_the_ci_workflow():
    """One source of truth: CI's FLOOR and this gate's cannot drift apart."""
    floor = testing._ci_floor()
    assert floor is not None and floor > 0
    text = testing.CI_WORKFLOW.read_text(encoding="utf-8")
    assert f"FLOOR={floor}" in text


def test_a_missing_workflow_leaves_the_floor_unset(tmp_path, monkeypatch):
    monkeypatch.setattr(testing, "CI_WORKFLOW", tmp_path / "absent.yml")
    assert testing._ci_floor() is None
    # Without a floor the gate still refuses a run that skipped everything.
    assert verdict(0, (366, 366))[0] != 0


def test_junit_counts_are_read_from_a_real_report(tmp_path):
    report = tmp_path / "r.xml"
    report.write_text(
        '<?xml version="1.0"?><testsuites><testsuite name="pytest" '
        'errors="0" failures="0" skipped="4" tests="20" time="1"/></testsuites>',
        encoding="utf-8")
    assert testing._junit_counts(report) == (20, 4)


def test_an_absent_or_malformed_report_reads_as_unknown(tmp_path):
    assert testing._junit_counts(tmp_path / "absent.xml") is None
    broken = tmp_path / "broken.xml"
    broken.write_text("not xml at all", encoding="utf-8")
    assert testing._junit_counts(broken) is None


# --- Criterion 2 of the Verifier round: setup problems are not verdicts ---


def test_absent_submodules_make_the_host_gate_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(testing, "SUBMODULE_WITNESS", tmp_path / "cJSON.c")
    reason = testing._probe_make()
    assert reason is not None and "submodule" in reason


def test_present_submodules_leave_the_host_gate_available(tmp_path, monkeypatch):
    witness = tmp_path / "cJSON.c"
    witness.write_text("", encoding="utf-8")
    monkeypatch.setattr(testing, "SUBMODULE_WITNESS", witness)
    assert testing._probe_make() == testing._probe_toolchain()


@pytest.mark.parametrize("code", [testing.EXIT_FAILED, testing.EXIT_UNAVAILABLE])
def test_the_non_zero_codes_are_distinct_and_not_argparse_usage(code):
    assert code not in (0, 2)
    assert testing.EXIT_FAILED != testing.EXIT_UNAVAILABLE
