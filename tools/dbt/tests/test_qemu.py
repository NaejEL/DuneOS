"""Tests for the pure logic of `dbt qemu` (LEG-27).

Everything here runs without QEMU, without ESP-IDF and without a board:
log-assertion matching, panic/reboot detection, timeout handling and flash
image composition.
"""

import signal
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from dbt import qemu  # noqa: E402


# A boot log shaped like the real thing: the ROM banner, then the klog ring
# drained to stdout by qemu_smoke, then its own marker.
GOOD_LOG = """\
ESP-ROM:esp32s3-20210327
Build:Mar 27 2021
rst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)
I (31) boot: ESP-IDF v6.0.1 2nd stage bootloader
<<<DUNEOS-QEMU-SMOKE klog begin>>>
[I][vfs] LittleFS mounted at / (root)
[I][vfs] VFS ready (/ /tmp /dev)
[I][loader]   [0] qemu_smoke  v0.1.0  /bin/qemu_smoke.dap
[I][loader] scan: 1 app(s) found
[I][duneos] autoboot: launching 'qemu_smoke' v0.1.0
<<<DUNEOS-QEMU-SMOKE klog end>>>
<<<DUNEOS-QEMU-SMOKE app_main reached duneos_exit(0)>>>
"""

RAN_MARKER = "<<<DUNEOS-QEMU-SMOKE app_main reached duneos_exit(0)>>>"
RAN_LABEL  = "qemu_smoke app_main ran to its duneos_exit(0) call"


class FakeClock:
    """Monotonic clock advancing by `step` on every call."""

    def __init__(self, step: float = 1.0) -> None:
        self.now  = 0.0
        self.step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


def _reader(chunks):
    """read_chunk() draining a list, then blocking forever on ''."""
    queue = list(chunks)

    def read_chunk(_timeout):
        return queue.pop(0) if queue else ""

    return read_chunk


# ---------------------------------------------------------------------------
# Assertion matching
# ---------------------------------------------------------------------------

def test_good_log_matches_every_assertion():
    m = qemu.SmokeMatcher()
    m.feed(GOOD_LOG)
    assert m.missing == []
    assert m.all_matched
    assert not m.panicked


def test_missing_app_marker_is_reported():
    m = qemu.SmokeMatcher()
    m.feed(GOOD_LOG.replace(RAN_MARKER, ""))
    assert m.missing == [RAN_LABEL]
    assert not m.all_matched


def test_assertion_split_across_chunks_still_matches():
    m = qemu.SmokeMatcher()
    cut = GOOD_LOG.index("app_main reached") + 6
    m.feed(GOOD_LOG[:cut])
    assert m.missing == [RAN_LABEL]
    m.feed(GOOD_LOG[cut:])
    assert m.missing == []


def test_the_marker_label_claims_only_that_app_main_ran():
    """The marker is printed before duneos_exit(0): it proves the loaded code
    ran, not that the exit or the unload were clean. The label must not
    overclaim, or a reader trusts a proof the bench cannot give."""
    labels = [label for label, _ in qemu.ASSERTIONS]
    assert RAN_LABEL in labels
    assert not any("clean-exit" in label or "exited" in label
                   for label in labels)


def test_empty_output_matches_nothing():
    m = qemu.SmokeMatcher()
    m.feed("")
    assert len(m.missing) == len(qemu.ASSERTIONS)


def test_a_different_app_does_not_satisfy_the_scan_assertion():
    m = qemu.SmokeMatcher()
    m.feed(GOOD_LOG.replace("qemu_smoke", "hello_world"))
    assert "/flash/bin scanned (found qemu_smoke.dap)" in m.missing


# ---------------------------------------------------------------------------
# Panic / reboot detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("crash", [
    "Guru Meditation Error: Core 0 panic'ed (LoadProhibited)",
    "abort() was called at PC 0x4008a1b2",
    "assert failed: xQueueSemaphoreTake queue.c:1653",
    "Task watchdog got triggered. The following tasks did not reset:",
    "CORRUPT HEAP: Bad tail at 0x3fca",
])
def test_panic_patterns_are_detected(crash):
    m = qemu.SmokeMatcher()
    m.feed(GOOD_LOG + crash + "\n")
    assert m.panicked
    assert m.failure_reasons()


def test_single_boot_banner_is_not_a_reboot():
    m = qemu.SmokeMatcher()
    m.feed(GOOD_LOG)
    assert m.boot_count == 1
    assert not m.rebooted
    assert not m.panicked


def test_second_boot_banner_is_a_reboot():
    m = qemu.SmokeMatcher()
    m.feed(GOOD_LOG + GOOD_LOG)
    assert m.boot_count == 2
    assert m.rebooted
    assert m.panicked


def test_panic_during_the_settle_window_beats_a_full_match():
    """All assertions matched is not enough: a crash right after must fail."""
    clock = FakeClock()
    consumer = qemu.SmokeConsumer(qemu.SmokeMatcher(), settle_s=5.0, clock=clock)
    assert consumer(GOOD_LOG) is None
    assert consumer("Guru Meditation Error: Core 0 panic'ed") == "panic"


def test_settle_window_elapsing_reports_complete():
    clock = FakeClock()
    consumer = qemu.SmokeConsumer(qemu.SmokeMatcher(), settle_s=2.0, clock=clock)
    assert consumer(GOOD_LOG) is None      # settle window opens at t=0 → 2
    assert consumer("") is None            # t=1, too early
    assert consumer("") == "complete"      # t=2


# ---------------------------------------------------------------------------
# Loop, timeout and verdict
# ---------------------------------------------------------------------------

def test_run_loop_completes_on_a_good_run():
    matcher  = qemu.SmokeMatcher()
    consumer = qemu.SmokeConsumer(matcher, settle_s=0.0, clock=FakeClock())
    status = qemu.run_loop(_reader([GOOD_LOG]), consumer, timeout_s=100.0,
                           clock=FakeClock())
    assert status == "complete"
    assert qemu.verdict(status, matcher, 100.0) == (
        qemu.EXIT_OK, "PASS: every assertion matched")


def test_run_loop_times_out_when_the_system_stays_silent():
    matcher  = qemu.SmokeMatcher()
    consumer = qemu.SmokeConsumer(matcher, clock=FakeClock())
    status = qemu.run_loop(_reader([]), consumer, timeout_s=3.0,
                           clock=FakeClock())
    assert status == "timeout"
    code, message = qemu.verdict(status, matcher, 3.0)
    assert code == qemu.EXIT_TIMEOUT
    assert "timeout" in message.lower()
    assert "not a failed assertion" in message


def test_run_loop_times_out_on_a_partial_boot():
    matcher  = qemu.SmokeMatcher()
    consumer = qemu.SmokeConsumer(matcher, clock=FakeClock())
    partial  = GOOD_LOG.split("autoboot")[0]
    status = qemu.run_loop(_reader([partial]), consumer, timeout_s=5.0,
                           clock=FakeClock())
    assert status == "timeout"
    code, message = qemu.verdict(status, matcher, 5.0)
    assert code == qemu.EXIT_TIMEOUT
    assert RAN_LABEL in message


def test_run_loop_stops_on_panic():
    matcher  = qemu.SmokeMatcher()
    consumer = qemu.SmokeConsumer(matcher, clock=FakeClock())
    status = qemu.run_loop(_reader(["boot\nrst:0x1 (POWERON)\n",
                                    "Guru Meditation Error\n"]),
                           consumer, timeout_s=100.0, clock=FakeClock())
    assert status == "panic"
    code, message = qemu.verdict(status, matcher, 100.0)
    assert code == qemu.EXIT_PANIC
    assert "guru meditation" in message


def test_run_loop_reports_eof_when_qemu_dies_early():
    def read_chunk(_timeout):
        return None

    matcher  = qemu.SmokeMatcher()
    consumer = qemu.SmokeConsumer(matcher, clock=FakeClock())
    status = qemu.run_loop(read_chunk, consumer, timeout_s=100.0,
                           clock=FakeClock())
    assert status == "eof"
    code, message = qemu.verdict(status, matcher, 100.0)
    assert code == qemu.EXIT_ASSERT
    assert "exited before" in message


def test_eof_inside_the_settle_window_is_not_a_pass():
    """QEMU dying one poll after the last marker must not read as green: the
    crash window the bench exists to watch never elapsed."""
    matcher  = qemu.SmokeMatcher()
    consumer = qemu.SmokeConsumer(matcher, settle_s=2.0,
                                  clock=FakeClock(step=0.1))

    chunks = [GOOD_LOG, None]

    def read_chunk(_timeout):
        return chunks.pop(0) if chunks else None

    status = qemu.run_loop(read_chunk, consumer, timeout_s=100.0,
                           clock=FakeClock())
    assert status == "eof"
    assert matcher.missing == []
    assert not consumer.settled
    code, message = qemu.verdict(status, matcher, 100.0,
                                 settled=consumer.settled)
    assert code == qemu.EXIT_ASSERT
    assert "crash window" in message


def test_eof_after_the_settle_window_is_a_pass():
    matcher = qemu.SmokeMatcher()
    matcher.feed(GOOD_LOG)
    code, message = qemu.verdict("eof", matcher, 100.0, settled=True)
    assert code == qemu.EXIT_OK
    assert message.startswith("PASS")


def test_exit_codes_are_all_distinct():
    codes = {qemu.EXIT_OK, qemu.EXIT_ASSERT, qemu.EXIT_TIMEOUT,
             qemu.EXIT_PANIC, qemu.EXIT_QEMU_MISSING, qemu.EXIT_BUILD,
             qemu.EXIT_CONFIG}
    assert len(codes) == 7
    assert qemu.EXIT_OK == 0
    assert 0 not in codes - {qemu.EXIT_OK}


def test_a_configuration_error_does_not_masquerade_as_an_assertion_failure():
    """CI must be able to tell a misconfigured bench from a red firmware."""
    assert qemu.EXIT_CONFIG != qemu.EXIT_ASSERT
    with pytest.raises(SystemExit) as excinfo:
        qemu._config_error("unknown board 'nope'")
    assert excinfo.value.code == qemu.EXIT_CONFIG


def test_missing_qemu_exits_with_its_own_code():
    with pytest.raises(SystemExit) as excinfo:
        qemu._qemu_missing()
    assert excinfo.value.code == qemu.EXIT_QEMU_MISSING


# ---------------------------------------------------------------------------
# Flash image composition
# ---------------------------------------------------------------------------

# Copied verbatim from build-esp32s3-qemu/flash_args, produced by ESP-IDF
# v6.0.1. The option spelling is HYPHENATED (esptool_py's FLASH_SUB_ARGS):
# a fixture using --flash_size would validate a format IDF does not emit and
# would hide a lookup that never hits.
FLASH_ARGS = """\
--flash-mode dio --flash-freq 80m --flash-size 8MB
0x0 bootloader/bootloader.bin
0x8000 partition_table/partition-table.bin
0x10000 duneos.bin
"""

# Same file from a 4 MB board: the size must reach the composed image, not
# quietly fall back to a hardcoded 8 MB.
FLASH_ARGS_4MB = FLASH_ARGS.replace("--flash-size 8MB", "--flash-size 4MB")

# Pre-v6 spelling, kept working so an older or patched IDF does not silently
# lose the size.
FLASH_ARGS_UNDERSCORE = """\
--flash_mode dio --flash_freq 80m --flash_size 4MB
0x0 bootloader/bootloader.bin
"""


def test_parse_flash_args():
    opts, entries = qemu.parse_flash_args(FLASH_ARGS)
    assert opts["flash-size"] == "8MB"
    assert opts["flash-mode"] == "dio"
    assert opts["flash-freq"] == "80m"
    assert entries == [
        (0x0,     "bootloader/bootloader.bin"),
        (0x8000,  "partition_table/partition-table.bin"),
        (0x10000, "duneos.bin"),
    ]


def test_parse_flash_args_normalises_the_underscore_spelling():
    opts, _ = qemu.parse_flash_args(FLASH_ARGS_UNDERSCORE)
    assert opts["flash-size"] == "4MB"
    assert qemu.flash_size_from_opts(opts) == 4 * 1024 * 1024


@pytest.mark.parametrize("text,expected", [
    (FLASH_ARGS,      8 * 1024 * 1024),
    (FLASH_ARGS_4MB,  4 * 1024 * 1024),
])
def test_flash_size_reaches_the_composed_image(text, expected):
    opts, entries = qemu.parse_flash_args(text)
    size  = qemu.flash_size_from_opts(opts)
    assert size == expected
    image = qemu.compose_flash_image([(off, b"x") for off, _ in entries], size)
    assert len(image) == expected


def test_flash_size_is_required():
    opts, _ = qemu.parse_flash_args("--flash-mode dio\n0x0 bootloader.bin\n")
    with pytest.raises(ValueError, match="no --flash-size"):
        qemu.flash_size_from_opts(opts)


@pytest.mark.parametrize("text,expected", [
    ("8MB", 8 * 1024 * 1024),
    ("4MB", 4 * 1024 * 1024),
    ("512KB", 512 * 1024),
    ("1024", 1024),
])
def test_parse_flash_size(text, expected):
    assert qemu.parse_flash_size(text) == expected


@pytest.mark.parametrize("text", ["detect", "keep", "", "MB", "eight MB"])
def test_parse_flash_size_rejects_non_literal_sizes(text):
    """There is no chip to ask, so 'detect'/'keep' cannot be resolved: guessing
    would produce an image of the wrong length."""
    with pytest.raises(ValueError):
        qemu.parse_flash_size(text)


def test_compose_flash_image_places_every_part():
    image = qemu.compose_flash_image(
        [(0x0, b"boot"), (0x10, b"app"), (0x20, b"sysbin")], 0x40)
    assert len(image) == 0x40
    assert image[0:4] == b"boot"
    assert image[0x10:0x13] == b"app"
    assert image[0x20:0x26] == b"sysbin"
    assert image[4:0x10] == b"\xff" * 12       # erased flash between parts


def test_compose_flash_image_rejects_overflow():
    with pytest.raises(ValueError, match="overflows"):
        qemu.compose_flash_image([(0x30, b"too long")], 0x32)


def test_compose_flash_image_rejects_overlap():
    with pytest.raises(ValueError, match="overlaps"):
        qemu.compose_flash_image([(0x0, b"abcd"), (0x2, b"xy")], 0x10)


# ---------------------------------------------------------------------------
# Diagnostics: telling "the kernel hung" from "the kernel is fine but mute"
# ---------------------------------------------------------------------------

# Real capture, trimmed: an ESP32-S3 that stops at the exact point where
# ESP-IDF hands the console over from the ROM path to the stdout/VFS path.
HUNG_EARLY_LOG = """\
ESP-ROM:esp32s3-20210327
rst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)
I (31) boot: ESP-IDF v6.0.1 2nd stage bootloader
I (104) boot: Loaded app from partition at offset 0x10000
I (136) heap_init: Initializing. RAM available for dynamic allocation:
I (141) spi_flash: detected chip: gd
"""

BOOTED_LOG = HUNG_EARLY_LOG + """\
I (149) main_task: Started on CPU0
I (157) main_task: Calling app_main()
"""


def test_progress_markers_locate_where_the_boot_stopped():
    m = qemu.SmokeMatcher()
    m.feed(HUNG_EARLY_LOG)
    seen = dict(m.progress_report())
    assert seen["IDF early-log path alive"] is True
    # The decisive one: nothing from the stdout/VFS console path ever arrived.
    assert seen["IDF stdout-log path alive"] is False
    assert seen["app_main entered (IDF)"] is False


def test_progress_markers_separate_a_mute_kernel_from_a_hung_one():
    m = qemu.SmokeMatcher()
    m.feed(BOOTED_LOG)
    seen = dict(m.progress_report())
    assert seen["IDF stdout-log path alive"] is True
    assert seen["app_main entered (IDF)"] is True
    # Still no assertion matched: app_main ran, the payload said nothing.
    assert m.missing == [label for label, _ in qemu.ASSERTIONS]


def test_progress_never_influences_the_verdict():
    m = qemu.SmokeMatcher()
    m.feed(BOOTED_LOG)
    code, _ = qemu.verdict("timeout", m, 10.0)
    assert code == qemu.EXIT_TIMEOUT


APP_CRASH_LOG = BOOTED_LOG + """\
E (323) duneos/supervisor: 'qemu_smoke' exited (code 127)
E (325) duneos/supervisor: 'qemu_smoke' CPU exception: cause=0 PC=0x40393490
"""


def test_dead_payload_is_an_assertion_failure_not_a_timeout():
    m = qemu.SmokeMatcher()
    c = qemu.SmokeConsumer(m, settle_s=0.0)
    assert c(APP_CRASH_LOG) == "appfail"
    code, message = qemu.verdict("appfail", m, 10.0)
    assert code == qemu.EXIT_ASSERT
    assert "CPU exception" in message
    assert "did not crash" in message


def test_a_clean_exit_code_is_not_read_as_an_app_failure():
    m = qemu.SmokeMatcher()
    m.feed("E (323) duneos/supervisor: 'qemu_smoke' exited (code 0)\n")
    assert m.app_failures == []


def test_app_failure_does_not_pre_empt_a_complete_run():
    """A crash line after every marker matched is the panic/settle path's job,
    not the payload-died shortcut's."""
    m = qemu.SmokeMatcher()
    c = qemu.SmokeConsumer(m, settle_s=0.0, clock=lambda: 0.0)
    c(GOOD_LOG)
    assert m.all_matched
    assert c("E (999) duneos/supervisor: 'qemu_smoke' exited (code 127)\n") \
        != "appfail"


# ---------------------------------------------------------------------------
# klog ring reconstruction
# ---------------------------------------------------------------------------

def test_format_klog_ring_before_the_first_wrap():
    data = b"hello" + b"\x00" * 11
    assert qemu.format_klog_ring(data, 5) == "hello"


def test_format_klog_ring_after_a_wrap_restores_oldest_first():
    # 8-byte ring written 11 times: bytes 3..10 are live, oldest at index 3.
    data = bytes("89abcdef", "ascii")      # slots 8..15 of a 16-byte stream
    assert qemu.format_klog_ring(data, 11) == "bcdef89a"


def test_format_klog_ring_handles_an_untouched_ring():
    assert qemu.format_klog_ring(b"\x00" * 16, 0) == ""
    assert qemu.format_klog_ring(b"", 42) == ""


# ---------------------------------------------------------------------------
# Emulator discovery — the binary is off PATH by design (on_request IDF tool)
# ---------------------------------------------------------------------------

from dbt.toolchain import esp_idf  # noqa: E402


def _fake_tools_tree(root: Path, version: str) -> Path:
    binary = root / "tools" / "qemu-xtensa" / version / "qemu" / "bin" / \
        esp_idf.QEMU_PROGRAM
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    return binary


def test_find_qemu_resolves_from_the_idf_tools_tree(tmp_path, monkeypatch):
    """export.sh does not put an on_request tool on PATH; looking only there is
    why the command used to advise the install the user had just run."""
    expected = _fake_tools_tree(tmp_path, esp_idf.QEMU_TOOL_VERSION)
    monkeypatch.setenv("IDF_TOOLS_PATH", str(tmp_path))
    monkeypatch.delenv("DUNEOS_QEMU", raising=False)
    monkeypatch.setattr(esp_idf.shutil, "which", lambda _n: None)
    assert esp_idf.find_qemu() == expected
    assert esp_idf.qemu_available() is True


def test_find_qemu_prefers_the_newest_version_directory(tmp_path, monkeypatch):
    _fake_tools_tree(tmp_path, "esp_develop_9.0.0_20240101")
    newest = _fake_tools_tree(tmp_path, "esp_develop_9.2.2_20250817")
    monkeypatch.setenv("IDF_TOOLS_PATH", str(tmp_path))
    monkeypatch.delenv("DUNEOS_QEMU", raising=False)
    monkeypatch.setattr(esp_idf.shutil, "which", lambda _n: None)
    assert esp_idf.find_qemu() == newest


def test_find_qemu_falls_back_to_path(tmp_path, monkeypatch):
    monkeypatch.setenv("IDF_TOOLS_PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("DUNEOS_QEMU", raising=False)
    monkeypatch.setattr(esp_idf.shutil, "which", lambda _n: "/usr/bin/qemu")
    monkeypatch.setattr(esp_idf.Path, "home", staticmethod(lambda: tmp_path))
    assert esp_idf.find_qemu() == Path("/usr/bin/qemu")


def test_find_qemu_returns_none_when_nothing_is_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("IDF_TOOLS_PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("DUNEOS_QEMU", raising=False)
    monkeypatch.setattr(esp_idf.shutil, "which", lambda _n: None)
    monkeypatch.setattr(esp_idf.Path, "home", staticmethod(lambda: tmp_path))
    assert esp_idf.find_qemu() is None
    assert esp_idf.qemu_available() is False


def test_qemu_extra_args_suppress_the_open_eth_nic():
    """idf.py only adds `-nic user,model=open_eth` when '-nic' is absent from
    the extra args, and that NIC is the only reason libslirp is needed."""
    assert "-nic" in esp_idf.QEMU_EXTRA_ARGS_BASE
    assert "none" in esp_idf.QEMU_EXTRA_ARGS_BASE


# ---------------------------------------------------------------------------
# Matcher hygiene: anchored panic pattern, incremental scanning
# ---------------------------------------------------------------------------

def test_the_word_panic_alone_is_not_a_panic():
    """The bench echoes the captured stream; a line merely containing "Panic"
    (a symbol name, a doc line, an echoed pattern list) must not force
    EXIT_PANIC on an otherwise healthy run."""
    m = qemu.SmokeMatcher()
    m.feed(GOOD_LOG + "I (410) app: PANIC_PATTERNS handler registered\n")
    assert not m.panicked


def test_the_real_panic_wording_is_still_detected():
    m = qemu.SmokeMatcher()
    m.feed("Core 0 panic'ed (LoadProhibited). Exception was unhandled.\n")
    assert "panic handler" in m.panics


def test_incremental_feed_matches_the_same_things_as_one_shot():
    whole = qemu.SmokeMatcher()
    whole.feed(GOOD_LOG)
    piecewise = qemu.SmokeMatcher()
    for i in range(0, len(GOOD_LOG), 7):
        piecewise.feed(GOOD_LOG[i:i + 7])
    assert sorted(piecewise.matched) == sorted(whole.matched)
    assert sorted(piecewise.progress) == sorted(whole.progress)
    assert piecewise.boot_count == whole.boot_count


def test_boot_banners_are_counted_once_each_across_chunks():
    log = GOOD_LOG + GOOD_LOG
    m = qemu.SmokeMatcher()
    for i in range(0, len(log), 3):
        m.feed(log[i:i + 3])
    assert m.boot_count == 2
    assert m.rebooted


# ---------------------------------------------------------------------------
# GDB port: an environment fault must never be scored as a firmware fault
# ---------------------------------------------------------------------------

def test_pick_gdb_port_returns_a_bindable_port():
    import socket

    port = qemu.pick_gdb_port()
    assert 1024 < port <= 65535
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("", port))


def test_pick_gdb_port_does_not_hand_out_a_port_twice_in_a_row():
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("", 0))
        held.listen(1)
        taken = held.getsockname()[1]
        assert qemu.pick_gdb_port() != taken


def test_qemu_startup_failure_recognises_a_busy_gdb_port():
    text = ("Failed to find an available port: Address already in use\n"
            "qemu-system-xtensa: could not start gdbserver\n")
    assert qemu.qemu_startup_failure(text) is not None


def test_qemu_startup_failure_stays_quiet_on_a_normal_log():
    assert qemu.qemu_startup_failure(GOOD_LOG) is None


def test_a_dead_gdb_port_is_a_config_error_not_five_missing_assertions():
    """The bug this closes: QEMU never started, yet the bench blamed the firmware."""
    m = qemu.SmokeMatcher()
    m.feed("qemu-system-xtensa: could not start gdbserver: "
           "Address already in use\n")
    code, message = qemu.verdict("eof", m, timeout_s=180.0, settled=True)
    assert code == qemu.EXIT_CONFIG
    assert code != qemu.EXIT_ASSERT
    assert "environment" in message


def test_a_real_early_exit_is_still_an_assertion_failure():
    """The config check must not swallow a genuine early death."""
    m = qemu.SmokeMatcher()
    m.feed("ESP-ROM:esp32s3-20210327\nI (31) boot: ESP-IDF v6.0.1 2nd stage bootloader\n")
    code, _ = qemu.verdict("eof", m, timeout_s=180.0, settled=True)
    assert code == qemu.EXIT_ASSERT


# ---------------------------------------------------------------------------
# verdict() must not depend on SmokeConsumer's ordering (criterion 8)
# ---------------------------------------------------------------------------

def test_verdict_reports_a_panic_even_when_the_status_says_complete():
    m = qemu.SmokeMatcher()
    m.feed(GOOD_LOG + "Guru Meditation Error: Core 0 panic'ed\n")
    assert m.all_matched
    code, message = qemu.verdict("complete", m, timeout_s=180.0, settled=True)
    assert code == qemu.EXIT_PANIC
    assert "guru meditation" in message


def test_verdict_reports_a_reboot_even_when_the_status_says_eof():
    m = qemu.SmokeMatcher()
    m.feed(GOOD_LOG + "rst:0x3 (SW_RESET),boot:0x8\n")
    assert m.all_matched and m.rebooted
    code, _ = qemu.verdict("eof", m, timeout_s=180.0, settled=True)
    assert code == qemu.EXIT_PANIC


# ---------------------------------------------------------------------------
# Leaked emulators: a killed dbt must not leave QEMU spinning on a port
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform.startswith("win"),
                    reason="process-group kill is POSIX-only here")
def test_reaping_kills_a_tracked_process_group():
    import subprocess

    proc = subprocess.Popen([sys.executable, "-c",
                             "import time; time.sleep(300)"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True)
    esp_idf._LIVE_IDF_PROCS.add(proc)
    try:
        esp_idf._reap_live_idf_procs()
        assert proc.poll() is not None
        assert proc not in esp_idf._LIVE_IDF_PROCS
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        esp_idf._LIVE_IDF_PROCS.discard(proc)


def test_signal_and_exit_hooks_are_installed_once():
    esp_idf._CLEANUP_HOOKED = False
    try:
        esp_idf._hook_process_cleanup()
        assert esp_idf._CLEANUP_HOOKED
        first = signal.getsignal(signal.SIGTERM)
        esp_idf._hook_process_cleanup()
        assert signal.getsignal(signal.SIGTERM) is first
    finally:
        signal.signal(signal.SIGINT, signal.default_int_handler)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)


# ---------------------------------------------------------------------------
# PSRAM sizing and configuration failures (SPEC-leg-30)
# ---------------------------------------------------------------------------

# The failure chain of SPEC-leg-30, verbatim: 32 MiB of PSRAM detected, the
# whole external-memory vaddr window consumed, no room left for the flash mmap
# of the partition table.
VADDR_EXHAUSTED_LOG = """\
ESP-ROM:esp32s3-20210327
rst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)
I (31) boot: ESP-IDF v6.0.1 2nd stage bootloader
I (99) esp_psram: Found 32MB PSRAM device
I (120) esp_psram: Adding pool of 32448K of PSRAM memory to heap allocator
E (140) mmap: esp_mmu_map(479): no such vaddr range
E (141) partition: load_partitions returned 0x105
E (142) esp_littlefs: partition "sysbin" could not be found
"""


@pytest.mark.parametrize("declared, expected", [
    (8, " -m 8M"),
    (32, " -m 32M"),
    (0, ""),
])
def test_declared_psram_size_reaches_the_emulator(declared, expected):
    assert esp_idf.qemu_psram_args(declared) == expected


def test_psram_args_are_appended_after_the_base_args():
    """idf.py appends --qemu-extra-args last, and QEMU's last -m wins."""
    extra = esp_idf.QEMU_EXTRA_ARGS_BASE + esp_idf.qemu_psram_args(8)
    assert extra.endswith("-m 8M")
    assert "-nic none" in extra


@pytest.mark.parametrize("cfg, expected", [
    ({"board": {"psram_size_mb": 8}}, 8),
    ({"board": {"psram_size_mb": 0}}, 0),
    ({"board": {}}, 0),
    ({}, 0),
    ({"board": {"psram_size_mb": None}}, 0),
])
def test_declared_psram_mb_reads_the_board_yaml(cfg, expected):
    assert qemu.declared_psram_mb(cfg) == expected


def test_the_qemu_psram_board_declares_eight_megabytes():
    """The bench's own board file is the source the emulator is sized from."""
    import yaml

    path = REPO_ROOT / "boards" / "esp32s3-qemu-psram" / "board.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert qemu.declared_psram_mb(cfg) == 8


def test_vaddr_exhaustion_is_a_configuration_error_not_a_timeout():
    m = qemu.SmokeMatcher(psram_declared_mb=8)
    m.feed(VADDR_EXHAUSTED_LOG)
    assert m.config_failures
    code, message = qemu.verdict("timeout", m, 180.0)
    assert code == qemu.EXIT_CONFIG
    assert "vaddr" in message


def test_a_psram_size_the_board_never_declared_is_reported():
    m = qemu.SmokeMatcher(psram_declared_mb=8)
    m.feed("I (99) esp_psram: Found 32MB PSRAM device\n")
    assert m.psram_found_mb == 32
    assert "declares 8 MiB" in (m.psram_mismatch or "")


def test_the_declared_psram_size_is_not_a_mismatch():
    m = qemu.SmokeMatcher(psram_declared_mb=8)
    m.feed("I (99) esp_psram: Found 8MB PSRAM device\n")
    assert m.psram_found_mb == 8
    assert m.psram_mismatch is None
    assert m.config_problems() == []


def test_a_board_without_psram_never_reports_a_mismatch():
    m = qemu.SmokeMatcher(psram_declared_mb=0)
    m.feed(GOOD_LOG)
    assert m.psram_mismatch is None
    code, _ = qemu.verdict("complete", m, 180.0)
    assert code == qemu.EXIT_OK


def test_psram_configuration_never_disturbs_a_good_run():
    m = qemu.SmokeMatcher(psram_declared_mb=8)
    m.feed("I (99) esp_psram: Found 8MB PSRAM device\n")
    m.feed(GOOD_LOG)
    code, message = qemu.verdict("complete", m, 180.0)
    assert code == qemu.EXIT_OK
    assert message.startswith("PASS")


# --- LEG-30 follow-up: console-format patterns and cross-check reporting ----

# Trimmed verbatim from a green `dbt qemu --board esp32s3-qemu-psram` run.
# The two level-'E' lines a successful run really does emit are kept: the
# harmless LittleFS reformat of /data, and the supervisor announcing a clean
# exit. Nothing here may score as a failure.
GREEN_RUN_CONSOLE = """\
ESP-ROM:esp32s3-20210327
rst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)
I (31) boot: ESP-IDF v6.0.1 2nd stage bootloader
I (60) boot: Loaded app from partition at offset 0x10000
I (78) heap_init: Initializing. RAM available for dynamic allocation:
I (79) esp_psram: Found 8MB PSRAM device
I (84) main_task: Started on CPU0
I (91) main_task: Calling app_main()
E (105) esp_littlefs: ./managed_components/joltwallet__littlefs/src/littlefs/lfs.c:1383:error: Corrupted dir pair at {0x0, 0x1}
W (106) esp_littlefs: mount failed,  (-84). formatting...
<<<DUNEOS-QEMU-SMOKE app_main entered>>>
<<<DUNEOS-QEMU-SMOKE klog begin>>>
[I][duneos/vfs] LittleFS mounted at / (root)
[I][duneos/vfs] VFS ready (/ /data /tmp /dev)
[I][duneos/loader] exec pool IRAM=0x4039bb84 DRAM(base word)=0x3fcabb84 (64 KB)
[I][duneos/loader]   [0] qemu_smoke  v0.1.0  /bin/qemu_smoke.dap
[I][duneos/loader] scan: 1 app(s) found
[I][duneos] autoboot: launching 'qemu_smoke' v0.1.0
<<<DUNEOS-QEMU-SMOKE klog end>>>
<<<DUNEOS-QEMU-SMOKE app_main reached duneos_exit(0)>>>
E (183) duneos/supervisor: 'qemu_smoke' exited (code 0)
"""

KLOG_ERROR_LABEL = "DuneOS klog error channel alive (level E reached the console)"
LOADER_REFUSED_LABEL = "the loader refused the app"


def test_the_klog_error_marker_matches_the_console_format_klog_actually_emits():
    """klog.c strips its own '[E][tag] ' prefix before forwarding to ESP_LOG."""
    m = qemu.SmokeMatcher()
    m.feed("E (161) duneos/vfs: mount failed\n")
    assert KLOG_ERROR_LABEL in m.progress


def test_the_klog_error_marker_no_longer_waits_for_the_ring_buffer_form():
    """The bracketed form only exists inside the ring, dumped far too late."""
    m = qemu.SmokeMatcher()
    m.feed("[E][duneos/vfs] mount failed\n")
    assert KLOG_ERROR_LABEL not in m.progress


def test_the_console_patterns_anchor_per_line_not_per_capture():
    """Without re.MULTILINE a '^' pattern would only ever match at offset 0."""
    m = qemu.SmokeMatcher()
    m.feed(GREEN_RUN_CONSOLE)
    assert KLOG_ERROR_LABEL in m.progress


def test_a_line_tail_cannot_pose_as_the_start_of_a_console_line():
    m = qemu.SmokeMatcher()
    m.feed("I (5) boot: prefixE (161) duneos/loader: not a real line start\n")
    assert LOADER_REFUSED_LABEL not in m.app_failures
    assert KLOG_ERROR_LABEL not in m.progress


def test_the_loader_failure_pattern_matches_a_real_loader_error():
    m = qemu.SmokeMatcher()
    m.feed("E (161) duneos/loader: arch mismatch: app='riscv32' "
           "kernel='xtensa-esp32s3'\n")
    assert LOADER_REFUSED_LABEL in m.app_failures
    assert m.app_failed


def test_the_loader_failure_pattern_does_not_fire_on_a_successful_run():
    """The newly-live pattern must never turn a green run red."""
    m = qemu.SmokeMatcher(psram_declared_mb=8)
    m.feed(GREEN_RUN_CONSOLE)
    assert m.app_failures == []
    assert not m.app_failed
    code, message = qemu.verdict("complete", m, 180.0)
    assert code == qemu.EXIT_OK
    assert message.startswith("PASS")


def test_the_loader_failure_pattern_ignores_the_loader_info_lines():
    """A green run's loader chatter is level 'I' and stays in the klog ring."""
    m = qemu.SmokeMatcher()
    m.feed("[I][duneos/loader] scan: 1 app(s) found\n"
           "E (105) esp_littlefs: lfs.c:1383:error: Corrupted dir pair\n"
           "E (183) duneos/supervisor: 'qemu_smoke' exited (code 0)\n")
    assert m.app_failures == []


def test_a_loader_error_split_across_two_chunks_is_still_matched():
    m = qemu.SmokeMatcher()
    m.feed("I (5) boot: something\nE (161) duneos/loa")
    m.feed("der: unresolved symbol: 'foo'\n")
    assert LOADER_REFUSED_LABEL in m.app_failures


def test_the_psram_cross_check_reports_when_it_could_not_run():
    """An esp_psram line that never appears must not fail silently."""
    m = qemu.SmokeMatcher(psram_declared_mb=8)
    m.feed(GOOD_LOG)
    assert m.psram_found_mb is None
    assert m.psram_mismatch is None
    warning = m.psram_check_unavailable
    assert warning and "did not run" in warning
    assert "esp_psram" in warning


def test_the_unavailable_cross_check_is_a_warning_not_a_verdict():
    m = qemu.SmokeMatcher(psram_declared_mb=8)
    m.feed(GOOD_LOG)
    assert m.config_problems() == []
    code, _ = qemu.verdict("complete", m, 180.0)
    assert code == qemu.EXIT_OK


def test_a_board_without_psram_stays_silent_about_the_cross_check():
    m = qemu.SmokeMatcher(psram_declared_mb=0)
    m.feed(GOOD_LOG)
    assert m.psram_check_unavailable is None


def test_a_detected_psram_size_clears_the_unavailable_warning():
    m = qemu.SmokeMatcher(psram_declared_mb=8)
    m.feed(GREEN_RUN_CONSOLE)
    assert m.psram_found_mb == 8
    assert m.psram_check_unavailable is None


def test_a_reworded_esp_psram_line_is_reported_rather_than_ignored():
    """The failure mode SPEC-leg-30's third risk names: a future IDF wording."""
    m = qemu.SmokeMatcher(psram_declared_mb=8)
    m.feed("I (79) esp_psram: PSRAM device found, size 8 MB\n")
    assert m.psram_found_mb is None
    assert m.psram_check_unavailable is not None


# ---------------------------------------------------------------------------
# Payloads — SPEC-leg-04 (qemu_calloc)
#
# The bench is the only executable path for the calloc overflow guard, so what
# is pinned here is that a red payload actually reddens `dbt qemu`: a verdict
# nobody reads is not a verdict.
# ---------------------------------------------------------------------------

CALLOC_GOOD_LOG = """\
ESP-ROM:esp32s3-20210327
rst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)
I (31) boot: ESP-IDF v6.0.1 2nd stage bootloader
<<<DUNEOS-QEMU-CALLOC app_main entered>>>
<<<DUNEOS-QEMU-CALLOC klog begin>>>
[I][vfs] LittleFS mounted at / (root)
[I][loader]   [0] qemu_calloc  v0.1.0  /bin/qemu_calloc.dap
[I][loader] scan: 1 app(s) found
[I][duneos] autoboot: launching 'qemu_calloc' v0.1.0
<<<DUNEOS-QEMU-CALLOC klog end>>>
qemu_calloc: slot-heap overflow(SIZE_MAX/2, 4) -> 0x0
qemu_calloc: fallback probe(16384) -> 0x3fca1234 (want non-NULL)
<<<DUNEOS-QEMU-CALLOC every check passed>>>
"""

CALLOC_MARKER = "<<<DUNEOS-QEMU-CALLOC every check passed>>>"
CALLOC_LABEL  = "qemu_calloc passed every calloc-overflow check (SPEC-leg-04)"


def _calloc_matcher() -> "qemu.SmokeMatcher":
    p = next(p for p in qemu.PAYLOADS if p.app == qemu.CALLOC_APP)
    return qemu.SmokeMatcher(assertions=p.assertions, progress=p.progress,
                             app_failures=p.app_failures)


def test_the_calloc_payload_is_part_of_the_bench():
    apps = [p.app for p in qemu.PAYLOADS]
    assert qemu.SMOKE_APP in apps
    assert qemu.CALLOC_APP in apps


def test_every_payload_has_a_source_directory_and_a_manifest():
    for p in qemu.PAYLOADS:
        app_dir = REPO_ROOT / "apps" / "user" / p.app
        assert (app_dir / "duneos.yaml").is_file(), p.app
        assert list(app_dir.glob("*.c")), p.app


def test_each_payload_asserts_on_its_own_app_not_another():
    for p in qemu.PAYLOADS:
        joined = " ".join(pat for _, pat in p.assertions)
        assert p.app in joined
        for other in qemu.PAYLOADS:
            if other.app != p.app:
                assert other.app not in joined


def test_a_good_calloc_run_matches_every_assertion():
    m = _calloc_matcher()
    m.feed(CALLOC_GOOD_LOG)
    assert m.missing == []
    assert not m.panicked
    assert qemu.verdict("complete", m, 10.0) == (qemu.EXIT_OK,
                                                 "PASS: every assertion matched")


def test_a_failed_calloc_check_fails_the_run():
    """Criterion 7: the app withholds its marker when a check fails, and that
    is what has to reach the exit code."""
    m = _calloc_matcher()
    m.feed(CALLOC_GOOD_LOG.replace(
        CALLOC_MARKER,
        "<<<DUNEOS-QEMU-CALLOC FAILED: overflowing product did not return NULL>>>"))
    assert m.missing == [CALLOC_LABEL]
    code, message = qemu.verdict("timeout", m, 10.0)
    assert code != qemu.EXIT_OK
    assert CALLOC_LABEL in message


def test_a_calloc_payload_exiting_non_zero_is_seen_as_an_app_failure():
    m = _calloc_matcher()
    c = qemu.SmokeConsumer(m, settle_s=0.0)
    log = CALLOC_GOOD_LOG.replace(CALLOC_MARKER, "") + (
        "E (323) duneos/supervisor: 'qemu_calloc' exited (code 1)\n")
    assert c(log) == "appfail"
    code, _ = qemu.verdict("appfail", m, 10.0)
    assert code == qemu.EXIT_ASSERT


def test_the_smoke_payload_marker_cannot_pass_for_the_calloc_one():
    """A qemu_smoke boot log leaves every app-specific calloc assertion
    missing. The two board-level ones (/flash mounted, scan completed) are
    deliberately app-agnostic and do match."""
    m = _calloc_matcher()
    m.feed(GOOD_LOG)
    assert m.missing == [
        f"/flash/bin scanned (found {qemu.CALLOC_APP}.dap)",
        f"{qemu.CALLOC_APP}.dap loaded and launched",
        CALLOC_LABEL,
    ]
