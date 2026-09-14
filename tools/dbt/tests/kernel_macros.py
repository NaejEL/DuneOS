"""Which DUNEOS_* macros the kernel needs from board_config.h, per board.

LEG-31 was a board that could emit CONFIG_DUNEOS_DRV_I2C and not define
DUNEOS_I2C0_SDA_PIN. Nothing in the tree connected the two: the symbol comes
from bspgen's fragment, the macro from bspgen's header, and only the compiler
ever saw both. This module rebuilds that connection without a toolchain.

Both halves are computed, never listed:
  * which sources a board compiles — from the `set(DUNEOS_KERNEL_SRCS …)` and
    `list(APPEND DUNEOS_KERNEL_SRCS …)` statements in the CMake files, with the
    `if(CONFIG_DUNEOS_*)` blocks around them;
  * which DUNEOS_* names are board macros — every DUNEOS_* token referenced by
    those sources, minus every name a tracked header defines.

A hand-maintained allow-list here would grow until it asserted nothing, which is
the failure LEG-38-08 records. `test_board_macros.py` keeps it honest two ways:
a board that must fail the check, and a self-check that the CMake parse did not
silently drop a source. A dropped source is the one failure this module can have
that looks exactly like success.
"""

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# One pattern for both namespaces: CONFIG_DUNEOS_DRV_I2C has no word boundary
# before its DUNEOS_, so a bare `\bDUNEOS_` pattern silently never matches a
# CONFIG_ symbol and every `#ifdef CONFIG_DUNEOS_DRV_*` block reads as live.
_TOKEN = re.compile(r"\b(?:CONFIG_)?DUNEOS_[A-Z0-9_]+\b")
_DEFINE = re.compile(r"^\s*#\s*define\s+(DUNEOS_[A-Z0-9_]+)")
_ENUMERATOR = re.compile(r"^\s*(DUNEOS_[A-Z0-9_]+)\s*(?:=|,)")
_CPP_COND = re.compile(r"^\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)$")
_SET_VAR = re.compile(r'^\s*set\(\s*(\w+)\s+"([^"]*)"\s*\)')
_DEFINED = re.compile(r"\bdefined\s*(?:\(\s*([A-Za-z_]\w*)\s*\)|([A-Za-z_]\w*))")
_IDENT = re.compile(r"\b[A-Za-z_]\w*\b")
_INTEGER = re.compile(r"-?\d+$")
# Everything a #if may still contain once its identifiers are integers. `=` is
# here only so a malformed expression reaches eval() and fails there.
_ARITHMETIC = re.compile(r"[0-9\s+\-*/%()<>=&|^]*$")
_SRCS_OPEN = re.compile(r"(?:set\(|list\(\s*APPEND\s+)DUNEOS_KERNEL_SRCS")
_QUOTED = re.compile(r'"([^"]*)"')

# The one source token that cannot resolve to a tracked file: blobs_gen.c is
# written into the build directory by the same CMakeLists at configure time.
UNRESOLVABLE = {"${BLOBS_GEN_C}"}

# Which arch/*/arch.cmake an IDF build of this cpu actually includes, taken from
# the guards at the head of each file: xtensa_esp32s3 self-selects on
# IDF_TARGET_ARCH == xtensa (every Xtensa target, S3 or not), xtensa_esp32 adds
# the RMII Ethernet HAL on plain ESP32 only.
ARCH_DIRS_BY_CPU = {
    "esp32":   ("xtensa_esp32s3", "xtensa_esp32"),
    "esp32s2": ("xtensa_esp32s3",),
    "esp32s3": ("xtensa_esp32s3",),
}


def cmake_files(cpu):
    return [REPO_ROOT / "kernel" / "duneos_kernel" / "CMakeLists.txt"] + [
        REPO_ROOT / "arch" / d / "arch.cmake" for d in ARCH_DIRS_BY_CPU.get(cpu, ())]


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


_CMAKE_NON_AND = re.compile(r"\b(?:OR|NOT)\b")


def _cmake_gate(stripped):
    """The CONFIG_ symbols an `if()` requires, or the empty set when it is not
    a plain conjunction of them.

    missing_macros() satisfies a gate with `g <= configs`, which is an AND. A
    condition holding OR or NOT does not mean that, and collapsing its operands
    into one set turns `if(A OR B)` into `if(A AND B)`: the source is then
    skipped for a board enabling only A, which compiles it — the LEG-31
    blindness this module exists to remove, and invisible from the result.
    `if(CONFIG_DUNEOS_DRV_USB_MSC OR CONFIG_DUNEOS_DRV_USB_CDC)` around
    drv_usb.c is the instance in the tree; bspgen emits the two independently.

    A condition whose parens do not balance on this line is refused for the
    same reason: cmake_sources() hands over the opening line only, so the `OR`
    of a wrapped `if(A\n   OR B)` would never reach the test above and the
    operands already read would collapse to `{A}` — the same AND, reached by a
    pure reformat. test_the_cmake_parse_drops_no_source promises a source can
    leave the parse only by leaving the file, never by being reformatted; the
    gate has to hold that promise too.

    Either way the condition is refused rather than translated and the statement
    counts as unguarded. That over-demands — a board enabling neither symbol
    still gets the file scanned — which is a false positive a reader dismisses,
    the direction this translation is allowed to fail in. It is not a claim
    about the module: a dropped source and a skipped #else branch both
    under-report, which is why the self-checks in this file exist.
    """
    if _CMAKE_NON_AND.search(stripped):
        return frozenset()
    if stripped.count("(") != stripped.count(")"):
        return frozenset()
    return frozenset(t for t in _TOKEN.findall(stripped) if t.startswith("CONFIG_"))


def _expand(text, variables):
    for name, value in variables.items():
        text = text.replace("${%s}" % name, value)
    return text


def cmake_sources(cmake_path):
    """(token, resolved path or None, frozenset of gating CONFIG symbols).

    A DUNEOS_KERNEL_SRCS statement spans as many lines as it likes, so the scan
    stays open until the closing paren: reading only the opening line drops
    every multi-line block, and the drop is invisible from the result.
    """
    variables = {"CMAKE_CURRENT_LIST_DIR": str(cmake_path.parent)}
    stack = []
    found = []
    depth = 0
    gate = frozenset()
    for raw in cmake_path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0]
        if depth == 0:
            m = _SET_VAR.match(line)
            if m:
                variables[m.group(1)] = _expand(m.group(2), variables)
            stripped = line.strip()
            if stripped.startswith("if("):
                stack.append(_cmake_gate(stripped))
                continue
            if stripped.startswith(("elseif(", "else(")) and stack:
                stack[-1] = frozenset()
                continue
            if stripped.startswith("endif(") and stack:
                stack.pop()
                continue
            m = _SRCS_OPEN.search(line)
            if not m:
                continue
            gate = frozenset().union(*stack) if stack else frozenset()
            line = line[m.end():]
            depth = 1

        for token in _QUOTED.findall(line):
            resolved = _expand(token, variables)
            if "${" in resolved:
                found.append((token, None, gate))
                continue
            path = Path(resolved)
            if not path.is_absolute():
                path = cmake_path.parent / path
            found.append((token, path, gate))
        depth += line.count("(") - line.count(")")
        if depth <= 0:
            depth = 0
    return found


def quoted_c_tokens(cmake_path):
    """Every quoted token naming a .c file, ignoring `set(VAR "…")` lines.

    The independent count the parse is checked against: a source can only leave
    cmake_sources() by leaving the file, never by being reformatted.
    """
    tokens = []
    for raw in cmake_path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0]
        if _SET_VAR.match(line):
            continue
        tokens += [t for t in _QUOTED.findall(line) if t.endswith(".c")]
    return tokens


def kernel_sources(cpu):
    """Compiled source -> the gate sets under which it is compiled.

    A source reached by an unguarded statement carries the empty gate, which is
    satisfied by every board: `vfs.c` — the functional LEG-31 consumer — is one
    of those, and it must be scanned for every board, not only for I2C ones.
    """
    sources = {}
    for cmake_path in cmake_files(cpu):
        for _token, path, gate in cmake_sources(cmake_path):
            if path is not None and path.exists():
                sources.setdefault(path, set()).add(gate)
    return sources


def _live_refs(path, board_defines, enabled_configs):
    """DUNEOS_* tokens this file reaches with these board macros and symbols.

    A reference under `#ifdef DUNEOS_X` where the board does not define
    DUNEOS_X is dead code for that board (drv_spi.c probes SPI1/2/3 this way)
    and must not be demanded; a reference under `#ifdef CONFIG_DUNEOS_DRV_X` is
    demanded only when the fragment emits that symbol (vfs.c's board.info I2C
    block); a value test like `#if DUNEOS_HAS_SD` is evaluated (_eval_cond).
    Conditions naming an identifier bspgen does not own stay live, so an unknown
    never silences the check. An #else or #elif branch is skipped rather than
    guessed at, which can under-report but cannot invent.
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
                stack.append(_branch_dead(kind, rest, board_defines, enabled_configs))
                dead_depth += stack[-1]
            elif kind in ("elif", "else") and stack:
                dead_depth -= stack[-1]
                stack[-1] = 1
                dead_depth += 1
            elif kind == "endif" and stack:
                dead_depth -= stack.pop()
            continue
        if dead_depth:
            continue
        refs.update(_TOKEN.findall(raw.split("//", 1)[0]))
    return refs - local_defines


class _NotEvaluable(Exception):
    pass


def _branch_dead(kind, rest, board_defines, enabled_configs):
    if kind in ("ifdef", "ifndef"):
        names = _TOKEN.findall(rest)
        if len(names) != 1:
            return 0
        present = _is_defined(names[0], board_defines, enabled_configs)
        return (0 if present else 1) if kind == "ifdef" else (1 if present else 0)
    value = _eval_cond(rest, board_defines, enabled_configs)
    return 0 if value is None or value else 1


def _is_defined(name, board_defines, enabled_configs):
    if name.startswith("CONFIG_"):
        return name in enabled_configs
    return name in board_defines


def _eval_cond(rest, board_defines, enabled_configs):
    """The value of a `#if` expression, or None when it is not evaluable.

    `#if DUNEOS_HAS_SD` and `#if DUNEOS_SD_CD_PIN >= 0` are value tests, not
    defined() tests, and vfs.c guards the whole SD block with the first: reading
    them as live demands DUNEOS_SD_* from every board that has no SD card. The
    C rule that an identifier no #define gives a value to is 0 inside #if makes
    this an evaluation rather than a guess — but only while every identifier is
    one bspgen owns. Anything else — a DUNEOS_ macro with a non-integer value, a
    non-DUNEOS identifier, or a CONFIG_ symbol from ESP-IDF's sdkconfig — returns
    None and stays live, because over-demanding a macro is a false positive a
    reader can dismiss and under-demanding one is the blindness this file exists
    to remove.
    """
    expr = rest.split("/*", 1)[0].split("//", 1)[0].strip()
    if not expr:
        return None

    def substitute_defined(m):
        name = m.group(1) or m.group(2)
        # A CONFIG_ symbol outside CONFIG_DUNEOS_ comes from ESP-IDF's sdkconfig,
        # which bspgen does not own and this module cannot see. Answering "not
        # defined" would class the branch dead and stop demanding the macros
        # inside it — the one direction that reintroduces LEG-31 blindness.
        if name.startswith("CONFIG_") and not name.startswith("CONFIG_DUNEOS_"):
            raise _NotEvaluable
        return "1" if _is_defined(name, board_defines, enabled_configs) else "0"

    def substitute_identifier(m):
        name = m.group(0)
        if name.startswith("CONFIG_DUNEOS_"):
            return "1" if name in enabled_configs else "0"
        if not name.startswith("DUNEOS_"):
            raise _NotEvaluable
        value = board_defines.get(name, "0").strip()
        if not _INTEGER.match(value):
            raise _NotEvaluable
        return value

    try:
        expr = _IDENT.sub(substitute_identifier, _DEFINED.sub(substitute_defined, expr))
    except _NotEvaluable:
        return None
    # `!` binds tighter than a comparison in C and looser in Python, so an
    # expression still carrying one after defined() is substituted is refused
    # rather than translated.
    if "!" in expr.replace("!=", ""):
        return None
    if not _ARITHMETIC.match(expr):
        return None
    try:
        return eval(expr.replace("&&", " and ").replace("||", " or "), {"__builtins__": {}})
    except (SyntaxError, ValueError, ZeroDivisionError, TypeError):
        return None


def board_config_defines(header_path):
    """DUNEOS_* name -> the literal text bspgen defined it as."""
    defines = {}
    for line in header_path.read_text(encoding="utf-8").splitlines():
        m = _DEFINE.match(line)
        if m:
            defines[m.group(1)] = line[m.end():].strip()
    return defines


def enabled_configs(fragment_text):
    return {line.split("=", 1)[0]
            for line in fragment_text.splitlines()
            if line.startswith("CONFIG_DUNEOS_") and line.endswith("=y")}


def missing_macros(cpu, fragment_text, header_path, known_names=None):
    """Board macros the kernel this board compiles references and bspgen did
    not define. Empty is the invariant; anything in it fails to compile."""
    known = header_defined_names() if known_names is None else known_names
    configs = enabled_configs(fragment_text)
    board_defines = board_config_defines(header_path)
    missing = set()
    for path, gates in kernel_sources(cpu).items():
        if not any(g <= configs for g in gates):
            continue
        for name in _live_refs(path, board_defines, configs):
            if name.startswith("CONFIG_") or name in known or name in board_defines:
                continue
            missing.add(name)
    return missing
