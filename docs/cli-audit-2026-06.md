# DuneOS CLI toolset — audit, plan & roadmap (2026-06)

Audit of the `apps/system/bin/` utilities, the gaps in each, the tools that are
missing, and a phased roadmap to a coherent, modern-feeling shell environment.
Companion ADRs: [032](adr/032-captured-output-streaming.md) (streaming output,
**implemented**), [033](adr/033-cli-coreutils-conventions.md) (shared
conventions), [034](adr/034-shell-pipelines-redirection.md) (pipes/redirection,
**proposed** — the keystone for text-processing tools).

## How a bin works today (baseline)

- A bin is an ET_REL `.dap` in `/flash/bin` or `/sd/bin`, run **captured** by the
  shell (same task, no `duneos_exit`). It reads `argc/argv/cwd` from
  `/tmp/.exec_args` via `duneos_bin_args()` ([bin_args.h](../kernel/duneos_kernel/include/duneos/bin_args.h)).
- Output: the app writes to `STDOUT_FILENO`; the shell now **streams** it live
  (ADR 032) — no size limit, so `cat` of a large file works.
- Just landed: `duneos_bin_getopts()` / `duneos_opt()` / `duneos_bin_err()` /
  `duneos_bin_badopt()` give every bin one bundled-flag parser (`-lah`) and one
  error/usage style (ADR 033). `ls` is the first consumer.

## Cross-cutting findings

1. **No pipes or redirection.** `exec_line` splits on spaces and runs one command.
   There is no `|`, `>`, `>>`, `<`. This is the single biggest limiter: `grep`,
   `wc`, `sort`, `head`, `tee`, `hexdump` are only worth shipping once their
   output can flow into/out of each other and files. ADR 034. Streaming (032) is
   the enabling primitive — a pipe is "sink of A = stdin of B".
2. **~10 bins each redefine `out`/`outf`/`outn`.** Pure duplication; should be a
   shared `duneos_bin_out*()` in `bin_args.h` (ADR 033). ~150 lines deleted.
3. **CRLF is hardcoded everywhere (`"\r\n"`).** The shell already translates
   `\n → \r\n` in `sh_write`. Bins should emit plain `\n`; the backend owns line
   endings (a serial shell needs CRLF, g_shell's textview does not). ADR 033.
4. **Single-argument tools.** `rm`, `cat`, `mkdir`, `mv` take exactly one/two
   operands — no `rm a b c`, no `cat f1 f2`. Trivial loop, big usability win.
5. **No `cp`.** We can `mv` (rename) but not copy — a surprising gap for a board
   that drags `.dap` files around between `/sd`, `/flash`, and USB-MSC.
6. **Errors go to stdout (fd 1), not stderr (fd 2).** Acceptable while there's no
   redirection, but once `>` exists, diagnostics must not land in the redirected
   file. ADR 034 reserves fd 2 routing.

## Per-bin audit

Legend: ✅ good · 🟡 works, gaps · 🔴 broken/missing.

| Bin | State | Notes / gaps |
|-----|-------|--------------|
| `cat` | 🟡 | Streams fine now. One file only; no `-n` (number), `-A`/`-v` (show non-print), no stdin (`cat -`). |
| `ls` | ✅ | `-l -a -h` + bundling done. Future: sort, `-R`, `-1`, columnar default, `-t/-S`. |
| `ifconfig` | ✅ | Loads on any board now (net-info stubs, ADR 032 sibling fix). Read-only; no `up/down`/static config. |
| `free` | 🟡 | Rich already. Add `-h` (human) and `-b/-k`; otherwise solid. |
| `ps` | ✅ | Per-app mem view. Could add `-- ` no-op for habit; fine. |
| `gpio` | ✅ | Subcommand UX, hand-parsed but clean. Could add `watch`/`mon`. |
| `input` | ✅ | Event dump; fine. |
| `klog` | 🟡 | Dumps the ring once. No `-f` (follow / `dmesg -w`), no `-n N`, no level filter. High value: `-f`. |
| `mkdir` | 🟡 | No `-p` (parents), single arg. |
| `mv` | 🟡 | Two args only; no `mv a b c/ ` into a dir. |
| `rm` | 🔴-ish | No `-r` (dir tree), no `-f` (ignore missing), single arg. Can't delete a directory at all today. |
| `ping` | ✅ | Good. Could honour `-c count` / `-i` to match habit (currently positional count). |
| `reboot` | ✅ | Minimal by design. |
| `restart` | 🟡 | Restarts a service; no companion `stop`/`kill`, no `start`. |
| `services` | ✅ | Clean listing. Pairs with a future `stop`/`start`. |
| `tail` | 🟡 | `-f` works (hand-parsed); but plain mode tails a fixed **512 bytes**, not `-n LINES`. No `-c`. |
| `battery` | ✅ | Two-backend read; fine. Add `-h`/loop. |

## Missing tools (proposed)

Grouped by how much new plumbing they need.

### Tier A — standalone, no new kernel/shell features (ship anytime)

| Tool | Why it matters on DuneOS |
|------|--------------------------|
| `cp` | Copy `.dap`/configs between `/sd`, `/flash`, USB-MSC. Closes the `mv`-but-no-copy gap. |
| `head` | First N lines/bytes — symmetry with `tail`. |
| `touch` | Create empty files / bump mtime (init markers, test fixtures). |
| `du` | Tree size of `/sd/apps`, `/flash` — see what's eating the LittleFS/FAT. |
| `df` | Free space per mount (FAT `/sd`, LittleFS `/flash`, tmpfs) — the storage analogue of `free`. |
| `stat` | File metadata (size, type, mtime) — debugging the VFS. |
| `wc` | Bytes/lines/words of a file (works standalone on a path; shines with pipes later). |
| `hexdump`/`xxd` | Inspect binaries, `/dev/*` registers, `.dap` headers. Pairs with `i2cscope`. |
| `uname` | Board/kernel/arch/ABI version — one-line "what am I running". |
| `date` | Show (and later set) the RTC; ambient clock already depends on it. |
| `sleep` | Delay — needed the moment init/scripts exist. |
| `sync` | Flush LittleFS/FAT before yanking the SD/USB. |
| `md5sum`/`sha1sum` | Verify a sideloaded `.dap` matches what was built. |
| `clear` | Already a g_shell builtin; promote to a bin (emit `\x1b[2J\x1b[H`) for serial. |
| `kill`/`stop` + `start` | Stop/start a service by name — completes `restart`/`services`. |

### Tier B — want pipes/redirection first (ADR 034)

| Tool | Note |
|------|------|
| `grep` | The headline tool. Filtering `klog -f \| grep ERROR`, `ls \| grep .dap`. |
| `sort`, `uniq`, `cut`, `tr`, `nl`, `tee` | Classic text pipeline; low value without `\|`. |
| `find` | Tree walk + predicate; useful standalone too but mostly feeds `xargs`/pipes. |
| `xargs` | Needs pipes + arg streaming. |

### Tier C — block / device I/O

| Tool | Note |
|------|------|
| `dd` | Block copy with `if/of/bs/count` — image a partition, dump a `/dev` device, write a raw blob. Genuinely useful for flashing/forensics; bounded scope. |
| `mount`/`umount` | Today mounts are fixed (flash + SD). Becomes real when removable/extra FS land. |

## Roadmap

Phased so each step ships something usable and de-risks the next.

**Phase A — conventions + cleanup (no new features).** Adopt ADR 033 across all
17 bins: shared `out/outf`, `\n` (not `\r\n`), shared `getopts`. Multi-operand
`rm`/`cat`/`mkdir`/`mv`. `rm -rf`, `mkdir -p`, `tail -n N`, `klog -f`. Pure
userspace; only a sysbin reflash. *Biggest UX/consistency win per hour.*

**Phase B — Tier-A new tools.** `cp`, `head`, `touch`, `du`, `df`, `stat`,
`uname`, `date`, `sleep`, `sync`, `wc`, `hexdump`, `clear`, `stop`/`start`. Each
is small and independent; add to the contest profile's `apps_flash`/SD set as
they land.

**Phase C — pipes & redirection (ADR 034).** Teach `exec_line` to parse `|`,
`>`, `>>`, `<`; route stdin/stdout through `/dev/shellpipe`-style sinks + tmpfs
files; reserve fd 2 for diagnostics. Unlocks Tier B.

**Phase D — Tier-B text tools.** `grep`, `wc` (pipe mode), `sort`, `uniq`, `cut`,
`tr`, `nl`, `tee`, `find`.

**Phase E — Tier-C device I/O.** `dd`, and `mount`/`umount` if/when removable
storage arrives.

## Constraints to respect (from CLAUDE.md / hard-won lessons)

- Captured bins run in the **shell's task**: stack buffers only (static BSS fails
  `api_read` bounds), free what you malloc, close what you open, never
  `duneos_exit`.
- Keep each bin tiny — they share the shell's ~8 KiB stack and the global heap.
- `.dap` size matters for the 1 MiB sysbin; the launchable set lives on SD, only
  the essential tools go in `apps_flash`.
- New shared helpers belong in `bin_args.h` (header-only, no link deps).
