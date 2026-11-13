# ADR 042 — Input intents translate in the SDK, from a board topology the kernel serves

**Status:** Accepted · 2026-11-11

## Context

`apps/user/launcher/launcher.c` dropped every event that was not `INPUT_EV_KEY`. The
T-Embed's rotary reaches `/dev/input/event0` as `INPUT_EV_REL` / `REL_WHEEL`, and
nothing in `apps/` or `sdk/` consumed it, so the board rendered a launcher it
could not navigate. One `.dap` has to be navigable on a keyboard matrix, on a
rotary with two buttons, and later on a touchscreen, with no per-board source
edit — which display portability already achieves by querying geometry through
`ioctl(FB_GET_INFO)` at runtime.

## Decision

**Apps dispatch on nine intents, not on key codes.** `NONE, PREV, NEXT,
ALT_PREV, ALT_NEXT, VALIDATE, BACK, TEXT, ERASE` — derived from what the
`sdk/ui` widgets already switch on, not invented. 2-D continuous control
(`snake`, `tetris`, `sdk/game`) is not expressible here and keeps raw codes.

**Translation lives in an SDK library** (`sdk/input/libinput.c`), **driven by a
runtime query** (`ioctl(INPUT_GET_TOPOLOGY)`) whose map is generated into the
kernel's `board_config.h` — not into the app's `_board.h`, so the `.dap` stays
board-independent. The three rejected alternatives:

- **A translator daemon.** `drv_input.c` has one device node and no fan-out, so a
  daemon reading the ring steals events from the foreground app, and
  re-injecting through `INPUT_INJECT_EVENT` feeds the same ring.
- **A kernel translator.** Synthesising `KEY_UP` from `REL_WHEEL` inside
  `drv_input.c` would silently fix the nine apps that then read raw codes, but it
  destroys the rotary delta `ui_wave` needs and contradicts
  [ADR 019](019-linux-device-interface-semantics.md)'s evdev-faithful semantics.
- **Compile-time mapping per board.** That is the thing this replaces: it makes
  every GUI app board-specific, which is what `_board.h` is for and what a
  `.dap` must not be.

The kernel's only change is the query. The map is `static const`, never
allocated: the CardPuter runs 50-80 KiB free heap and a map the ioctl allocates
is a map the ioctl can fail to serve.

**A button declares `intent:`, and its key code is derived from it.** Replacing
`code: KEY_ENTER` with `intent: VALIDATE` is a breaking board-schema change, so
bspgen and boardgen both reject the old spelling by name rather than generating
an empty map. The derived code keeps the six unmigrated raw readers behaving
exactly as before on the T-Embed.

**Long-press is in v1** so that *vocabulary reachability* is a board-declaration
property now rather than an API change later. `reachable_intents()` computes what
a board's topology can express, and a gap is written into the generated header
("Intents NOT reachable on this board: …") and asserted by a test. The T-Embed
reaches everything but the second axis; that is reported, not silently
incomplete.

**Each pure core is a TU of its own, and each app lists what it links.** The
translation core and the on-screen keyboard state machine carry no fd, no
allocation, no FreeRTOS and no ESP-IDF, and are compiled host-side with
`-Wall -Wextra -Werror`; their rendering and fd halves are separate files. The
text-entry entry point reaches the keyboard only through a backend pointer the
app passes — no weak symbol, no registry — so an app that does not list
`ui_vkbd.c` links zero bytes of it. App code shares a 64 KiB exec pool, and a
board with a physical keyboard must not pay for one it cannot use. The mechanism
is documented in `<duneos/ui_vkbd.h>` and nowhere else.

`sdk/input/libinput.c` is deliberately **not** resolved by a `capabilities:`
token: `input` already means the producer side (the pin defines the scanning
daemons need), and per-app `sources:` is what makes the claim above verifiable
by symbol-table inspection rather than by argument.

## Consequences

**Accepted and documented:** six apps still read `/dev/input/event0` and switch
on `KEY_*` — bin/input, drview, files, g_shell, i2cscope, radar. Nine did before
this cycle; launcher, waves and wifi were migrated. They are unchanged and
keyboard-only — on a rotary board they appear frozen. Enumerated once, at the top
of `sdk/input/include/duneos/input.h`, and contradicted nowhere. (The spec's
out-of-scope line says eight and its context section says ten; both counted the
two producer-side opens. Six is the consumer count.) An app becomes portable by reading intents; no kernel
change will do it for them.

**A conflict, settled: the widget mapping moves, not the topology.** The first
implementation inverted waves on the CardPuter — `ui_wave_intent` sent the
primary pair (PREV/NEXT) to pan, so with `KEY_UP`→PREV the up arrow panned
where it used to zoom, against the CardPuter's long-standing LEFT/RIGHT = pan,
UP/DOWN = zoom. The Product Owner's ruling was to keep the CardPuter exactly, so
`ui_wave_intent` now sends **PREV/NEXT to zoom and ALT_PREV/ALT_NEXT to pan**:
the topology binds UP/DOWN to the primary pair and LEFT/RIGHT to ALT, so this
restores the keyboard behaviour without touching the shared map. Waves is the
one widget whose primary axis is not its selection — it is the zoom, by the
keyboard it was designed on.

**The rotary consequence, and the modifier that answers it.** On a board whose
only navigation is one wheel, the primary pair is all it reaches, so waves could
zoom but never pan. A double-click of VALIDATE toggles an ALT latch in
`libinput`'s translation core (`duneos_input_state_t.alt_latched`), and while it
is set the axis emits the ALT pair — the wheel pans. It is the rotary analogue
of the CardPuter's Fn/Shift latch: a modifier reached by a gesture the hardware
has. Armed only on a board that declares an axis (`topo->n_axes > 0`), so a
keyboard board, which already reaches ALT through its arrows, never latches and
its VALIDATE is only ever VALIDATE. The T-Embed's wheel therefore zooms at rest
and pans while latched; the Product Owner chose that default over its inverse to
keep the mechanism board-agnostic (no per-topology default-latch state).

`DUNEOS_ABI_VERSION` is unchanged: the new surface is an ioctl, and no kernel
export-table entry was added.
