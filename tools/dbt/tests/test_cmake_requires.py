"""LEG-33A: the shape of DUNEOS_KERNEL_REQUIRES.

CONFIG_* is empty during ESP-IDF's requirements phase, so a REQUIRES entry
under a CONFIG_DUNEOS_* guard is absent exactly when the board that needs it is
being configured, and the failure is a missing header rather than a missing
component. The rule is therefore structural, and the one entry that cannot obey
it is named below rather than admitted by a loose pattern.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

KERNEL_CMAKE = REPO_ROOT / "kernel" / "duneos_kernel" / "CMakeLists.txt"
MAIN_CMAKE = REPO_ROOT / "main" / "CMakeLists.txt"
ARCH_CMAKES = sorted((REPO_ROOT / "arch").glob("*/arch.cmake"))

# espressif/esp_tinyusb is published only for targets with a USB-OTG MAC
# (kernel/duneos_kernel/idf_component.yml), so an unconditional REQUIRES is a
# configure error on esp32 and esp32c3. Its headers are reached only from
# sources carrying the same guard, so the requirements phase never needs it.
GUARDED_EXCEPTIONS = {("kernel/duneos_kernel/CMakeLists.txt", "espressif__esp_tinyusb")}

_CONFIG_GUARD = re.compile(r"\bCONFIG_DUNEOS_\w+")
_CONSUMER = re.compile(r"#\s*([\w./\-]+\.(?:c|h)):(\d+)")
_COMPONENT = re.compile(r"^\s*([A-Za-z_][\w]*)\s*(?:#.*)?$")


def _rel(path):
    return path.relative_to(REPO_ROOT).as_posix()


def _requires_entries(path):
    """(component, guard symbols, comment) for every REQUIRES entry in the file."""
    stack = []
    collecting = False
    entries = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        code = raw.split("#", 1)[0]
        stripped = code.strip()
        if stripped.startswith("if("):
            stack.append(frozenset(_CONFIG_GUARD.findall(stripped)))
        elif stripped.startswith(("elseif(", "else(")) and stack:
            stack[-1] = frozenset()
        elif stripped.startswith("endif(") and stack:
            stack.pop()

        gate = frozenset().union(*stack) if stack else frozenset()

        if re.search(r"(?:set\(|list\(APPEND)\s*DUNEOS_KERNEL_REQUIRES", code):
            collecting = True
            # A one-line append carries its component on the same line.
            tail = re.sub(r".*DUNEOS_KERNEL_REQUIRES", "", code).strip(" )")
            if tail:
                entries.append((tail, gate, raw))
                collecting = False
            continue
        if not collecting:
            continue
        if stripped.startswith(")") or stripped == "":
            collecting = False
            continue
        m = _COMPONENT.match(code)
        if m:
            entries.append((m.group(1), gate, raw))
        else:
            collecting = False
    return entries


def _main_requires():
    """main/CMakeLists.txt uses idf_component_register's own REQUIRES list."""
    entries = []
    collecting = False
    for raw in MAIN_CMAKE.read_text(encoding="utf-8").splitlines():
        code = raw.split("#", 1)[0]
        if re.match(r"^\s*REQUIRES\s*$", code):
            collecting = True
            continue
        if not collecting:
            continue
        if code.strip().startswith(")"):
            break
        m = _COMPONENT.match(code)
        if m:
            entries.append((m.group(1), frozenset(), raw))
    return entries


ALL_FILES = [KERNEL_CMAKE, *ARCH_CMAKES]


@pytest.mark.parametrize("path", ALL_FILES, ids=_rel)
def test_no_requires_entry_sits_under_a_config_guard(path):
    offenders = [(component, sorted(gate))
                 for component, gate, _ in _requires_entries(path)
                 if gate and (_rel(path), component) not in GUARDED_EXCEPTIONS]
    assert offenders == [], (
        f"{_rel(path)} guards REQUIRES on CONFIG_DUNEOS_*, which is empty during "
        f"the requirements phase: {offenders}")


def test_the_named_exception_is_still_the_one_it_claims_to_be():
    """A stale exception is an allow-list entry nobody rereads. It has to keep
    matching something, and match only that."""
    guarded = {(_rel(path), component)
               for path in ALL_FILES
               for component, gate, _ in _requires_entries(path) if gate}
    assert guarded == GUARDED_EXCEPTIONS


def test_no_component_is_declared_both_unconditionally_and_per_arch():
    """esp_netif was listed in the kernel component and again in the S3 arch
    file; the second copy had no effect and hid that the first one existed."""
    core = {component for component, gate, _ in _requires_entries(KERNEL_CMAKE)
            if not gate}
    for path in ARCH_CMAKES:
        arch = {component for component, _, _ in _requires_entries(path)}
        assert core & arch == set(), f"{_rel(path)} repeats {sorted(core & arch)}"


@pytest.mark.parametrize("path", [KERNEL_CMAKE, MAIN_CMAKE,
                                  REPO_ROOT / "arch" / "xtensa_esp32s3" / "arch.cmake"],
                         ids=_rel)
def test_every_requires_entry_names_a_consumer(path):
    entries = _main_requires() if path == MAIN_CMAKE else _requires_entries(path)
    assert entries, f"{_rel(path)}: no REQUIRES entry parsed"
    for component, _gate, raw in entries:
        assert _CONSUMER.search(raw), \
            f"{_rel(path)}: {component} names no consumer as file:line"


@pytest.mark.parametrize("path", [KERNEL_CMAKE, MAIN_CMAKE,
                                  REPO_ROOT / "arch" / "xtensa_esp32s3" / "arch.cmake"],
                         ids=_rel)
def test_every_named_consumer_resolves(path):
    entries = _main_requires() if path == MAIN_CMAKE else _requires_entries(path)
    for component, _gate, raw in entries:
        m = _CONSUMER.search(raw)
        assert m, f"{_rel(path)}: {component} names no consumer as file:line"
        for base in (path.parent, REPO_ROOT):
            target = (base / m.group(1)).resolve()
            if target.is_file():
                break
        assert target.is_file(), f"{_rel(path)}: {component} points at {m.group(1)}"
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        assert int(m.group(2)) <= len(lines), \
            f"{_rel(path)}: {component} points past the end of {m.group(1)}"
