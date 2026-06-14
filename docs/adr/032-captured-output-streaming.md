# ADR 032 — Streaming captured-app output (no spool, no whole-output buffer)

**Status:** Accepted · 2026-06-13

## Context

The shell runs CLI tools *captured*: same task, output redirected so the shell
can render it (serial: CRLF to the CDC; g_shell: wrapped into a textview). The
original mechanism ([loader.c](../../kernel/duneos_loader/src/loader.c)
`duneos_loader_run_captured`) spooled the app's `fd 1` to a `/tmp` file, then
after the app exited read the whole file back into one `malloc(size + 1)` and
handed it to the shell, which `sh_out`'d it (doubling again for `\n → \r\n`).

That costs **~4× the output size in heap, transiently**: the tmpfs file +
the readback buffer + the CRLF-expanded copy. On the no-PSRAM CardPuter
(~50–80 KiB free heap) `cat` of even a modest file truncated or OOM'd — the
user's "cat est limité en nombre de caractères de sorties". It also delays all
output to *after* the command finishes, and forbids unbounded producers (a
`-f` follow tool could never return its buffer).

## Decision

**The shell registers a sink; the loader points the captured app's `fd 1` at it;
every `write()` the app makes is delivered to the sink as it happens.** No spool,
no read-back, no whole-output buffer.

- New kernel char device **`/dev/shellpipe`**
  ([drv_shellpipe.c](../../kernel/duneos_kernel/src/drivers/drv_shellpipe.c)): a
  write-only sink device whose `write()` forwards to a registered callback.
  `duneos_shellpipe_set_sink(fn, ctx)` (header
  [shellpipe.h](../../kernel/duneos_kernel/include/duneos/shellpipe.h)).
- New loader entry **`duneos_loader_run_captured_streamed(app, sink, ctx)`**:
  same setjmp/exit semantics as the buffered path (ADR 016), but instead of a
  `/tmp` file it registers the sink and opens `/dev/shellpipe` at fd 1. Exported
  in the symbol table for the shells.
- Each shell backend provides `sh_write(data, len)` and passes it (via
  `stream_sink`) as the sink:
  - **serial** (`shell_core.c`): translate `\n → \r\n` into a small stack window,
    `raw_write` to the CDC. `sh_out` now routes through `sh_write` too — the old
    `malloc(2×len)` path is gone.
  - **g_shell**: a persistent row accumulator (`sh_write`/`sh_flush`) wraps the
    streamed chunks into the scrollback across arbitrary chunk boundaries; the
    submit handler flushes the trailing partial row.

The sink runs in the shell's own task (captured apps share it), and
`devfs_write` holds no lock, so the sink may safely re-enter the VFS to write to
the console or render.

## Consequences

- **Output is unbounded by heap.** `cat` of a large file streams in 256-byte
  reads straight to the terminal; peak extra RAM is one ~128-byte stack window.
- A captured tool can stream indefinitely before exiting (enables `tail -f`,
  `klog -f` in captured mode).
- Output appears **as it is produced**, not all at once at the end.
- One missing-sink safety: with no sink set, `/dev/shellpipe` discards writes and
  reports success, so an app that prints with no shell attached never blocks.
- The buffered `duneos_loader_run_captured` stays for now (no other caller), but
  the shells no longer use it; it can be retired later.
- **Keystone for pipes** ([[ADR-034]]): a pipeline is just "sink of stage A =
  stdin of stage B" — the sink indirection generalises directly.

## Alternatives considered

- **Keep buffered, chunk the read-back / `sh_out`.** Smaller change, but still
  spools the whole output to tmpfs first (bounded by heap) and still delays
  output to end-of-command. Rejected: doesn't lift the limit, only raises it.
- **`dup2` the shell's device fd onto fd 1.** Works for the serial shell (fd 1 →
  CDC) but g_shell has *no* output fd — it renders to a display. A sink callback
  is the one abstraction that fits both backends.
- **A real pipe()/FIFO.** No pipe primitive in DuneOS yet, and a single in-task
  sink callback is far simpler than a buffered FIFO with two ends.

## Cross-references

- ADR 016 — captured-app exit semantics (the setjmp/longjmp checkpoint reused
  here unchanged).
- [[ADR-033]] — CLI conventions (bins emit `\n`, the backend owns CRLF — relies
  on the sink doing translation).
- [[ADR-034]] — shell pipelines/redirection (builds the sink into a pipe).
