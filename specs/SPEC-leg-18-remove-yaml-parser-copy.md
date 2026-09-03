Status: PROPOSED

# SPEC-leg-18 — Make the YAML parser test exercise the real code

## Context

Finding LEG-18 (major, S): `tests/host/test_known_yaml.c` does not test the `wifi_daemon` YAML
parser — it tests a **verbatim copy** of that parser, transcribed from
`apps/system/wifi_daemon/wifi_daemon.c` into the test file. The header comment asks for manual
resynchronisation whenever the parser changes, but no automatic mechanism enforces it and nothing
signals the drift.

The test can therefore stay green indefinitely while the parser actually embedded diverges: the
suite offers a guarantee it does not hold. This is the "bug factory" pattern already recorded in
`docs/backlog.md`, and a case where a test is more dangerous than no test, because it manufactures
confidence with no basis.

## Scope

Compile and test the real production source of the parser, instead of a copy.

## Acceptance criteria

1. `tests/host/test_known_yaml.c` no longer contains a copy of the parser body: the parsing code
   under test is the one compiled from the production source file.
2. The parser is extracted into a compilation unit shared between `wifi_daemon` and the test, or the
   test directly includes/compiles the production source; either way there is **only one** definition
   of the parser in the repository.
3. `make -C tests/host test` compiles and passes: every case in `known_yaml_cases.h` runs, 0
   failures.
4. A behavioural change in the production parser (for example rejecting a syntax previously accepted)
   makes `make -C tests/host test` fail without any test file having been touched — this is the
   property the spec must establish, and it must be demonstrated explicitly.
5. `wifi_daemon` still builds and works: parsing `known.yaml` on target produces the same results as
   before the change.
6. The header comment demanding manual resynchronisation is removed, since it no longer has any
   purpose.

## Out of scope

Rewriting the YAML parser itself; extending the test case set; applying the same treatment to any
other copies in the repository (to be handled as separate findings); replacing the `tassert.h`
harness.

## Risks

Extracting the parser from an embedded application into a shared unit may pull ESP-IDF dependencies
into the host build and break `tests/host`. Should that happen, the extraction must isolate the pure
logic (libc only) from the rest of the daemon — which stays in scope, but increases the effort.
Criterion 5 requires verifying the daemon still works on target, not merely that it compiles.

## Open questions

None
