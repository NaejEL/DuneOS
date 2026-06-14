# ADR 034 — Shell pipelines & redirection (`|`, `>`, `>>`, `<`)

**Status:** Accepted (Model A) · 2026-06-13 — Model B (threaded streaming) deferred

## Implementation note (2026-06-13)

Shipped **Model A** in [shell_cmds.c](../../apps/system/shell_core/shell_cmds.c):
`run_pipeline` splits a statement on top-level `|`, runs each stage serially, and
feeds each stage's output file to the next. A stage's stdout goes to a sink — the
terminal (`stream_sink`), a `>`/`>>` file, or a `/tmp/.pipeN` scratch — via
`duneos_loader_run_captured_streamed` for bins and a redirectable `sh_emit` for
builtins. **Stdin is supplied as a trailing file argument** (our text tools all
take file args), which sidesteps fd-0 redirection entirely. Redirect/scratch fds
are forced ≥ 3 so they never collide with the loader reopening `/dev/shellpipe`
at fd 1. Glob (`*`/`?`/`[..]`) expansion landed alongside, in the tokenizer.
Model B and `$(...)` remain future work.

## Context

`exec_line` ([shell_cmds.c](../../apps/system/shell_core/shell_cmds.c)) splits a
line on spaces and runs exactly one command. There is no `|`, `>`, `>>`, `<`.
That caps the toolset: `grep`, `wc`, `sort`, `uniq`, `cut`, `tr`, `tee`, `head`,
`hexdump`, `find` are only worth shipping when their output can flow into one
another and into files (see [docs/cli-audit-2026-06.md](../cli-audit-2026-06.md),
Tier B). It also means no `cmd > file` to capture output and no `cmd < file` to
feed input.

Streaming captured output ([[ADR-032]]) already introduced the key primitive: a
command's stdout is a **sink** (`/dev/shellpipe` → callback), not a fixed device.
A pipeline is "make stage A's sink be stage B's stdin."

## Decision (proposed)

Teach the shell to parse and run pipelines and redirections, reusing the sink
mechanism and tmpfs, with **no MMU, no `fork`, no real `pipe()` kernel object**.

### Parsing

Split the line into pipeline **stages** on `|`, then per stage strip trailing
`> file` / `>> file` / `< file` redirections before arg tokenisation. Quoting
(`"…"`) is a prerequisite so filenames/patterns with spaces survive — fold in a
minimal quote pass here.

### Execution model (no fork)

Captured bins run synchronously in the shell's task and return when done, so a
classic concurrent pipe (both ends live) isn't available without threads. Two
viable models:

- **A. Serialized via tmpfs (recommended first).** Run stage 1 with its sink
  writing to a tmpfs scratch file; run stage 2 with stdin bound to that file and
  its sink to the next scratch file; …; the last stage's sink is the shell's real
  output (or the `>` file). Simple, deterministic, no concurrency; cost is each
  inter-stage buffer hits tmpfs (heap-backed, freed immediately after). Fine for
  the short outputs a shell pipeline actually moves.
- **B. True streaming via a stage thread.** Spawn stage 1 on its own task writing
  into a `xStreamBuffer`; stage 2 reads it as stdin. Unbounded + concurrent, but
  needs a per-stage task and the captured-app contract loosened. Defer until a
  pipeline genuinely needs liveness (e.g. `klog -f | grep`).

Start with **A**; it covers `ls | grep .dap`, `cat f | wc`. Add **B** only for
the follow case.

### Redirection

- `>` / `>>`: bind the final stage's sink to a tmpfs/SD/flash file opened
  `O_WRONLY|O_CREAT|O_TRUNC` / `O_APPEND` instead of the shell output.
- `<`: bind stage 1's stdin (fd 0) to the file. Requires bins to read stdin — a
  new convention (`cmd -` / no-operand reads fd 0).

### stderr

Reserve **fd 2** for diagnostics and route it to the shell output even when fd 1
is redirected, so `cmd > out.txt` doesn't bury error messages in the file. Bins
migrate `duneos_bin_err` to fd 2 ([[ADR-033]]).

## Consequences

- Unlocks the entire Tier-B text-processing toolset and output capture — the
  shell starts to feel like a real one.
- Model A is small and safe: it's a loop over stages with sink rebinding, reusing
  `/dev/shellpipe` and tmpfs; no new kernel object.
- Needs a quoting pass and a stdin-reading convention for bins — both modest but
  touch the parser and several tools.
- Model B (threaded streaming) is a later, opt-in addition for live follows.
- fd 2 routing is a small loader/devfs change (a second sink, or a fixed
  `/dev/klog`-style target).

## Alternatives considered

- **Status quo (one command per line).** Rejected: permanently caps the toolset;
  the user explicitly wants a modern shell.
- **Real `pipe()` + `fork()`.** No MMU, no process model; out of scope and
  unnecessary for a single-task captured-bin design.
- **Only redirection, no pipes.** Half the win; pipes are the bigger usability
  unlock and fall out of the same sink rebinding.

## Cross-references

- [[ADR-032]] — streaming output / `/dev/shellpipe` (the sink this builds on).
- [[ADR-033]] — CLI conventions (operands, stdin reading, fd 2 for errors).
- ADR 016 — captured-app exit (Model B would relax the captured contract).
