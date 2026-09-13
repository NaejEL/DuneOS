# LEG-17/18/19/21 — Make the gates cover, not just report honestly
Status: APPROVED

Supersedes `SPEC-leg-17-realign-adr-012.md`, `SPEC-leg-18-remove-yaml-parser-copy.md`,
`SPEC-leg-19-pure-logic-tests.md`. LEG-39 was already merged into LEG-17.
One branch, one cycle.

## Context

The two previous cycles made the gates *honest* — a green run can no longer mean
nothing ran (read guard in `conftest.py`, ran-count `FLOOR`, the self-proof step).
This one makes them **cover**.

**LEG-18 is the honesty defect in a third form: the wrong object under test.**
`tests/host/test_known_yaml.c` compiles a *copy* of the wifi_daemon parser, so the
suite is green by construction whatever the shipped parser does. The copy has
already diverged from `wifi_daemon.c:74-124`:

- Signatures differ; the header's "byte-identical" claim is false today.
- **On `open()` failure the shipped parser returns `-1`, the copy returns `0`.**
  That value is what `load_known()` (`:174-177`) uses to fall back from
  `/data/wifi/known.yaml` to the `/etc` seed. The copy cannot express the bug: a
  regression to `0` keeps the suite green while first-boot WiFi silently stops
  seeding.
- `merge_legacy()` (`:133-168`) is not covered at all.

**Three hand-rolled parsers of the same grammar must agree, and do not.**
`wifi_daemon.c`, `apps/system/bin/iw/iw.c:157-197` (the *writer*), and
`apps/user/wifi/wifi.c:118-171`. On an empty `- ssid:` the daemon drops the entry
and `iw` keeps it; `iw`/`wifi` trim space/tab/CR where the daemon uses `isspace()`.
`iw` writes the file the daemon reads — that is a "network saved but never joined"
in waiting. `known_yaml_cases.h:5-7` already admits the three-way duplication.

Fix cost is low: the parser needs only `<ctype.h> <fcntl.h> <string.h> <unistd.h>`;
`tests/host/Makefile:37-41` already compiles shipped sources directly
(`sdk/re/re.c`, `sdk/proto/i2c_decode.c`); `builder.py:154-165` supports
`sources: [$SDK/...]`, so a shared `sdk/` unit links into all three apps with no
capability-map change and no kernel edit.

**LEG-17 (LEG-39 merged).** ADR 012 (Accepted) prescribes Greatest, a `dbt test`
verb and `docs/testing.md`. What exists: `tassert.h` (41 lines, hand-rolled),
**6** suites — the ROADMAP row says 5, it predates `test_manifest_depth` — plus a
libFuzzer target, driven by `make`. No `dbt test` verb, no `docs/testing.md`.

**The live contradiction, and its cause.** `CONTRIBUTING.md:31-47`, added
yesterday, tells a newcomer to run four gates. Two cannot run on a fresh clone:
`pytest==9.1.1` is pinned in `tools/constraints.txt` but **absent from `_DEPS` in
`tools/dbt.py:27-34`**, so the venv dbt bootstraps lacks it; and
`tests/host/Makefile:73-89` deliberately keeps the fuzz target off `all`/`test`
because clang may be missing (LEG-39 measured that `esp-clang` cannot substitute —
Xtensa/RISC-V backends only, no host `libclang_rt.fuzzer`).

**LEG-21 is closed on substance.** `README.md:341-344` links `CONTRIBUTING.md`,
which names all four commands, and `test_repo_dependencies.py:261-270` enforces it.
Only `ROADMAP.md:710` is stale, and its text is wrong twice (predates
`CONTRIBUTING.md`; cites a pytest invocation `pytest.ini` has superseded).

## Product Owner decisions

1. **Supersede ADR 012's Greatest clause.** Keep `tassert.h`; ship the `dbt test`
   and `docs/testing.md` half. Record the deviation as ADR 041 superseding ADR 012
   clause by clause, per `docs/adr/000-process.md:37`; ADR 012's status line points
   at it. Rationale: every property ADR 012 wanted from Greatest (header-only,
   host+target, tiny) is met by the 41-line `tassert.h`, and ADR 012 rejected
   custom macros as a time sink — a sink already paid.
2. **`dbt test` exits non-zero when a gate is merely unavailable**, with a code
   distinct from "a gate failed". `--allow-missing` downgrades to a warning and
   exit 0. This is the honesty principle the previous cycles installed.
3. **QEMU is opt-in (`--qemu`).** It is the only gate needing a board switch and a
   full clean; default-on would report unavailable on every cardputer run.
4. **Empty `- ssid:` follows the daemon: drop the entry.** It is what the fixture
   table already pins.

## Scope

**A. LEG-18 — put the shipped parser under test**
- Extract the known.yaml grammar into one shared unit under `sdk/` (header + `.c`),
  exporting the parse entry point and the value-trimming helper. **Keep the
  `-1` / `0` / `>=0` return contract** the daemon's seed fallback depends on.
- `wifi_daemon.c`, `iw.c` and `wifi.c` use it; their copies are deleted. The three
  behaviours resolve to the daemon's (PO decision 4), including `isspace()`.
- `test_known_yaml.c` compiles the shared source, as `test_re` does.
- Add the cases the copy could not express: missing file, seed fallback, empty
  `- ssid:`, and the `merge_legacy` grammar.

**B. LEG-17 — `dbt test`, `docs/testing.md`, ADR 041**
- `dbt test` verb in `tools/dbt/cli.py` + a module under `tools/dbt/`. Cheapest
  first: pytest, then `make -C tests/host test`; `--fuzz` and `--qemu` opt-in.
- Probes each gate before running and reports per-gate
  `ran / passed / failed / unavailable (reason)`. An unavailable gate is never
  silently a pass.
- `pytest` added to `_DEPS` in `tools/dbt.py` — already pinned, so
  `test_every_installed_package_is_pinned` stays green.
- Exit codes, asserted by tests: `0` only when every selected gate ran and passed;
  a distinct non-zero for "could not run"; a distinct non-zero for "failed".
  `dbt qemu` already exits 6 on `.duneos_board` mismatch — `dbt test --qemu` must
  surface that as **unavailability**, not failure, and must not rewrite
  `.duneos_board`.
- `docs/testing.md`: how to *write* a test — harness API, naming, adding a suite to
  the Makefile or to pytest collection, what belongs host-side vs QEMU vs hardware.
  It must **not** restate CONTRIBUTING's pre-PR gate list; it links to it.

**C. LEG-19 — cover the pure logic**
- pytest cases for `validate_manifest` (`manifest.py:70`), `decode_perms`
  (`capability_map.py:56`), `resolve` (`capabilities.py:148`, including
  `CapabilityNotApplicable` / `kernel_served`).
- `tests/host/test_ld2450.c` over `ld2450_decode_frame` / `ld2450_parser_feed`:
  both worked examples from the header (`0x86B1 → +1713`, `0x030E → -782`),
  absent-slot zero blocks, bad header/tail, resync over a garbage prefix, a header
  byte inside garbage. The decoder is already split pure/IO
  (`sdk/sensor/include/duneos/ld2450.h:17-23`) and compiles on host as-is —
  **no extraction, no loader path, no LEG-37 exposure.**
- `tests/host/Makefile` and CI `FLOOR` updated.

**D. LEG-21 — correct `ROADMAP.md:710` to DONE** with the real closing evidence.
No new documentation.

## Acceptance criteria

1. One parser implementation in the tree: one `.c` under `sdk/`, zero in `apps/`,
   zero in `tests/`. *Automated.*
2. `tests/host/Makefile`'s `test_known_yaml` rule names the `sdk/` source.
   *Automated* (Makefile is tracked, not generated) + `make -C tests/host test` green.
3. **The suite bites the shipped parser.** Mutating the shared parser's missing-file
   return from `-1` to `0` makes `make -C tests/host test` exit non-zero. *Builder
   performs the mutation and records the output*; the case is permanent. Not
   automatable in CI.
4. **The fresh-clone claim is measured and resolved.** From a clean clone with no
   `tools/.dbt-venv/`: Builder documents which of the four CONTRIBUTING gates run
   and which fail, with exact errors. After the change, `python tools/dbt.py test`
   runs the pytest and host-C gates on that same clone. *Transcript in the PR* + an
   automated case asserting `pytest` is in `_DEPS`.
5. `dbt test` exit codes are distinguishable: all green → 0; one gate forced
   unavailable → documented non-zero naming the gate and reason; a gate failing →
   a different documented non-zero. *Automated* with the probes stubbed.
6. `dbt test --qemu` leaves `.duneos_board` byte-identical on a cardputer checkout
   and reports QEMU unavailable, not failed. *Automated.*
7. `tests/host/test_ld2450.c` builds and passes, both worked examples included.
8. New pytest cases for `validate_manifest`, `decode_perms`, `resolve`, each with
   at least one rejection path.
9. `FLOOR` raised to sit a few below the new ran count; CI prints `ran` ≥ `FLOOR`.
10. **No duplicated gate list.** The four pre-PR commands appear in exactly one
    tracked file (`CONTRIBUTING.md`); `docs/testing.md` and `CLAUDE.md` link to it.
    *Automated* — extend `test_contributing_names_the_commands_that_exist` with a
    uniqueness assertion over tracked Markdown.
11. ADR 012's status line names ADR 041; ADR 041 states clause by clause what is
    kept, replaced or dropped. *Automated* presence check; reasoning is human.
12. LEG-17/18/19/21 ROADMAP rows updated, and LEG-17's "5 suites" corrected to the
    real count. *Automated* case asserting the row's count matches `TESTS` in
    `tests/host/Makefile`.
13. All gates green in CI, `apps-build` included (three apps change).

## Out of scope

bspgen outputs; the kernel, loader, `libdune`, `abi.h`, the boot path; vendoring
Greatest and rewriting the six suites (PO decision 1); `dbt tui` test action
(LEG-40); rewriting `known.yaml` into a real YAML parser or changing its format;
coverage measurement and perf suites (ADR 012 excludes both); README restructuring
beyond the LEG-21 row.

## Risks

- **Behavioural change in WiFi apps.** Unifying three parsers changes at least two.
  `iw` writes what the daemon reads, so a mismatch is a user-visible "network saved
  but never joined". The unified behaviour is the daemon's, pinned by the fixture
  table, and criterion 1's cases must cover the disagreements named in Context.
  On-board save-then-connect validation is advisable before merge; nothing here
  touches the kernel, so LEG-37 does not apply.
- **Three apps gain a shared SDK dependency**, so `.dap` sizes shift slightly.
  Userspace only; no kernel heap or PSRAM consequence.
- **`dbt test` becomes a fifth thing that can go stale.** If it drifts from
  CONTRIBUTING's list the repo is back to two sources of truth. Criterion 10 guards
  it; keep it.
- **Raising `FLOOR` is manual** and a later cycle can forget it.
- **Superseding an Accepted ADR** is cheap on paper and expensive in credibility if
  done casually. Defensible here — the harness exists and works, the framework
  choice never materialised — and it is a recorded PO decision, not a Builder's.

## Open questions

None.
