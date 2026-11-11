## Phase 1 — Stack detection

`K stack detect --root .` (ST-01 to ST-04) searches the project root and one level of subfolders (monorepo) for the markers `*.sln`/`*.csproj` (.NET), `pyproject.toml`/`requirements.txt`/`setup.py`/`environment.yml`/`*.ipynb` (Python), `package.json` (Node/TS), `CMakeLists.txt`/`Makefile` (C/C++), `go.mod` (Go), `Cargo.toml` (Rust), `Dockerfile`/`docker-compose*.yml`/`compose*.yml` (containers), `ansible.cfg`/`playbooks/`/`roles/` (Ansible), `*.tf` (Terraform), and returns `manifests` (paths found), `families` and `stacks`. Read the returned manifests to extract the actual scripts and dependencies: that judgement stays with the skill.

Establish and keep for the following phases: languages and frameworks present; the actual **build command** (e.g. `dotnet build`, `npm run build`, `cmake --build build`, absent if not applicable); the actual **test command** (e.g. `dotnet test`, `pytest`, `npm test`, `go test ./...`, `ansible-lint` + `molecule test` for Ansible); the **lint/format command** if configured (`ruff`, `eslint`, `clang-format`, `dotnet format`…); existing conventions: `CLAUDE.md`, `.editorconfig`, linter configuration, test folder structure; core stacks: the `stacks` list returned by `stack detect` (among `node`, `dotnet`, `python`, `cpp`, `rust`).

If no test command exists, note it: the Builder prompt must require setting up the stack's standard test tooling (e.g. `pytest` for Python, `xunit` for .NET), and the final report will flag it to the user.

## Phase 4 — `.claude/harness/` core and thin CI scripts `ci/factory.sh` and `ci/factory.ps1`

Copy the core into the project with `K sf copy-core --root .`: `_core` as is under `.claude/harness/`, with its `CORE_VERSION` constant, without bytecode, versioned with the project. Report the state of each rendered file. Never a mixed core: if an already-present file differs, nothing is copied and the command exits with exit 1 (`copied` false, `blocked_by` lists the files involved): stop there and display its message, without generating the CI scripts or continuing the installation, because the generated `factory-run` would not work.

Generate two complete, equivalent scripts (bash with `set -euo pipefail`, PowerShell with `$ErrorActionPreference = 'Stop'`) that:

- take as a **mandatory argument** the path of an approved spec, and a **`--yolo` option** (`-Yolo` in PowerShell);
- check that the spec file exists and contains `Status: APPROVED`, otherwise fail with a clear message;
- export `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` and `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` (without them, `claude -p` may launch subagents in the background and exit before they finish), then build the call: `claude -p "/factory-run '<spec-path>'" --output-format json --max-turns 100`, completed by default with `--permission-mode acceptEdits --allowedTools "<allowlist>"`, or with `--dangerously-skip-permissions` if `--yolo`;
- the `--allowedTools` **allowlist** is built from Phase 1: always `Read`, `Glob`, `Grep`, `Write`, `Edit`, `Bash(git *)`, the interpreter chosen for the core (`Bash(python *)`, `Bash(python3 *)` or `Bash(py *)`), plus the project's actual commands (e.g. `Bash(dotnet *)`, `Bash(pytest *)`, `Bash(npm *)`) — only those detected;
- write the output to `factory-logs/factory-<timestamp>.json` (create the folder; timestamp `date +%Y%m%d-%H%M%S` in bash, `Get-Date -Format yyyyMMdd-HHmmss` in PowerShell) and **propagate the exit code** of `claude`.

## Phase 5 — Token savings `.claude/settings.json` and `.gitignore`

A single call merges the files of phases 5 and 6 and returns the state of each: `K sf apply --root . --stacks <stacks returned by stack detect in Phase 1, comma-separated>`.

- **Token savings** (SF-01): `permissions.deny` rules in `Read(...)` covering only the detected stack's artefacts, plus `Read(./factory-logs/**)` and `Read(./.git/objects/**)`; core `STACK_DENY` mapping table; arrays merged without duplicates, every existing key preserved.
- **`.gitignore`** (SF-03): `factory-logs/` and `.harness/runs/` (cycle journals), created or completed without duplicating a line.

## Phase 6 — VS Code tasks `.vscode/tasks.json`

SF-02 merge: version `2.0.0`, input `factorySpec` (“Path of the approved spec (specs/SPEC-*.md)”) and three tasks: **“Factory: CI run”** (`./ci/factory.sh ${input:factorySpec}`, `windows` variant as `pwsh -File ci/factory.ps1`), **“Factory: CI run (YOLO)”** (`--yolo` / `-Yolo`), **“Factory: nouvelle spec (session)”** (reminder `Open Claude Code and type: /factory-run "<your need>"`); existing tasks and inputs preserved, nothing duplicated.

## Phase 6b — Repository knowledge base (optional)

The factory spends most of its budget rediscovering the repository: each Planner starts from a blank context, each Verifier rereads what the Builder just wrote. The `code-index` skill installs a local ripgrep-backed server that answers these questions from an index versioned under `.knowledge/`, and additionally carries the **notes** that cycles leave behind — decisions, pitfalls, sensitive areas — shared through git with the other users of the repository.

Ask the user, in a single `AskUserQuestion` call, whether they want to install it now: `K gate decide --root . --id software-factory.code-index` (opens the journal `.harness/runs/software-factory-<timestamp>.jsonl`), then `K gate answer --run <run> --id software-factory.code-index --answer "<install|refuse>"`. If they agree, invoke the `code-index` skill: it detects the factory that was just installed and wires the three agents and `/factory-run` to it on its own. If they decline, do nothing: the factory works entirely without it, and `code-index` can be installed at any time on an already-equipped repository. **Never install without consent**: the base adds a versioned folder to the repository, which is a decision to make. In headless execution, do not ask the question: `K gate answer --run <run> --id software-factory.code-index --headless` (fallback `skip`), and install nothing.

## Phase 7 — Final report

End with a structured report: detected stack (languages, chosen commands, core interpreter); list of files with their state as returned by the core (created / merged / identical / customized-kept), `.claude/harness/` included; absence of test tooling if applicable; usage in two lines (session: `/factory-run "need"`; CI: `ci/factory.sh specs/SPEC-x.md`). If `$ARGUMENTS` contained a need, conclude with the exact command to run: `/factory-run "<the need>"`.
