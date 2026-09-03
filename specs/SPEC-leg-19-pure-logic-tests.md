Status: PROPOSED

# SPEC-leg-19 — Cover the host-testable pure logic with tests

## Context

Finding LEG-19 (major, M): several pure-logic functions, testable on the host without hardware, have
no tests.

On the C side — `sdk/sensor/libld2450.c`: `ld2450_decode_frame()` and `ld2450_parser_feed()` include
libc only. These are binary frame decoders fed by an external sensor, that is, exactly the kind of
code where a bounds error is costly and a host test is cheapest.

On the Python side — no coverage for `tools/dbt/manifest.py:70 validate_manifest`,
`tools/dbt/capability_map.py:56 decode_perms` and `:70 check_app`, and
`tools/dbt/capabilities.py:148 resolve`. These functions decide which permissions an application
receives and which libraries get linked: an error there yields either a non-functional image or an
application holding permissions it should not have.

This finding depends on LEG-08: Python tests must be run by CI before it is worth writing more.

## Scope

Add host tests for the LD2450 decoder (C, in `tests/host/`) and for the four Python tooling functions
listed above.

## Acceptance criteria

1. A C host test covers `ld2450_decode_frame()` with at minimum: a valid frame whose decoded values
   are checked field by field, a truncated frame, a frame with an incorrect header, and a frame whose
   declared length exceeds the supplied buffer. Each case asserts a return value or an expected
   output, never merely the absence of a crash.
2. A C host test covers `ld2450_parser_feed()` by feeding the parser byte by byte and verifying that
   a frame split across several calls is reassembled correctly, and that a stream containing stray
   bytes before the header is resynchronised.
3. `make -C tests/host test` compiles and runs these tests; 0 failures.
4. A pytest test covers `validate_manifest` with at least one valid manifest accepted and, for each
   mandatory field, a manifest missing it, rejected with an error identifying the offending field.
5. A pytest test covers `decode_perms` (mapping between bitmask and permission names, both ways if
   the function allows) and `check_app` (an application whose declared permissions cover its needs
   passes; an application missing a required permission fails with a message naming it).
6. A pytest test covers `capabilities.resolve`: a declared capability resolves to the expected
   library, and an unknown capability produces an explicit error rather than silence.
7. The pytest suite is collected and run by CI (inherited from LEG-08) and passes.

## Out of scope

Tests requiring hardware; coverage of `tools/dbt/tui.py`, `flashimg.py` or `deploy.py`; code coverage
measurement; kernel or loader tests; reworking the test harness (see LEG-17).

## Risks

Writing tests against current behaviour freezes any existing bugs into expected behaviour. If a test
case reveals behaviour that looks incorrect, it must be raised as a separate finding rather than
recorded as-is in an assertion: a test that documents a bug without flagging it is a trap for the
next reader.

## Open questions

None
