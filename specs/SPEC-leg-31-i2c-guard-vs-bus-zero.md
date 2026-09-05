Status: PROPOSED

# SPEC-leg-31 — `CONFIG_DUNEOS_DRV_I2C` means "an I2C section exists", but the code it gates means "bus 0 is declared"

## Context

Found by audit while closing the QEMU bench (SPEC-leg-28/29/30). It is **latent**: no board in
the repo instantiates it today, which is the only reason it has never fired.

The generator gates the I2C driver on the mere presence of the section:

```python
tools/duneos-bspgen.py:717        if board.get("i2c"):
tools/duneos-bspgen.py:718            lines += ["CONFIG_DUNEOS_DRV_I2C=y", ""]
```

That tests the **presence** of an `i2c:` list, not **which bus IDs it contains**. The pin macros,
by contrast, are emitted strictly per declared bus, named after the bus ID:

```python
tools/duneos-bspgen.py:264-275    i2c_buses = board.get("i2c", [])
                                  for i2c in i2c_buses:
                                      iid = i2c["id"]
                                      pfx = f"DUNEOS_I2C{iid}"
                                      … {pfx}_SDA_PIN / {pfx}_SCL_PIN / {pfx}_FREQ_HZ
```

And the kernel compiles code under the driver symbol that dereferences **bus 0 by name**,
unconditionally:

```c
kernel/duneos_kernel/src/vfs.c:356-362
    #ifdef CONFIG_DUNEOS_DRV_I2C
        /* I2C0 pins, so apps (e.g. i2cscope's bus sniffer) can find SCL/SDA at
           runtime … */
            (int)DUNEOS_I2C0_SCL_PIN, (int)DUNEOS_I2C0_SDA_PIN);
```

So a `board.yaml` declaring

```yaml
i2c:
  - id: 1
    sda_pin: 8
    scl_pin: 9
```

— a perfectly legal section with no bus 0 — emits `CONFIG_DUNEOS_DRV_I2C=y`, defines only
`DUNEOS_I2C1_SDA_PIN` / `DUNEOS_I2C1_SCL_PIN` / `DUNEOS_I2C1_FREQ_HZ` in `board_config.h`, and
**fails to compile** `vfs.c` on undefined `DUNEOS_I2C0_SCL_PIN` / `DUNEOS_I2C0_SDA_PIN`. It is
reproducible from a clean tree with no stale `sdkconfig` involved: bspgen, then `idf.py build`.

The general shape is worth naming, because it is the same defect class SPEC-leg-28 and
SPEC-leg-32 belong to and it will recur on the next per-instance peripheral (SPI emits a
`DUNEOS_SPI{idx}_*` block of the same shape at `tools/duneos-bspgen.py:250-261` — macros at
`252-256`, `DUNEOS_SPI{idx}_BUS_SHARED` at `260`):

> **A guard whose meaning is "the driver is compiled" is used to gate code whose meaning is
> "instance N is declared."** The two are only accidentally equal, and only while every board
> happens to number its first bus 0.

### Why SPI cannot exhibit this bug — finding, closes Open question 3

Verified against the current tree, and it is the single most useful thing this spec has to say
about the general shape.

```python
tools/duneos-bspgen.py:247    for idx, bus in enumerate(raw_buses, start=1):
tools/duneos-bspgen.py:248        spi_id = bus["id"]
tools/duneos-bspgen.py:252        … _define(f"DUNEOS_SPI{idx}_HOST", …)
```

SPI's index is `idx`, a **dense 1-based counter** produced by `enumerate(raw_buses, start=1)` — it
is not the YAML id (`bus["id"]` is kept separately in `spi_id` and used only to pick the host enum
and to detect SD sharing). Consequently `DUNEOS_SPI1_*` **always exists whenever any raw bus
exists**, whatever ids the board file uses. `boards/m5stack-cardputer/board.yaml:45` declares
`spi: - id: 3` with `role: raw` and gets `DUNEOS_SPI1_*`, which is precisely why its comment at
`board.yaml:34-35` says "exposed as `/dev/spi-1` (first raw bus, indexed from 1)".

I2C does the opposite:

```python
tools/duneos-bspgen.py:267    for i2c in i2c_buses:
tools/duneos-bspgen.py:268        iid = i2c["id"]
tools/duneos-bspgen.py:269        pfx = f"DUNEOS_I2C{iid}"
```

`iid` is the **raw YAML id**. That single line is where the whole mismatch comes from: the macro
name is chosen by the board author, while the consumer at `kernel/duneos_kernel/src/vfs.c:362`
hardcodes `0`.

**Generalisation to record, because it is the reusable rule:** *an index macro family must be
emitted from a dense counter, or the guard that gates its consumer must name the instance.* SPI
already satisfies the first half — **by accident, not by design**, since nothing in the generator
or in a test states that `idx` must stay dense. Whoever fixes I2C should note that invariant next
to the SPI loop, or the next refactor that "simplifies" `enumerate(…)` into `bus["id"]` reintroduces
the same defect one peripheral over.

### Coupling with SPEC-leg-32

`apps/user/i2cscope` resolves its two capture pins at run time from `board.info`
(`apps/user/i2cscope/i2cscope.c:109-110`, `board_info_int("i2c0_scl", DEFAULT_SCL_PIN)` /
`board_info_int("i2c0_sda", DEFAULT_SDA_PIN)`) — and `board.info` is written by the very lines this
spec fixes, `kernel/duneos_kernel/src/vfs.c:356-362`. `i2cscope` is also the only in-tree consumer
of `/dev/logic0`, which is what SPEC-leg-32 decides the fate of. **The two specs touch the same
code**: whichever lands second must re-read the other's changes to `vfs.c:356-362` rather than
assume the block it saw at drafting time. Criterion 2 below (byte-for-byte `/dev` introspection
output on `m5stack-cardputer`) is the shared guard.

## Scope

Make the guard and the code agree on what they assert, for I2C. Whichever direction is taken, the
result must be that a board declaring any legal set of bus IDs either compiles, or is rejected by
bspgen with a diagnostic naming the board and the missing bus — never a compile error inside
`vfs.c` that points at the kernel instead of at the board file.

Two directions, **recorded rather than chosen**:

1. **Emit per-instance symbols.** bspgen emits a per-bus symbol (e.g. `DUNEOS_I2C0_PRESENT`, or a
   `CONFIG_DUNEOS_DRV_I2C{iid}=y` family) alongside the pin macros, and the code guards its bus-0
   dereference on the symbol that actually means "bus 0 is declared". Truthful, and it generalises
   to SPI; costs a schema/Kconfig addition and a new symbol per bus.
2. **Make the code discover buses instead of assuming bus 0.** The `/dev` introspection text in
   `vfs.c` iterates whatever buses `board_config.h` declares (an X-macro list, in the shape
   `tools/duneos-bspgen.py:432` already uses for GPIO expanders) rather than naming `I2C0`. No new
   Kconfig symbol; the board file stays the only place bus IDs are written.

Direction 2 is the better fit for the existing generator idiom; direction 1 is the smaller diff.
The choice belongs to whoever takes the work, and it should be made for SPI at the same time or
explicitly deferred with a reason.

## Acceptance criteria

1. A throwaway board declaring `i2c: [{id: 1, sda_pin: …, scl_pin: …}]` and nothing else builds
   clean (`idf.py build`, no new warning) — or is rejected by `tools/duneos-bspgen.py` with a
   message naming the board and the unsatisfied requirement. A silent success that then fails at
   compile time does **not** satisfy this criterion.
2. Every board that declares bus 0 keeps exactly the `/dev` introspection output it has today, byte
   for byte, and its generated files change only as criterion 4 allows. **Those boards are
   `esp32s3-devkitc` (`board.yaml:37-41`), `lilygo-t-embed-cc1101` (`board.yaml:38-42`) and
   `kincony-A16` (`board.yaml:17-21`) — not `m5stack-cardputer`**, whose `i2c:` block is commented
   out at `boards/m5stack-cardputer/board.yaml:65-69` (the Grove port is the LD2450 radar's UART1,
   PO decision 2026-08-09). The cardputer emits neither `CONFIG_DUNEOS_DRV_I2C` nor any
   `DUNEOS_I2C0_*` macro today, so it exercises the `#else` of `vfs.c:356` and proves nothing here.
3. `python tools/dbt.py qemu --board esp32s3-qemu` and `--board esp32s3-qemu-psram` both still
   exit **0** with all five assertions matched.
4. Every generated file reaches its new content through `board.yaml` or
   `tools/duneos-bspgen.py`; no generated file is hand-edited, and all boards regenerate with only
   the intended diff.
5. A pytest case in `tools/dbt/tests` covers the id-1-without-id-0 board at the generator level:
   whatever the chosen direction emits (or the error it raises) is asserted, so the latent case
   stops being latent. `./tools/.dbt-venv/bin/python -m pytest tools/dbt/tests -q` passes.
6. Every existing board still builds with no new warning after a fullclean
   (`m5stack-cardputer`, `esp32s3-devkitc`, `lilygo-t-embed-cc1101`, both QEMU boards).

## Out of scope

- `CONFIG_DUNEOS_DRV_LOGIC`'s unconditional emission — SPEC-leg-32. Same defect family, different
  question (it needs a product ruling; this one does not).
- The REQUIRES-level over-declaration — SPEC-leg-33. (`main/main.c:72`'s stale comment moved to
  SPEC-leg-20's documentation-coherence batch.)
- Any change to `i2c_bus.c`, `drv_i2c.c` or `hal_i2c.c` behaviour. This spec is about which
  symbols are emitted and what the code may assume from them, not about how I2C transfers work.
- Renumbering or reordering any existing board's buses.
- Applying the fix to SPI. **Deferred with a reason rather than left implicit: SPI has no defect to
  fix** — its index macros come from a dense counter (Open question 3, closed). What SPI wants is a
  one-line comment at `tools/duneos-bspgen.py:247` recording that `idx` must stay dense; adding it
  is welcome here, and anything larger is not.

## Risks

- **`vfs.c` is on every board's boot path.** The `/dev` introspection text it produces is read by
  userspace tooling (`i2cscope`'s bus sniffer is named in the comment at
  `kernel/duneos_kernel/src/vfs.c:357`). Changing its shape — not just its guard — silently breaks
  a consumer that no build catches. Criterion 2 exists for this.
- **The bug is invisible until someone writes the board that triggers it.** Without criterion 5's
  test, a fix can regress at any time and nothing in the repo notices, exactly as nothing noticed
  for the life of the current code.
- **A new `CONFIG_DUNEOS_DRV_I2C{n}` family is a Kconfig surface that grows per bus.** Direction 1
  is the smaller diff today and the larger maintenance surface later; weigh it against direction 2
  rather than picking on diff size.
- **Not an ABI change** — no exported symbol or ABI struct layout moves, so no
  `DUNEOS_ABI_VERSION` bump is expected. If the chosen direction changes anything in `abi.h`, that
  assumption is void and the bump is mandatory.

## Open questions

1. Direction 1 (per-instance symbols) or direction 2 (code discovers buses)? See Scope.
2. Should a board declaring an I2C bus that is not 0 be **supported** or **rejected**? Supporting
   it is more work; rejecting it with a clear bspgen error is legitimate if bus 0 is a deliberate
   platform invariant — but then that invariant must be written down and enforced at generation
   time, not discovered at compile time.
3. ~~Does SPI have a live consumer that dereferences a fixed index the same way?~~ **Closed by a
   finding: SPI cannot exhibit this bug at all.** `tools/duneos-bspgen.py:247` emits the
   `DUNEOS_SPI{idx}_*` family from `enumerate(raw_buses, start=1)` — a dense 1-based counter — so
   `DUNEOS_SPI1_*` exists whenever any raw bus does, whatever ids the board file uses. I2C's
   `iid = i2c["id"]` at `tools/duneos-bspgen.py:268` is the raw YAML id, and that is exactly where
   the mismatch comes from. See "Why SPI cannot exhibit this bug" in Context for the generalisation
   (dense counter, or name the instance in the guard) and for the warning that SPI gets this right
   by accident and nothing protects the invariant.
