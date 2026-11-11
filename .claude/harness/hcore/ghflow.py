# Deterministic gh/git sequences of ship-pr (D11).
#
# - SP-11: preflight, in order, stop on the first failure with the expected
#   message; working tree state and owner/repo.
# - SP-12: missing upstream or unpushed commits -> git push -u origin HEAD.
# - SP-13: looks up an open PR branch -> target, otherwise creates it.
# - SP-14: PR state (headRefOid, mergeable, mergeStateStatus).
# - SP-15: collects the round diff (round 1: gh pr diff; afterwards:
#   git diff <HEAD before the previous round's fix>..HEAD, hence the fix
#   commit, + files already flagged).
# - SP-16: inline comment with the headRefOid re-read at posting time;
#   on HTTP 422 only, file-level fallback then, if that also fails with
#   HTTP 422, a PR comment as a last resort. Any other error stops the skill.
# - SP-17: one commit per round "fix(review): round N fixes — <summary>",
#   optional trailers after a blank line, then a plain push (never an amend,
#   never a force-push); a failed push is resumed without a second commit.
# - SP-18: reply to and resolve the threads of fixed findings; threads of
#   findings not fixed: reply with the reason, left open. Progress saved
#   after each step (no reply posted twice on a relaunch).
# - SP-19: merge with a merge commit and branch deletion, refused if a theme
#   is not covered or if the loop is not finished.
# - D3: adds .harness/runs/ (and, RG-05, the project registry files) to
#   .git/info/exclude, idempotent.
# - FD-10: the comment body is the PR comment conversion of the finding schema.

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import findings, registry as registry_mod, shippr
from .proc import Result

HTTP_422 = re.compile(r"\bHTTP 422\b")   # gh message: "... (HTTP 422)"

Run = Callable[..., Result]
EXCLUDE_LINE = ".harness/runs/"

MSG_GH_AUTH = "gh not authenticated — run `gh auth login`"
MSG_NO_GITHUB = "no GitHub remote"
MSG_ON_TARGET = "you are already on {target}, switch to the branch to merge"
MSG_NO_TARGET = "the target branch {target} does not exist on origin"

THREADS_QUERY = (
    "query($owner:String!,$repo:String!,$number:Int!){"
    " repository(owner:$owner,name:$repo){ pullRequest(number:$number){"
    " reviewThreads(first:100){ nodes{ id isResolved"
    " comments(first:10){ nodes{ databaseId } } } } } } }"
)
RESOLVE_MUTATION = (
    "mutation($t:ID!){ resolveReviewThread(input:{threadId:$t}){ thread{ id isResolved } } }"
)


class Stop(RuntimeError):
    """Explicit stop: the message is meant for the user."""


def _must(res: Result, what: str) -> Result:
    if not res.ok:
        raise Stop("%s failed: %s" % (what, (res.err or res.out).strip()))
    return res


def preflight_checks(run: Run, target: str) -> Dict[str, Any]:
    """SP-11, preflight steps 1 to 4."""
    if not run(["gh", "auth", "status"]).ok:
        raise Stop(MSG_GH_AUTH)
    remote = run(["git", "remote", "get-url", "origin"])
    if not remote.ok or "github.com" not in remote.out:
        raise Stop(MSG_NO_GITHUB)
    branch = _must(run(["git", "rev-parse", "--abbrev-ref", "HEAD"]), "git rev-parse").out.strip()
    if branch == target:
        raise Stop(MSG_ON_TARGET.format(target=target))
    fetched = run(["git", "fetch", "origin", target])
    if not fetched.ok or not run(["git", "rev-parse", "--verify", "origin/" + target]).ok:
        raise Stop(MSG_NO_TARGET.format(target=target))
    return {"branch": branch}


def worktree_and_repo(run: Run) -> Dict[str, Any]:
    """SP-11, steps 5 (observation only, the gate stays with the prompt) and 7."""
    status = _must(run(["git", "status", "--porcelain"]), "git status").out
    repo = _must(run(["gh", "repo", "view", "--json", "owner,name", "-q",
                      '.owner.login + "/" + .name']), "gh repo view").out.strip()
    dirty = [line for line in status.splitlines() if line.strip()]
    return {"dirty": bool(dirty), "dirty_files": dirty, "owner_repo": repo}


def ensure_exclude(run: Run, root: Path) -> bool:
    """D3 and RG-05: adds .harness/runs/ and the project registry files to
    .git/info/exclude if missing (under lock, atomically). Returns True if a
    line was added."""
    added = registry_mod.git_exclude(Path(root), (EXCLUDE_LINE,) + registry_mod.PROJECT_EXCLUDE, run)
    if added is None:
        raise Stop("git rev-parse --git-path info/exclude failed: not a git repository")
    return bool(added)


def sync_upstream(run: Run) -> Dict[str, Any]:
    """SP-12."""
    upstream = run(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    push = not upstream.ok
    if not push:
        ahead = _must(run(["git", "rev-list", "@{u}..HEAD", "--count"]), "git rev-list").out.strip()
        push = int(ahead or "0") > 0
    if push:
        _must(run(["git", "push", "-u", "origin", "HEAD"]), "git push -u origin HEAD")
    return {"pushed": push}


def pr_state(run: Run, number: int) -> Dict[str, Any]:
    """SP-14."""
    res = _must(run(["gh", "pr", "view", str(number), "--json",
                     "headRefOid,mergeable,mergeStateStatus"]), "gh pr view")
    data = json.loads(res.out)
    data["conflicting"] = data.get("mergeable") == "CONFLICTING"
    return data


def find_or_create_pr(run: Run, state: Dict[str, Any], title: Optional[str] = None,
                      body: Optional[str] = None) -> Dict[str, Any]:
    """SP-13: reuses the open PR, otherwise creates it if title and body are provided."""
    target, branch = state["target"], state["branch"]
    res = _must(run(["gh", "pr", "list", "--head", branch, "--base", target, "--state", "open",
                     "--json", "number,url,title"]), "gh pr list")
    prs = json.loads(res.out or "[]")
    created = False
    if prs:
        number, url = prs[0]["number"], prs[0]["url"]
    elif title is None or body is None:
        log = _must(run(["git", "log", "origin/%s..HEAD" % target, "--oneline"]), "git log").out
        stat = _must(run(["git", "diff", "origin/%s...HEAD" % target, "--stat"]), "git diff --stat").out
        return {"number": None, "log": log, "stat": stat}
    else:
        _must(run(["gh", "pr", "create", "--base", target, "--title", title, "--body", body]), "gh pr create")
        view = json.loads(_must(run(["gh", "pr", "view", "--json", "number,url"]), "gh pr view").out)
        number, url, created = view["number"], view["url"], True
    state["pr"], state["url"] = number, url
    result = {"number": number, "url": url, "created": created}
    result.update(pr_state(run, number))
    return result


def collect_diff(run: Run, state: Dict[str, Any]) -> str:
    """SP-15."""
    if state["round"] == 1 or not state.get("prev_round_sha"):
        return _must(run(["gh", "pr", "diff", str(state["pr"])]), "gh pr diff").out
    out = _must(run(["git", "diff", "%s..HEAD" % state["prev_round_sha"]]), "git diff").out
    flagged = sorted({c["file"] for c in state["candidates"].values() if c["status"] == "CONFIRMED"})
    if flagged:
        out += _must(run(["git", "diff", "origin/%s...HEAD" % state["target"], "--"] + flagged),
                     "git diff flagged files").out
    return out


def comment_body(c: Dict[str, Any]) -> str:
    """FD-10: body of the comment of a candidate, through the finding schema."""
    return findings.to_pr_comment(findings.adapt("shippr-reviewer", c, fid=c.get("id") or "comment"))


def post_comment(run: Run, state: Dict[str, Any], fid: str) -> Dict[str, Any]:
    """SP-16."""
    c = state["candidates"].get(fid)
    if c is None or c["status"] != "CONFIRMED":
        raise Stop("only a CONFIRMED finding is posted: %s" % fid)
    sha = pr_state(run, state["pr"])["headRefOid"]
    base = ["gh", "api", "repos/%s/pulls/%d/comments" % (state["owner_repo"], state["pr"]),
            "-f", "body=" + comment_body(c), "-f", "commit_id=" + sha, "-f", "path=" + c["file"]]
    res = run(base + ["-F", "line=%d" % c["line"], "-f", "side=" + c.get("side", "RIGHT")])
    mode = "inline"
    if not res.ok:
        if not HTTP_422.search(res.err + res.out):
            raise Stop("inline comment refused (not HTTP 422): %s" % (res.err or res.out).strip())
        res = run(base + ["-f", "subject_type=file"])
        mode = "file"
        if not res.ok and not HTTP_422.search(res.err + res.out):
            raise Stop("file-level comment refused (not HTTP 422): %s" % (res.err or res.out).strip())
    if res.ok:
        c["comment_id"] = json.loads(res.out)["id"]
        return {"finding": fid, "mode": mode, "comment_id": c["comment_id"], "commit_id": sha}
    body = "%s\n\n(%s, line %d)" % (comment_body(c), c["file"], c["line"])
    _must(run(["gh", "pr", "comment", str(state["pr"]), "--body", body]), "gh pr comment")
    return {"finding": fid, "mode": "pr", "comment_id": None, "commit_id": sha}


def _no_checkpoint() -> None:
    pass


def commit_message(round_number: int, summary: str, trailers: Sequence[str] = ()) -> str:
    """SP-17: subject, then each trailer (attribution) after a blank line."""
    message = "fix(review): round %d fixes — %s" % (round_number, summary.strip())
    if trailers:
        message += "\n\n" + "\n".join(trailers)
    return message


def commit_round(run: Run, state: Dict[str, Any], reg: registry_mod.Registry, summary: str,
                 fixed: List[str], not_fixed: Dict[str, str], trailers: Sequence[str] = (),
                 checkpoint: Callable[[], None] = _no_checkpoint) -> Dict[str, Any]:
    """SP-17 and SP-09.

    The fix commit and the outcomes are recorded (state 'unpushed', then
    checkpoint) BEFORE the push: a failed push leaves the hash recorded, and a
    relaunch of the round only pushes again, it never commits a second time."""
    pending = state.get("unpushed")
    if pending and pending["round"] == state["round"]:
        _must(run(["git", "push"]), "git push")
        state["commits"].append({"round": pending["round"], "hash": pending["hash"], "summary": pending["summary"]})
        state["unpushed"] = None
        checkpoint()
        return {"commit": pending["hash"], "fixed": pending["fixed"], "not_fixed": pending["not_fixed"],
                "resumed": True}
    if "\n" in summary.strip() or "\r" in summary.strip():
        # Subject line only: a body glued to it without a blank line cannot be
        # amended afterwards (never an amend).
        raise Stop("the commit summary must be a single line")
    ids = fixed + list(not_fixed)
    duplicates = sorted({fid for fid in ids if ids.count(fid) > 1})
    if duplicates:
        raise Stop("finding(s) recorded twice: %s" % ", ".join(duplicates))
    for fid in ids:
        c = state["candidates"].get(fid)
        if c is None or c["round"] != state["round"] or c["status"] != "CONFIRMED" or c["outcome"]:
            raise Stop("%s is not a pending CONFIRMED finding of the current round" % fid)
    commit = None
    if fixed:
        pre = _must(run(["git", "rev-parse", "HEAD"]), "git rev-parse").out.strip()
        shippr.mark_pre_fix(state, pre)
        _must(run(["git", "add", "-A"]), "git add -A")
        _must(run(["git", "commit", "-m", commit_message(state["round"], summary, trailers)]), "git commit")
        commit = _must(run(["git", "rev-parse", "HEAD"]), "git rev-parse").out.strip()
        state["unpushed"] = {"round": state["round"], "hash": commit, "summary": summary.strip(),
                             "fixed": list(fixed), "not_fixed": list(not_fixed)}
    for fid in fixed:
        shippr.set_outcome(state, reg, fid, True, commit=commit)
    for fid, reason in not_fixed.items():
        shippr.set_outcome(state, reg, fid, False, reason=reason)
    if commit is not None:
        checkpoint()
        _must(run(["git", "push"]), "git push")
        state["commits"].append({"round": state["round"], "hash": commit, "summary": summary.strip()})
        state["unpushed"] = None
    return {"commit": commit, "fixed": fixed, "not_fixed": list(not_fixed)}


def handle_threads(run: Run, state: Dict[str, Any],
                   checkpoint: Callable[[], None] = _no_checkpoint) -> Dict[str, Any]:
    """SP-18. Each posted reply, then each finished thread, is marked and
    checkpointed before the next step: a relaunch after an interruption never
    posts a reply twice."""
    owner, repo = state["owner_repo"].split("/", 1)
    res = _must(run(["gh", "api", "graphql", "-f", "query=" + THREADS_QUERY, "-f", "owner=" + owner,
                     "-f", "repo=" + repo, "-F", "number=%d" % state["pr"]]), "gh api graphql")
    nodes = json.loads(res.out)["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    thread_of = {}
    for node in nodes:
        for com in node["comments"]["nodes"]:
            thread_of[com["databaseId"]] = node["id"]
    resolved, left_open = [], []
    for c in state["candidates"].values():
        if c["round"] != state["round"] or c["outcome"] is None or c["thread_done"] or c["comment_id"] is None:
            continue
        replies = "repos/%s/pulls/%d/comments/%d/replies" % (state["owner_repo"], state["pr"], c["comment_id"])
        if c["outcome"] == "FIXED":
            if not c.get("replied"):
                _must(run(["gh", "api", replies, "-f", "body=Fixed in %s." % c["commit"]]), "thread reply")
                c["replied"] = True
                checkpoint()
            thread = thread_of.get(c["comment_id"])
            if thread is None:
                raise Stop("thread not found for comment %s" % c["comment_id"])
            _must(run(["gh", "api", "graphql", "-f", "query=" + RESOLVE_MUTATION, "-f", "t=" + thread]),
                  "thread resolution")
            resolved.append(c["id"])
        else:
            _must(run(["gh", "api", replies, "-f", "body=" + c["reason"]]), "thread reply")
            left_open.append(c["id"])
        c["thread_done"] = True
        checkpoint()
    return {"resolved": resolved, "left_open": left_open}


def merge(run: Run, state: Dict[str, Any], check_only: bool) -> Dict[str, Any]:
    """SP-19 and SP-05."""
    refusal = shippr.merge_refusal(state)
    if refusal:
        raise Stop(refusal)
    if check_only:
        result = shippr.summary(state)
        result["pr_state"] = pr_state(run, state["pr"])
        return result
    _must(run(["gh", "pr", "merge", str(state["pr"]), "--merge", "--delete-branch"]), "gh pr merge")
    _must(run(["git", "checkout", state["target"]]), "git checkout")
    _must(run(["git", "pull"]), "git pull")
    state["merged"] = True
    return {"merged": True, "pr": state["pr"], "target": state["target"]}
