"""SPEC-dup-01 criterion 1 — one ELF32 reader in the tree, not two.

Everything reads the git index, never the working tree (LEG-38): a gate must
not depend on what one machine happens to have on disk. `git add` before
running the suite, or you measure the previous tree.

Scope: tracked .c and .h files. A second ELF reader is C — and .knowledge/ is a
generated search index whose tracked blobs carry strings from whatever was
indexed last, so asserting over it would measure the index's freshness rather
than the tree's content.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

READER_SOURCE = "lib/elf/elf_parse.c"
READER_HEADER = "lib/elf/include/duneos/elf_parse.h"

# The callers of the shared reader, outside the host suites. tests/host drives
# it directly on purpose — that is the harness, not a second implementation.
EXPECTED_CALLERS = {
    "kernel/duneos_loader/src/loader.c",
    "sdk/app/libappmeta.c",
}

# The field-offset table the hand-written SDK reader carried. Assembled from
# pieces so this file is not itself a hit for the search it performs.
HAND_OFFSETS = tuple(["EH_" + n for n in ("SHOFF", "SHNUM", "SHSTRNDX", "SHENTSIZE")]
                     + ["SH_" + n for n in ("OFFSET", "SIZE_FIELD")])


def git(*args):
    proc = subprocess.run(["git", "-C", str(REPO_ROOT), *args],
                          capture_output=True, check=True)
    return proc.stdout.decode(errors="replace")


@pytest.fixture(scope="module")
def index():
    out = {}
    for line in git("ls-files", "-s").splitlines():
        meta, _, path = line.partition("\t")
        _mode, sha, _stage = meta.split()
        if path.endswith((".c", ".h")):
            out[path] = sha
    return out


@pytest.fixture(scope="module")
def blob(index):
    def read(path):
        assert path in index, f"{path} is not a tracked .c/.h file"
        return git("cat-file", "blob", index[path])
    return read


def _ident(name):
    # \b breaks on nothing inside an identifier: DUNEOS_ELF_REJ_SH_OFFSET must
    # not count as an occurrence of SH_OFFSET.
    return re.compile(r"(?<![A-Za-z0-9_])" + name + r"(?![A-Za-z0-9_])")


@pytest.mark.parametrize("ident", HAND_OFFSETS)
def test_no_hand_written_elf_field_offsets_survive(ident, index, blob):
    pattern = _ident(ident)
    hits = sorted(path for path in index if pattern.search(blob(path)))
    assert hits == [], (
        f"{ident} is a hand-written ELF field offset; the shared reader types "
        f"the headers instead. Found in: " + ", ".join(hits))


def test_the_reader_is_defined_once(index, blob):
    definition = re.compile(r"^int\s+duneos_elf_image_open\s*\(", re.MULTILINE)
    definers = sorted(path for path in index
                      if path.endswith(".c") and definition.search(blob(path)))
    assert definers == [READER_SOURCE]


def test_the_reader_is_declared_once(index, blob):
    declaration = re.compile(r"^int\s+duneos_elf_image_open\s*\(", re.MULTILINE)
    declarers = sorted(path for path in index
                       if path.endswith(".h") and declaration.search(blob(path)))
    assert declarers == [READER_HEADER]


def test_the_reader_has_exactly_two_callers_outside_the_host_suites(index, blob):
    callers = sorted(path for path in index
                     if path.endswith(".c")
                     and not path.startswith("tests/")
                     and path != READER_SOURCE
                     and "duneos_elf_image_open(" in blob(path))
    assert set(callers) == EXPECTED_CALLERS, (
        "the kernel loader and the SDK's libappmeta are the two consumers of "
        f"the shared reader; found {callers}")


def test_the_sdk_reader_goes_through_the_shared_unit(blob):
    appmeta = blob("sdk/app/libappmeta.c")
    assert '#include "duneos/elf_parse.h"' in appmeta
    assert "duneos_elf_image_open(" in appmeta
    # The three helpers the hand-written reader needed and the shared unit
    # replaces. Their absence is what makes the fuzz target cover this path.
    for gone in ("rd16", "rd32", "read_name"):
        assert not _ident(gone).search(appmeta), \
            f"{gone}() is a leftover of the hand-written reader"


def test_the_shared_unit_lives_under_lib_and_is_tracked(index):
    assert READER_SOURCE in index
    assert READER_HEADER in index
    assert "lib/elf/include/duneos/elf.h" in index


def test_no_live_reference_to_the_old_location(index, blob):
    """Criterion 11bis, for the files this test can read.

    tests/host/Makefile and the loader's CMakeLists are checked here rather
    than by eye, because a stale path there fails the build on somebody else's
    machine and not on the author's.
    """
    for path in ("tests/host/Makefile", "kernel/duneos_loader/CMakeLists.txt"):
        text = git("cat-file", "blob", git("rev-parse", f":{path}").strip())
        assert "duneos_loader/src/elf_parse" not in text
        assert "duneos_loader/include/duneos/elf_parse" not in text
        assert "lib/elf" in text
