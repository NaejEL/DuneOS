# ADR 037 — Shell scripting in shell_core (variables, expansion, sequencing)

**Status:** Accepted · 2026-06-13 — control flow added 2026-06-14

## Update (2026-06-14) — control flow + `source` landed

Control flow shipped: `if C; then …; [elif C; then …;] [else …;] fi`,
`for V in WORDS; do …; done` (WORDS expanded — vars + globs), and
`while C; do …; done` (with a runaway guard). A `source FILE` / `.` builtin runs
a script through the same engine. Implementation:
`run_script` splits input into `;`/newline statements (quote-aware, comments
skipped, inline `then`/`do`/`else` bodies split off), then a small recursive
interpreter (`ctrl_exec` over a statement array, `find_block_end` for matching
`fi`/`done` with nesting) executes them; `run_andor` handles `&&`/`||` within a
statement, below which sit the pipeline/assignment layers. Redirect filenames
are now variable-expanded too. Self-test: `boards/<board>/etc/selftest.sh`.

### Update (2026-06-14b) — substitution, arithmetic, break/continue

Added: command substitution `$(cmd)` (runs the command capturing stdout via a
`/tmp` scratch — re-enters the command machinery, protected by the loader's
captured-run **stack guard** so "too deep" is a graceful refusal, not a crash),
integer arithmetic `$((expr))` (`+ - * / %`, parens, vars — enables counting
loops `i=$((i+1))`), and `break`/`continue` in `for`/`while`.

Still out: `case`, and interactive multi-line block entry (a block must be one
logical line or live in a `source`d file — the statement engine supports
multi-line, only the continuation-prompt reader is pending). Line-continuation
`\<newline>` is also not handled.

## Context

`exec_line` ran exactly one space-split command per line: no variables, no
quoting, no `$?`, no `;`/`&&`/`||`. The CLI roadmap
([cli-audit-2026-06](../cli-audit-2026-06.md)) calls for a "modern shell" feel,
and light glue scripting is the natural complement to the new text tools
(`grep`/`sed`/`find`). A *full* language (data structures, richer control) is the
job of the PSRAM-gated interpreter ([[ADR-035]]), not the always-present shell —
so this is scoped to lightweight scripting that fits every board.

The shell is a spawned daemon with its own data pool, so static state (a small
variable table) is fine here — unlike captured bins.

## Decision

Add a scripting front-end to the shared dispatcher
([shell_cmds.c](../../apps/system/shell_core/shell_cmds.c)), reaching all three
shells (usb / uart / g_shell). **Implemented now:**

- **Variables.** `NAME=value` assignment (a standalone statement); a fixed table
  (`SH_MAXVARS`). `set` lists them.
- **Expansion.** `$NAME`, `${NAME}`, and `$?` (last exit status), in bare words
  and inside `"..."`. `'...'` is literal.
- **Quoting.** `'...'` (no expansion), `"..."` (expansion, no word-split), and
  `\` escapes.
- **Sequencing.** `;` (always), `&&` (on success), `||` (on failure), split at
  the top level respecting quotes, with short-circuit driven by `$?`.
- **`test` / `[ ]` builtin.** String (`-z -n = !=`), integer
  (`-eq -ne -lt -le -gt -ge`), and file (`-e -f -d`) tests — so conditions have a
  real exit status to branch on.
- **Exit status.** Builtins return status; a captured bin's status comes from
  `duneos_loader_get_captured_exit_code()`; "command not found" → 127.

**Deliberately deferred — control flow (`if`/`while`/`for`):** Proposed, next
step. It needs two things this foundation doesn't: a **statement reader** that
keeps consuming input until a block closes (`fi`/`done`) — interactive
continuation prompts on serial, multi-line accumulation in g_shell — and a small
**block interpreter** over the token stream (with `;`/newline statement
separation *inside* blocks, parsed *before* the `;` splitter runs). Shipping it
untested alongside the rest risked the shared shell; the foundation is the safe,
useful half and the prerequisite for it.

### Non-goals

- **Job control / background `&` / subshells.** Need a process model DuneOS does
  not have (no `fork`).
- **Post-expansion word splitting.** `$VAR` containing spaces stays one argument
  (safer; a deviation from POSIX noted in `help`).
- **Command substitution `$(...)`.** Feasible on the streaming sink ([[ADR-032]])
  — capture a command's output into a value — but folded into the control-flow
  phase, since both want the same statement-evaluation plumbing.

## Consequences

- The shell feels scriptable: `OUT=/sd/log; grep ERROR $OUT && echo found`.
- All three shells inherit it from one place; no per-backend parser.
- Foundation is bounded and low-risk (one tokenizer + a value table); control
  flow is isolated as a follow-up with a clear design.
- A scripting deviation (no `$VAR` splitting, assignment runs to end of
  statement) is documented in `help` to avoid surprise.

## Alternatives considered

- **Jump straight to full control flow.** Rejected for this pass: the multi-line
  reader + block parser is a sub-project; shipping it untested endangers the
  shared shell. Land the foundation, then build on it.
- **Push all scripting to MicroPython ([[ADR-035]]).** That's PSRAM-only; the
  always-present shell needs light glue on every board.
- **A separate `sh` scripting bin.** Duplicates the dispatcher; the shells
  already share `shell_cmds.c` — the natural home.

## Cross-references

- [[ADR-032]] — streaming output (enables future `$(...)`).
- [[ADR-034]] — pipelines (the other half of "modern shell"; shares the
  statement-evaluation plumbing with control flow).
- [[ADR-035]] — MicroPython (the heavyweight scripting path).
- [cli-audit-2026-06](../cli-audit-2026-06.md) — roadmap context.
