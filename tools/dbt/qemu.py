"""
qemu — hardware-free boot + loader smoke test (LEG-27, ADR 039).

`dbt qemu` builds the kernel and the flash image for a QEMU board, splices the
sysbin LittleFS image into the flash layout, boots the whole thing under
Espressif's qemu-xtensa and asserts on the serial output:

    /flash mounted → /flash/bin scanned → a .dap loaded → the app's own marker

It never opens a physical serial port and never reads `.duneos_port`: the only
channel is QEMU's stdio-backed UART0.

The emulator itself is launched through `plugin.run_qemu()` on the toolchain
plugin, next to `build_kernel` / `flash_kernel` / `monitor`. Everything in this
module above `cmd_qemu()` is pure logic and is unit-tested without QEMU
installed (`tools/dbt/tests/test_qemu.py`).

The emulator needs no manual setup: `qemu-xtensa` is an `on_request` IDF tool,
so it is neither installed by default nor put on PATH by `export.sh` — the
plugin resolves it inside the IDF tools tree and installs it if it is missing.
See docs/qemu-test-bench.md.
"""

import re
import socket
import sys
import time
from pathlib import Path

from .constants import DUNEOS_ROOT

# ---------------------------------------------------------------------------
# Contract of the bench
# ---------------------------------------------------------------------------

SMOKE_APP        = "qemu_smoke"
QEMU_BOARDS      = ("esp32s3-qemu", "esp32s3-qemu-psram")
DEFAULT_TIMEOUT_S = 180.0
# Keep reading after the last assertion matched: a panic one line later would
# otherwise be reported as a pass — the costliest failure mode for a bench.
DEFAULT_SETTLE_S  = 2.0

EXIT_OK           = 0
EXIT_ASSERT       = 1
EXIT_TIMEOUT      = 2
EXIT_PANIC        = 3
EXIT_QEMU_MISSING = 4
EXIT_BUILD        = 5
# A bench that cannot tell "the board is misconfigured" from "the firmware
# failed its assertions" wastes a CI investigation on every typo.
EXIT_CONFIG       = 6


def pick_gdb_port() -> int:
    """Reserve a free TCP port for QEMU's GDB server and return it.

    A fixed port is a bench-wide failure mode, not a preference: a leaked
    emulator from an earlier run still holds it, QEMU then dies at startup with
    "could not start gdbserver", and the run is reported as a firmware failure
    with every assertion missing. Asking the kernel for an ephemeral port
    removes that mode instead of classifying it.

    The socket is closed before QEMU binds, so a foreign process can still
    steal the port in between; `qemu_startup_failure()` catches that residual
    race and turns it into EXIT_CONFIG. Bound on INADDR_ANY to match the
    `-gdb tcp::<port>` QEMU is given.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("", 0))
        return int(sock.getsockname()[1])


# QEMU prints these on its own stdout and exits before the machine ever runs.
# They are environment faults, not firmware faults, and must never be scored
# as missing assertions.
QEMU_STARTUP_FAILURES: tuple[tuple[str, str], ...] = (
    ("QEMU could not open its GDB server port",
     r"could not start gdbserver|Failed to find an available port"),
    ("the requested TCP port is already in use",
     r"Address already in use"),
)


def qemu_startup_failure(text: str) -> str | None:
    """Name the environment fault that stopped QEMU from starting, or None."""
    for label, pattern in QEMU_STARTUP_FAILURES:
        if re.search(pattern, text):
            return label
    return None

# (label, regex). Order is the boot order, and it is the order failures are
# reported in — the first missing one is where the system stopped.
ASSERTIONS: tuple[tuple[str, str], ...] = (
    ("/flash mounted",
     r"LittleFS mounted at / \(root\)"),
    (f"/flash/bin scanned (found {SMOKE_APP}.dap)",
     rf"\[\d+\]\s+{SMOKE_APP}\s+v\S+\s+/bin/{SMOKE_APP}\.dap"),
    ("scan completed",
     r"scan: [1-9]\d* app\(s\) found"),
    (f"{SMOKE_APP}.dap loaded and launched",
     rf"autoboot: launching '{SMOKE_APP}'"),
    # The marker is printed by app_main immediately *before* duneos_exit(0),
    # so it proves the loaded code ran — it says nothing about what the exit
    # itself did. The label is worded to claim only that. Everything after the
    # app drains the klog ring (unload, slot release, exit accounting) is out
    # of this bench's reach: those lines land in the ring with no reader left.
    (f"{SMOKE_APP} app_main ran to its duneos_exit(0) call",
     r"<<<DUNEOS-QEMU-SMOKE app_main reached duneos_exit\(0\)>>>"),
)

# Which of these actually reach the emulated UART, and which are insurance:
#
#   REACHABLE — the panic handler prints these through `panic_print_char`,
#   which writes the UART register directly and bypasses klog entirely:
#     "Guru Meditation Error", "Panic|panic'ed", "CPU halted",
#     and the second `rst:0x…` banner (BOOT_BANNER) on the reboot that follows.
#
#   USUALLY UNREACHABLE — "abort() was called", "assert failed:",
#   "Task watchdog got triggered" and "CORRUPT HEAP" are ESP_EARLY_LOG /
#   esp_rom_printf output, and `klog_capture_rom_output()`
#   (kernel/duneos_kernel/src/klog.c) redirects that channel into the ring
#   buffer. They only show up when a reader is still draining the ring —
#   i.e. while qemu_smoke is alive. They are kept because they cost nothing
#   and do fire in that window, not because they can be relied on.
#
# Consequence to keep in mind when reading a red run: a panic that halts
# *without* rebooting and without a Guru Meditation dump surfaces as a
# timeout (EXIT_TIMEOUT), not as EXIT_PANIC. Both are failures; only the
# label differs.
PANIC_PATTERNS: tuple[tuple[str, str], ...] = (
    ("guru meditation",   r"Guru Meditation Error"),
    ("abort() called",    r"abort\(\) was called"),
    ("assert failed",     r"assert failed:"),
    ("task watchdog",     r"Task watchdog got triggered"),
    ("heap corruption",   r"CORRUPT HEAP"),
    ("stack overflow",    r"stack (?:overflow|protection fault)"),
    # Anchored on the panic handler's own wording ("Core 0 panic'ed (…)"):
    # a bare /Panic/ also fires on any line that merely mentions the word,
    # including this bench's own output echoed back into a capture.
    ("panic handler",     r"Core\s+\d+\s+panic'ed"),
    ("cpu halted",        r"CPU halted"),
)

# The ROM prints this once per reset. A second occurrence means the system
# rebooted — silence after a crash must never read as success.
BOOT_BANNER = r"rst:0x[0-9a-fA-F]+"

# Diagnostics, NOT assertions: they never influence the exit code. They exist
# because the assertions above all travel on one channel — qemu_smoke draining
# the klog ring to its stdout — so a run where the app never starts produces
# five identical [MISS] lines and says nothing about where the system stopped.
#
# Everything below travels on channels the console genuinely carries whatever
# DuneOS does: ESP-IDF's own ROM-path early logs, ESP-IDF's stdout-path logs,
# and klog level 'E' (the one level klog.c forwards to ESP_LOG). Reading the
# last marker reached tells a reader which half of the boot to investigate.
PROGRESS_MARKERS: tuple[tuple[str, str], ...] = (
    ("ROM bootloader ran",            r"ESP-ROM:esp32"),
    ("2nd stage bootloader ran",      r"boot: ESP-IDF"),
    ("app image loaded from flash",   r"boot: Loaded app from partition"),
    ("IDF early-log path alive",      r"heap_init: Initializing"),
    ("IDF stdout-log path alive",     r"main_task: Started on CPU"),
    ("app_main entered (IDF)",      r"main_task: Calling app_main\(\)"),
    # Matches the ESP_LOG form, not klog's own "[E][tag] " prefix: klog_write()
    # (kernel/duneos_kernel/src/klog.c) strips that prefix before forwarding
    # (`ESP_LOGE(tag, "%s", line + prefix_len)`), so the console only ever
    # carries "E (161) duneos/…". The bracketed form exists solely inside the
    # ring buffer, which is dumped long after the boot window this marker
    # covers — anchored on it, the marker could never fire.
    ("DuneOS klog error channel alive (level E reached the console)",
     r"^E \(\d+\) duneos"),
    # Written by the payload to /dev/uart0 as its first instruction, so it
    # does not depend on where a supervisor-launched app's fd 1 points. Seen
    # without the stdout markers = the app ran and its stdout goes nowhere.
    (f"{SMOKE_APP} app_main entered (via /dev/uart0)",
     r"<<<DUNEOS-QEMU-SMOKE app_main entered>>>"),
)

# The supervisor reports a dying app at level 'E', which does reach the
# console. Seeing it means the loader ran and the payload died — a different
# failure from "nothing ever started", and no reason to sit out the timeout.
APP_FAILURE_PATTERNS: tuple[tuple[str, str], ...] = (
    (f"{SMOKE_APP} exited non-zero",
     rf"'{SMOKE_APP}' exited \(code (?!0\))\d+\)"),
    (f"{SMOKE_APP} hit a CPU exception",
     rf"'{SMOKE_APP}' CPU exception: cause=\d+ PC=0x[0-9a-fA-F]+"),
    # Same klog-prefix correction as the progress marker above. Every klog_e()
    # in kernel/duneos_loader/src/loader.c is an abort of a load or a run;
    # a green run emits no loader line at level E at all (verified on both QEMU
    # boards), so making this pattern live cannot turn a pass into a failure.
    ("the loader refused the app",
     r"^E \(\d+\) duneos/loader: "),
)

# Boot-log signatures of a board/emulator mismatch rather than a firmware
# fault. No source change fixes these, and left unnamed they surface as five
# identical [MISS] lines and a timeout — which is exactly the diagnosis
# SPEC-leg-30 cost a cycle of. They are scored as EXIT_CONFIG, like a QEMU
# that never started.
CONFIG_FAILURE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("the external-memory vaddr window is exhausted — more PSRAM was mapped "
     "than the chip's 32 MiB data window can hold alongside the flash mmap",
     r"esp_mmu_map\(\d+\): no such vaddr range"),
    ("the partition table could not be mapped (load_partitions → "
     "ESP_ERR_NO_MEM)",
     r"load_partitions returned 0x105"),
)

# esp_psram announces the size it detected before anything maps it. The bench
# knows what the board declares, so the two can be compared instead of trusted
# — a future QEMU whose ssi_psram model reports a different size then fails
# loudly here rather than silently re-defining the board.
PSRAM_DETECTED = r"esp_psram: Found (\d+)MB PSRAM device"


def declared_psram_mb(board_cfg: dict) -> int:
    """MiB of PSRAM the board's YAML declares (0 when it declares none).

    board.yaml is the single source of truth for what the hardware is; the
    emulator is told this, not the other way round.
    """
    value = (board_cfg or {}).get("board", {}).get("psram_size_mb", 0)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Log analysis (pure)
# ---------------------------------------------------------------------------

class SmokeMatcher:
    """Incremental matcher over the captured serial stream.

    Chunks arrive at arbitrary boundaries, so each feed rescans only the new
    text plus a short tail of the previous one — enough for a pattern split
    across a chunk boundary, without re-scanning the whole capture every time
    (which is quadratic over a long run).
    """

    # Longer than any pattern above, so no match can straddle two windows.
    _OVERLAP = 512

    # Patterns anchored with `^` (the console's "E (161) tag: …" lines) mean
    # "start of a console line", not "start of the capture".
    _FLAGS = re.MULTILINE

    def __init__(self,
                 assertions: tuple[tuple[str, str], ...] = ASSERTIONS,
                 panics: tuple[tuple[str, str], ...] = PANIC_PATTERNS,
                 boot_banner: str = BOOT_BANNER,
                 progress: tuple[tuple[str, str], ...] = PROGRESS_MARKERS,
                 app_failures: tuple[tuple[str, str], ...] = APP_FAILURE_PATTERNS,
                 config_failures: tuple[tuple[str, str], ...] = CONFIG_FAILURE_PATTERNS,
                 psram_declared_mb: int = 0
                 ) -> None:
        F = self._FLAGS
        self._assertions = [(label, re.compile(pat, F)) for label, pat in assertions]
        self._panics     = [(label, re.compile(pat, F)) for label, pat in panics]
        self._progress   = [(label, re.compile(pat, F)) for label, pat in progress]
        self._appfails   = [(label, re.compile(pat, F)) for label, pat in app_failures]
        self._cfgfails   = [(label, re.compile(pat, F)) for label, pat in config_failures]
        self._banner     = re.compile(boot_banner, F)
        self._psram      = re.compile(PSRAM_DETECTED, F)
        self.psram_declared_mb = int(psram_declared_mb)
        self.psram_found_mb: int | None = None
        self.text        = ""
        self.matched: list[str] = []
        self.panics:  list[str] = []
        self.progress: list[str] = []
        self.app_failures: list[str] = []
        self.config_failures: list[str] = []
        self.boot_count = 0
        self._scanned = 0
        self._banner_next = 0

    def feed(self, chunk: str) -> None:
        if not chunk:
            return
        self.text += chunk
        start  = max(0, self._scanned - self._OVERLAP)
        # Rewind to the start of the line `start` falls in: with re.MULTILINE
        # `^` also matches at position 0 of whatever string is searched, so a
        # window opening mid-line would let a line's tail pose as a line start.
        if start:
            start = self.text.rfind("\n", 0, start) + 1
        window = self.text[start:]
        for found, patterns in ((self.matched, self._assertions),
                                (self.panics, self._panics),
                                (self.progress, self._progress),
                                (self.app_failures, self._appfails),
                                (self.config_failures, self._cfgfails)):
            for label, rx in patterns:
                if label not in found and rx.search(window):
                    found.append(label)
        if self.psram_found_mb is None:
            m = self._psram.search(window)
            if m:
                self.psram_found_mb = int(m.group(1))
        for m in self._banner.finditer(window):
            abs_start = start + m.start()
            if abs_start >= self._banner_next:
                self.boot_count += 1
                self._banner_next = abs_start + 1
        self._scanned = len(self.text)

    @property
    def app_failed(self) -> bool:
        return bool(self.app_failures)

    @property
    def psram_mismatch(self) -> str | None:
        """Name the disagreement between declared and detected PSRAM, or None.

        Silence is not a mismatch: a board declaring no PSRAM never prints the
        line, and neither does one whose firmware crashed before esp_psram ran.
        """
        if self.psram_declared_mb <= 0 or self.psram_found_mb is None:
            return None
        if self.psram_found_mb == self.psram_declared_mb:
            return None
        return (f"the board declares {self.psram_declared_mb} MiB of PSRAM but "
                f"the emulator reported {self.psram_found_mb} MiB")

    @property
    def psram_check_unavailable(self) -> str | None:
        """Say when the declared/detected cross-check could not be made at all.

        `psram_mismatch` treats an absent detection as "no mismatch", which is
        the only safe verdict but also a silent one: an ESP-IDF that reworded
        the esp_psram line would disable the check with no signal, which is
        precisely the third risk SPEC-leg-30 asked to fail loudly about. This
        is diagnostic (it never reaches `config_problems`), because the same
        silence is produced by a firmware that died before esp_psram ran — a
        failure the run already reports for what it is.
        """
        if self.psram_declared_mb <= 0 or self.psram_found_mb is not None:
            return None
        return (
            f"PSRAM cross-check did not run: the board declares "
            f"{self.psram_declared_mb} MiB but no line matching "
            f"{PSRAM_DETECTED!r} appeared on the console. Either the firmware "
            f"never reached esp_psram init (see the boot progress above), or "
            f"this ESP-IDF reworded that log line — in which case PSRAM_DETECTED "
            f"in tools/dbt/qemu.py needs updating, and until it is, a board "
            f"mapping the wrong amount of PSRAM will not be caught."
        )

    def config_problems(self) -> list[str]:
        reasons = list(self.config_failures)
        mismatch = self.psram_mismatch
        if mismatch:
            reasons.append(mismatch)
        return reasons

    def progress_report(self) -> list[tuple[str, bool]]:
        return [(label, label in self.progress) for label, _ in self._progress]

    @property
    def missing(self) -> list[str]:
        return [label for label, _ in self._assertions if label not in self.matched]

    @property
    def all_matched(self) -> bool:
        return not self.missing

    @property
    def rebooted(self) -> bool:
        return self.boot_count > 1

    @property
    def panicked(self) -> bool:
        return bool(self.panics) or self.rebooted

    def failure_reasons(self) -> list[str]:
        reasons = list(self.panics)
        if self.rebooted:
            reasons.append(f"system rebooted ({self.boot_count} boot banners)")
        return reasons


class SmokeConsumer:
    """Turns matcher state into a stop reason for `run_loop`.

    Returns "panic" as soon as the system crashes, and "complete" only once
    every assertion has matched *and* the settle window elapsed without a
    crash appearing.
    """

    def __init__(self, matcher: SmokeMatcher,
                 settle_s: float = DEFAULT_SETTLE_S,
                 clock=time.monotonic) -> None:
        self.matcher   = matcher
        self.settle_s  = settle_s
        self.clock     = clock
        self._settle_at: float | None = None

    def __call__(self, chunk: str) -> str | None:
        self.matcher.feed(chunk)
        if self.matcher.panicked:
            return "panic"
        # Stop as soon as the payload is known dead: the remaining assertions
        # can no longer match, and waiting out the timeout would relabel a
        # diagnosed crash as "the expected state was never reached".
        if self.matcher.app_failed and not self.matcher.all_matched:
            return "appfail"
        if self.matcher.all_matched:
            now = self.clock()
            if self._settle_at is None:
                self._settle_at = now + self.settle_s
            elif now >= self._settle_at:
                return "complete"
        return None

    @property
    def settled(self) -> bool:
        """True once the post-assertion crash window has really elapsed.

        An EOF one poll after the last assertion matched leaves this False:
        QEMU died in the window the bench exists to watch, which is a failure,
        not a pass.
        """
        return self._settle_at is not None and self.clock() >= self._settle_at


def run_loop(read_chunk, consume, timeout_s: float,
             clock=time.monotonic, poll_s: float = 0.1) -> str:
    """Drive `consume` from `read_chunk` until it stops, EOF, or timeout.

    read_chunk(poll_s) returns the next text, "" when nothing is available yet,
    or None at end of stream. Returns one of "complete", "panic", "eof",
    "timeout".
    """
    deadline = clock() + timeout_s
    while True:
        chunk  = read_chunk(poll_s)
        reason = consume(chunk if chunk else "")
        if reason:
            return reason
        if chunk is None:
            return "eof"
        if clock() >= deadline:
            return "timeout"


def verdict(status: str, matcher: SmokeMatcher, timeout_s: float,
            settled: bool = True) -> tuple[int, str]:
    """Map a run_loop status + matcher state to (exit code, message).

    `settled` is `SmokeConsumer.settled`: an EOF only counts as a pass once the
    crash window has elapsed.
    """
    # Checked before the status, not after: a crash the consumer happened to
    # see in the same chunk as the last assertion, or one that arrived after it
    # stopped reading, is still a crash. Criterion 8 must not depend on
    # SmokeConsumer's internal ordering.
    if matcher.panicked:
        reasons = ", ".join(matcher.failure_reasons()) or "unknown"
        return EXIT_PANIC, f"FAIL (panic): the system crashed or rebooted — {reasons}"
    if status == "panic":
        reasons = ", ".join(matcher.failure_reasons()) or "unknown"
        return EXIT_PANIC, f"FAIL (panic): the system crashed or rebooted — {reasons}"
    startup = qemu_startup_failure(matcher.text)
    if startup and not matcher.progress:
        return EXIT_CONFIG, (
            f"CONFIG ERROR: QEMU never started — {startup}. This is an "
            f"environment problem, not a firmware failure; no assertion was "
            f"ever given a chance to match."
        )
    # Ranked above the assertion outcomes: when the board is configured wrong
    # every assertion misses, and reporting that as a firmware failure sends
    # the reader to the wrong half of the system.
    problems = matcher.config_problems()
    if problems:
        return EXIT_CONFIG, (
            f"CONFIG ERROR: the board as booted does not match the board as "
            f"declared — {', '.join(problems)}. This is a configuration "
            f"problem, not a firmware failure."
        )
    if status == "appfail":
        reasons = ", ".join(matcher.app_failures) or "unknown"
        missing = ", ".join(matcher.missing) or "none"
        return EXIT_ASSERT, (
            f"FAIL (assertion): the kernel reported the loaded app dead before "
            f"it reached its marker — {reasons}. The system itself did not "
            f"crash. Still missing: {missing}"
        )
    if status == "timeout":
        missing = ", ".join(matcher.missing) or "none"
        return EXIT_TIMEOUT, (
            f"FAIL (timeout): no expected state reached within {timeout_s:g}s. "
            f"This is a timeout, not a failed assertion. Still missing: {missing}"
        )
    if status == "eof":
        missing = ", ".join(matcher.missing) or "none"
        if matcher.missing:
            return EXIT_ASSERT, (
                f"FAIL (assertion): QEMU exited before the run completed. "
                f"Missing: {missing}"
            )
        if not settled:
            return EXIT_ASSERT, (
                "FAIL (assertion): every assertion matched, but QEMU exited "
                "before the post-assertion crash window elapsed. A run that "
                "dies right after the last marker is not a pass."
            )
        return EXIT_OK, "PASS: every assertion matched"
    if matcher.missing:
        return EXIT_ASSERT, f"FAIL (assertion): missing {', '.join(matcher.missing)}"
    return EXIT_OK, "PASS: every assertion matched"


# ---------------------------------------------------------------------------
# Flash image composition (pure)
# ---------------------------------------------------------------------------

_SIZE_UNITS = {"KB": 1024, "MB": 1024 * 1024, "B": 1}


def parse_flash_size(value: str) -> int:
    """'8MB' → 8388608.

    Raises ValueError on anything that is not a literal size — esptool also
    accepts 'detect' and 'keep', which it resolves from the attached chip.
    There is no chip here, so guessing would silently produce an image of the
    wrong length; fail loudly instead.
    """
    v = value.strip().upper()
    for suffix, mult in _SIZE_UNITS.items():
        if v.endswith(suffix):
            digits = v[: -len(suffix)]
            if digits.isdigit():
                return int(digits) * mult
            break
    if v.isdigit():
        return int(v)
    raise ValueError(
        f"cannot size the flash image from --flash-size {value!r}: expected a "
        f"literal size such as '8MB' or '4MB', not a value esptool resolves "
        f"from the attached chip"
    )


def flash_size_from_opts(opts: dict[str, str]) -> int:
    """Read the flash size out of parsed flash_args options."""
    if "flash-size" not in opts:
        raise ValueError(
            "flash_args declares no --flash-size; cannot size the flash image"
        )
    return parse_flash_size(opts["flash-size"])


def parse_flash_args(text: str) -> tuple[dict[str, str], list[tuple[int, str]]]:
    """Parse the ESP-IDF `flash_args` argfile.

    Returns ({option: value}, [(offset, relative binary path)]).

    Option keys are normalised to the hyphenated spelling. ESP-IDF v6.0.1
    emits `--flash-size` (esptool_py/project_include.cmake, FLASH_SUB_ARGS)
    while older versions emitted `--flash_size`; a lookup that guesses wrong
    silently falls back to a default image size, which is exactly the bug this
    normalisation removes.
    """
    opts: dict[str, str] = {}
    entries: list[tuple[int, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("--"):
            toks = line.split()
            i = 0
            while i < len(toks):
                if toks[i].startswith("--") and i + 1 < len(toks) \
                        and not toks[i + 1].startswith("--"):
                    opts[toks[i][2:].replace("_", "-")] = toks[i + 1]
                    i += 2
                else:
                    i += 1
            continue
        parts = line.split()
        if len(parts) >= 2:
            entries.append((int(parts[0], 16), parts[1]))
    return opts, entries


def compose_flash_image(parts: list[tuple[int, bytes]], size: int) -> bytes:
    """Lay binaries out in an erased (0xFF) flash image of `size` bytes.

    This is what `idf.py qemu` does with esptool merge-bin, done here so the
    sysbin LittleFS image can be placed at its partition offset — without it
    /flash would be empty under emulation and there would be no .dap to load.
    """
    image = bytearray(b"\xff" * size)
    written: list[tuple[int, int]] = []
    for offset, data in sorted(parts):
        end = offset + len(data)
        if end > size:
            raise ValueError(
                f"binary at 0x{offset:x} ({len(data)} B) overflows the "
                f"{size} B flash image"
            )
        for w_start, w_end in written:
            if offset < w_end and w_start < end:
                raise ValueError(
                    f"binary at 0x{offset:x} overlaps the one at 0x{w_start:x}"
                )
        image[offset:end] = data
        written.append((offset, end))
    return bytes(image)


# ---------------------------------------------------------------------------
# klog ring reconstruction (pure)
# ---------------------------------------------------------------------------

def format_klog_ring(data: bytes, write_abs: int) -> str:
    """Rebuild the kernel log from a raw dump of klog.c's circular buffer.

    `write_abs` is the absolute byte counter (`s_write_abs`); the ring wraps
    once it exceeds the buffer, so oldest-first order has to be restored
    before the text means anything.
    """
    if not data:
        return ""
    size = len(data)
    if write_abs <= 0:
        return ""
    if write_abs < size:
        raw = data[:write_abs]
    else:
        cut = write_abs % size
        raw = data[cut:] + data[:cut]
    return raw.decode("utf-8", errors="replace").replace("\x00", "")


# ---------------------------------------------------------------------------
# Stream plumbing (used by the toolchain plugin)
# ---------------------------------------------------------------------------

def chunk_reader(stream):
    """Wrap a binary stream in a non-blocking read_chunk(timeout) callable.

    A daemon thread drains the pipe so the main loop can enforce its own
    deadline even when QEMU has gone quiet (or wedged).
    """
    import queue
    import threading

    q: "queue.Queue[bytes | None]" = queue.Queue()

    # read1() returns as soon as any data is available; plain read(n) would
    # block until n bytes arrived and hold back a partial boot line.
    read1 = getattr(stream, "read1", None)

    def pump() -> None:
        try:
            while True:
                data = read1(4096) if read1 else stream.read(1)
                if not data:
                    break
                q.put(data)
        finally:
            q.put(None)

    threading.Thread(target=pump, daemon=True).start()
    eof = False

    def read_chunk(timeout: float) -> str | None:
        nonlocal eof
        if eof:
            return None
        try:
            item = q.get(timeout=timeout)
        except queue.Empty:
            return ""
        if item is None:
            eof = True
            return None
        buf = bytearray(item)
        while True:
            try:
                more = q.get_nowait()
            except queue.Empty:
                break
            if more is None:
                q.put(None)
                break
            buf += more
        return bytes(buf).decode("utf-8", errors="replace")

    return read_chunk


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

def _config_error(message: str):
    """Bail out with EXIT_CONFIG, never with EXIT_ASSERT.

    A misconfigured bench and a firmware that failed its assertions call for
    completely different investigations; CI must be able to tell them apart.
    """
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(EXIT_CONFIG)


def _qemu_missing():
    print("\nERROR: qemu-system-xtensa could not be installed automatically.",
          file=sys.stderr)
    print("  Tried: $DUNEOS_QEMU, the ESP-IDF tools tree "
          "(~/.espressif/tools/qemu-xtensa/<version>/qemu/bin), PATH, then\n"
          "  `idf_tools.py install qemu-xtensa` (expected version "
          "esp_develop_9.2.2_20250817).", file=sys.stderr)
    print("  See docs/qemu-test-bench.md.", file=sys.stderr)
    sys.exit(EXIT_QEMU_MISSING)


class _as_config_error:
    """Turn a helper's bare `sys.exit(...)` into EXIT_CONFIG.

    `find_compiler`, `build_kernel` and the flashimg image builder all bail out
    with `sys.exit("ERROR: …")`, which exits 1 — the code this bench reserves
    for "the firmware failed its assertions". A missing build dependency must
    not present as a red firmware.
    """

    def __init__(self, what: str) -> None:
        self.what = what

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, _tb):
        if exc_type is not SystemExit:
            return False
        code = exc.code
        if code in (0, None) or code == EXIT_CONFIG:
            return False
        detail = code if isinstance(code, str) else f"exited with {code}"
        _config_error(f"{self.what}: {detail}")
        return False


def _read_board_file() -> str | None:
    board_file = DUNEOS_ROOT / ".duneos_board"
    try:
        lines = board_file.read_text().strip().splitlines()
    except OSError:
        return None
    return lines[0].strip() if lines else None


def _active_board() -> str:
    board = _read_board_file()
    if board is None:
        _config_error(".duneos_board not set — run `python tools/dbt.py setup`")
    return board


def _board_file_changed_under_run(snapshot: str) -> None:
    """Turn concurrent interference into EXIT_CONFIG instead of a false red.

    CMake reads .duneos_board on every configure and aborts when it differs
    from the board the build dir was configured for; when the file is momentarily
    absent — `echo ... > .duneos_board` from another terminal truncates before
    writing — it takes its own hardcoded fallback instead. Either way the run
    dies for a reason that has nothing to do with the firmware, and the report
    must say so rather than blame the bench.
    """
    now = _read_board_file()
    if now == snapshot:
        return
    _config_error(
        f".duneos_board changed underneath this run: it held '{snapshot}' at "
        f"start and holds {'nothing' if now is None else repr(now)} now.\n"
        "  Something else rewrote the active board while the run was in flight "
        "(another dbt invocation, an editor, or an `echo ... > .duneos_board` in\n"
        "  another terminal). This is interference, not a firmware failure — "
        "restore the board file and re-run before investigating anything else."
    )


def _build_sysbin(board: str, build_dir: Path) -> Path:
    """Build the LittleFS image through the existing flashimg path.

    Staging is profile-driven so exactly one app ships, and `no_init` leaves
    /flash without an init.yaml: the kernel then takes the autoboot path, which
    is the one that scans /flash/bin and is what this bench must exercise.
    """
    from .flashimg import cmd_flashimg

    class _Args:
        pass

    a = _Args()
    a.build      = False
    a.safe       = False
    a.port       = None
    a.board      = board
    a.image_only = True
    a.no_init    = True
    a.out_dir    = build_dir
    a.profile    = {
        "name":        "qemu-smoke",
        "board":       board,
        "apps_flash":  [SMOKE_APP],
        "init_flash":  [],
    }
    cmd_flashimg(a)
    return build_dir / "sysbin.bin"


def _write_flash_image(build_dir: Path, board: str, sysbin: Path) -> Path:
    from .flashimg import _get_sysbin_offset

    flash_args = build_dir / "flash_args"
    if not flash_args.exists():
        _config_error(
            f"{flash_args} not found — build the kernel first (drop --no-build)"
        )
    opts, entries = parse_flash_args(flash_args.read_text())
    try:
        size = flash_size_from_opts(opts)
    except ValueError as exc:
        _config_error(f"{flash_args}: {exc}")

    parts: list[tuple[int, bytes]] = []
    for offset, rel in entries:
        blob = build_dir / rel
        if not blob.exists():
            _config_error(f"{blob} listed in flash_args is missing")
        parts.append((offset, blob.read_bytes()))
    parts.append((_get_sysbin_offset(board), sysbin.read_bytes()))

    out = build_dir / "qemu_flash_duneos.bin"
    out.write_bytes(compose_flash_image(parts, size))
    print(f"  flash image → {out}  ({size // (1024 * 1024)} MB)")
    return out


def cmd_qemu(args) -> None:
    from .setup import run_bspgen
    from .toolchain import get_board_plugin
    from .builder import build_single

    # Snapshot, not a poll: every failure path re-reads the file once and
    # compares, so a board switch landing mid-run is reported as interference.
    active = _active_board()
    board = getattr(args, "board", None) or active
    if board != active:
        _config_error(
            f"--board is '{board}' but .duneos_board is '{active}'.\n"
            f"  sdkconfig is board-specific: switch with a full clean, e.g.\n"
            f"    echo {board} > .duneos_board"
        )
    if not (DUNEOS_ROOT / "boards" / board / "board.yaml").exists():
        _config_error(f"unknown board '{board}'")
    if board not in QEMU_BOARDS:
        print(f"  [warn] '{board}' is not one of the QEMU boards "
              f"({', '.join(QEMU_BOARDS)}) — it may declare hardware the "
              f"emulator does not model.")

    build_dir = Path(getattr(args, "build_dir", None) or (DUNEOS_ROOT / f"build-{board}"))
    if not build_dir.is_absolute():
        build_dir = DUNEOS_ROOT / build_dir
    timeout_s = float(getattr(args, "timeout", DEFAULT_TIMEOUT_S))

    print(f"dbt qemu — board '{board}', build dir {build_dir}")

    plugin, arch, cpu, board_cfg = get_board_plugin()
    if not hasattr(plugin, "run_qemu"):
        _config_error(f"toolchain plugin for board '{board}' has no run_qemu()")

    if getattr(plugin, "qemu_platform_supported", lambda: True)() is False:
        _config_error(
            "QEMU is not supported on this platform — Espressif ships no "
            "qemu-xtensa build dbt can drive here. Run `dbt qemu` on Linux or "
            "macOS (or in the espressif/idf container)."
        )

    # Checked before bspgen, the kernel build, the app build and the image
    # compose: a missing emulator is a five-second answer, not a discovery
    # made after ten minutes of compiling. `qemu-xtensa` is an `on_request`
    # IDF tool, so "absent" is the normal first-run state — install it rather
    # than printing the command the user would have to type.
    if getattr(plugin, "qemu_available", lambda: True)() is False:
        print("\nqemu-system-xtensa not found — installing it now.")
        if not getattr(plugin, "install_qemu", lambda: False)():
            _qemu_missing()
    qemu_bin = getattr(plugin, "find_qemu", lambda: None)()
    if qemu_bin:
        print(f"  emulator: {qemu_bin}")

    if not run_bspgen(board, None):
        _config_error(f"bspgen failed for board '{board}'")

    # One sdkconfig per build directory: the project default is a single root
    # file shared by every build dir, so two boards built side by side would
    # overwrite each other's cached Kconfig.
    build_dir.mkdir(parents=True, exist_ok=True)
    sdkconfig = build_dir / "sdkconfig"

    if not getattr(args, "no_build", False):
        print("\nBuilding kernel…")
        with _as_config_error("kernel build could not start"):
            rc = plugin.build_kernel(DUNEOS_ROOT / "boards" / board, build_dir,
                                     None, sdkconfig=sdkconfig)
        if rc != 0:
            _board_file_changed_under_run(active)
            sys.exit(EXIT_BUILD)

        print(f"\nBuilding {SMOKE_APP}…")
        with _as_config_error("cross-compiler not found"):
            tc = plugin.find_compiler(arch, cpu)
        if not build_single(DUNEOS_ROOT / "apps" / "user" / SMOKE_APP,
                            plugin, arch, cpu, board_cfg, tc):
            sys.exit(EXIT_BUILD)

    print("\nBuilding flash image…")
    with _as_config_error("flash image could not be built"):
        sysbin = _build_sysbin(board, build_dir)
    flash_image = _write_flash_image(build_dir, board, sysbin)

    psram_mb = declared_psram_mb(board_cfg)
    if psram_mb:
        print(f"  board declares {psram_mb} MiB PSRAM — the emulator is sized "
              f"to match")

    matcher  = SmokeMatcher(psram_declared_mb=psram_mb)
    echo     = not getattr(args, "quiet", False)
    consumer = SmokeConsumer(matcher, settle_s=DEFAULT_SETTLE_S)

    def consume(chunk: str) -> str | None:
        if chunk and echo:
            sys.stdout.write(chunk)
            sys.stdout.flush()
        return consumer(chunk)

    if getattr(args, "no_gdb_diag", False):
        gdb_port = None
    else:
        requested = getattr(args, "gdb_port", None)
        gdb_port = int(requested) if requested else pick_gdb_port()
    post_mortem: list[str] = []

    def inspect(status: str) -> None:
        # Only worth the seconds it costs when something went wrong, and only
        # possible while the machine is still up — hence the callback.
        if status == "complete" or gdb_port is None:
            return
        post_mortem.extend(
            _post_mortem(plugin, build_dir, gdb_port, matcher)
        )

    print(f"\nRunning QEMU (timeout {timeout_s:g}s)…\n")
    status, rc = plugin.run_qemu(build_dir, flash_image, timeout_s, consume,
                                 sdkconfig=sdkconfig, gdb_port=gdb_port,
                                 before_kill=inspect, psram_mb=psram_mb)

    if status == "unavailable":
        _qemu_missing()
    if status == "no-sdk":
        _config_error("ESP-IDF not found. Run `python tools/dbt.py setup`.")

    code, message = verdict(status, matcher, timeout_s, settled=consumer.settled)
    print("\n" + "=" * 60)
    for label, _ in ASSERTIONS:
        mark = "ok  " if label in matcher.matched else "MISS"
        print(f"  [{mark}] {label}")
    print("  --- boot progress (diagnostics, never assertions) ---")
    for label, seen in matcher.progress_report():
        print(f"  [{'ok  ' if seen else '  --'}] {label}")
    if psram_mb:
        found = matcher.psram_found_mb
        print(f"  --- PSRAM: declared {psram_mb} MiB, emulator reported "
              f"{'nothing' if found is None else f'{found} MiB'} ---")
        unavailable = matcher.psram_check_unavailable
        if unavailable:
            print(f"  [warn] {unavailable}")
    for label in matcher.config_problems():
        print(f"  [CONF] {label}")
    if matcher.app_failures:
        for label in matcher.app_failures:
            print(f"  [FAIL] {label}")
    # rc is None whenever the verdict was reached while QEMU was still alive,
    # which is the normal case. Printing "0" there would fabricate a success
    # right next to a possible FAIL line.
    print(f"  qemu process: "
          f"{'still running (killed by the runner)' if rc is None else f'exited with {rc}'}")
    print(f"  {message}")
    print("=" * 60)
    for line in post_mortem:
        print(line)
    if code != 0:
        _board_file_changed_under_run(active)
    sys.exit(code)


def _post_mortem(plugin, build_dir: Path, gdb_port: int,
                 matcher: SmokeMatcher) -> list[str]:
    """Ask the emulator what the system was actually doing when it failed.

    This is the answer to "silence proves nothing": the console cannot carry
    the kernel's info-level klog lines (klog.c forwards only level 'E'), so a
    failed run used to be indistinguishable from a healthy but mute one. GDB
    reads both the CPU backtraces and the klog ring straight out of memory, so
    the report names the state instead of inferring it from an absence.

    Diagnostic only: nothing here feeds the matcher or the exit code.
    """
    report = getattr(plugin, "qemu_gdb_report", None)
    if report is None:
        return []
    elf = build_dir / "duneos.elf"
    dump = build_dir / "qemu_klog_ring.bin"
    text, data, write_abs = report(elf, gdb_port, dump)

    out = ["", "--- post-mortem: what the emulated system was doing ---"]
    ring = format_klog_ring(data or b"", write_abs)
    if ring:
        out.append("DuneOS klog ring (read from memory, oldest first):")
        out.extend("  " + line for line in ring.splitlines())
    else:
        out.append("DuneOS klog ring: empty — the kernel logged nothing, so "
                   "execution never reached duneos_vfs_init().")
    out.append("")
    out.append("CPU backtraces:")
    frames = [line.rstrip() for line in text.splitlines()
              if line.startswith(("#", "*", "  Id")) or "Thread " in line]
    if frames:
        out.extend("  " + line for line in frames)
    else:
        # No frames means GDB itself failed (not installed, port refused): say
        # so with its own words rather than printing an empty heading.
        out.append("  unavailable — GDB reported:")
        out.extend("  " + line.rstrip()
                   for line in text.strip().splitlines()[:12])
    out.append("--- end post-mortem ---")
    return out
