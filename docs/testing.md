# Writing a test

Which gates to run before a PR, and in what order, is
[`CONTRIBUTING.md`](../CONTRIBUTING.md) — this file does not repeat it.
`python tools/dbt.py test` runs what the current machine can run and exits
non-zero when a gate merely could not run (`--allow-missing` to accept that,
`--fuzz` / `--qemu` for the two opt-in gates).

This file is about writing the test in the first place.

## Where a test belongs

| The thing under test | Where |
| --- | --- |
| Pure C logic with no ESP-IDF dependency — a parser, a decoder, a validator | `tests/host/`, run by `make` |
| Python tooling — `dbt`, bspgen, the repo's own invariants | `tools/**/test_*.py`, run by pytest |
| The kernel booting, mounting and loading a `.dap` | the QEMU bench (ADR 039), `tools/dbt/qemu.py` |
| Anything needing a real peripheral, a real SD card, or real timing | on the board, by hand, before merge |

The boundary that matters is the first row against the last: a host test that
needs a `#define` to fake a peripheral is a host test of the fake. Split the
unit instead, the way `sdk/sensor/libld2450.c` splits decoding from I/O.

## A host C suite

`tests/host/tassert.h` is the harness: `CHECK(cond)`, `CHECK_INT(got, want)`,
`CHECK_STR(got, want)`, and `t_report("name")` as the return value of `main`.
Each suite is one `test_<unit>.c` with its own `main`; there is no runner and
no registration.

Adding one is two steps:

1. Write `tests/host/test_<unit>.c`.
2. Add `test_<unit>` to `TESTS` in `tests/host/Makefile` and give it a rule.

**The rule compiles the shipped source.** Not a copy of it, not an extract:
the `.c` the firmware links.

```make
test_ld2450: test_ld2450.c $(ROOT)/sdk/sensor/libld2450.c \
             $(ROOT)/sdk/sensor/include/duneos/ld2450.h tassert.h
	$(CC) $(CFLAGS) -I$(ROOT)/sdk/sensor/include test_ld2450.c \
	    $(ROOT)/sdk/sensor/libld2450.c -o $@
```

If the unit will not compile on host, that is the finding. `sdk/net/known_yaml.c`
exists because the known.yaml parser lived inside three app `.c` files that
could not; the test compiled a fourth copy and was green whatever the shipped
parsers did. Extract, then test the extract.

Suites build with `-Werror` and `-D_POSIX_C_SOURCE=200809L`.

## A pytest case

`pytest.ini` collects `tools/**/test_*.py`, so a new file under
`tools/dbt/tests/` needs no registration. Two rules:

- **Build what you assert against.** The root `conftest.py` fails any test that
  opens a path git ignores — `sdkconfig.board`, `board_config.h`, anything
  under `build/`. Generate the input into `tmp_path` (the `regenerated_root`
  fixture does this for bspgen), or read the tracked blob with
  `git cat-file` as `test_repo_dependencies.py` does. A test that reads a build
  output passes only on the machine that produced it (LEG-38).
- **Stub the environment, not the logic.** `test_testing.py` asserts `dbt test`'s
  exit codes with every gate replaced by a lambda: the verdict is the subject,
  the presence of `clang` is not.

Raise `FLOOR` in `.github/workflows/ci.yml` in the same commit as the tests you
add. It counts tests that *ran*, so a mass-skip lands under it; forgetting to
raise it weakens the gate silently and nothing goes red.

## Proving a test bites

A new suite that would stay green against a broken implementation has bought
nothing. Before believing one, break the thing it covers and watch it fail:

```bash
# sdk/net/known_yaml.c: the missing-file return the seed fallback branches on
sed -i 's|if (fd < 0) return -1;|if (fd < 0) return 0;|' sdk/net/known_yaml.c
make -C tests/host test   # must exit non-zero
git checkout sdk/net/known_yaml.c
```

The CI job does the same for the pytest gate, with a deliberately failing
scratch test, on every run.
