# ADR 026 — File-type associations ("open with")

**Status:** Proposed · 2026-06-07

## Context

The file explorer (`files`) opens a selected file. It has built-in viewers for
generic content — `ui_pager` (text), `ui_hexview` (binary), and a `.dr` image
blit. But specific types should open in a dedicated app: `.lua` in a Lua REPL,
`.png`/`.jpg` in an image viewer (once a decoder exists), `.scn` in `i2cscope`,
a `.dap` launched, etc.

Where does the file-type → app association live? Two shapes:

1. **In the file explorer's manifest** — a central extension→app table the
   explorer carries.
2. **In each app's manifest** — every app declares the types it opens; a
   resolver finds the handler.

## Decision (proposed)

**Apps declare the file types they open, in their own manifest. A resolver maps
a file to a handler and launches it.** This mirrors freedesktop `.desktop`
`MimeType=` / Android intent-filters, and matches DuneOS's already-decentralised,
declarative model (`capabilities:`, `icon:` are app-declared too — [[ADR-015]],
[[ADR-023]]).

Rejecting the central table (option 1): it couples the explorer to every app and
must be hand-edited for each new one — exactly what the icon/capability model
avoided.

### Manifest field

```yaml
opens: [".lua"]                 # lua REPL
opens: [".png", ".jpg", ".dr"]  # image viewer
opens: [".scn"]                 # i2cscope scenarios
```

A list of lower-cased extensions (a later refinement may add globs or real MIME
types; extensions are enough to start and map cleanly to FAT's 8.3 names).
Embedded in the manifest blob verbatim, ignored by the kernel ([[ADR-006]]).

### Resolution

A resolver — initially the explorer itself, later a shared `open` SDK helper or
a system service — scans candidate app dirs (`/sd/apps`, `/flash/bin`) reading
each `.dap`'s manifest with `libappmeta` (which already extracts manifest
fields), builds an `ext → app path` map, and on "open" launches the matching
app. Types no app claims fall back to the explorer's built-in viewers.

### Passing the file to the handler (sub-decision, to design)

The handler needs the target path. DuneOS apps are `app_main(void)` today, so
this needs one of:

- **Launch argument** — `duneos_supervisor_launch(path, argv)` threading an
  arg string the loader exposes to the app (closest to `execve`/`open`). Cleanest
  but touches the loader/supervisor ABI.
- **IPC** — launch the app, then `duneos_send()` the path to its mailbox. No ABI
  change, but the app must expect an opening message.

Recommendation: the launch-argument path (an `argv`-style single string), since
"open file X with app Y" is fundamentally `exec(Y, X)`. Tracked as the
implementation prerequisite for this ADR.

### Multiple handlers

If more than one app claims an extension, present a chooser (the carousel/list
already exist); absent UI, take the first match. A future `default` hint in the
manifest or a user override file (`/flash/etc/open/defaults`) can pin a choice.

## Consequences

- New file types "just work": ship an app declaring `opens:` and the explorer
  routes to it — no explorer change.
- Requires a small argument-passing mechanism (above) before it's usable for
  real handoff; until then the explorer's built-in viewers cover text/hex/.dr.
- `libappmeta` gains an `opens` field; the resolver is a few dozen lines reusable
  by any "open this" caller.

## Cross-references

- [[ADR-015]] — declarative, decentralised app metadata (this extends it).
- [[ADR-019]] — Linux-faithful (the freedesktop association model).
- [[ADR-023]] — app icons (same "declare in the app, resolve by scanning" shape).
- [[ADR-014]] — capability resolution (dbt-side analogue of matching needs to
  providers).
- [[ADR-006]] — manifest extensibility (unknown fields ignored).
