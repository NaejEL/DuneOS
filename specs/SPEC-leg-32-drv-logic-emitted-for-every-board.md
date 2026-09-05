Status: PROPOSED

# SPEC-leg-32 — `CONFIG_DUNEOS_DRV_LOGIC=y` is emitted for every board, contradicting its own Kconfig default

## The product question comes first

This spec cannot be scoped, let alone implemented, before one ruling:

> **Is `/dev/logic0` an always-available DuneOS feature — like `/dev/null`, `/dev/klog`,
> `/dev/uart0` — or a board-declared peripheral, like I2C, SPI or the battery gauge?**

The repository currently asserts **both**, in two places, and one of them is wrong:

- `tools/duneos-bspgen.py` emits `CONFIG_DUNEOS_DRV_LOGIC=y` **unconditionally, for every board**,
  which asserts *feature*.
- `kernel/duneos_kernel/Kconfig:31-33` declares the symbol `default n`, which asserts *peripheral*.

Everything below is written so that whoever answers the question has the evidence in hand. The
answer decides the shape of the whole change; this spec deliberately does **not** decide it (see
Open questions).

## Context

Found by audit while closing the QEMU bench (SPEC-leg-28/29/30), and explicitly deferred out of
SPEC-leg-28's Out of scope as "same defect class, one layer up".

The unconditional emission, in the `# ---- DuneOS drivers ----` block:

```python
tools/duneos-bspgen.py:711-715
    # ---- DuneOS drivers ----
    lines += ["# DuneOS kernel drivers", "CONFIG_DUNEOS_DRV_NULL=y",
              "CONFIG_DUNEOS_DRV_UART=y", "CONFIG_DUNEOS_DRV_KLOG=y",
              "CONFIG_DUNEOS_DRV_GPIO=y",
              "CONFIG_DUNEOS_DRV_LOGIC=y", ""]
```

`NULL` / `UART` / `KLOG` belong there by design — they are the always-present devices. `GPIO` and
`LOGIC` were added to the same list, and only `LOGIC` has a Kconfig default contradicting it.

Confirmed in a generated artefact, on a board whose YAML declares no peripherals at all:

```
boards/esp32s3-qemu/sdkconfig.board:23    CONFIG_DUNEOS_DRV_LOGIC=y
```

Against the Kconfig that says the opposite:

```
kernel/duneos_kernel/Kconfig:31-33
    config DUNEOS_DRV_LOGIC
        bool "Logic-capture device (/dev/logic0)"
        default n
```

And **no `board.yaml` in the repo has a `logic:` section** — the generator has no input that could
turn the symbol on or off, because the schema has no key for it. `tools/duneos-bspgen.py` never
reads one (`grep -n 'logic' tools/duneos-bspgen.py` returns only the emission line).

Consequences today, on every DuneOS board:

- `kernel/duneos_kernel/CMakeLists.txt:118-120` compiles `src/drivers/logic/drv_logic.c`, since its
  guard is the always-emitted symbol.
- `drv_logic.c:91` is `DUNEOS_DRIVER_REGISTER(5, drv_logic_register);` — a GCC constructor runs at
  every boot and registers the device, so `/dev/logic0` exists on every board by fiat.
- `arch/xtensa_esp32s3/arch.cmake:34` links `hal/hal_logic.c` **unconditionally**, outside the
  guarded peripheral-HAL block SPEC-leg-28 introduced at `arch.cmake:44-58` — so the HAL is linked
  even on the hypothetical board where the driver symbol were off.
- `arch/xtensa_esp32s3/arch.cmake:67` carries `esp_hw_support` in `DUNEOS_KERNEL_REQUIRES` for it.

**This is correctness and size, not a hang.** Unlike SPEC-leg-28's `esp_adc`, the constructor here
is DuneOS's own `duneos_dev_register()` — it touches no hardware and pulls in no ESP-IDF driver.
`hal_logic.c` reads GPIO through `REG_READ(GPIO_IN_REG)` (`arch/xtensa_esp32s3/hal/hal_logic.c:39`)
only when a capture is actually requested, so nothing runs at boot that could stall under
qemu-xtensa. Both QEMU boards are green with the code linked in. The cost is a device on every
board that no board declared, plus ~4.4 KB of source's worth of text and its component dependency,
on a part with 8 MB of flash and no PSRAM.

## Scope

Make bspgen's behaviour and the Kconfig default state the same thing, in whichever direction the
product ruling chooses. **Both branches are kept below for the record; the ruling took the
peripheral branch** (Product ruling, parts 1 and 2), so the first block is the work and the second
is the road not taken.

**If `/dev/logic0` is a board-declared peripheral** (Kconfig is right, bspgen is wrong):

- Add a `logic:` section to the `board.yaml` schema, documented in the schema comment block at the
  head of `tools/duneos-bspgen.py` alongside `i2c:` / `spi:` / `battery:`.
- Emit `CONFIG_DUNEOS_DRV_LOGIC=y` only when that section is present, moving the line out of the
  unconditional block at `tools/duneos-bspgen.py:711-715`.
- Guard `hal/hal_logic.c` in `arch/xtensa_esp32s3/arch.cmake:34` on `CONFIG_DUNEOS_DRV_LOGIC`,
  moving it into the peripheral-HAL block at `arch.cmake:44-58` — exactly the SPEC-leg-28 pattern,
  **SRCS only, never REQUIRES**.
- Decide, per board, which boards declare it. A board that loses `/dev/logic0` loses it for its
  apps: `apps/`-side consumers must be checked before, not after.

**If `/dev/logic0` is an always-available DuneOS feature** (bspgen is right, Kconfig is wrong):

- Change `kernel/duneos_kernel/Kconfig` to `default y`, so a board built without bspgen's fragment
  still gets the device the platform promises.
- `arch.cmake:34` is then already correct as written, and nothing else changes.
- Record the ruling where a future reader meets it: the Kconfig help text, and CLAUDE.md's
  "Kernel driver selection model" list, which currently shows `drv_null` / `drv_uart` / `drv_klog`
  as the always-on set and does not mention `LOGIC`.

Either way the outcome is that **one** artefact states the policy and the other follows it.

## Acceptance criteria

The ruling is now complete (peripheral, bare key, `m5stack-cardputer` only, GPIO untouched), so
**criterion 1 applies and criterion 2 is void**. Criterion 2 is kept struck through rather than
deleted, so a reader can see which branch was not taken.

1. A board whose `board.yaml` has no `logic:` section emits no
   `CONFIG_DUNEOS_DRV_LOGIC` in its `sdkconfig.board`, compiles no `drv_logic.c.obj` and no
   `hal_logic.c.obj`, and the symbol `drv_logic_register` is **absent from the build's
   `duneos.map`**. Verified by grepping the link map, for `boards/esp32s3-qemu`.
2. ~~*(feature ruling)* `kernel/duneos_kernel/Kconfig`'s `DUNEOS_DRV_LOGIC` is `default y`…~~
   **Void — the feature branch was not taken.** `DUNEOS_DRV_LOGIC` keeps its `default n`
   (`kernel/duneos_kernel/Kconfig:31-33`) unchanged, and any diff touching that line fails this
   spec.
2bis. `boards/m5stack-cardputer/board.yaml` gains a bare `logic:` key and **no pin list**; the
   schema comment block at the head of `tools/duneos-bspgen.py` documents it as bare, alongside
   `i2c:` / `spi:` / `battery:`. No other board gains the key. A generator check that rejects
   `logic:` in the absence of `i2c:` fails this criterion — see Product ruling, part 2, Q2.
3. `tools/duneos-bspgen.py` and `kernel/duneos_kernel/Kconfig` no longer disagree: whichever
   direction is taken, no board can be built where one says the driver is on and the other says it
   is off. Asserted by a pytest case, not by inspection.
4. A board that keeps `/dev/logic0` still opens it and completes one capture — the device is not
   merely present in `/dev` but functional. Verified on `m5stack-cardputer` hardware or via
   `apps/`-side exercise; a build-only check does not satisfy this.
5. `python tools/dbt.py qemu --board esp32s3-qemu` and `--board esp32s3-qemu-psram` both still exit
   **0** with all five assertions matched.
6. Every existing board still builds with no new warning after a fullclean (`m5stack-cardputer`,
   `esp32s3-devkitc`, `lilygo-t-embed-cc1101`, both QEMU boards), and all boards regenerate with
   only the intended diff. No generated file is hand-edited.
7. No `board.yaml`, `board_config.h`, `sdkconfig.board`, `partitions.csv` or `idf_target.txt` is
   edited by hand; every change reaches them through the YAML or `tools/duneos-bspgen.py`.
8. `./tools/.dbt-venv/bin/python -m pytest tools/dbt/tests -q` passes, including the new case from
   criterion 3.

## Out of scope

- `CONFIG_DUNEOS_DRV_GPIO`, emitted from the same unconditional block at
  `tools/duneos-bspgen.py:714`. **Settled by the Product ruling, part 2, Q4: `/dev/gpiochip0` is a
  controller, not a function — it stays unconditional and this spec does not touch it.** Note that
  it *does* carry a contradicting Kconfig default (`kernel/duneos_kernel/Kconfig:25-27`,
  `default n`), contrary to what an earlier draft of this section claimed; making that line agree
  with the ruling (`default y`) is the residual item recorded under Open questions.
- The I2C guard / bus-0 mismatch — SPEC-leg-31.
- The REQUIRES-level over-declaration — SPEC-leg-33. (`main/main.c:72`'s stale comment moved to
  SPEC-leg-20's documentation-coherence batch.)
  `esp_hw_support` at `arch/xtensa_esp32s3/arch.cmake:67` is *referenced* by `hal_logic.c` and is
  therefore not part of that spec's list; whether it survives depends only on this one's ruling.
- Any change to `drv_logic.c`, `hal_logic.c`, `logic_ioctl.h` or ADR 020's design. This spec
  decides **whether** the device is linked, never how it captures.
- Adding a logic-capture consumer app, or extending capture to a second arch.

## Risks

- **Removing `/dev/logic0` from a board is a userspace-visible break.** Nothing in the kernel build
  catches an app that opens it. Any `apps/` consumer must be found before the emission changes, not
  after a board stops booting its shell.
- **The ruling propagates.** `GPIO` sits in the same unconditional block; a "peripheral" ruling
  invites the same treatment there, and a "feature" ruling legitimises adding future drivers to a
  list nobody re-examines. Whichever way it goes, the decision belongs in CLAUDE.md's driver
  selection model, or the next audit re-derives it.
- **`arch.cmake:34` is SRCS, and the component is `WHOLE_ARCHIVE`**
  (`kernel/duneos_kernel/CMakeLists.txt:209` `idf_component_register(`, keyword at
  `CMakeLists.txt:228`), so a SRCS entry is force-linked whether
  referenced or not. That is why guarding it is behavioural and why the guard must go on SRCS only:
  `CONFIG_*` is empty during the ESP-IDF requirements phase (CLAUDE.md, "arch.cmake guard
  pattern"), so the same guard on a REQUIRES entry hides headers at compile time from the boards
  that need them.
- **Moving sources in and out of the component's source list is resolved at configure time only** —
  every board needs a fullclean before its next build, or the change appears not to have taken.
- **Not an ABI change**: no exported symbol or ABI struct layout moves, so no `DUNEOS_ABI_VERSION`
  bump is expected. Removing a device from `/dev` is nonetheless a platform-contract change and
  should be announced as one.

## Product ruling (2026-09-05, product owner)

**`/dev/logic0` is a PERIPHERAL, not an always-available feature.** It must not be enabled by
default. `kernel/duneos_kernel/Kconfig`'s `default n` is therefore right and `tools/duneos-bspgen.py`
is wrong: the unconditional emission is the defect to fix, and `hal_logic.c` at
`arch/xtensa_esp32s3/arch.cmake:34` gains the same guard SPEC-leg-28 applied to its neighbours.

**On the pin list, one finding changes the shape of the fix.** The capture pin list already exists
and is NOT board configuration: it is supplied per capture by the application through
`ioctl(LOGIC_SET_CONFIG)` — `apps/user/i2cscope/sniff.c:72-76` passes
`.channel_gpio = { g_scl_pin, g_sda_pin }, .n_channels = 2`, validated by `logic_set_config()`
at `kernel/duneos_kernel/src/drivers/logic/drv_logic.c:24-38` against `LOGIC_MAX_CHANNELS`.
`/dev/logic0` is deliberately generic (ADR 020, Accepted): a swappable-backend capture device, not
an I2C-specific one.

Consequently a `logic:` section is NOT required for the device to work, and a bare key would be
enough to mean "compile the driver". A pin list in `board.yaml` would only buy validation and
documentation — declaring which GPIOs are physically safe to sample on that board — and that is a
genuine but separate benefit. The spec should decide between the two, not assume the pin list is
functionally necessary.

**Coupling worth knowing before implementing:** the sniffer's two pins are resolved at runtime from
`board.info` (`apps/user/i2cscope/i2cscope.c:109-110`, `board_info_int("i2c0_scl"/"i2c0_sda")`),
i.e. from the board's **`i2c:`** declaration, with `DEFAULT_SCL_PIN`/`DEFAULT_SDA_PIN` as fallback.
That `board.info` block is `kernel/duneos_kernel/src/vfs.c:356-362` — the exact code SPEC-leg-31
reports as broken. A board wanting the i2cscope sniffer therefore needs a working `i2c:`
declaration regardless of what `logic:` becomes, and the two specs touch the same lines.

## Product ruling, part 2 (2026-09-05, product owner) — Q2, Q3, Q4 settled

The three questions left open above are now closed. Each is recorded with its reasoning, because a
ruling without its argument is re-litigated by the next reader who notices the same asymmetry.

### Q3 — bare `logic:` key, **no pin list**. Settled by mechanism/policy separation.

DuneOS follows the Unix mechanism/policy split as a stated design principle: the kernel provides
the mechanism, userspace decides the policy. **The capture pins are policy.** The application
picks them per capture, at run time, through `ioctl(LOGIC_SET_CONFIG)` — ADR 020
(`docs/adr/020-logic-capture-device.md:34-35`) states the contract as "pick the channels (a GPIO
per bit position, channel 0 = bit 0…)", and `apps/user/i2cscope/sniff.c:72-77` exercises it with
`.channel_gpio = { g_scl_pin, g_sda_pin }, .n_channels = 2`. Putting a pin list in `board.yaml`
would move that policy into build-time configuration, which is backwards.

The decisive supporting fact, verified against the tree: **`arch/xtensa_esp32s3/hal/hal_logic.c`
references no `board_config.h` macro at all.** It includes `duneos/hal_logic.h`, FreeRTOS,
`esp_cpu.h`, `esp_private/esp_clk.h`, `soc/soc.h` and `soc/gpio_reg.h`
(`arch/xtensa_esp32s3/hal/hal_logic.c:14-19`) and nothing else; `grep -c board_config` on it
returns **0**. The backend needs zero board-declared resources.

Therefore a declared pin list would be **validated by nothing and enforced by nothing**:
`logic_set_config()` (`kernel/duneos_kernel/src/drivers/logic/drv_logic.c:24-37`) accepts whatever
the ioctl passes, bounded only by `LOGIC_MAX_CHANNELS`
(`kernel/duneos_kernel/include/duneos/logic_ioctl.h:32`, = 8) on the driver side and by
`DUNEOS_HAL_LOGIC_MAX_CH` (`kernel/duneos_kernel/include/duneos/hal_logic.h:13`, also 8) on the HAL
side at `arch/xtensa_esp32s3/hal/hal_logic.c:52`. Nothing in either path could consult a board-level
list, and adding a consultation would be new policy in the kernel, not validation of existing
policy. A pin list would be decorative.

**A bare key is the whole declaration needed**, exactly like `wifi:`. Its only job is to mean
"compile the driver on this board".

### Q4 — **No.** `CONFIG_DUNEOS_DRV_GPIO` stays unconditional, and this spec must not touch it. Settled by the controller/function distinction.

Under Linux, `/dev/gpiochipN` is a **controller**: it exists if and only if the SoC has a GPIO
block, which every ESP32 does. It is neither optional nor board-declared — no device tree turns the
GPIO controller off, because there is no such thing as an ESP32 without one.

`/dev/logic0` is a **function layered on top of that controller** — an analyser built out of GPIO
reads, not a hardware peripheral of its own. `drv_logic.c` even goes through the GPIO HAL to arm its
channels (`duneos_hal_gpio_set_dir()` / `duneos_hal_gpio_set_pull()` at
`kernel/duneos_kernel/src/drivers/logic/drv_logic.c:30-33`), which is the dependency direction that
makes the distinction concrete: the function needs the controller, never the reverse.

**Controllers are platform; functions are opt-in.** That is precisely why `DRV_GPIO` unconditional
is correct while `DRV_LOGIC` unconditional is not, and it is the answer to the symmetry a reader
will notice in the emission block at `tools/duneos-bspgen.py:711-715`. The two symbols sit on the
same line of Python and belong to two different categories.

> **Correction to this spec's own Out of scope section, found while settling Q4.**
> The claim that `CONFIG_DUNEOS_DRV_GPIO` "has no contradicting Kconfig default" is **false**.
> `kernel/duneos_kernel/Kconfig:25-27` declares `config DUNEOS_DRV_GPIO` / `bool "GPIO chip
> (/dev/gpiochip0)"` / `default n` — the same `default n` as `DUNEOS_DRV_LOGIC` at
> `kernel/duneos_kernel/Kconfig:31-33`. GPIO therefore carries the *identical* bspgen-vs-Kconfig
> contradiction. The Q4 ruling above resolves the *policy* (GPIO is platform, stays unconditional in
> bspgen) but leaves the Kconfig default disagreeing with it; making them agree means
> `DUNEOS_DRV_GPIO` becomes `default y`. That is a one-line change in a file this spec was told not
> to touch, so it is recorded as the single residual open item below rather than absorbed here.

### Q2 — `m5stack-cardputer` only, today. Settled by the tree, not by philosophy.

The only in-tree consumer of `/dev/logic0` is `apps/user/i2cscope`. It resolves its two capture pins
at run time from `board.info` (`apps/user/i2cscope/i2cscope.c:109-110`,
`board_info_int("i2c0_scl", DEFAULT_SCL_PIN)` / `board_info_int("i2c0_sda", DEFAULT_SDA_PIN)`), i.e.
from the board's `i2c:` declaration as written into `board.info` by
`kernel/duneos_kernel/src/vfs.c:356-362`.

**Rule to record:** `logic:` goes on a board that **ships a capture application**, and it wants a
working `i2c:` on that board for the sniffer to resolve real pins instead of falling back. Today
that board is **`m5stack-cardputer` alone** — it is the only board carrying a display and keyboard,
hence the only one that can run `i2cscope` at all.

The **QEMU boards do not get it**: they declare no I2C, no capture test exists, and ADR 039's bench
scope is deliberately peripheral-free. Any other board gets `logic:` when it ships a capture app,
not before.

> **Finding that qualifies this rule — verified, and it contradicts the obvious phrasing.**
> `m5stack-cardputer` **does not declare `i2c:` today**: the block is commented out at
> `boards/m5stack-cardputer/board.yaml:65-69`, because the Grove HY2.0 port (GPIO1/2) was reassigned
> to UART1 for the HLK-LD2450 radar (PO decision 2026-08-09, `specs/SPEC-radar-ld2450.md`), as the
> comment at `boards/m5stack-cardputer/board.yaml:58-64` explains. The boards that *do* declare
> `i2c:` are `kincony-A16` (`board.yaml:17`), `lilygo-t-embed-cc1101` (`board.yaml:38`) and
> `esp32s3-devkitc` (`board.yaml:37`) — none of which ships a capture app.
>
> So a literal rule "`logic:` requires `i2c:` **and** a capture app" would select the **empty set**.
> The `i2c:` coupling is real but it is **not a precondition**: with no `i2c:`,
> `CONFIG_DUNEOS_DRV_I2C` is not emitted, the `board.info` block at `vfs.c:356-362` is compiled out,
> and `i2cscope` falls back to `DEFAULT_SCL_PIN` / `DEFAULT_SDA_PIN`
> (`apps/user/i2cscope/i2cscope.c:22-23`, GPIO1/2) — which on this board are now the radar's UART
> pins. The sniffer therefore still *builds and runs* on the cardputer; it just samples the wrong
> two lines until the `i2c:` block is uncommented.
>
> **The outcome stands (`m5stack-cardputer` only), the criterion is "ships a capture app".** The
> `i2c:` declaration is what makes that app *useful*, not what makes it *eligible*, and the
> implementer must not write a bspgen check that rejects `logic:` without `i2c:` — it would reject
> the one board the ruling selects.

## Open questions

1. ~~Feature or peripheral?~~ **Settled: peripheral.** Product ruling, part 1.
2. ~~Which boards declare `logic:`?~~ **Settled: `m5stack-cardputer` only.** Product ruling, part 2,
   Q2 — with the qualification that the criterion is "ships a capture app", not "declares `i2c:`".
3. ~~Bare key or pin list?~~ **Settled: bare key, no pin list.** Product ruling, part 2, Q3.
4. ~~Does `CONFIG_DUNEOS_DRV_GPIO` deserve the same ruling?~~ **Settled: no — GPIO is a controller,
   not a function, and stays unconditional.** Product ruling, part 2, Q4.

**One residual item, discovered while settling Q4 and deliberately left out of this spec's scope:**
`kernel/duneos_kernel/Kconfig:25-27` declares `DUNEOS_DRV_GPIO` as `default n` while
`tools/duneos-bspgen.py:714` emits it for every board — the same class of disagreement this spec
fixes for `LOGIC`, with the opposite correct resolution (`default y`, since Q4 rules GPIO is
platform). It is one line in a file this spec must not touch and it changes no board's build today,
since every board gets the fragment. It needs its own trivial spec, or a line in whichever spec next
opens `Kconfig`. Recorded here so the next audit finds the ruling instead of re-deriving it.
