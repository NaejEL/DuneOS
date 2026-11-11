Drive a full cycle of this repository's software factory on the input `$ARGUMENTS`. You, the main session, are the orchestrator: you write neither code nor spec yourself, you delegate to the subagents `factory-planner`, `factory-builder` and `factory-verifier`, and you hold the gates.

## Core

The deterministic rules of the cycle are carried by the copy of the core under `.claude/harness/`: never reimplement them by hand. Below, `H` stands for `{{interpreter}} .claude/harness/harness.py`. Each command returns a JSON object; its `action` field dictates what follows: never skip a call or decide in the core's place.

**Every subagent launched is recorded** with `H factory-run record --run <run> --agent <name> --duration-ms <ms> --tokens <n>` (values from the Agent tool's usage block). Without a duration: `H begin --run <run> --slot <name>` just before the agent; without tokens: omit the option; a missing measurement is journaled as `null` and flagged, never blocking; if only known after the call, it completes the slot's last entry with `H journal amend --run <run> --slot <name> --duration-ms <ms> --tokens <n>`. The Verifier's output is written as is with the Write tool to `.harness/runs/verifier-<n>.md`, then passed via `--output` (never via stdin or through the shell: backticks, `$`, apostrophes). `--dead` if the agent died.

**Gates**: `H gate decide --run <run> --id <id>`, then AskUserQuestion if the decision is `ask`, then `H gate answer --run <run> --id <id> --answer-file .harness/runs/gate-answer.txt` (answer written with Write; `reask` returned: ask the question again). With `--default`, the catalog's default answer is applied; with no interaction possible (headless), `H gate answer --run <run> --id <id> --headless`.

## 1. Qualify the input

Write `$ARGUMENTS` as is with the Write tool to `.harness/runs/factory-run-arguments.txt` (never on a shell command line: a need may contain quotes, `$` or backticks), then `H factory-run start --arguments-file .harness/runs/factory-run-arguments.txt`. The command opens the journal `.harness/runs/factory-run-<timestamp>.jsonl` and returns `mode`:

- `build` (spec `APPROVED`) → step 3;
- `approve` (spec `PROPOSED`, or `BLOCKED` fully answered: integrate the answers) → approval gate of step 2, then step 3;
- `plan` → treat `need` as the need → step 2.

Exit 1: spec `BLOCKED` with unanswered questions, or spec `DELIVERED`; display the stderr message (it cites the `STOP-*.md` file) and stop.

The command removes from `$ARGUMENTS` `--models "role=modele;…"` (roles `factory.planner`, `factory.builder`, `factory.verifier`, this cycle only; unknown role or value → exit 2) and returns `models`: pass each subagent the role's `model` parameter when `pass_param` is true, never for `inherit`. It also removes `--autonomy gate|auto` (this run only), `--ship <cible>`, `--no-ship`, `--deliver-only`, `--worktree` and the internal options `--chained-from`, `--chain-id`; the effective policy is returned (`autonomy`) and passed to any chained skill. With `--worktree`, a need first goes through the Plan phase in this session, then `H worktree create` and `H worktree transfer`, the child `factory-run <spec> --ship <cible>` runs in the worktree, waiting is done with `H chain wait`, and `H worktree remove --merged` cleans up if the PR is merged.

## 2. Plan phase and approval

1. Launch the `factory-planner` subagent with the full need. Record it.
2. Write `specs/SPEC-<need-slug>.md` headed by `Status: PROPOSED` followed, on contiguous lines, by `Blocking questions: Q<n>, … | none`, `Files scope: <globs>` (if the spec can bound it), `Depends on:`, `Priority:`. Each open question is `- Q<n> — <text>` with a `Default:` line (mandatory outside a blocking question).
3. `H factory-run bind --run <run> --spec <spec>` then `H spec settle --run <run> --file <spec>`. In `auto`, an unanswered blocking question sets the spec to `BLOCKED` and stops the cycle (exit 1, STOP `blocked`); otherwise each non-blocking question gets its `Default:` (`Answer: … (default applied automatically)`, a single `factory-run.questions` decision journaled). In `gate`, ask the questions (gate `factory-run.questions`, option `draft options`: the options of the draft).
4. Approval (gate `factory-run.approval`: `approval / iterate`): `H gate decide --run <run> --id factory-run.approval`, AskUserQuestion if `ask`, then `H gate answer --run <run> --id factory-run.approval --answer "<option>"` (or `--default`, or `--headless` with no interaction possible → stop, exit 1). Approved: `H spec set-status <spec> APPROVED`; otherwise iterate.
5. Never build without an approved spec.

Defaults under the `auto` policy: `factory-run.profile` → `first profile of the stamp` (the first profile in the stamp, the first one retained at installation); `factory-run.questions` → `Planner default` (the `Default:` declared by the Planner; nothing is invented, a blocking question blocks the spec); `factory-run.approval` → `approval` (approve, without waiting, a blocking question having already blocked the spec); `factory-run.ship` → `deliver and run ship-pr` (deliver and launch ship-pr; a green build is delivered when CH-02 holds; the other option is `do not deliver`).

## 3. Build phase

`H activate --run <run> --skill factory-run --session "${CLAUDE_SESSION_ID}" --spec <spec>` activates the scope hooks. Then launch the `factory-builder` subagent (fresh context) with the path of the approved spec. At the end of the cycle — success, failure or stop — `H deactivate --run <run> --session "${CLAUDE_SESSION_ID}"`. Any stop (exit 1, message `STOP: … (see <file>)`) is final: display the message and stop, no further command on this run.

## 4. Verify phase — fix loop, 3 iterations maximum

Launch a `factory-verifier` subagent (blank context, independent of the Builder) with the spec path, record its JSON block, then according to `action`:

- `success` (`APPROVED` **and** `tests_passed` true): exit the loop;
- `fix`: relaunch `factory-builder` with the spec **and the complete list of returned `issues`** (file, severity, description) and the instruction to fix without regressing; then a new Verifier;
- `relaunch`: relaunch **once** the agent that died or whose output was unusable, without consuming an iteration;
- `fail`: **fail explicitly** by publishing `reason` and the remaining issues (3rd iteration not approved, or second failure of an agent). Never approve by exhaustion.

**A silent Verifier is not a satisfied Verifier**: never treat the absence of a verdict as an approval, never move on given a missing verdict. A cycle that delivers unverified code because its verifier went down is worse than a cycle that fails.

## 5. Final report

Modified files (`git status`), test result, verdict, number of iterations, journal path, next action. On `success` the spec is set to `DELIVERED` by the core.

Delivery (gate `factory-run.ship`, unless `--no-ship`): `H factory-run deliver --run <run> [--trailer "<attribution line>"]` creates the `factory/<slug>` branch if needed, commits `feat(factory): <title>` (never a push) and returns `ship_args`; without `--deliver-only`, invoke `ship-pr <ship_args>` through the Skill tool; condition false → nothing is committed, the reason is journaled, suggest `/ship-pr <cible>`. Outside delivery, do not commit in the user's place.
