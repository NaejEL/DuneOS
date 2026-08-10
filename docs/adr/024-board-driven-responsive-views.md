# ADR 024 — Board-driven responsive app views

**Status:** Accepted · 2026-06-06

## Context

DuneOS apps are board-portable binaries: the same `.dap` should run on a
CardPuter (240×135), a T-Embed (320×170), a DevKit + arbitrary panel, etc.,
without changes. Screen geometry varies widely.

The data is already there:

- `boards/<board>/board_config.h` has `DUNEOS_DISPLAY_WIDTH/HEIGHT`.
- `/flash/board.info` exposes `width:` / `height:` at runtime (vfs.c), the
  declarative board-portability contract apps already read for I²C pins etc.
- `ui_size(ui, &w, &h)` returns the live screen size from the display driver.

What's missing is **discipline**: several apps hardcode pixel dimensions instead
of deriving them from the screen. The icon work surfaced it — the launcher
carried a fixed 32 px icon (too small on every board, no room left unused), and
`i2cscope` places its value column at a literal `x = 150`. Such constants look
fine on the CardPuter and break (clip, overflow, or waste space) on any other
panel — defeating the portable-binary goal.

## Decision

**App layout is derived from the runtime screen size, never hardcoded.** The
screen size comes from the board: `ui_size()` is the authoritative runtime
source; `/flash/board.info` (`width`/`height`) is its declarative mirror for
code that runs before/without opening the display, and for tooling. The two
agree by construction (both trace to `DUNEOS_DISPLAY_WIDTH/HEIGHT`).

Concretely, the convention apps follow:

- Fetch `sw`/`sh` once (`ui_size`), and express positions/sizes **relative to
  them** or to theme metrics (`bar_h`, the 8 px glyph cell, `theme->pad`).
  Centre with `sw/2`; size columns/grids as fractions of `sw`/`sh`; compute how
  many list rows fit from the available height.
- No literal pixel coordinate that assumes a specific panel. A constant is only
  acceptable when it is a true intrinsic (glyph is 8 px) — not a layout guess.
- Assets that have an intrinsic size (icons, [[ADR-023]]) are stored once and
  **scaled at render** to the screen-derived target, rather than authored
  per-board.

`board.info` stays the portability contract: any new geometry an app needs to
adapt (e.g. DPI, colour depth) is added there, mirroring `board_config.h`, so a
board declares its display once and every app adapts.

## Consequences

- A portable `.dap` lays out correctly on any panel — the contest demo can ship
  the same launcher/apps for CardPuter and T-Embed unchanged.
- An audit + fix pass over existing apps is required. Known hardcodes:
  - launcher icon size — now derived (focused 48 px, previews scaled); centring
    already uses `sw`/`sh`. ✅
  - `i2cscope` register/xfer value column at literal `x = 150` and a few row
    pitches — to migrate to `sw`-relative. ⏳
  - review `g_shell`, `gfx_demo`, `splash` for fixed coordinates.
- libui is the natural place to grow helpers that encode this, so apps get
  responsiveness for free. **Shipped 2026-06-06:** `ui_screen_w/h`,
  `ui_pct_w/h(ui, pct)`, and the `ui_carousel` widget (sizes its icons from its
  own height — grows with the screen). More widgets should follow the pattern.

## Cross-references

- [[ADR-023]] — app icons (stored at one size, scaled to the screen here).
- [[ADR-019]] — Linux-faithful / board-portable surface (`board.info` contract).
- [[ADR-015]] — declarative board-driven architecture (`board.info` generated
  from `board.yaml`).
