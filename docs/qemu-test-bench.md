# QEMU test bench — booting DuneOS without hardware

`dbt qemu` boots a real DuneOS flash image under Espressif's QEMU fork and asserts on the
serial output that the boot sequence went all the way through the ELF loader:

```
/flash mounted → /bin scanned → qemu_smoke.dap loaded → the app's own exit marker
```

Shape and rationale: [ADR 039](adr/039-qemu-test-bench.md). Scope: `SPEC-leg-27`.

**What a green run proves — and what it does not.** It validates the loader, the VFS and the
boot sequence. It says nothing about the shipping board: the QEMU boards declare no display,
no keyboard and no SD, so the CardPuter's display/keyboard/SD paths stay untested. Reporting a
green run without that caveat overstates it.

## Installing the emulator — you don't

```bash
echo esp32s3-qemu > .duneos_board        # or esp32s3-qemu-psram
python tools/dbt.py qemu
```

That is the whole procedure. No `source export.sh`, no `export PATH=…`, no distribution
package.

`qemu-xtensa` is an **`on_request`** tool in ESP-IDF's `tools/tools.json`: it is not installed
with the SDK, and — the part that costs people an afternoon — `export.sh` does **not** put it
on PATH even after it is installed. So `dbt qemu`:

1. resolves `qemu-system-xtensa` itself, in this order: `$DUNEOS_QEMU`, the IDF tools tree
   (`$IDF_TOOLS_PATH` or `~/.espressif`, then `tools/qemu-xtensa/<version>/qemu/bin/`), then
   PATH;
2. runs `idf_tools.py install qemu-xtensa` on its own when nothing is found (no root needed,
   ~30 MB download, announced before it starts so it does not look like a hang);
3. prepends the resolved directory to the PATH of the `idf.py qemu` child, so ESP-IDF's own
   `qemu` action finds the same binary.

Expected version, as pinned by ESP-IDF v6.0.1: **`esp_develop_9.2.2_20250817`**.

The resolution happens before bspgen, the kernel build, the app build and the image compose —
a broken setup costs seconds, not a full build. Exit code **4** if the emulator is still
unusable after the automatic install; exit **6** on Windows, where Espressif ships no
`qemu-xtensa` build dbt can drive, reported as "not supported on this platform" rather than a
fruitless PATH hunt.

### No `libslirp`, on purpose

`idf.py qemu` normally adds `-nic user,model=open_eth`, and that virtual NIC is the only
reason the pinned QEMU build needs `libslirp.so.0` from the distribution. The NIC is
**conditional** (`tools/idf_py_actions/qemu_ext.py`: `if '-nic' not in qemu_extra_args`), so
dbt passes `--qemu-extra-args "-nic none"` and the device is never created. This bench does
not test networking and has no business instantiating an Ethernet controller.

Consequence: no `sudo apt install libslirp0` / `sudo pacman -S libslirp` step, on a developer
machine or in CI. If you ever re-enable networking in the emulator, that dependency comes back
(ESP-IDF's QEMU page lists it among the system prerequisites).

## Running

Options: `--board` (must match `.duneos_board`), `--build-dir` (default `build-<board>`),
`--timeout` (default 180 s), `--no-build`, `--quiet`, `--gdb-port` (default: a free ephemeral
port picked at launch), `--no-gdb-diag`.

`--no-build` skips **dbt's own** kernel and app build steps. The app is then genuinely not
rebuilt, but the kernel still is: ESP-IDF's `qemu` action declares `dependencies: ['all']`
(`tools/idf_py_actions/qemu_ext.py`), so `idf.py qemu` brings the kernel up to date whatever the
flag says. The flag saves the app build and dbt's explicit kernel invocation, nothing more.

The command needs no hardware: it reads neither `.duneos_port` nor any serial device, and
takes no keyboard input. QEMU is stopped by the runner as soon as the verdict is known.

### Exit codes

| Code | Meaning |
| ---: | --- |
| 0 | every assertion matched, and no crash appeared in the settle window |
| 1 | assertion failure (including QEMU exiting before the run completed) |
| 2 | timeout — the expected state was never reached |
| 3 | panic or reboot detected |
| 4 | `qemu-system-xtensa` missing and the automatic install failed |
| 5 | kernel or app build failure |
| 6 | configuration error (unknown board, `--board` ≠ `.duneos_board`, missing `flash_args` or a binary it lists, unresolvable `--flash-size`, ESP-IDF or the cross-compiler not found, a missing Python build dependency such as `littlefs-python`, QEMU unsupported on this platform, or QEMU exiting at startup because it could not open its GDB port) |

Code 1 also covers the case where the **payload** died: the supervisor reports a non-zero app
exit or a CPU exception at klog level `E`, which does reach the console. The runner stops
there instead of sitting out the timeout, and says the system itself did not crash.

A timeout, a failed assertion and a configuration error are deliberately distinct codes with
distinct messages: they call for different investigations. In particular a misconfigured bench
(code 6) never presents as a red firmware (code 1).

An EOF — QEMU exiting on its own — counts as a pass **only** once the settle window has
elapsed. A run that dies immediately after printing the last marker exits **1**, not 0.

### Which crash patterns are actually observable

`klog.c` forwards only level `E` to `ESP_LOG`, and `klog_capture_rom_output()`
(`kernel/duneos_kernel/src/klog.c`) redirects the ROM console channel into the ring buffer. That
splits the runner's `PANIC_PATTERNS` in two, and a reader should not trust the dead half:

| Pattern | Reachable over the emulated UART? |
| --- | --- |
| `Guru Meditation Error`, `Core N panic'ed`, `CPU halted` | **yes** — the panic handler writes them with `panic_print_char`, bypassing klog |
| second `rst:0x…` boot banner | **yes** — printed by the ROM of the next boot |
| `abort() was called`, `assert failed:`, `Task watchdog got triggered`, `CORRUPT HEAP` | **only while `qemu_smoke` is alive** and draining the ring — they are `ESP_EARLY_LOG` / `esp_rom_printf` output, which `klog_capture_rom_output()` swallows |

Consequence: a panic that halts **without** rebooting and without a Guru Meditation dump is
reported as a timeout (code 2), not a panic (code 3). Both are failures; only the label differs.

### Reading a failed run: silence is never the answer

Every assertion above travels on a single channel — `qemu_smoke` draining the klog ring to
its stdout. If the payload never starts, all five report `[MISS]` and say nothing about *why*.
Absence proved nothing, and a healthy-but-mute kernel looked exactly like a hung one.

Two mechanisms close that gap. Neither can make a run pass: they do not feed the matcher and
do not touch the exit code.

**Boot progress markers.** The verdict block lists what the console genuinely carried,
whatever DuneOS did — ESP-IDF's ROM-path early logs, ESP-IDF's stdout/VFS logs, and klog level
`E`. The two that matter most:

| Marker | If missing |
| --- | --- |
| `IDF stdout-log path alive` (`main_task: Started on CPU…`) | execution never got past ESP-IDF's own startup — the fault is below DuneOS |
| `app_main entered (IDF)` (`main_task: Calling app_main()`) | the kernel's `app_main` was never called |

**Post-mortem over QEMU's GDB port.** QEMU is started with `-gdb tcp::<port>` (no `-S`, the
machine runs freely), where `<port>` is an ephemeral port the OS hands out at launch. A fixed
port was a bench-wide failure mode: an emulator leaked by an earlier run still holds it, QEMU
then dies at startup with "could not start gdbserver", and the run scores every assertion as
missing — an environment fault reported as a firmware fault. `--gdb-port` pins it when a
debugger has to be attached by hand. The residual race (something grabs the port between the
probe and QEMU's bind) is caught by `qemu_startup_failure()` and exits `EXIT_CONFIG` (6), not
`EXIT_ASSERT`. On any non-passing verdict, and while the emulator is still alive, dbt
attaches the ESP-IDF Xtensa GDB and prints:

* a backtrace of **both** CPUs — the frame the system is actually stuck in;
* the **klog ring read straight out of memory** (`s_ring` / `s_write_abs`, re-ordered
  oldest-first). This is the whole kernel boot log, info level included, without the payload
  and without touching a line of kernel code.

`--no-gdb-diag` turns it off, `--gdb-port` moves it. It costs a couple of seconds and only
runs when something failed.

### Why a reboot counts as a failure

What always reaches the UART after a crash is the ROM boot banner of the *next* boot. The runner counts `rst:0x…` banners:
more than one means the system rebooted, and the run fails. Without that check a crash would
present as silence, and silence after a partial match is the failure mode a test bench must
never call a pass.

For the same reason the runner keeps reading for a couple of seconds after the last assertion
matches: a crash one line later must not be reported green.

## The two boards

| Board | PSRAM | Loader branch covered |
| --- | --- | --- |
| `esp32s3-qemu` | none (`CONFIG_SPIRAM=n`) | DRAM section allocation |
| `esp32s3-qemu-psram` | 8 MB quad (`CONFIG_SPIRAM=y`) | `#ifdef CONFIG_SPIRAM` section allocation |

Both declare only UART0 and flash — `has_sd: false`, `wifi: false`, no display, no keyboard, no
I2C, no SPI. They are ordinary boards: `duneos-bspgen.py` generates their `board_config.h`,
`sdkconfig.board`, `partitions.csv` and `idf_target.txt` like any other.

### `qemu: true` — the one key that means "not silicon"

Both boards also declare `qemu: true`, from which bspgen emits `CONFIG_DUNEOS_TARGET_QEMU=y`.
It has exactly one effect, in `loader.c`'s exec write path, and it exists because the emulator
does not implement the IRAM/DRAM SRAM alias — blocker 2 below, where the reasoning lives. No
hardware board declares it, and none may: the write path it selects is wrong on silicon. Its
meaning is narrow on purpose — "internal SRAM is not dual-mapped here" — not "relax things
under emulation".

### `console: uart` also switches off ESP-IDF's secondary console

Both boards declare `console: uart`. On a chip that carries a USB-Serial-JTAG unit, ESP-IDF
keeps `CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG=y` on top of the primary UART console, and
the side effect that matters here is not the mirroring — it is **which VFS ends up owning
global fd 1**. See blocker 3 below for the exact mechanism. The consequence is that an
application's `write(1, …)` goes to a peripheral QEMU does not model, while the kernel's
`ESP_LOGE` still comes out, because it travels the `stdout` `FILE *` (a different, higher fd
bound to `/dev/console`) rather than raw `fd 1`. That asymmetry produced five `[MISS]`
assertions on runs where the payload demonstrably executed its whole `app_main`.

`duneos-bspgen.py` therefore emits `CONFIG_ESP_CONSOLE_SECONDARY_NONE=y` whenever a
`board.yaml` **explicitly** declares `console: uart` on such a chip: an explicit declaration is
a statement that UART is the board's only console. Boards that merely fall through to the
`uart` default (`esp32s3-devkitc`) say nothing about the secondary console and keep ESP-IDF's
USB-Serial-JTAG stdout mirror; boards on a chip without that unit (`kincony-A16`, ESP32) get no
symbol at all, since none exists in their Kconfig. This is acceptance criterion 7b.

Switching board requires a clean build (`sdkconfig` is board-specific), so each board gets its
own build directory **and its own `sdkconfig`** (`build-<board>/sdkconfig`, passed as
`-D SDKCONFIG=…`). Nothing is shared with the default `build/`.

## The payload app

`apps/user/qemu_smoke` is the only app staged into the image. It drains `/dev/klog` to its
stdout — that is what makes the kernel's boot lines observable from outside, since klog does not
forward info-level messages to the console — then prints
`<<<DUNEOS-QEMU-SMOKE app_main reached duneos_exit(0)>>>` and calls `duneos_exit(0)`.

That marker proves the loaded code actually ran, rather than merely being parsed — **and nothing
more**. It is printed *before* `duneos_exit(0)`, so it says nothing about the exit itself. Nor can
it: the app is the only reader draining the klog ring, so everything the kernel logs after it
exits (loader unload, slot release, supervisor exit accounting) is outside this bench's reach.
The assertion is labelled `qemu_smoke app_main ran to its duneos_exit(0) call` for that reason —
proving a clean exit would need a second, still-running reader, which this spec does not commit
to.

Draining the ring is no longer the only way to see the kernel's boot lines: the post-mortem
reads the same ring out of memory over the GDB port, without needing the app to be alive. The
app's drain stays because it is the channel the *assertions* travel on, and because an
assertion must not be satisfiable by a debugger reading kernel memory — criterion 6 requires
the payload's own code to have run.

The image ships **no `/init.yaml`**, on purpose: the kernel then takes the autoboot path,
which is the one that scans `/bin` — the code path this bench exists to exercise.

## Blockers — where each board stands

**`esp32s3-qemu` passes end to end today.** `python tools/dbt.py qemu --board esp32s3-qemu`
exits **0** with all five assertions matched, no patch applied. Blockers 1, 2 and 3 below are all
closed.

**`esp32s3-qemu-psram` passes end to end too**, since SPEC-leg-30. It had a fourth blocker,
independent of the three: `idf.py qemu` hardcodes `-M esp32s3 -m 32M`, and on that machine `-m`
sizes the emulated `ssi_psram` device. ESP-IDF v6.0.1 has no say in how much of it gets mapped —
`CONFIG_SPIRAM_TYPE_*` is a vestigial Kconfig choice that nothing in `components/esp_psram`
reads, so the driver auto-detects and maps whatever the model reports. 32 MiB of mapped PSRAM
fills the ESP32-S3's whole 32 MiB external-memory data vaddr window, and the flash mmap of the
partition table then has none left: `sysbin` is never found.

`dbt qemu` now passes `-m <psram_size_mb>M`, read from the board's own `board.yaml`, so the
emulator models the board as declared instead of re-defining it by default. The bench also
cross-checks the size esp_psram reports against the declaration and scores a disagreement — or
either signature of vaddr exhaustion — as **exit 6 (configuration)**, never as a firmware
failure. Tracked as [`SPEC-leg-30`](../specs/SPEC-leg-30-qemu-psram-vaddr-exhaustion.md).

Blockers 1 and 2 were found on the first real execution (2026-09-03, `esp32s3-qemu`) and
confirmed with GDB against the running emulator. Neither was a defect in this bench, and neither
was fixable from `tools/`. Blocker 1 is closed by removing the over-link (SPEC-leg-28), blocker 2
by a board-scoped guard (SPEC-leg-29), and blocker 3, which was DuneOS's own, by SPEC-leg-27's
criterion 7b.

**1. (fixed, by guarding the HAL source) ESP-IDF's ADC calibration constructor never returns
under QEMU.** `esp_adc` registers
`adc_hw_calibration()` as a GCC constructor (`components/esp_adc/adc_common.c:74`); it runs
from `__libc_init_array()` in `start_cpu0_default()` (`components/esp_system/startup.c:183`)
and busy-waits on `SENS.sar_meas1_ctrl2.meas1_done_sar`
(`components/esp_hal_ana_conv/esp32s3/include/hal/adc_ll.h:1085`), which the emulator never
sets. Boot stops there — before `main_task`, before `app_main`, with no panic and no reboot,
which is exactly the observed trace. DuneOS links `esp_adc` unconditionally
(`arch/xtensa_esp32s3/arch.cmake:35` and `:55`) even for a board that declares no ADC, so
every board was affected under emulation. Verified by removing both lines: the boot then runs
to completion (`main_task: Calling app_main()`, `/flash` mounted, `/bin` scanned,
`qemu_smoke` found and launched).

*Resolution (SPEC-leg-28, 2026-09-04).* `arch.cmake` now guards its
`list(APPEND DUNEOS_KERNEL_SRCS …)` entry for `hal/hal_adc.c` on
`CONFIG_DUNEOS_DRV_BATTERY_ADC_SIMPLE`, the symbol that already gates the HAL's sole consumer —
same treatment for `hal_i2c.c`, `hal_spi.c` and `hal_encoder.c`. No new symbol, no bspgen change.
`esp_adc` deliberately **stays** in `DUNEOS_KERNEL_REQUIRES`: an unreferenced archive member is
not linked and its constructor never runs, whereas guarding a REQUIRES entry on a `CONFIG_*`
symbol is illegal — those variables are empty during the ESP-IDF requirements phase.

**2. (fixed, by a board-scoped guard) QEMU does not alias the ESP32-S3 IRAM and DRAM views of
internal SRAM.** With blocker 1
patched out, the loader maps its exec pool at `IRAM=0x40393454 / DRAM=0x3fca3454` (the correct
`0x6F0000` hardware alias) and the app dies instantly: `cause=0` (illegal instruction) at
`PC=0x40393490`, four words into its own text. Writing `0x12345678` at `0x3fca3454` through
GDB leaves `0x40393454` at zero, and writing `0xdeadbeef` at `0x40393460` leaves `0x3fca3460`
unchanged: on this machine model the two windows are **separate memories**, not two views of
one SRAM. The loader writes app code through the DRAM alias (the only way on real hardware)
and executes it through the IRAM address, so under QEMU it executes zeroes.

*Resolution (SPEC-leg-29, option 1, 2026-09-04): a board-scoped compile-time guard.* A board
declares `qemu: true` in its `board.yaml`; `duneos-bspgen` emits `CONFIG_DUNEOS_TARGET_QEMU=y`
into that board's `sdkconfig.board`; `loader.c` sets `s_build_scratch = iram` under that symbol
and keeps `iram_word_dram_alias()` otherwise. Only `esp32s3-qemu` and `esp32s3-qemu-psram`
declare it — the symbol is absent from every hardware board's `sdkconfig.board`, so the silicon
write path is not merely safe but **literally unchanged**.

Why this was chosen over fixing the emulator or doing nothing. The guard's only sacrifice is
proving that writes through the DRAM alias land at the IRAM view — and QEMU **cannot test that
anyway**, because it does not implement the alias. So the choice was never "full coverage vs
degraded coverage"; it was *zero* coverage (nothing runs at all) versus everything covered
except a property the emulator is incapable of checking. ELF parsing, relocation arithmetic,
symbol resolution, scan, select, launch, VFS and mount are identical in both builds. Fixing it
upstream in Espressif's QEMU fork (`memory_region_init_alias()`) remains the better answer in
principle and would let the guard be deleted, but its timeline is outside this project's
control — Espressif tag their own DIRAM aliasing tests `[heap][qemu-ignore]`, i.e. they exclude
the property rather than fix it. The minimal bidirectional GDB reproduction above is ready to
file upstream.

**The honest cost, stated rather than mitigated: two divergent write paths.** If someone later
breaks the alias arithmetic (`iram_word_dram_alias`, `build_install_exec`), this bench stays
green and says nothing. That is the standard test-double trade-off. It is bounded, the guard in
`loader.c` carries a comment saying exactly this, and a periodic hardware run remains the only
proof of the DRAM-alias path.

> **Must not be re-attempted — changing the loader's *hardware* write path.** Making the loader
> write at the IRAM address *unconditionally* was tested as a throwaway experiment. It makes the
> `.dap` run correctly under QEMU, and it is **incorrect on real silicon**. Recorded here so
> nobody rediscovers it as a fix:
>
> - IRAM is 32-bit-access-only (`mem_alloc.rst:126`); the `SOC_BYTE_ACCESSIBLE_*` range covers
>   the DRAM view only.
> - The loader performs unaligned **byte** stores into the instruction stream while relocating,
>   and reads sections straight into the exec block. Both raise `LoadStoreError` through the
>   IRAM window on hardware.
>
> The guard above is *not* that change: it compiles the IRAM path only into boards that never
> run on silicon. The `qemu:` key's meaning is correspondingly narrow — "internal SRAM is not
> dual-mapped here" — and must not become a general "relax things under emulation" switch.

**3. (fixed) The payload's `write(1, …)` went to the USB-Serial-JTAG VFS.** Found on
2026-09-03 and closed the next day: a supervisor-launched app's `fd 1` resolved to
`/dev/secondary` (`usb_serial_jtag_write`), which QEMU does not model, so every `write(1, …)`
returned -1 and no payload output ever reached the console. Unlike blockers 1 and 2 this one
was DuneOS's own, and is criterion 7b.

*Mechanism, re-derived from ESP-IDF v6.0.1 source on 2026-09-04 after the diagnosis was
challenged.* The build has `CONFIG_VFS_SUPPORT_IO=y`, so the compiled console path is
`components/esp_stdio/stdio_vfs.c`. Reading `console_write()` there suggests the secondary is
only an additive mirror that can never return -1 — and that reading is correct, but it does not
apply, because **fd 1 is not the console fd**. `esp_vfs_open()`
(`components/vfs/vfs_calls.c:32-56`) calls the filesystem's `open` callback *first* and only
then allocates the global fd (`register_fd`, line 55). The very first open of `/dev/console`
therefore recurses: `console_open()` (`stdio_vfs.c:51-79`) itself opens `/dev/uart/0` and, under
`CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG`, `/dev/secondary` — and those two nested opens
claim global fds **0 and 1** before the outer `/dev/console` open is registered as fd 2. Newlib's
`stdout`/`stderr` are `FILE *`s wrapping that console fd, so `printf` and `ESP_LOG` are
unaffected; raw `write(1, …)` is not, and lands on `/dev/secondary`. There,
`usb_serial_jtag_write()`
(`components/esp_driver_usb_serial_jtag/src/usb_serial_jtag_vfs.c:182-187`) returns **-1**
outright when `!usb_serial_jtag_is_connected()` — which is always the case under QEMU. That is
the -1, and it matches the original GDB observation exactly: `fd 1 -> vfs_index=1`
(`/dev/secondary`), `s_vfs[1]->vfs->write = usb_serial_jtag_write`, `api_write(fd=1, …) = -1`.

With `CONFIG_ESP_CONSOLE_SECONDARY_NONE=y` the nested open of `/dev/secondary` disappears, so
the fds shift down by one: fd 0 = `/dev/uart/0`, **fd 1 = `/dev/console`**, and
`console_write()` compiles down to a single `write()` on the primary UART fd. GDB against the
live machine after the fix shows exactly that, with no `/dev/secondary` in the registered VFS
table. The fix works because it removes an fd from the boot-time open sequence, not because it
removes a mirror.

Consequence for the spec: acceptance criterion 6 — proving a `.dap` was loaded and its own code
ran — is **satisfied on `esp32s3-qemu`**. All three blockers are closed (SPEC-leg-28,
SPEC-leg-29, and SPEC-leg-27's own criterion 7b), and the bench passes there with all five
assertions matched including the payload's own marker — and on `esp32s3-qemu-psram` too, once
SPEC-leg-30 sized the emulated PSRAM from the board declaration.

## CI

`.github/workflows/ci.yml` runs the `qemu-smoke` job as a matrix over both boards. Each matrix
leg is its own job, hence its own build directory and `sdkconfig`, and the job fails if either
exit code is non-zero.

**Both legs gate.** The job carried `continue-on-error` while the PSRAM board was red; every
blocker is now closed (SPEC-leg-27's criterion 7b, SPEC-leg-28, SPEC-leg-29, SPEC-leg-30) and it
has been removed entirely, so a regression in either board turns CI red the day it lands.
