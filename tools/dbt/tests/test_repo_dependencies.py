"""SPEC-leg-pin-and-share-dependencies: the repo pins and shares what it needs.

Everything here reads the git index, never the working tree and never a build
output (LEG-38). `git cat-file` on a tracked blob is the whole data source, so a
clean checkout and a machine mid-build see the same thing.
"""

import fnmatch
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

MAX_TRACKED_BYTES = 256 * 1024

# The four pre-PR gates, as CONTRIBUTING.md spells them.
PRE_PR_COMMANDS = (
    "dbt.py flash kernel --build-only",
    "make -C tests/host test",
    "python -m pytest -q",
    "dbt.py qemu --board esp32s3-qemu",
)

# A generated index tree, not authored content — excluded for the same reason
# the size bound already excludes submodule contents. This is not an allowlist:
# do not add file names to it.
GENERATED_TREE = ".knowledge/"


def git(*args):
    proc = subprocess.run(["git", "-C", str(REPO_ROOT), *args],
                          capture_output=True, check=True)
    return proc.stdout.decode()


@pytest.fixture(scope="module")
def index():
    """path -> (mode, sha) for every entry in the index."""
    out = {}
    for line in git("ls-files", "-s").splitlines():
        meta, _, path = line.partition("\t")
        mode, sha, _stage = meta.split()
        out[path] = (mode, sha)
    return out


@pytest.fixture(scope="module")
def blob(index):
    def read(path):
        assert path in index, f"{path} is not tracked"
        return git("cat-file", "blob", index[path][1])
    return read


def packages_on(command_line):
    """Package names on a `pip install` line, minus flags and their values.

    `pip` itself is dropped: bootstrapping the installer is not a project
    dependency, and pinning it would contradict the `--upgrade pip` it appears in.
    """
    tokens = [token for token in command_line.split() if token != "\\"]
    tokens = tokens[tokens.index("install") + 1:]
    names, skip = [], False
    for token in tokens:
        if skip:
            skip = False
            continue
        if token in ("-c", "-r", "--constraint", "--requirement"):
            skip = True
            continue
        if token.startswith("-"):
            continue
        if token != "pip":
            names.append(token)
    return names


def pip_install_lines(ci_yaml):
    # pip3/pip3.11 and any run of whitespace count; a shell continuation is
    # joined first so a wrapped package is named, not the stray backslash.
    joined = re.sub(r"\\\n\s*", " ", ci_yaml)
    return [line.strip() for line in joined.splitlines()
            if re.search(r"\bpip[0-9.]*\s+install\b", line)]


def normalise(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def constraint_pins(blob):
    pins = {}
    for line in blob("tools/constraints.txt").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pins[line] = line
    return pins


# --- Criterion 1 ---------------------------------------------------------


def test_license_and_notice_are_tracked_and_advertised(index, blob):
    assert "LICENSE" in index
    assert "NOTICE" in index
    license_text = blob("LICENSE")
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in license_text
    assert "Version 3, 29 June 2007" in license_text

    readme = blob("README.md")
    assert "Lesser General Public License" in readme
    assert "(LICENSE)" in readme


# --- Criterion 2 ---------------------------------------------------------


def test_notice_covers_every_third_party_directory(index, blob):
    notice = blob("NOTICE")
    directories = {path.split("/")[1] for path in index
                   if path.startswith("third_party/")}
    assert directories, "no third_party/ entry found in the index"
    for name in directories:
        assert name in notice, f"third_party/{name} is undocumented in NOTICE"
    assert "MIT" in notice
    assert "BSD-3-Clause" in notice


# --- Criterion 3 ---------------------------------------------------------


def test_readme_states_the_linking_position(blob):
    readme = blob("README.md")
    assert "libdune.a" in readme
    assert "not** a derivative work" in readme or "not a derivative work" in readme


# --- Criterion 4 ---------------------------------------------------------


def test_lock_files_are_no_longer_ignored():
    # --no-index, or the tracked dependencies.lock masks the live rule.
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "--no-index", "-q",
         "--", "dependencies.lock"],
        capture_output=True)
    assert proc.returncode != 0, "dependencies.lock is still matched by a .gitignore rule"


# --- Criterion 6 ---------------------------------------------------------


def test_every_constraint_is_an_exact_pin(index, blob):
    assert "tools/constraints.txt" in index
    pins = constraint_pins(blob)
    assert pins, "tools/constraints.txt declares nothing"
    for entry in pins:
        # '*' excluded: `pytest==9.*` is a range, not the exact pin asked for.
        assert re.fullmatch(r"[A-Za-z0-9._-]+==[^\s=<>!~*]+", entry), \
            f"'{entry}' is not a name==version pin"


# --- Criterion 7 ---------------------------------------------------------


def test_every_installed_package_is_pinned(blob):
    pinned = {normalise(entry.split("==")[0]) for entry in constraint_pins(blob)}

    dbt = blob("tools/dbt.py")
    deps = re.search(r"_DEPS = \[(.*?)\]", dbt, re.S)
    assert deps, "could not find _DEPS in tools/dbt.py"
    wanted = {normalise(name) for name in re.findall(r'"([^"]+)"', deps.group(1))}

    for line in pip_install_lines(blob(".github/workflows/ci.yml")):
        wanted |= {normalise(name) for name in packages_on(line)}

    missing = sorted(wanted - pinned)
    assert not missing, f"installed but unpinned in tools/constraints.txt: {missing}"


# --- Criterion 8 ---------------------------------------------------------


def test_every_install_site_passes_the_constraints_file(blob):
    sites = [line for line in pip_install_lines(blob(".github/workflows/ci.yml"))
             if packages_on(line)]
    assert len(sites) == 4, f"expected 4 dependency installs in ci.yml, found {len(sites)}"
    for line in sites:
        assert "-c tools/constraints.txt" in line, f"unconstrained install: {line}"

    dbt = blob("tools/dbt.py")
    assert '"-m", "pip", "install", "-c", str(_CONSTRAINTS)' in dbt


# --- Criterion 10 --------------------------------------------------------


def test_devcontainer_is_tracked_and_pinned_to_the_ci_tag(index, blob):
    assert ".devcontainer/devcontainer.json" in index
    assert ".devcontainer/Dockerfile" in index

    tag = re.search(r"^ARG DOCKER_TAG=(\S+)$", blob(".devcontainer/Dockerfile"), re.M)
    assert tag, "no default ARG DOCKER_TAG in the Dockerfile"

    ci_tags = set(re.findall(r"container:\s*espressif/idf:(\S+)",
                             blob(".github/workflows/ci.yml")))
    assert ci_tags, "no espressif/idf container in ci.yml"
    assert ci_tags == {tag.group(1)}, \
        f"devcontainer pins {tag.group(1)}, CI runs {sorted(ci_tags)}"


# --- Criterion 11 --------------------------------------------------------


def test_index_is_lf_only(index, blob):
    assert ".gitattributes" in index
    assert "* text=auto eol=lf" in blob(".gitattributes")

    crlf = [line for line in git("ls-files", "--eol").splitlines()
            if line.startswith("i/crlf")]
    assert not crlf, f"CRLF recorded in the index: {crlf}"


# --- Criterion 12 --------------------------------------------------------


def test_root_gitattributes_defers_to_the_generated_index(blob):
    def patterns(text):
        return [line.split()[0] for line in text.splitlines()
                if line.strip() and not line.lstrip().startswith("#")]

    governed = patterns(blob(".knowledge/.gitattributes"))
    for pattern in patterns(blob(".gitattributes")):
        if pattern == "*":
            continue
        clashes = [name for name in governed if fnmatch.fnmatch(name, pattern)]
        assert not clashes, \
            f"root .gitattributes pattern '{pattern}' also targets {clashes}, " \
            "which .knowledge/.gitattributes governs"


# --- Criterion 13 --------------------------------------------------------


def test_no_tracked_file_exceeds_the_size_bound(index):
    # Gitlinks (mode 160000) are submodule commits; their contents are not here.
    blobs = {path: sha for path, (mode, sha) in index.items()
             if mode != "160000" and not path.startswith(GENERATED_TREE)}

    query = "".join(f"{sha}\n" for sha in blobs.values())
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "cat-file", "--batch-check=%(objectsize)"],
        input=query.encode(), capture_output=True, check=True)
    sizes = dict(zip(blobs, (int(n) for n in proc.stdout.split())))

    oversized = {path: size for path, size in sizes.items() if size > MAX_TRACKED_BYTES}
    assert not oversized, (
        "tracked files above %d bytes: %s" % (
            MAX_TRACKED_BYTES,
            ", ".join(f"{path} ({size} B)" for path, size in sorted(oversized.items()))))


# --- Criterion 15 --------------------------------------------------------


def test_contributing_names_the_commands_that_exist(index, blob):
    assert "CONTRIBUTING.md" in index
    assert "(CONTRIBUTING.md)" in blob("README.md")
    assert "tools/duneos-bspgen.py" in blob("CONTRIBUTING.md")

    contributing = blob("CONTRIBUTING.md")
    for command in PRE_PR_COMMANDS:
        assert command in contributing, f"CONTRIBUTING.md does not name `{command}`"


# --- SPEC-leg-17/18/19/21 criterion 10 -----------------------------------


def test_the_pre_pr_gate_list_lives_in_exactly_one_file(index, blob):
    """Two copies of the list is two sources of truth, and the second one rots.
    Other files link to CONTRIBUTING.md instead of restating it."""
    markdown = sorted(path for path in index
                      if path.endswith(".md") and not path.startswith(GENERATED_TREE))
    restating = [path for path in markdown
                 if path != "CONTRIBUTING.md"
                 and sum(command in blob(path) for command in PRE_PR_COMMANDS) >= 3]
    assert not restating, (
        "these files restate CONTRIBUTING.md's pre-PR gate list instead of "
        f"linking to it: {', '.join(restating)}")


def test_the_documents_that_describe_testing_link_to_the_list(blob):
    for path in ("docs/testing.md", "CLAUDE.md"):
        assert "CONTRIBUTING.md" in blob(path), \
            f"{path} does not link to CONTRIBUTING.md"


# --- SPEC-leg-17/18/19/21 criterion 4 ------------------------------------


def test_the_venv_dbt_bootstraps_can_run_the_pytest_gate(blob):
    """CONTRIBUTING tells a newcomer to run `python -m pytest -q`; the venv
    tools/dbt.py builds is the only Python environment the repo provisions."""
    deps = re.search(r"_DEPS = \[(.*?)\]", blob("tools/dbt.py"), re.S)
    assert deps, "could not find _DEPS in tools/dbt.py"
    names = {normalise(name) for name in re.findall(r'"([^"]+)"', deps.group(1))}
    assert "pytest" in names, "tools/dbt.py does not install pytest"


# --- Criterion 16 --------------------------------------------------------


def test_both_globs_carry_configure_depends(blob):
    # Spelled through a variable because the requirements phase re-runs this
    # file in CMake script mode, where CONFIGURE_DEPENDS is a hard error.
    text = blob("kernel/duneos_kernel/CMakeLists.txt")
    globs = [line for line in text.splitlines() if "file(GLOB" in line]
    assert len(globs) == 2, f"expected 2 file(GLOB) calls, found {len(globs)}"
    for line in globs:
        assert "${_DUNEOS_GLOB_DEPENDS}" in line or "CONFIGURE_DEPENDS" in line, \
            f"stale glob: {line.strip()}"

    guard = re.search(
        r"if\(CMAKE_SCRIPT_MODE_FILE\)\s*"
        r'set\(_DUNEOS_GLOB_DEPENDS ""\)\s*'
        r"else\(\)\s*"
        r"set\(_DUNEOS_GLOB_DEPENDS CONFIGURE_DEPENDS\)\s*"
        r"endif\(\)", text)
    assert guard, "the script-mode guard no longer yields CONFIGURE_DEPENDS on a real configure"


# --- Criterion 19 --------------------------------------------------------


def test_no_component_is_declared_at_any_version(index, blob):
    manifests = [path for path in index if path.endswith("idf_component.yml")]
    assert manifests, "no idf_component.yml is tracked"
    for path in manifests:
        for number, line in enumerate(blob(path).splitlines(), 1):
            assert not re.match(r'\s*version:\s*"?\*"?\s*$', line), \
                f"{path}:{number} leaves a component unbounded"
