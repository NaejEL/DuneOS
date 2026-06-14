# ADR 033 — CLI coreutils conventions (shared option parsing, output, errors)

**Status:** Accepted · 2026-06-13

## Context

The `apps/system/bin/` tools grew one at a time, so they diverged: ~10 of them
each define their own `out`/`outf`/`outn`; option handling ranges from none, to
`strcmp(argv[i], "-l")`, to hand-rolled loops; error strings follow no single
shape; and every tool hardcodes `"\r\n"`. The result is inconsistent UX (some
flags work, most don't), duplicated code, and CRLF baked into apps that
shouldn't care about the terminal. See [docs/cli-audit-2026-06.md](../cli-audit-2026-06.md).

## Decision

One set of header-only helpers in
[bin_args.h](../../kernel/duneos_kernel/include/duneos/bin_args.h), and four
conventions every bin follows.

### 1. Option parsing — `duneos_bin_getopts`

```c
duneos_opts_t o;
if (duneos_bin_getopts(argc, argv, "lah", &o) != 0)   /* unknown flag */
    return duneos_bin_badopt("ls", o.bad, USAGE);
int long_fmt = duneos_opt(&o, 'l');
for (int i = o.argi; i < argc; i++) { /* operands */ }
```

Supports **bundled** flags (`-lah` == `-l -a -h`), `--` to end options, and
unknown-flag detection. Option *arguments* (`-n 5`) are read by hand from the
next `argv[]` by the few tools that need them — kept out of the shared parser to
keep it a dozen lines.

### 2. Output — bins emit `\n`, the backend owns line endings

Bins write plain `\n`. The shell sink translates (`\n → \r\n` on serial; the
g_shell textview needs no CR). No bin hardcodes `\r\n`. This is correct layering:
a tool doesn't know whether it's talking to a UART, a USB-CDC, or a framebuffer.
Relies on streaming output ([[ADR-032]]).

### 3. Errors — `duneos_bin_err(cmd, msg)` / `duneos_bin_badopt(cmd, bad, usage)`

Uniform `"<cmd>: <message>"`. (Errors go to `fd 1` today; [[ADR-034]] moves them
to `fd 2` once redirection exists, so a `>` does not capture diagnostics.)

### 4. Multi-operand by default

`rm a b c`, `cat f1 f2`, `mkdir d1 d2` loop over `argv[o.argi .. argc)`. A tool
that fails on one operand reports it and continues, exiting non-zero overall
(once exit codes are surfaced).

### Shared output helpers (Phase A)

`duneos_bin_out(s)` / `duneos_bin_outf(fmt, …)` will replace the ~10 private
copies, deleting ~150 lines. Added in `bin_args.h` alongside `getopts`.

## Consequences

- Every tool gains consistent flags and errors; adding the next flag is editing
  one `valid` string + one `if (duneos_opt(...))`.
- ~150 lines of duplicated I/O scaffolding removed.
- Apps stop knowing about CRLF — the same bin renders correctly in any shell.
- Helpers stay header-only in `bin_args.h`: no link dependency, no ABI surface,
  works for `/flash/bin` and `/sd/bin` alike.
- Constraint unchanged: captured bins use **stack** buffers (static BSS fails
  `api_read` bounds), free what they malloc, close what they open, never
  `duneos_exit`.

## Alternatives considered

- **A real `getopt()` from newlib.** Pulls global state (`optarg`/`optind`) and
  is heavier than the 12-line bundled-flag parser we need; our tools don't use
  option-arguments enough to justify it.
- **A `libbin.a` static lib.** A header in `bin_args.h` is simpler, already the
  established place for bin glue, and avoids a per-bin link step.
- **Leave CRLF in bins.** Rejected: bakes terminal assumptions into every tool
  and double-handles newlines now that the sink translates.

## Cross-references

- [[ADR-032]] — streaming output (the sink that does `\n → \r\n`).
- [[ADR-034]] — pipelines/redirection (fd 2 for diagnostics; operands vs flags).
- CLAUDE.md "Hard-Won Lessons" — captured-app contract, stack-only buffers.
