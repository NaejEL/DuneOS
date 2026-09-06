# LEG-39 — `dbt doctor` + `dbt test`: a clone can run every gate
Status: PROPOSED
Overlaps: LEG-17 — resolve before building

**LEG-17 already prescribes a `dbt test` entry point**: ADR 012 (Accepted) names
Greatest, a `dbt test` command and `docs/testing.md`, and the audit's finding is
that we built something else (`tests/host/tassert.h`, driven by `make`). This
spec must NOT open a second, parallel prescription for the same command. Either
LEG-39 subsumes LEG-17's `dbt test` clause and LEG-17 narrows to the Greatest /
docs/testing.md question, or LEG-39 is folded into LEG-17. Decide that first;
implementing both as written produces two owners for one verb.

## Context

Someone who clones DuneOS today cannot run what CI runs. Measured on the
maintainer's own machine, 2026-09-06:

| Gate | CI | Fresh clone |
| --- | --- | --- |
| C host suites (`make -C tests/host test`) | yes | yes |
| Python tooling (`pytest tools/dbt/tests`) | yes | **no — pytest absent** |
| QEMU boot+loader (`dbt qemu`) | yes | yes (needs board switch) |
| libFuzzer (`make fuzz-run`) | yes | **no — clang absent** |

Two of four gates are CI-only, and one of them — fuzz-elf — is the gate that
FOUND the LEG-34 defect. A developer who introduces the next defect of that
family cannot reproduce it locally; they learn about it from a red PR.

Measured, not assumed: ESP-IDF's bundled `esp-clang` CANNOT substitute.
Its backends are Xtensa/RISC-V only ("No available targets are compatible
with triple x86_64-unknown-linux-gnu"), it ships no host
libclang_rt.fuzzer/asan, and its ld.lld is broken here (libxml2.so.2). A
host clang must exist. Likewise no pytest exists in the ESP-IDF python env.

There is also no single command that runs the gates. `dbt` has 13 verbs;
none of them is `test`. The TUI shells out to `dbt` for everything it does
(`tui.py:1379 _stream()`), so it has nothing to call — which is why LEG-40
depends on this spec and not the reverse.

## Scope

1. **Discovery** — `tools/dbt/toolchain/host_tools.py`, following the order
   dbt already documents for the ESP-IDF: `DUNEOS_<TOOL>` env var ->
   `.duneos_<tool>` at repo root (gitignored) -> PATH -> known install paths
   per platform. Returns None rather than raising.
   - clang: a candidate counts only if it LINKS `-fsanitize=fuzzer` (the
     probe the Makefile already performs). A clang without compiler-rt is a
     clang that fails at the worst moment.
   - pytest: `python -m pytest --version` in the interpreter dbt is running
     under, then on PATH.
2. **`dbt doctor`** — one screen saying, per gate, whether it can run here,
   what is missing, and the exact command to fix it for the detected
   platform. Exit 0 when every gate is runnable, non-zero otherwise, so CI
   and a human read the same signal.
3. **Offered install, never silent** — when a tool is missing, print the
   platform's command (pacman -S clang / apt install clang libclang-rt-dev /
   brew install llvm / winget LLVM.LLVM; pip install pytest pyyaml textual)
   and OFFER to run it. Executes only after explicit confirmation. Never a
   package manager in non-interactive mode (stdin not a tty).
4. **`dbt test [host|python|qemu|fuzz|all]`** — one verb over the existing
   runners. Delegates to `make -C tests/host` and `pytest` and `dbt qemu`;
   duplicates none of their logic. Skips a gate whose tool is absent with a
   loud SKIPPED line naming `dbt doctor`, and never reports green for a gate
   it did not run.

## Acceptance criteria

- [ ] Discovery honours all four sources in order, per tool, each covered by
      a test.
- [ ] A clang that lacks the fuzzer runtime is REJECTED by discovery rather
      than returned and left to fail at link time. Test uses a stub that
      compiles but fails on `-fsanitize=fuzzer`.
- [ ] `dbt doctor` exits non-zero when a gate is unrunnable, 0 when all are.
- [ ] Non-interactive never invokes a package manager. Test asserts this.
- [ ] `dbt test all` on a machine with no clang and no pytest: exits
      non-zero, prints SKIPPED for those two gates, runs the other two, and
      does NOT print a green summary.
- [ ] `dbt test host` is exactly equivalent to `make -C tests/host test`
      (same exit code on both a passing and a deliberately broken tree).
- [ ] `pytest tools/dbt/tests -q` green, and the suite still passes on a
      machine WITHOUT clang — mirroring the Makefile's own rule that `test`
      stays runnable with no clang.
- [ ] `.duneos_clang` gitignored; documented in CLAUDE.md beside
      `.duneos_idf` / `.duneos_board` / `.duneos_port`.
- [ ] README/CLAUDE.md state the clone->green path in one place.

## Out of scope

- Installing anything without confirmation (PO decision: dbt discovers, the
  human authorises the privileged act).
- Vendoring a toolchain.
- Changing what any gate DOES; only how its tool is found and invoked.
- The TUI (LEG-40, layered on `dbt test`).

## Risks

- Windows/macOS branches are untestable here (Linux only). They must be
  written inert-and-honest rather than guessed-and-wrong.
- `clang-NN` ordering must sort NUMERICALLY (clang-9 vs clang-20).
- `dbt test` must not become a second source of truth for how tests run.
  If it grows logic the Makefile already has, the spec has been missed.
