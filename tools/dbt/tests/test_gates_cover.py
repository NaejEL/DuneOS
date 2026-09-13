"""SPEC-leg-17/18/19/21 criteria 1, 2, 11 and 12 — the suite tests the shipped
code, and the documents that describe it agree with it.

Everything reads the git index, never the working tree (LEG-38).
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

PARSER_SOURCE = "sdk/net/known_yaml.c"


def git(*args):
    proc = subprocess.run(["git", "-C", str(REPO_ROOT), *args],
                          capture_output=True, check=True)
    return proc.stdout.decode(errors="replace")


@pytest.fixture(scope="module")
def index():
    out = {}
    for line in git("ls-files", "-s").splitlines():
        meta, _, path = line.partition("\t")
        mode, sha, _stage = meta.split()
        out[path] = sha
    return out


@pytest.fixture(scope="module")
def blob(index):
    def read(path):
        assert path in index, f"{path} is not tracked"
        return git("cat-file", "blob", index[path])
    return read


# --- Criterion 1 ---------------------------------------------------------

# What a parse of the grammar looks like, as opposed to a write of it: a
# comparison against the "- ssid:" token.
_PARSES_SSID = re.compile(r'strncmp\s*\([^;]*?"- ssid:"')


def test_only_one_file_parses_the_known_yaml_grammar(index, blob):
    parsers = sorted(path for path in index
                     if path.endswith(".c") and _PARSES_SSID.search(blob(path)))
    assert parsers == [PARSER_SOURCE], (
        "the known.yaml grammar must have exactly one implementation; found "
        + ", ".join(parsers))


def test_no_copy_of_the_parser_survives_under_apps_or_tests(index, blob):
    for path in index:
        if not path.endswith((".c", ".h")):
            continue
        if not (path.startswith("apps/") or path.startswith("tests/")):
            continue
        assert not _PARSES_SSID.search(blob(path)), \
            f"{path} carries a copy of the known.yaml parser"


def test_the_three_apps_link_the_shared_parser(blob):
    for manifest in ("apps/system/wifi_daemon/duneos.yaml",
                     "apps/system/bin/iw/duneos.yaml",
                     "apps/user/wifi/duneos.yaml"):
        assert "$SDK/net/known_yaml.c" in blob(manifest), \
            f"{manifest} does not declare the shared parser"


# --- Criterion 2 ---------------------------------------------------------


def test_the_host_suite_compiles_the_shipped_parser(blob):
    makefile = blob("tests/host/Makefile")
    rule = re.search(r"^test_known_yaml:.*?(?=\n\S|\Z)", makefile,
                     re.MULTILINE | re.DOTALL)
    assert rule, "tests/host/Makefile has no test_known_yaml rule"
    assert PARSER_SOURCE.removeprefix("sdk/") in rule.group(0), \
        "the test_known_yaml rule does not name the sdk/ parser source"


def test_the_ld2450_suite_compiles_the_shipped_decoder(blob):
    makefile = blob("tests/host/Makefile")
    assert "test_ld2450" in makefile.split("TESTS", 1)[1].split("\n\n", 1)[0]
    rule = re.search(r"^test_ld2450:.*?(?=\n\S|\Z)", makefile,
                     re.MULTILINE | re.DOTALL)
    assert rule and "sensor/libld2450.c" in rule.group(0)


# --- Criterion 11 --------------------------------------------------------


def adr_path(index, number):
    matches = [p for p in index if p.startswith(f"docs/adr/{number}-")]
    assert len(matches) == 1, f"expected one ADR {number}, found {matches}"
    return matches[0]


def test_adr_012_points_at_its_superseding_adr(index, blob):
    status = blob(adr_path(index, "012")).split("\n\n", 2)[1]
    assert "**Status:**" in status
    assert "041" in status, "ADR 012's status line does not name ADR 041"


def test_adr_041_supersedes_adr_012_clause_by_clause(index, blob):
    text = blob(adr_path(index, "041"))
    assert "012" in text
    for verdict in ("Kept", "Replaced", "Dropped"):
        assert verdict in text, f"ADR 041 does not say what is {verdict.lower()}"


# --- Criterion 12 --------------------------------------------------------


def host_suite_count(makefile):
    block = re.search(r"^TESTS\s*:?=(.*?)(?=\n\S|\n\n)", makefile,
                      re.MULTILINE | re.DOTALL)
    assert block, "tests/host/Makefile has no TESTS list"
    return len(block.group(1).replace("\\", " ").split())


def roadmap_row(blob, tag):
    for line in blob("ROADMAP.md").splitlines():
        if line.startswith(f"| {tag} |"):
            return line
    pytest.fail(f"ROADMAP.md has no {tag} row")


def test_the_roadmap_suite_count_matches_the_makefile(blob):
    expected = host_suite_count(blob("tests/host/Makefile"))
    row = roadmap_row(blob, "LEG-17")
    counts = re.findall(r"\b(\d+)\s+suites\b", row)
    assert counts, "the LEG-17 row no longer states a suite count"
    assert [int(c) for c in counts] == [expected] * len(counts), \
        f"the LEG-17 row says {counts} suites; tests/host/Makefile builds {expected}"


@pytest.mark.parametrize("tag", ["LEG-17", "LEG-18", "LEG-19", "LEG-21"])
def test_the_closed_rows_are_marked_done(blob, tag):
    assert roadmap_row(blob, tag).rstrip().endswith("| DONE |")


# LEG-18 is the one row of the four that a host gate could not close: the parser
# behaves differently for a board carrying a hand-edited known.yaml, and only a
# board can observe that. It was run on an m5stack-cardputer on 2026-09-13 and
# the row is DONE on that evidence, not on a green CI. What this asserts is that
# the evidence stays named — a bare DONE here would be indistinguishable from the
# host-closable rows above, and the next reader could not tell which kind it is.
def test_leg_18_records_the_board_that_closed_it(blob):
    row = roadmap_row(blob, "LEG-18")
    assert "on-board" in row, \
        "the LEG-18 row no longer names the on-board run that closed it"
    assert "cardputer" in row.lower(), \
        "the LEG-18 row does not say which board validated it"
