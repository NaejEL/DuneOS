"""SPEC-dup-01 criterion 9 — the `$ROOT/` source prefix dbt gained so an app
can name a shared pure unit that lives under neither sdk/ nor its own tree.

Entered through dbt.builder directly: tools/dbt.py bootstraps a venv and
pip-installs from PyPI whenever the interpreter is not already inside it, which
is CI's case (recorded lesson). Nothing here reads a generated path.
"""

import subprocess
from pathlib import Path

import pytest

from dbt.builder import resolve_source, resolve_sources, source_include_dir
from dbt.constants import DUNEOS_ROOT, SDK_DIR

REPO_ROOT = Path(__file__).resolve().parents[3]

SHARED_UNIT = "$ROOT/lib/elf/elf_parse.c"


def test_the_constants_agree_with_this_checkout():
    assert DUNEOS_ROOT == REPO_ROOT
    assert SDK_DIR == REPO_ROOT / "sdk"


def test_root_prefix_resolves_against_the_repository_root():
    assert resolve_source(SHARED_UNIT, REPO_ROOT / "apps" / "user" / "launcher") \
        == REPO_ROOT / "lib" / "elf" / "elf_parse.c"


def test_the_resolved_shared_unit_exists():
    assert resolve_source(SHARED_UNIT, REPO_ROOT).is_file()


def test_sdk_prefix_is_unchanged():
    assert resolve_source("$SDK/app/libappmeta.c", REPO_ROOT / "apps") \
        == REPO_ROOT / "sdk" / "app" / "libappmeta.c"


def test_a_bare_entry_stays_app_relative():
    app_dir = REPO_ROOT / "apps" / "user" / "launcher"
    assert resolve_source("src/helper.c", app_dir) == app_dir / "src" / "helper.c"


def test_a_prefix_is_only_recognised_at_the_start():
    app_dir = Path("/anywhere")
    assert resolve_source("vendor/$ROOT/x.c", app_dir) == app_dir / "vendor/$ROOT/x.c"


def test_the_shared_units_include_dir_lands_on_the_compiler_command():
    paths, includes = resolve_sources([SHARED_UNIT], REPO_ROOT)
    want = REPO_ROOT / "lib" / "elf" / "include"
    assert includes == [want]
    assert paths == [REPO_ROOT / "lib" / "elf" / "elf_parse.c"]
    # The header the unit publishes is reachable through exactly that -I.
    assert (want / "duneos" / "elf_parse.h").is_file()
    assert (want / "duneos" / "elf.h").is_file()
    assert (want / "duneos" / "loader_limits.h").is_file()


def test_include_dirs_are_deduplicated_and_ordered():
    paths, includes = resolve_sources(
        [SHARED_UNIT, "$SDK/app/libappmeta.c", SHARED_UNIT], REPO_ROOT)
    assert len(paths) == 3
    assert includes == [REPO_ROOT / "lib" / "elf" / "include",
                        REPO_ROOT / "sdk" / "app" / "include"]


def test_source_include_dir_is_none_without_a_sibling_include():
    assert source_include_dir(REPO_ROOT / "main" / "main.c") is None


def test_the_launcher_declares_the_shared_unit():
    """Read from the git index, not the working tree (LEG-38)."""
    sha = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse",
         ":apps/user/launcher/duneos.yaml"],
        capture_output=True, check=True).stdout.decode().strip()
    manifest = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "cat-file", "blob", sha],
        capture_output=True, check=True).stdout.decode(errors="replace")
    assert SHARED_UNIT in manifest
    assert "$SDK/app/libappmeta.c" in manifest


@pytest.mark.parametrize("entry", ["$ROOT/lib/elf/elf_parse.c",
                                   "$SDK/app/libappmeta.c"])
def test_every_declared_prefix_resolves_inside_the_repository(entry):
    assert resolve_source(entry, REPO_ROOT).is_relative_to(REPO_ROOT)


@pytest.mark.parametrize("entry", [
    "$ROOT/../outside.c",
    "$ROOT/lib/../../outside.c",
    "$ROOT//etc/passwd",
    "$SDK/../outside.c",
    "$SDK/app/../../../outside.c",
    "$SDK//etc/passwd",
])
def test_a_traversing_prefix_entry_is_rejected(entry):
    """A prefix names a tree; an entry that leaves it must not compile."""
    with pytest.raises(SystemExit) as exc:
        resolve_source(entry, REPO_ROOT)
    assert "outside" in str(exc.value)


def test_a_traversing_entry_fails_the_app_build_rather_than_escaping(tmp_path):
    """resolve_sources propagates the refusal, so build_single reports it."""
    with pytest.raises(SystemExit):
        resolve_sources(["$ROOT/lib/elf/elf_parse.c", "$ROOT/../outside.c"],
                        tmp_path)


def test_a_prefix_naming_its_own_tree_is_not_a_traversal():
    """`$ROOT/lib/../lib/elf/elf_parse.c` stays inside and still resolves."""
    assert resolve_source("$ROOT/lib/../lib/elf/elf_parse.c", REPO_ROOT) \
        == REPO_ROOT / "lib" / "elf" / "elf_parse.c"
