Status: PROPOSED

# SPEC-leg-17 — Realign ADR 012, and make a clean clone able to run every gate

**LEG-39 was merged into this spec on 2026-09-06** rather than tracked beside it:
it prescribed the same `dbt test` verb that ADR 012 already prescribes, and one
verb cannot have two owners. What LEG-39 contributed is the measured evidence
below and the `dbt doctor` half, which ADR 012 never anticipated.

## Context

Finding LEG-17 (major, S): `docs/adr/012-test-strategy.md`, at status Accepted, decides to adopt the
Greatest framework vendored into `third_party/greatest/`, a `dbt test` subcommand, a
`kernel/duneos_kernel/tests/` layout and a `docs/testing.md` document.

None of these exists:

- `third_party/` contains only `cjson` and `littlefs`;
- `tools/dbt/cli.py` registers no `test` subparser;
- `docs/testing.md` is absent;
- the actual tests live in `tests/host/` and rely on a homegrown `tassert.h`.

An ADR at status Accepted describing a reality that does not exist is worse than no ADR at all: it
actively steers the reader — human or agent — towards phantom infrastructure. The repository holds
40 ADRs; their collective authority rests on each one being reliable.

## Context added by the LEG-39 merge — measured, not assumed

Criterion 4 below already required the documented test command to run from a
clean checkout. It does not. Measured on the maintainer's machine, 2026-09-06:

| Gate | CI | Fresh clone |
| --- | --- | --- |
| C host suites (`make -C tests/host test`) | yes | yes |
| Python tooling (`pytest tools/dbt/tests`) | yes | **no — pytest absent** |
| QEMU boot+loader (`dbt qemu`) | yes | yes, but only by clobbering `.duneos_board` |
| libFuzzer (`make fuzz-run`) | yes | **no — clang absent** |

Two of four gates are CI-only, and one of them is the gate that FOUND LEG-34.
A developer who introduces the next defect of that family cannot reproduce it;
they learn of it from a red PR.

ESP-IDF's bundled `esp-clang` CANNOT substitute — verified, not assumed: its
backends are Xtensa/RISC-V only ("No available targets are compatible with
triple x86_64-unknown-linux-gnu"), it ships no host `libclang_rt.fuzzer`/`asan`,
and its `ld.lld` is broken here (missing `libxml2.so.2`). No pytest exists in
the ESP-IDF python env either. A host clang and a pytest must genuinely be
installed.

## Scope

Restore consistency between ADR 012 and the code, AND make the resulting command
runnable from a clean clone. Three outcomes, the choice being the maintainer's:

- **(A)** implement what the ADR decides in full (vendor Greatest, add
  `dbt test`, create `kernel/duneos_kernel/tests/` and `docs/testing.md`);
- **(B)** acknowledge the decision was not followed: move ADR 012 to Superseded
  or Rejected, and write a successor ADR describing the strategy in force
  (`tests/host/` + `tassert.h` + `make`, pytest for the Python tooling);
- **(C) — the outcome the LEG-39 merge points at.** Keep the harness that
  exists (`tests/host/` + `tassert.h`; do NOT vendor Greatest, do NOT create the
  ADR's kernel test layout) and adopt the parts of the ADR that earn their
  place: `dbt test` and `docs/testing.md`. ADR 012 moves to Superseded with a
  successor ADR that says plainly which of its clauses were kept and which were
  dropped, and why.

**(C) is a real third answer, not A in disguise**, and it must be chosen
deliberately: it keeps a homegrown assert harness over a vendored framework, and
that is precisely the kind of decision that must not be made by default — the
failure mode this spec exists to correct.

### Added by the merge, whichever outcome is chosen

1. **Tool discovery** — `tools/dbt/toolchain/host_tools.py`, in the order dbt
   already documents for the ESP-IDF: `DUNEOS_<TOOL>` env var ->
   `.duneos_<tool>` at repo root (gitignored) -> PATH -> known install paths.
   Returns None rather than raising.
   - clang counts only if it LINKS `-fsanitize=fuzzer` (the probe the Makefile
     already performs). A clang without compiler-rt fails at the worst moment.
   - pytest: `python -m pytest --version` under dbt's own interpreter, then PATH.
2. **`dbt doctor`** — one screen: per gate, whether it can run here, what is
   missing, the exact command to fix it for the detected platform. Exit 0 only
   when every gate is runnable, so CI and a human read the same signal.
3. **Offered install, never silent** — print the platform's command
   (`pacman -S clang` / `apt install clang libclang-rt-dev` / `brew install
   llvm` / winget LLVM.LLVM; `pip install pytest pyyaml textual`) and OFFER to
   run it. Executes only after explicit confirmation. Never a package manager
   when stdin is not a tty.
4. **`dbt test [host|python|qemu|fuzz|all]`** delegates to the existing runners
   and duplicates none of their logic. A gate whose tool is absent is SKIPPED
   loudly, names `dbt doctor`, and is never counted as green.

## Acceptance criteria

1. `docs/adr/012-test-strategy.md` does not remain at status Accepted while describing absent
   infrastructure: either each of its elements exists (outcome A), or its status is changed and
   points to its successor (outcome B).
2. If outcome A is chosen: `third_party/greatest/` exists, `python tools/dbt.py test` runs and
   executes the test suite, `docs/testing.md` exists, and the test layout described by the ADR is in
   place.
3. If outcome B is chosen: a successor ADR exists, describes the actual test location, the harness
   actually used and the command to run it, and ADR 012 references it explicitly.
4. In both cases, the test command documented in the governing ADR is executable as written from a
   clean checkout and returns a zero exit code on the current commit.
5. No other ADR references ADR 012 in a way that became false after the change: cross-references are
   checked and corrected.
6. If outcome C is chosen: a successor ADR exists and states, clause by clause, which of ADR 012's
   decisions are kept and which are dropped — Greatest and the kernel test layout being the dropped
   ones — with the reason for each. "We did something else" is not a reason.
7. Discovery honours all four sources in order, per tool, each covered by a test.
8. A clang lacking the fuzzer runtime is REJECTED by discovery, not returned and left to fail at
   link time. Test uses a stub that compiles but fails on `-fsanitize=fuzzer`.
9. `dbt doctor` exits non-zero when any gate is unrunnable, 0 when all are.
10. Non-interactive never invokes a package manager. Asserted by a test.
11. `dbt test all` on a machine with neither clang nor pytest: exits non-zero, prints SKIPPED for
    those two gates, runs the other two, and prints no green summary.
12. `dbt test host` is exactly equivalent to `make -C tests/host test` — same exit code on a passing
    tree and on a deliberately broken one.
13. `pytest tools/dbt/tests -q` green, and the suite still passes on a machine WITHOUT clang,
    mirroring the Makefile's own rule that `test` stays runnable with no clang.
14. `.duneos_clang` gitignored and documented in CLAUDE.md beside `.duneos_idf` / `.duneos_board` /
    `.duneos_port`; the clone->green path stated in one place.

## Out of scope

Writing new tests (handled by LEG-19); removing the YAML parser copy (handled by LEG-18); a
consistency review of the other 39 ADRs; correcting the ADR count in the README (handled by LEG-22).

From the merge: installing anything without confirmation (PO decision — dbt discovers, the human
authorises the privileged act); vendoring a toolchain; changing what any gate DOES, only how its
tool is found and invoked; the TUI (LEG-40, layered on `dbt test`).

## Risks

Outcome A is markedly more costly than outcome B and reopens an architectural decision; outcome B is
cheap but requires stating in writing that a decision taken was not followed. The choice commits the
project's test strategy and must not be made by default, in either direction.

## Risks added by the merge

- Windows/macOS branches are untestable here (Linux only). They must be written inert-and-honest
  rather than guessed-and-wrong.
- `clang-NN` ordering must sort NUMERICALLY (clang-9 vs clang-20).
- `dbt test` must not become a second source of truth for how tests run. If it grows logic the
  Makefile already has, this spec has been missed.

## Open questions

Outcome A, B or C? The decision belongs to the maintainer and determines which of criteria 2, 3 or
6 applies. The LEG-39 merge points at C but does not settle it.
