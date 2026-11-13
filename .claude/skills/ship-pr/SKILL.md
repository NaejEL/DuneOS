---
name: ship-pr
description: Drives the full lifecycle of a GitHub pull request from the current branch — creating or resuming the PR, review rounds by 3 parallel reviewer agents with adversarial validation of the findings, inline comments, committed fixes and resolved threads, then merge (merge commit) after user validation under the `gate` policy, or under five verified conditions under the `auto` policy. Use when the user wants their current branch reviewed, fixed and merged into a target branch.
context: fork
user-invocable: true
argument-hint: "<target-branch> [--rounds N] [--themes \"t1;t2;t3\"] [--models \"role=model;...\"] [--spec <path>] [--autonomy gate|auto]"
---

# Ship PR — iterative review and merge of a pull request

Drive the full cycle from the current branch to a target branch: create or resume the GitHub PR, loop review → adversarial validation → inline comments → committed fixes → resolved threads, until findings are exhausted or the round cap is reached, then merge (merge commit) after explicit user validation and delete the working branch.

## Deterministic core

Bounds, counters, registry, journal and `gh`/`git` sequences of this skill are Python code (standard library, ≥ 3.9) in `${CLAUDE_SKILL_DIR}/../_core/`: CLI `harness.py`, package `hcore/`, tests `tests/`. Never replay them by hand: call the CLI and follow its JSON. Exit 1 = explicit stop: display the stderr message as is and stop. Exit 2 = usage error or invalid bound: display it and stop. Rules carried: SP-01, SP-02 (`hcore/counters.py`); SP-03, SP-04 (`hcore/verdicts.py`); SP-05 to SP-10 (`hcore/shippr.py`, `registry.py`, `relaunch.py`); SP-11 to SP-19 (`hcore/ghflow.py`); GT-01 to GT-08 (`hcore/gates.py`); MD-01 to MD-08 (`hcore/models.py`); JR-01, JR-02 (`hcore/journal.py`); FD-10 (`hcore/findings.py`); RG-01 to RG-07 (`hcore/registry.py`).

**Interpreter** (single rule, reproduced and tested by `hcore/interp.py`): `<PY>` = the first of `python`, `python3`, `py -3`, looked up in the PATH, that answers in under 5 s with a version ≥ 3.9; none → stop and say so. Retain it with this portable Bash loop (the timeout is set only if `timeout` exists; absent, as on macOS, run the loop with the Bash tool's timeout):

```bash
t=; command -v timeout >/dev/null && t="timeout 5"; for c in python python3 "py -3"; do $t $c -c "import sys; print(sys.version_info >= (3, 9))" 2>/dev/null | grep -q True && { echo "$c"; break; }; done
```

Below, `H` = `<PY> "${CLAUDE_SKILL_DIR}/../_core/harness.py"`, `K` = `H ship-pr` and `<run>` = the journal path returned by `start`.

**Transit files**: any untrusted text passed to the core (PR title and body, reasons, summary, free answer of a gate, agent output) is written as is with the Write tool to a file under `.harness/runs/`, never through stdin or a shell command (backticks, `$` and apostrophes would be interpreted or truncated there). The core deletes each transit file as soon as it reads it, successfully or not: rewrite it before each call, in all phases; an already consumed file is a usage error (exit 2) that names it.

**Journal**: each sub-agent is recorded in `.harness/runs/ship-pr-<timestamp>.jsonl` by its `reviewer` or `validator` command, with `--duration-ms <ms> --tokens <n>` taken from the usage block of the Agent tool (without duration: `H begin --run <run> --slot "reviewer:<theme>"` or `--slot "validator:<id>"` just before the agent; without tokens: omit the option; the recorded model is the role's), `--output <transit file>` where the agent's output is written, `--dead` if it died. A missing or invalid measure is logged `null` and reported (`journal_warnings`) without blocking anything; a duration or tokens known only after the call complete the slot's last entry through `H journal amend --run <run> --slot "<slot>" --duration-ms <ms> --tokens <n>`. The returned `action` field dictates what follows; never skip a call.

**Gates** (GT-01 to GT-08): every question to the user goes through `H gate decide --run <run> --id <id>`, which logs it and returns `decision`, `options`, `default` and `headless`. `ask` → AskUserQuestion with these options, then `H gate answer --run <run> --id <id> --answer "<retained option>"`, word for word (free input, option `input`: `--answer-file .harness/runs/gate-answer.txt`); `action` = `reask` (answer outside the options, "Other" for example) → ask the same question again. AskUserQuestion unavailable (headless) → `H gate answer --run <run> --id <id> --headless`, which returns `apply`: `stop` (exit 1, stop), otherwise apply `answer` (the declared default) and report it in the report as a headless fallback.

Parse `$ARGUMENTS`:

- **1st token** = target branch (`main`, `develop`…). If absent, before anything else: `H gate decide --id ship-pr.target` (without `--run`: opens the run), free input of the target branch, then `K start --run <run> --target <target> …`, which continues this run.
- **`--rounds N`** (SP-01) and **`--themes "a;b;c"`** (SP-02): passed as is to `start`, which bounds the rounds to `min(N, 3)` (default 3) and accepts 1 to 4 themes separated by `;` (one theme = one reviewer agent; default: the three themes of 3.2). An out-of-bounds value is an explicit error.
- **`--spec <path>`** (SM-07) and **`--autonomy gate|auto`** (AP-03, for this run only): passed as is to `start`, which logs the effective policy (`autonomy`).
- **`--models "role=model;…"`** (X6, MD-01 to MD-08): passed as is to `start`, which reads `.claude/models.json` (absent → `inherit`) then applies these pairs for this cycle only, never rewriting the file; roles `shippr.reviewer` and `shippr.validator`, values `inherit`, `opus`, `fable`, `sonnet`, `haiku` or a `claude-*` identifier; unknown role or value → exit 2. `start` returns `models`: launch each reviewer and each validator with the Agent tool's `model` parameter equal to the `model` of its role when `pass_param` is true, never for `inherit`.

## Mandatory rules (all phases)

- **Scope = the PR diff, nothing else.** No finding outside the diff hunks; no refactoring proposal, cosmetic renaming or speculative security hardening; severity floor: a style nitpick or a personal preference is not a finding.
- **"Zero findings" is a normal and acceptable answer.** This sentence appears verbatim in the prompt of every reviewer and every validator — never an invented finding to fill a quota.
- **Frugality**: reviewers = read-only agents receiving only the diff hunks and a minimal context (never an "explore the repo" mission); validators = mini-agents with a short prompt and low effort; 3 rounds at most; early exit as soon as a round produces no confirmed finding; from round 2, only the fix commits and the already flagged areas are reviewed — never a re-review of unchanged files.
- **A silent theme is not a reviewed theme — and blocks the merge** (SP-05). A dead reviewer (API error, dropped connection) or one whose output is unusable (SP-03) returns the same silence as a true "zero findings", and early exit would turn it into a green light. `reviewer` returns `relaunch`: relaunch **once**; on the second failure it returns `uncovered`: the theme is **not covered**, to be reported in the PR's summary comment and in the report. The core never counts this silence as a round without findings and **refuses the merge**: the human gate is not even offered, the skill stops explaining which angle was not reviewed and invites to relaunch. This skill acts on the remote repository: unlike an audit, it cannot just declare its own amputation. Same rule for validators (SP-06): a validator dead twice leaves its finding **unvalidated** (`unvalidated`) — neither posted as an inline comment, nor fixed on its own, it appears in the report as remaining to be decided.
- **One commit per round** (SP-17), message `fix(review): round N fixes — <summary>`. Never an amend, never a force-push.
- **Never a merge without explicit user validation under the `gate` policy, nor without the five SM-01 conditions in `auto`** (SM-08; in `gate`, the headless fallback of `ship-pr.merge` leaves the PR open).
- **Cumulative findings registry** (SP-07): `.harness/runs/ship-pr-<timestamp>.registry.jsonl`, kept by the core for all findings, **including REJECTED ones**. `reviewer` deduplicates each new finding against it (same file, normalized title) before validation: a rejected finding does not resurrect from one round to the next. Also drop, by judgement, a `to_validate` finding that duplicates a known finding (same subject, other title), before any validator: `K drop --run <run> --finding <id> --of <id> [--reason-file .harness/runs/drop.txt]` (reason written with the Write tool) logs it as a duplicate, and `end-round` no longer waits for it. The **project registry** `.claude/findings.jsonl` (RG-01 to RG-07: local, excluded by `.git/info/exclude`) is read by `start`: a finding REJECTED or decided in an earlier run, of any skill, comes back in `duplicates` with `previous` = `<status>@<skill>`, without a validator; a finding already fixed or left unfixed goes back to validation, with its `history`.

**Defaults under the `auto` policy** (AP-09, one line each, verbatim the one from the catalog): `ship-pr.target` → `ship_pr.target` of the stamp, else stop — the target comes from ship_pr.target of the stamp, else it cannot be guessed; `ship-pr.dirty` → abandon — committing or stashing changes the cycle did not make is outside the accepted scope: abandon, the only option that acts on nothing unknown; `ship-pr.conflict` → continue the review anyway — non destructive, condition C5 then leaves the PR open; `ship-pr.merge` → decision by SM-01 — decided by SM-01: merge if and only if the five conditions hold, never a stop.

## Phase 1 — Preflight

`K start --target <target> [--rounds N] [--themes "…"]` (SP-11) checks in order `gh` authentication, the GitHub remote, current branch ≠ target and the existence of `origin/<target>`, and stops at the first failure with its message: never continue on a red preflight. It adds `.harness/runs/` to `.git/info/exclude` if absent, opens the journal and the registry, and returns `run`, `themes`, `dirty` and `owner_repo`.

Then `H activate --run <run> --skill ship-pr --session "${CLAUDE_SESSION_ID}" --phase review` activates the scope hooks (HK-05: edits stay within the PR diff). At the end of the cycle — merge, PR left open, stop or failure — `H deactivate --run <run> --session "${CLAUDE_SESSION_ID}"`.

1. If `dirty` is true → gate `--id ship-pr.dirty`: **commit now** (propose a message) / **stash** (note it to recall in the final report) / **abandon**.
2. `K sync --run <run>` (SP-12): pushes the branch (`git push -u origin HEAD`) if the upstream is missing or if commits are unpushed.

## Phase 2 — Pull request

`K pr --run <run>` (SP-13) resumes the open PR from the current branch to the target. If there is none, it returns `log` and `stat`: derive the title (the intent of the change) and the body (summary + list of notable changes) from them, write the title (one line) to `.harness/runs/pr-title.txt` and the body to `.harness/runs/pr-body.md`, as is with the Write tool (never through stdin or the shell), then `K pr --run <run> --title-file .harness/runs/pr-title.txt --body-file .harness/runs/pr-body.md` creates the PR. The response carries `number`, `url`, `created`, `headRefOid` and the merge state (SP-14).

If `conflicting` is true → gate `--id ship-pr.conflict`: **resolve now** (merge `origin/<target>` into the branch, resolve, commit, push) / **continue the review anyway** / **abandon**.

## Phase 3 — Review loop (3 rounds maximum)

Repeat 3.1 → 3.6 until `end-round` returns an exit (SP-08): **`exhaustion`** (nominal success: a round without a CONFIRMED finding, all its themes covered) or **`cap`** (warning: round cap reached, to be reported distinctly in the report). A round without CONFIRMED in which a theme is not covered does not exit: its silence does not amount to exhaustion, the loop continues (SP-05). A round without a `post` finding goes directly from 3.3 to `end-round` (3.6).

### 3.1 Round scope

`K diff --run <run>` (SP-15): round 1, the full PR diff; following rounds, only the diff since the HEAD before the previous round's fix commit (this commit is therefore re-read) plus the hunks of files already flagged. Never a re-review of unchanged files.

### 3.2 Parallel reviewers

Launch **in a single message** the reviewer sub-agents (Explore type, read-only), one per theme returned by `start`. Default themes:

- (a) **logic and edge cases**: logic errors, inverted conditions, off-by-one, empty/null cases, unhandled errors;
- (b) **regressions and call-site contracts**: changed signatures, behaviors expected by existing callers, data compatibility;
- (c) **internal consistency of the diff**: dead code introduced, inconsistencies between the pieces of the change (one half modified, the other forgotten).

Each reviewer prompt contains: the diff hunks of the 3.1 scope (nothing more), the scope rules (no refactoring, no speculative security, no nitpick), the sentence "**zero findings is an acceptable and frequent answer**", the ban on posting anything on the PR, and the required output format — a single final JSON block (SP-03):

```json
{"findings": [{"file": "relative/path", "line": 42, "side": "RIGHT", "severity": "critical|major|minor", "title": "short summary", "description": "the bug and its trigger scenario", "fix": "suggested fix"}]}
```

Record each return through `K reviewer --run <run> --theme "<theme>" …`: it returns `relaunch`, `uncovered`, or the `to_validate` findings (with their `id`) and the `duplicates` dropped without a validator.

### 3.3 Adversarial validation

For each finding of `to_validate`, launch a **mini validator agent** (low effort, short prompt: the finding + the relevant hunk + the scope rules) tasked with challenging it: is it in the diff? is it a real bug with a concrete scenario? is it worth a fix here, or is it a disguised refactoring/nitpick? Required output (SP-04): `{"verdict": "CONFIRMED|REJECTED", "reason": "one-sentence justification"}`. Record through `K validator --run <run> --finding <id> …`: it returns `post` (CONFIRMED), `drop` (REJECTED), `relaunch` or `unvalidated`, and writes the verdict to the registry.

### 3.4 Inline comments

For each `post` finding: `K comment --run <run> --finding <id>` (SP-16) posts the inline comment (body rendered from the finding schema, FD-10) with the `headRefOid` re-read at post time. On HTTP 422 only (line outside the diff), it falls back to a file-level comment, then, if that fallback also returns a 422, as a last resort to a PR comment citing file and line; any other error, file-level fallback included, is a stop (exit 1).

### 3.5 Fixes

Apply each confirmed fix yourself (direct edit, no sub-agent), as close as possible to the suggested fix — without widening the scope. Then write with the Write tool the summary to `.harness/runs/summary.txt` and the reasons of the unfixed findings to `.harness/runs/not-fixed.json` (`{"<id>": "<reason>"}`): this text derives from the findings, it never goes through the command line (backticks and `$()` would be executed there). Then `K commit --run <run> --summary-file .harness/runs/summary.txt --fixed <id>… [--not-fixed-file .harness/runs/not-fixed.json] [--trailer "<line>"]…` (SP-17, SP-09): one pushed `fix(review)` commit, and each confirmed finding recorded in the registry, fixed (with the hash) or deliberately **not** fixed with its reason (false positive discovered at fix time, fix impossible without widening the scope).
Pass as `--trailer` the commit attribution line requested by the environment, if there is one. Push failure (exit 1): rerun the same command, which pushes the recorded commit without redoing it.

### 3.6 Reply and thread resolution

`K threads --run <run>` (SP-18) replies "Fixed in <hash>." then resolves the thread of each fixed finding; it replies with the reason and leaves **open** the thread of each unfixed finding. Then `K end-round --run <run>` (SP-08, SP-10): round counters, then loop exit or next round (loop back to 3.1). On loop exit, it publishes the run's registry to `.claude/findings.jsonl` (RG-03: REJECTED, fixed and unfixed, last state per finding), returned in `project_registry`.

## Phase 4 — Merge (human gate)

1. `K merge --run <run> --check --conditions` (SP-05, SP-19, SM-05) refuses — and the skill stops there, without a gate — if a theme was not covered. Otherwise it returns the summary (SP-10: raw/confirmed/rejected/deduplicated findings per round, fix commits with their hashes, resolved and still-open threads, unvalidated findings) and the up-to-date merge state: present it. It also returns `conditions` (SM-01: C1 zero validated finding remaining, C2 threads resolved, C3 CI green, C4 target unprotected, C5 no conflict), for information under `gate`. If `conflicting`, report it and offer to resolve before the merge question.
2. `gate` policy: gate `--id ship-pr.merge`: "Merge PR #<n> into <target>?" — options: **Merge** / **Leave the PR open**. `auto` policy (SM-02, SM-03): no question; `K merge --run <run> --auto` evaluates the five conditions, posts the decision comment on the PR (English, only once), then merges if all are true (in a registered worktree: `--merge` without checkout or pull, SM-06), otherwise leaves the PR open (exit 0) and the report says so; the SP-05 refusal counts as a false condition.
3. If the user validates: `K merge --run <run>` (SP-19) — explicit merge commit (`gh pr merge --merge --delete-branch`, never `--squash` nor `--rebase`) that deletes the remote branch and the local branch, then `git checkout <target> && git pull`. If a stash was made in Phase 1, remind the user to run `git stash pop` — without doing it on its own.

## Phase 5 — Final report

Emit a structured report: **PR** (number, URL, branch → target, created or resumed); **Rounds** (`| Round | Raw findings | Confirmed | Rejected/deduplicated |`); **Fixes** (one hash per round, with summary); **Threads** (resolved with hash cited / left open with reason); **Loop exit** (exhaustion = success; round cap = warning, a complementary human review is prudent); **Decision** (merged — merge commit, branch deleted — or left open, with the false conditions in `auto`); **Remaining** (stash to pop, conflicts, open threads, unvalidated findings, uncovered themes); **Journal** (journal and registry under `.harness/runs/`, project registry `.claude/findings.jsonl`).
