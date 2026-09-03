Status: PROPOSED

# SPEC-leg-09 — Pin the Python dependencies of the tooling

## Context

Finding LEG-09 (major, S): `tools/dbt.py` declares the required packages at l.25 with no version
constraint:

```python
_DEPS = ["littlefs-python", "esptool", "rich", "pyyaml", "textual", "Pillow"]
```

These packages are installed on first run into `tools/.dbt-venv/`, fetching the latest published
version every time. CI does the same with a bare `pip install pyyaml`
(`.github/workflows/ci.yml` l.35 and l.69). No `requirements.txt`, `pyproject.toml` or Python lock
exists in the repository.

Consequence: the same DuneOS commit does not build the same thing depending on the date. A major
release of `textual`, `esptool` or `Pillow` published tomorrow can break `dbt` on a commit that
worked yesterday, with no repository change to blame — and diagnosis is all the harder because
nothing in the repository records what was installed.

## Scope

Introduce a versioned Python dependency file with version constraints, and have both `dbt` and CI
use it.

## Acceptance criteria

1. A versioned Python dependency file (for example `tools/requirements.txt`) exists, is tracked, and
   declares each of the six `_DEPS` packages with a bounded version constraint (at minimum an upper
   bound on the major version).
2. `tools/dbt.py` installs its venv dependencies from that file rather than from an unconstrained
   literal list: the bare name list is no longer the source of truth for installation.
3. Both `pip install pyyaml` calls in `.github/workflows/ci.yml` are replaced by an installation
   from the same dependency file.
4. On a machine without `tools/.dbt-venv/`, a first run of `python tools/dbt.py` creates the venv,
   installs the declared versions, and the command completes.
5. The versions actually installed in the venv satisfy the declared constraints: `pip freeze` of the
   venv contradicts no constraint in the file.
6. The `_DEP_MODULE` mapping (pip name to import module name) stays consistent with the packages
   declared in the file.

## Out of scope

Migrating to `pyproject.toml` and packaging `dbt` as an installable distribution; adding a
hash-pinned Python lock; updating the currently installed versions; pinning ESP-IDF components
(handled by LEG-07 and LEG-16).

## Risks

Pinning versions older than those currently installed on the maintainer's machine may change TUI or
flashing behaviour: constraints must start from the versions actually in use today, read from
`pip freeze` of the existing venv, rather than from values chosen a priori.

## Open questions

None
