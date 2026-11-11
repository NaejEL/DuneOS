# Input portability — intents, not key codes
Status: APPROVED

One `.dap` must be navigable on a CardPuter (keyboard matrix), a LilyGO T-Embed
(rotary + 2 buttons) and later an ESP32-P4 with a touchscreen, without per-board
source edits. Display portability already works this way — the chain discovers
geometry at runtime through `ioctl(FB_GET_INFO)` and no app hardcodes a
resolution. Input does not.

## Context

**The defect, observed on hardware 2026-11-11.** `apps/user/launcher/launcher.c:268-272`
drops everything that is not `INPUT_EV_KEY` and feeds `ev.code` to
`ui_carousel_key()`. The T-Embed's rotary reaches `/dev/input/event0` as
`INPUT_EV_REL` / `REL_WHEEL` (`enc_quadrature.c:25-31`). **No app and no SDK file
in the tree consumes `INPUT_EV_REL`** — the only reader is the diagnostic
`apps/system/bin/input/input.c:62`. The two T-Embed buttons work at all only
because `boards/lilygo-t-embed-cc1101/board.yaml:124-132` declares
`code: KEY_ENTER` / `code: KEY_ESC`: the board already expresses an intent as a
key code, which is the shape this cycle generalises.

**Current surface.** `input_ioctl.h` defines `input_event_t{time_ms,type,code,value}`,
`INPUT_EV_KEY`/`INPUT_EV_REL`, `REL_WHEEL`, `INPUT_VAL_PRESS/RELEASE/REPEAT`, and
one ioctl (`INPUT_INJECT_EVENT`). There is no capability query. `drv_input.c` is a
64-entry ring with **one** device node and no fan-out. Producers: `kb_iomatrix`
and `btn_gpio` are userspace daemons injecting via ioctl; the encoder is
kernel-side. No producer emits `INPUT_VAL_REPEAT`.

**Consumers of raw codes:** 15 `ui_*_key()` call sites across 10 app files, and
10 sites that `open("/dev/input/event0")` directly. `tools/dbt/capabilities.py:125-134`
already reserves a commented-out `input` capability pointing at
`{sdk}/input/libinput.c` — the directory does not exist.

## Product Owner decisions

1. **`intent:` replaces `code:` for buttons.** One source of truth per button.
   Touches `btn_gpio` and `boardgen.py`'s code tables, and that is the point:
   two fields describing one button is the defect class this repo spent a day
   removing.
2. **Text entry is proven by migrating `wifi`'s password prompt.** No new app.
   Demonstrated on the CardPuter with the physical keyboard idle, then on the
   T-Embed.
3. **`snake` and `tetris` leave the T-Embed profile now**, because a rotary and
   two buttons cannot play them. **And the manifest marker is kept as a design
   intent to record**: an app that needs a keyboard should stay *visible* in the
   launcher where it is installed, carry a warning icon, and remain launchable —
   not silently hidden. Recording it is in scope; implementing the marker and
   the launcher's rendering of it is not (its own cycle).
4. **Long-press is designed in v1.** Not because either board needs it, but
   because it makes *vocabulary reachability* a board-declaration property now
   rather than an API change later: a board must be able to state that it can
   reach every intent its apps need, and a one-button board can only do that
   with press duration.

## Scope

**Where the translation lives — an SDK library fed by a runtime board query.**

- A **daemon is excluded**: the event ring has one node and no fan-out, so a
  translator reading it steals events from the foreground app, and re-injecting
  through `INPUT_INJECT_EVENT` feeds the same ring.
- A **kernel translator is excluded**: synthesising `KEY_UP` from `REL_WHEEL`
  inside `drv_input.c` would silently fix the 10 raw readers but destroy the
  rotary delta `ui_wave` needs, and contradict ADR 019's evdev-faithful
  semantics.
- The kernel's **only** change is a capability/mapping query ioctl, with the map
  generated into the kernel's `board_config.h` — not the app's `_board.h`, so
  the `.dap` stays board-independent. Same reason display geometry went runtime.
- **Accepted and documented limitation**: an app that keeps reading the event
  device and switching on `KEY_*` is unchanged and stays keyboard-only.

**Intent vocabulary — derived from what the widgets dispatch on, not invented:**

| Widget | Needs |
| --- | --- |
| `ui_list` | PREV, NEXT, VALIDATE, BACK |
| `ui_carousel` | PREV, NEXT, VALIDATE, BACK |
| `ui_pager`, `ui_hexview` | PREV, NEXT |
| `ui_wave` | PREV/NEXT (pan) **and** ALT_PREV/ALT_NEXT (zoom) |
| `ui_input` | TEXT(char), PREV/NEXT, ERASE, VALIDATE, BACK |

→ `NONE, PREV, NEXT, ALT_PREV, ALT_NEXT, VALIDATE, BACK, TEXT, ERASE`. Nothing
else. `KEY_DELETE` and `KEY_TAB` have no intent and stay reachable through the
unchanged raw path. 2-D continuous control (snake/tetris, `sdk/game/`) is **not**
expressible here and is out of scope.

**Files**

- `input_ioctl.h` — add the capability/mapping query and its payload. Reserve
  the absolute-pointer event type and axis codes for a future touchscreen,
  documented as not implemented. Existing codes, types and `INPUT_INJECT_EVENT`
  unchanged.
- `drv_input.c` — serve the new ioctl from the generated map; `ENOTTY` otherwise.
  The map is `static const` data derived from `board_config.h`, never allocated.
- `tools/duneos-bspgen.py` — emit the input-topology map, including per-button
  long-press intent where declared.
- `boards/m5stack-cardputer/board.yaml`, `boards/lilygo-t-embed-cc1101/board.yaml`
  — declare topology: per-button `intent:` (replacing `code:`), optional
  `intent_long:` with a threshold, encoder axis + counts-per-step, and whether
  the board has a full keyboard. Boards declaring no input source stay untouched
  and must report an **explicitly empty** map.
- `apps/system/btn_gpio/`, `tools/dbt/boardgen.py` — follow the `code:` →
  `intent:` move (PO decision 1).
- `sdk/input/libinput.c` + `include/duneos/input.h` — new, two separable units:
  (a) a **pure** translation core (`input_event_t` + map + state → intent),
  host-compilable, no fd, no allocation; (b) a thin fd wrapper over it. The
  split exists so (a) is testable host-side.
- `sdk/ui/` — **additive**: each widget gains an intent entry point. The existing
  `ui_*_key()` functions keep their present behaviour so all 15 call sites keep
  compiling and behaving; where convenient they become wrappers.
- `sdk/ui/ui_vkbd.c` (new TU) — on-screen keyboard driven by
  PREV/NEXT/VALIDATE/BACK, caller-owned state, no `malloc`.
- A text-entry entry point resolving at runtime to the existing `ui_input_t`
  line (keyboard present) or the vkbd (no keyboard).
- Migrated this cycle: `apps/user/launcher/launcher.c` (the observed defect),
  `apps/user/waves/waves.c` (exercises `ui_list` + `ui_wave`'s second axis), and
  `apps/user/wifi`'s prompt (PO decision 2).
- `profiles/t-embed-gfx/profile.yaml` — drop `snake` and `tetris`.
- `docs/backlog.md` — record the keyboard-required manifest marker (PO 3).
- Tests, one ADR for the boundary decision, and `FLOOR` raised in the same
  commit.

**Relative motion, repeat, long-press**

- Rotary: accumulate the signed delta, emit one PREV/NEXT per declared
  counts-per-step, and clamp intents produced from one `read()` batch so a fast
  spin cannot flood a redraw loop.
- **No velocity acceleration in v1** — an 8-item carousel does not want
  skipping. The state machine takes `time_ms` as an input so it can be added
  later without an API change.
- Repeat: `PRESS` and `REPEAT` both yield an intent, `RELEASE` yields NONE.
- **Long-press is implemented** (PO 4): a button may declare `intent_long:` and
  a threshold in ms. The state machine resolves short vs long on release, or on
  threshold crossing where the board declares that, using `time_ms` only — no
  timer, no task.

## Acceptance criteria

1. Host suite for the translation core, over a map constructed in the test body:
   keyboard → `KEY_UP`→PREV, `KEY_DOWN`→NEXT, `KEY_ENTER`→VALIDATE,
   `KEY_ESC`→BACK, printable→TEXT, `KEY_BACKSPACE`→ERASE; rotary → `REL_WHEEL`
   +1→NEXT, −1→PREV; release→NONE; unmapped→NONE.
2. Same suite, relative-motion accumulator: with counts-per-step N, N−1 counts
   emit nothing, N emit one intent, a batch above the clamp emits no more than
   the clamp, and a direction reversal does not leak a stale partial count.
3. Same suite, second axis: a board declaring an ALT source maps it to
   ALT_PREV/ALT_NEXT; a board that declares none **never** produces them.
4. Same suite, long-press: a press released before the threshold yields the
   short intent, one held past it yields `intent_long`, a button with no
   `intent_long` declared yields the short intent whatever the duration, and no
   combination yields two intents for one press.
5. Host suite for the vkbd state machine: grid traversal wraps, VALIDATE on a
   glyph inserts it, erase/submit/cancel behave, and the buffer is never written
   past `cap−1`.
6. The translation core and the vkbd compile with `-Wall -Wextra -Werror` under
   the host `CC` with no ESP-IDF, no FreeRTOS and no fd dependency.
7. A pytest over a `board.yaml` **constructed in the test** (LEG-38: never read
   `board_config.h` or `sdkconfig.board`) asserts bspgen emits the expected map,
   and an explicitly empty map for a board declaring no input source.
8. A second pytest iterates every tracked `boards/*/board.yaml` and asserts each
   yields either a complete map or an explicit "no input source" verdict — never
   a partial map, never an exception.
9. **Vocabulary reachability** (PO 4's real purpose): a pytest asserts that for
   every board declaring input, the set of intents reachable from its declared
   topology is computable, and that a board whose apps need an intent it cannot
   reach is reported rather than silently incomplete.
10. `FLOOR` in `.github/workflows/ci.yml` raised to match the new count, same
    commit.
11. `dbt flash kernel --build-only` succeeds for `m5stack-cardputer`,
    `lilygo-t-embed-cc1101` **and** `esp32s3-qemu` (no input declared), with the
    input driver object still absent from the last.
12. Every migrated app builds for both profiles (`dbt system build`).
13. The 15 existing `ui_*_key()` call sites are unmodified or behaviour-identical,
    and the 10 raw readers unmodified except where this cycle migrates them.
14. `dbt qemu` still passes for both QEMU boards.
15. Flash/RAM cost in the PR: measured `.text`/`.data`/`.bss` delta of the
    migrated launcher `.dap`, and the delta for an app linking the text-entry
    entry point **without** listing the vkbd source — the latter must be **0
    bytes** of vkbd code, verified by symbol-table absence.
16. Neither the translation core nor the vkbd references `malloc`/`free`/`calloc`,
    verified by symbol-table inspection of a built `.dap`.
17. `DUNEOS_ABI_VERSION` unchanged — the new surface is an ioctl, not a
    symbol-table entry. If a symbol must be added, bump it and say so.
18. **Board-only, LEG-37 applies, both boards before merge:** on the T-Embed,
    rotating the knob moves the carousel in the matching direction, one notch =
    one item, knob press launches, side key returns. On the CardPuter,
    launcher/waves navigation and g_shell typing are unchanged.
19. **Board-only:** the text-entry entry point presents the vkbd on the T-Embed
    and the existing line editor on the CardPuter, both producing the string.

## Out of scope

Generated files (`board_config.h`, `sdkconfig.board`, `partitions.csv`,
`idf_target.txt`, per-app `_board.h`). Touchscreen implementation or pointer
hit-testing — type reservation and documentation only. 2-D game control:
`sdk/game/`, `snake`, `tetris` keep raw codes and are not made rotary-playable.
**Implementing** the keyboard-required manifest marker and the launcher's
rendering of it (recorded only, PO 3). `drv_input.c`'s ring, blocking read,
`select()` semantics, single node, fan-out or foreground routing.
`kb_iomatrix`/`btn_gpio` scanning, keymaps, modifiers, auto-repeat generation.
The declarative exit key and input router of `docs/backlog.md:25`. `abi.h`,
`duneos_api_t`, `symbols.c`. Phase 26/27 work. `/board.info` (ADR 015:99 demotes
it; the ioctl is the carrier). Migrating the remaining 8 raw readers.

## Risks

- **Boards, not CI, are the gate (LEG-37).** This changes what two physical
  boards link and adds call depth on the launcher's input path. The watchpoint
  is armed, but neither QEMU nor the host corpus sees a stack or heap regression
  on the CardPuter. Both boards run before merge.
- **IRAM exec pool.** App code shares 64 KiB. libinput + intent paths + vkbd
  land in every GUI app that lists them, and the launcher plus a running child
  are resident together. Criterion 15 exists for this.
- **Kernel heap** is ~50-80 KiB free on the CardPuter: the ioctl's map must be
  `static const`, never allocated.
- **Static buffers in captured apps** break `api_read` pointer validation
  (Hard-Won Lessons). The vkbd and translation state are caller-owned, never
  file-scope BSS.
- **Half-migration.** 10 raw readers remain keyboard-only. Documented once, at
  the top of `include/duneos/input.h`, and contradicted nowhere.
- **`DUNEOS_INPUT_DEV` is conditional** (`boardgen.py:93` emits it only when a
  board declares input), so the SDK must compile and behave with the define and
  the device both absent.
- **Encoder direction and notch count are hardware facts.** A/B swap or a 2- or
  4-counts-per-notch detent cannot be derived from YAML; a wrong
  counts-per-step makes navigation feel broken while every host test passes.
  Criterion 18 is the only check.
- **`code:` → `intent:` is a breaking board-schema change.** Only two boards
  declare buttons, but bspgen must reject the old spelling with a named error
  rather than silently emitting nothing.
- **Scope creep via the vocabulary.** Any intent beyond the nine derived above
  is a design change needing PO sign-off.

## Open questions

None.
