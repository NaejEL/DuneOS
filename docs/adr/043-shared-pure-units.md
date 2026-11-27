# ADR 043 — A compile unit may be shared by the kernel and userspace, and it lives in `lib/`

**Status:** Accepted · 2026-11-14

## Context

Two files parsed an ET_REL ELF32 header, walked the section header table and
located `.duneos_manifest`: `kernel/duneos_loader/src/elf_parse.c` and
`sdk/app/libappmeta.c`. The kernel side was extracted to be host-testable, is
covered by two host suites, a pinned malformed corpus and a libFuzzer entry
point, and was hardened three times (LEG-01, LEG-34, LEG-41). The SDK side had
hand-written field offsets, no bounds, no test and no fuzz target, and parsed
files that come off an SD card. Hardening it would have meant hardening the same
parser twice, forever.

Both were already pure C over libc with injected or local I/O, so one
implementation was reachable. What was not reachable was a **location**: the
kernel unit sat inside an ESP-IDF component, and `kernel/duneos_loader/include`
is on no app's include path; the SDK tree is built per app by `tools/dbt` and
the kernel does not consume `sdk/`. `third_party/` is reserved for git
submodules. So the shared unit had nowhere to live that both sides already
reached, and the duplication was a consequence of the tree's shape rather than
of anyone's choice.

## Decision

**A compile unit may be shared by the kernel and userspace, and such a unit
lives under `lib/<name>/`** — owned by neither side, named by both.

- **What it may depend on:** C17 and libc, and nothing else. No ESP-IDF, no
  FreeRTOS, no `soc/`, no cJSON, no kernel header, no `libdune`, no `sdk/`
  header. I/O is **injected** (a caller-supplied read callback plus the size of
  the backing store), never opened by the unit, so the kernel can back it with
  a `FILE *`, an app with an fd, and a host test with a memory buffer.
- **What it must not do:** log, allocate on anybody's behalf without saying so,
  or return anything but ADR 001's `int` — 0 on success, `-errno` on failure.
  It reports *which* check failed through an out-parameter and lets the caller
  own the diagnostic, because only the caller knows how to print one.
- **Its public header lives at `lib/<name>/include/duneos/`**, so both sides
  include it by the same `<duneos/…>` name they already use. The directory is
  what each side adds to its include path; no copy, no alias.
- **Each side compiles it in place.** The kernel's component names it in `SRCS`
  the way `duneos_loader` already names `third_party/cjson/cJSON.c`; an app
  names it in `duneos.yaml` under the `$ROOT/` source prefix `dbt` gained for
  exactly this, and `dbt` adds the unit's `include/` automatically. There is no
  static library, no archive to version, and no third build system: one source
  file, compiled twice with each side's own flags. That is also what keeps the
  unit honest — it is compiled with `-ffreestanding -nostdlib -Os` for apps and
  with ESP-IDF's kernel flags, and the host suite compiles it a third time
  under `-Wall -Wextra -Werror`.
- **It is host-tested, and both consumers are.** A unit shared by two sides with
  one test is a unit whose second consumer is untested; `tests/host` compiles
  the shipped source of the unit *and* of each consumer that is pure enough to
  link, never a copy.

**Rejected: keeping it in the kernel component and teaching `dbt` to reach
in.** Apps already consume one kernel include directory
(`kernel/duneos_kernel/include`, as `SDK_INCLUDE`), so the precedent existed
and it is the cheaper diff — the kernel's compile unit, its CMake entry, the
`tests/host` rules and the loader's measured frame table would all have been
untouched. It was rejected because it encodes the wrong ownership: a unit the
SDK is required to link is not the kernel's to move or to change. The
Product Owner chose the neutral location and accepted its cost, which is real:
the kernel's compile unit moves too, so the LEG-37 risk class (a change that
alters what a board links, or how deep it recurses) now sits on both sides, and
the move's diff had to be shown rename-pure to keep the loader's frame depth a
derivation rather than a hope.

**Rejected: `sdk/elf/`.** `$SDK/elf/elf_parse.c` works with zero tooling
change, and the kernel could pull it the way it pulls cJSON. But then the
loader's ELF reader belongs to the SDK, which is the same ownership mistake
pointing the other way, and it makes `sdk/` mean two things.

**Rejected: a second copy kept in sync by a test.** That is the debt this
closes, and the four recorded LEG-38 instances are what a synchronisation gate
is worth.

## Consequences

- `lib/elf/` is the first such unit: `elf_parse.c` plus
  `include/duneos/{elf,elf_parse,loader_limits}.h`. The kernel loader and
  `sdk/app/libappmeta.c` are its two consumers, and a pytest reading the git
  index keeps that count at two.
- A check either side had, both sides now have: `e_shentsize !=
  sizeof(elf32_shdr_t)` was enforced by the SDK and not by the kernel, so it
  moved **into** the shared unit with its own reject enumerator and corpus
  case. **A dedup must not drop a check either side had** — that is how this
  debt class is created in the first place.
- The cost is paid by the consumer with the smallest budget. The shared unit
  allocates the whole section header table where the hand-written reader read
  16 headers at a time, so the launcher's transient peak per file roughly
  doubles against its 8 KiB per-app pool (ADR 025). A unit that cannot be
  cheap on both sides is not a candidate for this ADR; this one is, and the
  figure was measured rather than assumed.
- `DUNEOS_ABI_VERSION` is unchanged. A shared compile unit is not an ABI
  surface: nothing here enters the export table, and no struct crossing the
  kernel/app boundary moved.
- The next shared unit does not re-litigate where it goes. Candidates already
  visible: the app-file extension filter (`has_dap_ext()` in `launcher.c`
  versus `duneos_loader_name_is_app()`), and `third_party/cjson`'s own
  kernel-only placement.
