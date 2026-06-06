# ADR 023 — App icon assets (freedesktop-style shared theme dir)

**Status:** Proposed · 2026-06-06

## Context

The launcher should show a per-app icon. The manifest already carries an
`icon:` string ([Phase 25.4](../../ROADMAP_v2.md)), `appmeta_t` exposes it, the
`.dr` raster format + `duneos_image_load_dr()` decoder exist, and `dbt img
convert png→dr` ships. What is undecided is *where the icon image lives and how
it reaches the device*.

A built-in glyph atlas linked into the launcher (Flipper-style named glyphs) was
considered and **rejected**: app authors want to ship their own logo with their
app, and a fixed atlas can't carry third-party / side-loaded app art. The
constraint is that the `.dap` must **not** carry the icon inside the binary —
that would cost exec/data-pool budget ([[ADR-008]]) for a pixel blob the kernel
never needs, and bloat every binary.

## Decision (proposed)

Adopt the **freedesktop Icon Theme model**, mapped to DuneOS. The icon is a
*name* in the manifest; the *file* lives in a shared directory installed at
flash time; the launcher resolves name→file via a search path. The `.dap` never
embeds it.

| freedesktop | DuneOS |
| --- | --- |
| `Icon=foo` in the `.desktop` | `icon: foo` in `duneos.yaml` (already parsed) |
| `/usr/share/icons/hicolor/<sz>/apps/foo.png` | `/flash/share/icons/foo.dr` |
| the package install step copies the icon there | `dbt system`/`flashimg` copies the app's icon into the sysbin image |
| XDG icon search path | launcher search order (below) |
| `hicolor` fallback theme | a few generic `.dr` icons shipped in the OS image |

### Concretely

- **Manifest:** `icon: <name>` — a name, not a path (matches freedesktop).
- **Source:** the app ships `icon.png` in its directory; `dbt build` renders it
  to `build/icon.dr` automatically (non-fatal if Pillow is absent — the icon is
  optional). A hand-authored `icon.dr` is also accepted. Standard launcher size:
  **32×32 RGB565 (≈ 2 KB)** — one size; the launcher owns it.
- **Install (flashed apps):** when `dbt system`/`flashimg` assembles the sysbin
  LittleFS image, it copies each included app's icon to
  `/flash/share/icons/<name>.dr` — the "package install drops icons into
  /usr/share/icons" step. Mirrors the existing `/etc/` verbatim-copy convention
  (Phase 25.5).
- **Side-loaded apps (SD):** the `.dr` travels next to the `.dap`
  (`/sd/apps/foo.dap` + `/sd/apps/foo.dr`); `dbt deploy` copies it alongside.
  Ships the logo with the app without bloating the binary.
- **Launcher resolution (search path, first hit wins):**
  1. `<dap_dir>/<dapbase>.dr` (adjacent — covers side-loaded apps)
  2. `/sd/share/icons/<name>.dr`
  3. `/flash/share/icons/<name>.dr`
  4. OS generic fallback `/flash/share/icons/application.dr`
  5. a drawn placeholder if even that is missing
- **OS fallback set:** the image ships a handful of generic `.dr` icons
  (`application.dr`, `folder.dr`, …) — freedesktop's `hicolor` fallback, as files
  not a linked atlas.

### Why this satisfies the constraints

- **Lean `.dap`:** the icon is a separate file decoded on demand into a small
  heap buffer by the launcher — zero exec/data-pool cost, no per-binary bloat.
- **Ship-with-app:** authors deliver their own logo (flashed → installed to the
  shared dir; side-loaded → adjacent on SD).
- **Linux-faithful** ([[ADR-019]]): name-based reference + shared theme dirs +
  search path is exactly the XDG model; knowledge and tooling transfer.

## Consequences

- `dbt` gains an icon-install step in the image builder (and `deploy` copies the
  adjacent `.dr` for SD apps). A small `dbt` change, no app-code change.
- The launcher gains icon resolution + `duneos_image_load_dr` + blit per row,
  with graceful fallback. Bounded work.
- Apps opt in by shipping `icon.png` (or `icon.dr`) + setting `icon:`; apps
  without one get the generic fallback — no breakage.
- Standard launcher icon size fixed at 32×32 RGB565; multi-size theming
  (freedesktop's `<size>/` dirs) is deferred — one size is enough for the
  launcher today.
- `dbt build` gains a non-fatal build-time `icon.png` → `build/icon.dr` step
  (`build_app_icon`), so devs never run `dbt img convert` by hand. **Done
  2026-06-06.**

## Cross-references

- [[ADR-019]] — Linux-faithful semantics (this is the XDG icon model).
- [[ADR-008]] — memory budget (why the icon stays out of the `.dap`).
- Phase 25.4 (`icon:` manifest field), 25.5 (`.dr` format, `/etc` verbatim-copy
  convention this install step mirrors).
