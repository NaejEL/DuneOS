# Simulated gh for the ship-pr replays (not a test module).
#
# State persisted in the JSON file named by HCORE_FAKE_GH_STATE. The real git
# commands run in the current directory (throwaway repository).

import json
import os
import subprocess
import sys


def git(*args):
    return subprocess.run(["git"] + list(args), capture_output=True, text=True, encoding="utf-8").stdout


def opt(args, name):
    return args[args.index(name) + 1] if name in args else None


def fields(args):
    out = {}
    for i, a in enumerate(args):
        if a in ("-f", "-F") and i + 1 < len(args):
            key, _, value = args[i + 1].partition("=")
            out[key] = value
    return out


def main(argv):
    path = os.environ["HCORE_FAKE_GH_STATE"]
    state = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {
        "prs": [], "comments": [], "replies": [], "resolved": [], "merged": False, "calls": []}
    state["calls"].append(argv)
    out, code = "", 0
    if argv[:2] == ["auth", "status"]:
        pass
    elif argv[:2] == ["repo", "view"]:
        out = "acme/toy\n"
    elif argv[:2] == ["pr", "list"]:
        out = json.dumps([p for p in state["prs"] if p["head"] == opt(argv, "--head")])
    elif argv[:2] == ["pr", "create"]:
        head = git("rev-parse", "--abbrev-ref", "HEAD").strip()
        state["prs"].append({"number": 7, "url": "https://github.com/acme/toy/pull/7", "title": opt(argv, "--title"),
                             "body": opt(argv, "--body"), "head": head, "base": opt(argv, "--base")})
        out = "https://github.com/acme/toy/pull/7\n"
    elif argv[:2] == ["pr", "view"] and argv[2] == "--json":
        pr = state["prs"][-1]
        out = json.dumps({"number": pr["number"], "url": pr["url"]})
    elif argv[:2] == ["pr", "view"]:
        out = json.dumps({"headRefOid": git("rev-parse", "HEAD").strip(), "mergeable": "MERGEABLE",
                          "mergeStateStatus": "CLEAN"})
    elif argv[:2] == ["pr", "diff"]:
        out = git("diff", "origin/%s...HEAD" % state["prs"][-1]["base"])
    elif argv[:2] == ["pr", "comment"]:
        body = opt(argv, "--body")
        if body is None and opt(argv, "--body-file"):
            with open(opt(argv, "--body-file"), encoding="utf-8") as fh:
                body = fh.read()
        state["comments"].append({"id": None, "body": body})
    elif argv[:2] == ["pr", "checks"]:
        # SM-03: `checks` of the state file is a list of {name, bucket}; absent = no CI on the repository.
        checks = state.get("checks")
        if checks is None:
            sys.stderr.write("no checks reported on the branch\n")
            code = 1
        else:
            out = json.dumps(checks)
            code = 0 if all(c.get("bucket") == "pass" for c in checks) else 1
    elif argv[0] == "api" and "/branches/" in argv[1]:
        # SM-03: branch protection API; `protected` of the state file (default false).
        out = json.dumps({"protected": bool(state.get("protected", False))})
    elif argv[:2] == ["pr", "merge"]:
        pr = state["prs"][-1]
        subprocess.run(["git", "push", "-q", "origin", "HEAD:" + pr["base"]], capture_output=True)
        if state.get("emulate_delete_branch") and "--delete-branch" in argv:
            # What gh does with --delete-branch: remote branch deleted, switch to the target, local branch deleted.
            subprocess.run(["git", "checkout", "-q", pr["base"]], capture_output=True)
            subprocess.run(["git", "branch", "-q", "-D", pr["head"]], capture_output=True)
            subprocess.run(["git", "push", "-q", "origin", "--delete", pr["head"]], capture_output=True)
        state["merged"] = True
    elif argv[:2] == ["api", "graphql"]:
        f = fields(argv)
        if "resolveReviewThread" in f["query"]:
            state["resolved"].append(f["t"])
            out = json.dumps({"data": {"resolveReviewThread": {"thread": {"id": f["t"], "isResolved": True}}}})
        else:
            nodes = [{"id": "T%d" % c["id"], "isResolved": "T%d" % c["id"] in state["resolved"],
                      "comments": {"nodes": [{"databaseId": c["id"]}]}}
                     for c in state["comments"] if c["id"] is not None]
            out = json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": nodes}}}}})
    elif argv[0] == "api" and argv[1].endswith("/replies"):
        state["replies"].append({"to": int(argv[1].split("/")[-2]), "body": fields(argv)["body"]})
        out = json.dumps({"id": 1000 + len(state["replies"])})
    elif argv[0] == "api" and argv[1].endswith("/comments"):
        f = fields(argv)
        new_id = 100 + len(state["comments"])
        state["comments"].append({"id": new_id, "path": f.get("path"), "line": f.get("line"),
                                  "commit_id": f.get("commit_id")})
        out = json.dumps({"id": new_id})
    else:
        sys.stderr.write("fake gh: command not simulated %r\n" % (argv,))
        code = 2
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    sys.stdout.write(out)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
