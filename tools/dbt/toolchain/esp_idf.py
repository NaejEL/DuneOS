"""ESP-IDF toolchain plugin for dbt.

Handles:
  sdk=esp-idf, arch=xtensa-esp32s3  (ESP32-S3, ESP32-S2)
  sdk=esp-idf, arch=xtensa-esp32    (plain ESP32 — integrated RMII Ethernet MAC)
  sdk=esp-idf, arch=riscv32          (ESP32-C2/C3/C5/C6/H2/P4)
"""

SDK  = "esp-idf"
ARCH = ["xtensa-esp32s3", "xtensa-esp32s2", "xtensa-esp32", "riscv32"]

import atexit
import os
import platform
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from ..constants import (
    DUNEOS_ROOT, CFLAGS_COMMON, CFLAGS_XTENSA, CFLAGS_RISCV, LDFLAGS,
    RISCV_CPUS,
)


# ---------------------------------------------------------------------------
# Toolchain root
# ---------------------------------------------------------------------------

def find_toolchain_root() -> Path | None:
    """Locate the ESP-IDF v6.0.x installation root."""
    from ..setup import find_idf_root
    return find_idf_root()


# ---------------------------------------------------------------------------
# Compiler discovery
# ---------------------------------------------------------------------------

def find_compiler(arch: str, cpu: str) -> dict[str, Path]:
    """Locate the cross-compiler; return {cc, ld, objcopy, readelf, nm}."""
    is_win = platform.system() == "Windows"
    exe    = ".exe" if is_win else ""

    if arch == "riscv32" or cpu in RISCV_CPUS:
        prefixes = ["riscv32-esp-elf-", "riscv32-unknown-elf-"]
    else:
        prefixes = [f"xtensa-{cpu}-elf-", "xtensa-esp-elf-"]

    tool_names = ["gcc", "ld", "objcopy", "readelf", "nm", "ar"]
    keys       = ["cc",  "ld", "objcopy", "readelf", "nm", "ar"]

    def make_result(bin_dir: Path, prefix: str) -> dict[str, Path]:
        return {k: bin_dir / (prefix + t + exe) for k, t in zip(keys, tool_names)}

    def try_bin_dir(bin_dir: Path, prefix: str) -> dict[str, Path] | None:
        if (bin_dir / (prefix + "gcc" + exe)).exists():
            return make_result(bin_dir, prefix)
        return None

    # 1. PATH
    for prefix in prefixes:
        found = shutil.which(prefix + "gcc")
        if found:
            return make_result(Path(found).parent, prefix)

    # 2. Derive from IDF_PATH env var
    idf_path = os.environ.get("IDF_PATH")
    if idf_path:
        tools_root = Path(idf_path).parent.parent.parent / "tools" / "xtensa-esp-elf"
        if not tools_root.exists():
            tools_root = Path(idf_path).parent / "tools" / "xtensa-esp-elf"
        for version_dir in sorted(tools_root.glob("*"), reverse=True):
            bin_dir = version_dir / "xtensa-esp-elf" / "bin"
            for prefix in prefixes:
                result = try_bin_dir(bin_dir, prefix)
                if result:
                    return result

    # 3. Common install locations
    if is_win:
        candidates = [
            Path("C:/Espressif/tools/xtensa-esp-elf"),
            Path(os.environ.get("USERPROFILE", "C:/Users")) / ".espressif" / "tools" / "xtensa-esp-elf",
        ]
    else:
        candidates = [
            Path.home() / ".espressif" / "tools" / "xtensa-esp-elf",
            Path("/opt/espressif/tools/xtensa-esp-elf"),
        ]

    for base in candidates:
        if not base.exists():
            continue
        for version_dir in sorted(base.glob("*"), reverse=True):
            bin_dir = version_dir / "xtensa-esp-elf" / "bin"
            for prefix in prefixes:
                result = try_bin_dir(bin_dir, prefix)
                if result:
                    print(f"  [toolchain] {bin_dir} (prefix: {prefix})")
                    return result

    sys.exit(
        f"ERROR: {arch.upper()} cross-compiler not found.\n"
        f"Tried prefixes: {prefixes}\n"
        "Options:\n"
        "  1. Add the toolchain bin directory to PATH\n"
        "  2. Set IDF_PATH to your ESP-IDF installation\n"
        "  3. Install ESP-IDF (https://docs.espressif.com/)"
    )


# ---------------------------------------------------------------------------
# Compile / link flags
# ---------------------------------------------------------------------------

def _find_picolibc_include(cc: Path) -> Path | None:
    """Return the PicoLibc include dir bundled with the toolchain, if present.

    ESP-IDF v6 switched from Newlib to PicoLibc. The two libraries use
    different O_* / fcntl flag values (e.g. O_CREAT: newlib=0x200, picolibc=0x40).
    Adding -isystem <picolibc_include> ensures apps see the PicoLibc values.
    """
    candidate = cc.parent.parent / "picolibc" / "include"
    return candidate if candidate.is_dir() else None


def cflags(board_cfg: dict, tc: dict | None = None) -> list[str]:
    """Return compile flags for the given board configuration."""
    b    = board_cfg.get("board", {})
    arch = b.get("arch", "xtensa-esp32s3")
    cpu  = b.get("cpu", "esp32s3")

    flags = CFLAGS_COMMON + (
        CFLAGS_RISCV + [f"-mcpu={cpu}"] if arch == "riscv32"
        else CFLAGS_XTENSA
    )
    if tc:
        picolibc = _find_picolibc_include(Path(str(tc["cc"])))
        if picolibc:
            flags = [f"-isystem{picolibc}"] + flags

    return flags


def ldflags(board_cfg: dict) -> list[str]:
    """Return linker flags for app builds (ET_REL output)."""
    return LDFLAGS


def linker_script(board_dir: Path) -> Path | None:
    """App builds use -r (ET_REL) — no linker script needed."""
    return None


# ---------------------------------------------------------------------------
# Kernel build / flash / monitor
# ---------------------------------------------------------------------------

def _bat_arg(a: str) -> str:
    """Quote one argument for a generated cmd.exe .bat line.

    Without this, paths with spaces (e.g. C:\\Program Files\\...) split into
    multiple args and cmd.exe metacharacters are interpreted unexpectedly.
    """
    if a and not any(c in a for c in ' \t"&|<>^()%!'):
        return a
    return '"' + a.replace('"', '""') + '"'


def _active_idf_target() -> str:
    """Read the active board's idf_target.txt (e.g. 'esp32s3'), or '' if unknown."""
    board_file = DUNEOS_ROOT / ".duneos_board"
    if not board_file.exists():
        return ""
    board = board_file.read_text().strip()
    tgt = DUNEOS_ROOT / "boards" / board / "idf_target.txt"
    return tgt.read_text().strip() if tgt.exists() else ""


def _espressif_roots() -> list[Path]:
    """Candidate IDF tools trees, most specific first.

    `idf_tools.py` installs under <root>/tools/<tool>/<version>/…; the layout
    is the same on every platform, only the root moves.
    """
    roots: list[Path] = []
    env = os.environ.get("IDF_TOOLS_PATH")
    if env:
        roots.append(Path(env))
    if platform.system() == "Windows":
        roots.append(Path(os.environ.get("USERPROFILE", "C:/Users")) / ".espressif")
        roots.append(Path("C:/Espressif"))
    else:
        roots.append(Path.home() / ".espressif")
        roots.append(Path("/opt/espressif"))
    return roots


def _run_idf(idf_root: Path, idf_args: list[str]) -> int:
    """Invoke idf.py with the given arguments from the DuneOS repo root."""
    from ..setup import build_idf_env, idf_python
    is_win = platform.system() == "Windows"
    # Pin IDF_TARGET to the active board's chip so a stale env var left over
    # from a different board (e.g. IDF_TARGET=esp32 after working on an ESP32
    # board, then switching to an esp32s3 board) can't conflict with the
    # board's generated sdkconfig and abort idf.py.
    target = _active_idf_target()
    if is_win:
        import tempfile
        export   = idf_root / "export.bat"
        args_str = " ".join(_bat_arg(a) for a in idf_args)
        # list2cmdline escapes inner quotes with \" which cmd.exe does not
        # understand. Write a temp .bat instead.
        # export.bat changes cwd to IDF_PATH — cd /d back to DUNEOS_ROOT before
        # calling idf.py, otherwise CMake looks for CMakeLists.txt in the wrong dir.
        bat = (
            f'@call "{export}"\r\n'
            + (f'set "IDF_TARGET={target}"\r\n' if target else 'set "IDF_TARGET="\r\n')
            + f'cd /d "{DUNEOS_ROOT}"\r\n'
            f'idf.py {args_str}\r\n'
            f'exit /b %ERRORLEVEL%\r\n'
        )
        fd, bat_path = tempfile.mkstemp(suffix='.bat')
        try:
            # newline='' so the explicit \r\n in `bat` isn't re-translated to
            # \r\r\n by Windows text-mode newline handling.
            with os.fdopen(fd, 'w', newline='') as f:
                f.write(bat)
            result = subprocess.run(['cmd', '/c', bat_path])
        finally:
            try:
                os.unlink(bat_path)
            except OSError:
                pass
    else:
        env    = build_idf_env(idf_root)
        if target and env is not None:
            env["IDF_TARGET"] = target
        python = idf_python(idf_root)
        idf_py = idf_root / "tools" / "idf.py"
        if python and idf_py.exists():
            cmd = [str(python), str(idf_py)] + idf_args
        else:
            import shlex
            args_str = " ".join(shlex.quote(a) for a in idf_args)
            cmd      = ["bash", "-c",
                        f'source "{idf_root / "export.sh"}" && idf.py {args_str}']
            env      = None
        result = subprocess.run(cmd, cwd=DUNEOS_ROOT, env=env)

    return result.returncode


def _sdkconfig_args(sdkconfig: Path | None) -> list[str]:
    """CMake override pinning the sdkconfig file.

    Without it every build directory shares ${CMAKE_SOURCE_DIR}/sdkconfig, so
    two boards built side by side overwrite each other's cached Kconfig.
    """
    return ["-D", f"SDKCONFIG={sdkconfig}"] if sdkconfig else []


def build_kernel(board_dir: Path, build_dir: Path, port: str | None,
                 sdkconfig: Path | None = None) -> int:
    """Build the DuneOS kernel via idf.py. Returns the exit code."""
    idf_root = find_toolchain_root()
    if not idf_root:
        sys.exit("ERROR: ESP-IDF not found. Run 'dbt setup'.")
    return _run_idf(idf_root,
                    ["-B", str(build_dir)] + _sdkconfig_args(sdkconfig) + ["build"])


def flash_kernel(build_dir: Path, port: str, baud: int) -> int:
    """Flash the DuneOS kernel via idf.py. Returns the exit code."""
    idf_root = find_toolchain_root()
    if not idf_root:
        sys.exit("ERROR: ESP-IDF not found. Run 'dbt setup'.")
    return _run_idf(idf_root, ["-p", port, "flash"])


# Every idf.py started here, until it is reaped. A `finally:` in the caller
# covers the normal path, but not Ctrl-C and not a SIGTERM — and a survivor is
# not a harmless orphan: qemu-system-xtensa spins a CPU core at 100% and keeps
# holding whatever TCP port it was given, which is enough to make the *next*
# run fail with a firmware-looking verdict.
_LIVE_IDF_PROCS: set = set()
_CLEANUP_HOOKED = False


def _reap_live_idf_procs() -> None:
    for proc in list(_LIVE_IDF_PROCS):
        _kill_idf(proc)


def _hook_process_cleanup() -> None:
    """Arrange for orphaned emulators to be killed on exit and on a signal.

    SIGKILL of dbt itself remains unrecoverable — nothing in the process can
    run then. That is the one leak this cannot close.
    """
    global _CLEANUP_HOOKED
    if _CLEANUP_HOOKED:
        return
    _CLEANUP_HOOKED = True
    atexit.register(_reap_live_idf_procs)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous = signal.getsignal(sig)
        except (ValueError, OSError):
            continue

        def handler(signum, frame, _previous=previous):
            _reap_live_idf_procs()
            if callable(_previous):
                _previous(signum, frame)
            else:
                raise SystemExit(128 + signum)

        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            # Not the main thread, or the signal does not exist here.
            pass


def _popen_idf(idf_root: Path, idf_args: list[str], extra_path: Path | None = None):
    """Start idf.py with stdout+stderr on a pipe, in its own process group.

    The group matters: killing idf.py alone would leave the QEMU child running
    and holding the serial socket.

    `extra_path` is prepended to the child's PATH. `qemu-xtensa` is an
    `on_request` IDF tool: `export.sh` does not put it on PATH, so idf.py's
    own `qemu` action would not find it either — passing the directory here is
    what makes the emulator usable without the caller editing their PATH.
    """
    from ..setup import build_idf_env, idf_python
    is_win = platform.system() == "Windows"
    target = _active_idf_target()
    _hook_process_cleanup()

    def _track(proc):
        _LIVE_IDF_PROCS.add(proc)
        return proc

    if is_win:
        import tempfile
        export   = idf_root / "export.bat"
        args_str = " ".join(_bat_arg(a) for a in idf_args)
        bat = (
            f'@call "{export}"\r\n'
            + (f'set "IDF_TARGET={target}"\r\n' if target else 'set "IDF_TARGET="\r\n')
            + (f'set "PATH={extra_path};%PATH%"\r\n' if extra_path else '')
            + f'cd /d "{DUNEOS_ROOT}"\r\n'
            f'idf.py {args_str}\r\n'
            f'exit /b %ERRORLEVEL%\r\n'
        )
        fd, bat_path = tempfile.mkstemp(suffix='.bat')
        with os.fdopen(fd, 'w', newline='') as f:
            f.write(bat)
        proc = subprocess.Popen(
            ['cmd', '/c', bat_path],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        proc._duneos_bat = bat_path  # cleaned up by _kill_idf
        return _track(proc)

    env    = build_idf_env(idf_root)
    if target and env is not None:
        env["IDF_TARGET"] = target
    if extra_path and env is not None:
        env["PATH"] = f"{extra_path}{os.pathsep}{env.get('PATH', '')}"
    python = idf_python(idf_root)
    idf_py = idf_root / "tools" / "idf.py"
    if python and idf_py.exists():
        cmd = [str(python), str(idf_py)] + idf_args
    else:
        import shlex
        args_str = " ".join(shlex.quote(a) for a in idf_args)
        prefix   = (f'export PATH={shlex.quote(str(extra_path))}:"$PATH"; '
                    if extra_path else "")
        cmd = ["bash", "-c",
               f'source "{idf_root / "export.sh"}" && {prefix}idf.py {args_str}']
        env = None
    return _track(subprocess.Popen(
        cmd, cwd=DUNEOS_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE,
        start_new_session=True,
    ))


def _kill_idf(proc) -> None:
    """Terminate idf.py and every process it spawned (QEMU included)."""
    _LIVE_IDF_PROCS.discard(proc)
    if proc.poll() is None:
        if platform.system() == "Windows":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            import signal
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    bat = getattr(proc, "_duneos_bat", None)
    if bat:
        try:
            os.unlink(bat)
        except OSError:
            pass


QEMU_PROGRAM      = "qemu-system-xtensa"
QEMU_TOOL         = "qemu-xtensa"
QEMU_TOOL_VERSION = "esp_develop_9.2.2_20250817"


def qemu_platform_supported() -> bool:
    """False on Windows: Espressif ships no qemu-xtensa build dbt can drive.

    Reported as a configuration error rather than a fruitless PATH hunt —
    "not supported here" and "installed but not found" call for different
    reactions from whoever reads the failure.
    """
    return platform.system() != "Windows"


def find_qemu() -> Path | None:
    """Locate qemu-system-xtensa: DUNEOS_QEMU, then the IDF tools tree, then PATH.

    `qemu-xtensa` is an `on_request` tool in ESP-IDF's tools.json: installing
    it does NOT put it on PATH, because `export.sh` only exports tools marked
    for the current target. Looking only at PATH is why `dbt qemu` used to
    tell people to run the very install command they had just run.
    """
    override = os.environ.get("DUNEOS_QEMU")
    if override and Path(override).exists():
        return Path(override)

    exe = ".exe" if platform.system() == "Windows" else ""
    for root in _espressif_roots():
        base = root / "tools" / QEMU_TOOL
        if not base.is_dir():
            continue
        # Newest version directory first; the pinned one is named after its
        # release date, so a reverse sort is a chronological sort.
        for version_dir in sorted(base.glob("*"), reverse=True):
            candidate = version_dir / "qemu" / "bin" / (QEMU_PROGRAM + exe)
            if candidate.exists():
                return candidate

    found = shutil.which(QEMU_PROGRAM)
    return Path(found) if found else None


def qemu_available() -> bool:
    """True when the emulator binary can be resolved.

    Exposed so the caller can bail out before the expensive build steps
    instead of discovering the missing tool at launch time.
    """
    return find_qemu() is not None


def install_qemu() -> bool:
    """Install qemu-xtensa through idf_tools.py. Needs no root.

    Returns True when the binary is resolvable afterwards.
    """
    from ..setup import idf_python

    idf_root = find_toolchain_root()
    if not idf_root:
        return False
    tools_py = idf_root / "tools" / "idf_tools.py"
    if not tools_py.exists():
        return False
    python = idf_python(idf_root) or Path(sys.executable)

    env = dict(os.environ)
    env.setdefault("IDF_PATH", str(idf_root))
    print(f"  installing {QEMU_TOOL} (expected {QEMU_TOOL_VERSION}) — "
          f"this downloads ~30 MB, please wait…", flush=True)
    rc = subprocess.run([str(python), str(tools_py), "install", QEMU_TOOL],
                        env=env).returncode
    if rc != 0:
        return False
    return find_qemu() is not None


# QEMU never needs to talk to a network for this bench, and the open_eth NIC
# idf.py adds by default is what drags in libslirp — a distribution package the
# user would otherwise have to install by hand. `qemu_ext.py` only adds the NIC
# when '-nic' is absent from the extra args, so passing it here suppresses it.
QEMU_EXTRA_ARGS_BASE = "-nic none"


def qemu_psram_args(psram_mb: int) -> str:
    """QEMU arguments that make the machine model the board's declared PSRAM.

    `idf.py qemu` hardcodes `-M esp32s3 -m 32M` for every ESP32-S3, and on that
    machine `-m` sizes the emulated `ssi_psram` device. ESP-IDF v6.0.1 has no
    say in the matter: `CONFIG_SPIRAM_TYPE_*` is a vestigial Kconfig choice
    that no source in `components/esp_psram` reads, so the driver always
    auto-detects and maps whatever the model reports. Mapping 32 MiB fills the
    ESP32-S3's 32 MiB external-memory data vaddr window and leaves the flash
    mmap of the partition table with nothing (SPEC-leg-30).

    The size therefore has to reach the emulator, and it comes from the board's
    own `psram_size_mb` so the bench boots the board as declared rather than as
    the emulator happens to default. Repeated `-m` merges in QEMU with the last
    occurrence winning, and idf.py appends `--qemu-extra-args` after its own
    arguments, so this overrides the hardcoded 32M.

    A board declaring no PSRAM is left alone: its firmware has CONFIG_SPIRAM=n
    and never touches the device, and `-m 0` is not a size QEMU accepts.
    """
    return f" -m {int(psram_mb)}M" if int(psram_mb) > 0 else ""


def run_qemu(build_dir: Path, flash_image: Path | None, timeout_s: float,
             consume, sdkconfig: Path | None = None,
             gdb_port: int | None = None,
             before_kill=None, psram_mb: int = 0) -> tuple[str, int | None]:
    """Boot the built firmware under QEMU, non-interactively.

    `consume(chunk)` is called with every piece of serial output ("" when the
    stream is idle) and returns a stop reason, or None to keep going.

    `gdb_port` opens QEMU's GDB server on that TCP port without halting the
    machine, so the caller can inspect a wedged system post-mortem.

    Returns (status, returncode). `status` is one of "complete", "panic",
    "eof", "timeout" (from the consumer / the deadline), "unavailable" when
    the emulator is not installed, or "no-sdk" when ESP-IDF itself is missing.
    `returncode` is None whenever the verdict was reached with QEMU still
    alive — the normal case — and must not be reported as a process exit code.
    """
    from ..qemu import chunk_reader, run_loop

    idf_root = find_toolchain_root()
    if not idf_root:
        return "no-sdk", None
    qemu_bin = find_qemu()
    if not qemu_bin:
        return "unavailable", None

    extra = QEMU_EXTRA_ARGS_BASE + qemu_psram_args(psram_mb)
    if gdb_port:
        extra += f" -gdb tcp::{gdb_port}"

    idf_args = ["-B", str(build_dir)] + _sdkconfig_args(sdkconfig) + [
        "qemu", "--qemu-extra-args", extra,
    ]
    if flash_image:
        idf_args += ["--flash-file", str(flash_image)]

    proc   = _popen_idf(idf_root, idf_args, extra_path=qemu_bin.parent)
    status = "eof"
    try:
        status = run_loop(chunk_reader(proc.stdout), consume, timeout_s)
    finally:
        rc = proc.poll()
        # The machine is still alive here when the verdict was reached before
        # QEMU exited: that is the only moment a post-mortem over the GDB port
        # is possible, so the caller gets its shot before the kill.
        if before_kill is not None and rc is None:
            try:
                before_kill(status)
            except Exception as exc:                      # noqa: BLE001
                print(f"  [warn] post-mortem inspection failed: {exc}")
        _kill_idf(proc)
    return status, rc


def _find_gdb() -> Path | None:
    """Locate the Xtensa GDB shipped with ESP-IDF, or None."""
    exe = ".exe" if platform.system() == "Windows" else ""
    for name in ("xtensa-esp32s3-elf-gdb", "xtensa-esp-elf-gdb"):
        found = shutil.which(name)
        if found:
            return Path(found)
    for root in _espressif_roots():
        base = root / "tools" / "xtensa-esp-elf-gdb"
        if not base.is_dir():
            continue
        for version_dir in sorted(base.glob("*"), reverse=True):
            # The layout below the version directory differs between tool
            # packages: xtensa-esp-elf-gdb/<ver>/xtensa-esp-elf-gdb/bin, but
            # xtensa-esp-elf/<ver>/xtensa-esp-elf/bin. Try both, plus bin/.
            for middle in ("xtensa-esp-elf-gdb", "xtensa-esp-elf", "."):
                for name in ("xtensa-esp32s3-elf-gdb", "xtensa-esp-elf-gdb"):
                    candidate = version_dir / middle / "bin" / (name + exe)
                    if candidate.exists():
                        return candidate
    return None


def qemu_gdb_report(elf: Path, port: int, ring_dump: Path,
                    timeout_s: float = 60.0) -> tuple[str, bytes | None, int]:
    """Interrogate a still-running QEMU through its GDB port.

    Returns (gdb transcript, raw klog ring bytes or None, ring write counter).
    The ring is DuneOS's own boot log; reading it out of memory is the only way
    to see the kernel's info-level lines, since klog.c forwards only level 'E'
    to the console. It is diagnostic output — never an assertion source.
    """
    gdb = _find_gdb()
    if not gdb:
        return ("no Xtensa GDB found — install the ESP-IDF toolchain to get "
                "post-mortem backtraces"), None, 0
    if not elf.exists():
        return f"no ELF at {elf} — cannot symbolise", None, 0

    cmds = [
        "set pagination off", "set confirm off",
        f"target remote :{port}",
        "info threads",
        "thread apply all backtrace 25",
        "print s_write_abs",
        "print sizeof(s_ring)",
        f"dump binary memory {ring_dump} s_ring (char *)s_ring + sizeof(s_ring)",
        "detach",
    ]
    argv: list[str] = [str(gdb), "-batch", "-nx", str(elf)]
    for c in cmds:
        argv += ["-ex", c]
    try:
        res = subprocess.run(argv, capture_output=True, text=True,
                             timeout=timeout_s)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"GDB inspection failed: {exc}", None, 0

    text = (res.stdout or "") + (res.stderr or "")
    write_abs = 0
    import re as _re
    m = _re.search(r"^\$1 = (\d+)", text, _re.M)
    if m:
        write_abs = int(m.group(1))
    data = ring_dump.read_bytes() if ring_dump.exists() else None
    return text, data, write_abs


def monitor(port: str) -> None:
    """Open the ESP-IDF serial monitor."""
    idf_root = find_toolchain_root()
    if not idf_root:
        sys.exit("ERROR: ESP-IDF not found. Run 'dbt setup'.")
    _run_idf(idf_root, ["-p", port, "monitor"])
