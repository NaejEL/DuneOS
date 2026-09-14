"""Which DUNEOS_* macros the kernel needs from board_config.h, per board.

LEG-31 was a board that could emit CONFIG_DUNEOS_DRV_I2C and not define
DUNEOS_I2C0_SDA_PIN. Nothing in the tree connected the two: the symbol comes
from bspgen's fragment, the macro from bspgen's header, and only the compiler
ever saw both. This module rebuilds that connection without a toolchain.

Both halves are computed, never listed:
  * which sources a board compiles — from the `if(CONFIG_DUNEOS_*)` blocks that
    append to DUNEOS_KERNEL_SRCS in the CMake files;
  * which DUNEOS_* names are board macros — every DUNEOS_* token referenced by
    those sources, minus every name a tracked header defines.

A hand-maintained allow-list here would grow until it asserted nothing, which is
the failure LEG-38-08 records. `test_board_macros.py` keeps it honest with a
board that must fail.
"""

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

_MACRO = re.compile(r"\bDUNEOS_[A-Z0-9_]+\b")
_CONFIG = re.compile(r"\bCONFIG_DUNEOS_[A-Z0-9_]+\b")
_DEFINE = re.compile(r"^\s*#\s*define\s+(DUNEOS_[A-Z0-9_]+)")
_ENUMERATOR = re.compile(r"^\s*(DUNEOS_[A-Z0-9_]+)\s*(?:=|,)")
_CPP_COND = re.compile(r"^\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)$")
_SET_VAR = re.compile(r'^\s*set\(\s*(\w+)\s+"([^"]*)"\s*\)')
_APPEND_SRC = re.compile(r'"([^"]+)"')

# Which arch/*/arch.cmake an IDF build of this cpu actually includes, taken from
# the guards at the head of each file: xtensa_esp32s3 self-selects on
# IDF_TARGET_ARCH == xtensa (every Xtensa target, S3 or not), xtensa_esp32 adds
# the RMII Ethernet HAL on plain ESP32 only.
ARCH_DIRS_BY_CPU = {
    "esp32":   ("xtensa_esp32s3", "xtensa_esp32"),
    "esp32s2": ("xtensa_esp32s3",),
    "esp32s3": ("xtensa_esp32s3",),
}


def tracked_files(*patterns):
    out = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", *patterns],
                         capture_output=True, text=True, check=True).stdout
    return [REPO_ROOT / line for line in out.splitlines() if line]


def header_defined_names():
    """Every DUNEOS_* name a tracked header already provides — #define or
    enumerator. board_config.h is generated and therefore not tracked, which is
    what makes this subtraction leave exactly the board macros behind."""
    names = set()
    for path in tracked_files("*.h"):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            for pattern in (_DEFINE, _ENUMERATOR):
                m = pattern.match(line)
                if m:
                    names.add(m.group(1))
    return names


def _cmake_sources(cmake_path, variables):
    """(source path, frozenset of CONFIG_DUNEOS_* symbols gating it) per append."""
    variables = dict(variables)
    variables["CMAKE_CURRENT_LIST_DIR"] = str(cmake_path.parent)
    stack = []
    found = []
    for raw in cmake_path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0]
        m = _SET_VAR.match(line)
        if m:
            variables[m.group(1)] = _expand(m.group(2), variables)
        stripped = line.strip()
        if stripped.startswith("if("):
            stack.append(frozenset(_CONFIG.findall(stripped)))
            continue
        if stripped.startswith(("elseif(", "else(")) and stack:
            stack[-1] = frozenset()
            continue
        if stripped.startswith("endif(") and stack:
            stack.pop()
            continue
        if "DUNEOS_KERNEL_SRCS" not in line:
            continue
        gate = frozenset().union(*stack) if stack else frozenset()
        for src in _APPEND_SRC.findall(line):
            if "DUNEOS_KERNEL_SRCS" in src:
                continue
            resolved = _expand(src, variables)
            if "${" in resolved:
                continue
            path = Path(resolved)
            if not path.is_absolute():
                path = cmake_path.parent / path
            found.append((path, gate))
    return found


def _expand(text, variables):
    for name, value in variables.items():
        text = text.replace("${%s}" % name, value)
    return text


def kernel_sources(cpu):
    """Every compiled kernel source for this cpu, with the CONFIG symbols that
    gate it. An entry gated by the empty set is compiled on every board."""
    files = [REPO_ROOT / "kernel" / "duneos_kernel" / "CMakeLists.txt"]
    files += [REPO_ROOT / "arch" / d / "arch.cmake"
              for d in ARCH_DIRS_BY_CPU.get(cpu, ())]
    sources = {}
    for cmake_path in files:
        for path, gate in _cmake_sources(cmake_path, {}):
            if path.exists():
                sources.setdefault(path, set()).add(gate)
    return sources


def _live_refs(path, board_macros, enabled_configs):
    """DUNEOS_* tokens this file reaches with these board macros and symbols.

    A reference under `#ifdef DUNEOS_X` where the board does not define
    DUNEOS_X is dead code for that board (drv_spi.c probes SPI1/2/3 this way)
    and must not be demanded; a reference under `#ifdef CONFIG_DUNEOS_DRV_X` is
    demanded only when the fragment emits that symbol (vfs.c's board.info I2C
    block). Conditions we cannot evaluate — __has_include, arithmetic — stay
    live, so an unknown never silences the check.
    """
    refs = set()
    local_defines = set()
    dead_depth = 0
    stack = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m_def = _DEFINE.match(raw)
        if m_def:
            local_defines.add(m_def.group(1))
        cond = _CPP_COND.match(raw)
        if cond:
            kind, rest = cond.group(1), cond.group(2)
            if kind in ("if", "ifdef", "ifndef"):
                stack.append(_branch_dead(kind, rest, board_macros, enabled_configs))
                dead_depth += stack[-1]
            elif kind in ("elif", "else") and stack:
                # The taken branch is already accounted for; an alternative
                # branch is skipped rather than guessed at.
                dead_depth -= stack[-1]
                stack[-1] = 1
                dead_depth += 1
            elif kind == "endif" and stack:
                dead_depth -= stack.pop()
            continue
        if dead_depth:
            continue
        refs.update(_MACRO.findall(_strip_comment(raw)))
    return refs - local_defines


def _strip_comment(line):
    return line.split("//", 1)[0]


def _branch_dead(kind, rest, board_macros, enabled_configs):
    names = set(_MACRO.findall(rest))
    configs = {n for n in names if n.startswith("CONFIG_")}
    macros = names - configs
    if configs and not configs <= enabled_configs:
        return 1
    if kind == "ifdef" and len(macros) == 1 and not configs:
        return 0 if macros <= board_macros else 1
    if kind == "ifndef" and len(macros) == 1 and not configs:
        return 1 if macros <= board_macros else 0
    if kind == "if" and macros and _only_defined_tests(rest, macros):
        return 0 if macros <= board_macros else 1
    return 0


def _only_defined_tests(rest, macros):
    return all(re.search(r"defined\s*\(?\s*%s\b" % re.escape(m), rest) for m in macros)


def board_config_macros(header_path):
    return {m.group(1) for m in
            (_DEFINE.match(line)
             for line in header_path.read_text(encoding="utf-8").splitlines())
            if m}


def enabled_configs(fragment_text):
    return {line.split("=", 1)[0]
            for line in fragment_text.splitlines()
            if line.startswith("CONFIG_DUNEOS_") and line.endswith("=y")}


def missing_macros(cpu, fragment_text, header_path, known_names=None):
    """Board macros the kernel this board compiles references and bspgen did
    not define. Empty is the invariant; anything in it fails to compile."""
    known = header_defined_names() if known_names is None else known_names
    configs = enabled_configs(fragment_text)
    board_macros = board_config_macros(header_path)
    missing = set()
    for path, gates in kernel_sources(cpu).items():
        if not any(g <= configs for g in gates):
            continue
        for name in _live_refs(path, board_macros, configs):
            if name.startswith("CONFIG_") or name in known or name in board_macros:
                continue
            missing.add(name)
    return missing
