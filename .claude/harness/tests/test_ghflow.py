# Module hcore.ghflow: ship-pr gh/git sequences with simulated gh and git
# (SP-11 to SP-19), stop messages (CA7), git exclusion of the journal (D3, CA11).

import contextlib
import io
import json
import os
import sys
import unittest
from unittest import mock

import helpers
from hcore import ghflow, journal, registry, runs, shippr
from hcore.ghflow import Stop
from hcore.proc import Runner


def green_preflight():
    return (helpers.FakeRunner()
            .on(("git", "remote", "get-url", "origin"), out="https://github.com/acme/toy.git\n")
            .on(("git", "rev-parse", "--abbrev-ref", "HEAD"), out="feature\n"))


class PreflightTest(unittest.TestCase):
    def test_sp11_gh_not_authenticated(self):
        """SP-11."""
        run = green_preflight().on(("gh", "auth", "status"), code=1)
        with self.assertRaises(Stop) as ctx:
            ghflow.preflight_checks(run, "main")
        self.assertIn("gh not authenticated", str(ctx.exception))
        self.assertFalse(run.called("git"))

    def test_sp11_no_github_remote(self):
        """SP-11."""
        run = green_preflight().on(("git", "remote", "get-url", "origin"), out="https://gitlab.com/x/y.git\n")
        with self.assertRaises(Stop) as ctx:
            ghflow.preflight_checks(run, "main")
        self.assertIn("no GitHub remote", str(ctx.exception))

    def test_sp11_already_on_target(self):
        """SP-11."""
        run = green_preflight().on(("git", "rev-parse", "--abbrev-ref", "HEAD"), out="main\n")
        with self.assertRaises(Stop) as ctx:
            ghflow.preflight_checks(run, "main")
        self.assertIn("you are already on main", str(ctx.exception))

    def test_sp11_target_missing_on_origin(self):
        """SP-11."""
        run = green_preflight().on(("git", "rev-parse", "--verify", "origin/main"), code=128)
        with self.assertRaises(Stop) as ctx:
            ghflow.preflight_checks(run, "main")
        self.assertIn("the target branch main does not exist on origin", str(ctx.exception))

    def test_sp11_green_and_dirty_reported(self):
        """SP-11."""
        run = green_preflight()
        self.assertEqual(ghflow.preflight_checks(run, "main"), {"branch": "feature"})
        self.assertEqual(run.called("git", "fetch"), [["git", "fetch", "origin", "main"]])
        run.on(("git", "status", "--porcelain"), out=" M a.py\n").on(("gh", "repo", "view"), out="acme/toy\n")
        info = ghflow.worktree_and_repo(run)
        self.assertEqual(info, {"dirty": True, "dirty_files": [" M a.py"], "owner_repo": "acme/toy"})


class UpstreamTest(unittest.TestCase):
    def test_sp12_push_when_no_upstream(self):
        """SP-12."""
        run = helpers.FakeRunner().on(("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name"), code=128)
        self.assertTrue(ghflow.sync_upstream(run)["pushed"])
        self.assertEqual(run.called("git", "push"), [["git", "push", "-u", "origin", "HEAD"]])

    def test_sp12_push_only_when_ahead(self):
        """SP-12."""
        run = helpers.FakeRunner().on(("git", "rev-list"), out="0\n")
        self.assertFalse(ghflow.sync_upstream(run)["pushed"])
        self.assertFalse(run.called("git", "push"))
        run = helpers.FakeRunner().on(("git", "rev-list"), out="2\n")
        self.assertTrue(ghflow.sync_upstream(run)["pushed"])


def pr_view_state(run, sha="sha-head"):
    return run.on(("gh", "pr", "view", "7"), out=json.dumps(
        {"headRefOid": sha, "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"}))


class PullRequestTest(unittest.TestCase):
    def state(self):
        st = shippr.new_state("main", 3, ["a"])
        st.update(branch="feature", owner_repo="acme/toy")
        return st

    def test_sp13_existing_pr_reused(self):
        """SP-13."""
        run = pr_view_state(helpers.FakeRunner().on(("gh", "pr", "list"),
                                                    out='[{"number": 7, "url": "u", "title": "t"}]'))
        st = self.state()
        result = ghflow.find_or_create_pr(run, st)
        self.assertEqual((result["number"], result["created"], st["pr"]), (7, False, 7))
        self.assertFalse(run.called("gh", "pr", "create"))
        self.assertIn(["gh", "pr", "list", "--head", "feature", "--base", "main", "--state", "open",
                       "--json", "number,url,title"], run.calls)

    def test_sp13_missing_pr_returns_material_then_creates(self):
        """SP-13."""
        run = helpers.FakeRunner().on(("gh", "pr", "list"), out="[]").on(("git", "log"), out="abc fix\n")
        st = self.state()
        material = ghflow.find_or_create_pr(run, st)
        self.assertIsNone(material["number"])
        self.assertEqual(material["log"], "abc fix\n")
        pr_view_state(run.on(("gh", "pr", "view", "--json", "number,url"), out='{"number": 7, "url": "u"}'))
        result = ghflow.find_or_create_pr(run, st, "Title", "Body")
        self.assertTrue(result["created"])
        self.assertEqual(run.called("gh", "pr", "create")[0],
                         ["gh", "pr", "create", "--base", "main", "--title", "Title", "--body", "Body"])

    def test_sp14_conflict_detected(self):
        """SP-14."""
        run = helpers.FakeRunner().on(("gh", "pr", "view", "7"), out=json.dumps(
            {"headRefOid": "s", "mergeable": "CONFLICTING", "mergeStateStatus": "DIRTY"}))
        self.assertTrue(ghflow.pr_state(run, 7)["conflicting"])


class RoundFlowTest(helpers.TempDirCase):
    def setUp(self):
        super().setUp()
        self.reg = registry.Registry(self.tmp / "r.registry.jsonl")
        self.st = shippr.new_state("main", 3, ["a"])
        self.st.update(branch="feature", owner_repo="acme/toy", pr=7)
        out = helpers.reviewer_block([helpers.finding(file="src/x.py", line=4, title="Off by one")])
        self.fid = shippr.record_reviewer(self.st, self.reg, "a", out, False)[0]["to_validate"][0]["id"]
        shippr.record_validator(self.st, self.reg, self.fid, helpers.validator_block("CONFIRMED"), False)

    def test_sp15_round1_pr_diff_then_incremental(self):
        """SP-15."""
        run = helpers.FakeRunner().on(("gh", "pr", "diff"), out="DIFF1")
        self.assertEqual(ghflow.collect_diff(run, self.st), "DIFF1")
        self.st["round"], self.st["prev_round_sha"] = 2, "s1"
        run = helpers.FakeRunner().on(("git", "diff", "s1..HEAD"), out="INC\n").on(("git", "diff", "origin/main...HEAD"), out="FLAG\n")
        self.assertEqual(ghflow.collect_diff(run, self.st), "INC\nFLAG\n")
        self.assertIn(["git", "diff", "origin/main...HEAD", "--", "src/x.py"], run.calls)

    def test_sp15_reference_is_head_before_fix_commit_real_git(self):
        """SP-15: real replay on a throwaway repository; round 2 reviews the fix
        commit of round 1 (reference = HEAD before that commit)."""
        repo, bare = self.tmp / "repo", self.tmp / "origin.git"
        helpers.git(self.tmp, "init", "-q", "--bare", "-b", "main", str(bare))
        helpers.init_repo(repo)
        (repo / "a.txt").write_text("base\n", encoding="utf-8")
        helpers.git(repo, "add", "-A")
        helpers.git(repo, "commit", "-q", "-m", "base")
        helpers.git(repo, "remote", "add", "origin", bare.as_posix())
        helpers.git(repo, "push", "-q", "-u", "origin", "main")
        helpers.git(repo, "checkout", "-q", "-b", "feature")
        helpers.git(repo, "push", "-q", "-u", "origin", "feature")
        reviewed = helpers.git(repo, "rev-parse", "HEAD").strip()
        (repo / "src").mkdir()
        (repo / "src" / "x.py").write_text("x = 1\n", encoding="utf-8")
        (repo / "never_flagged.py").write_text("y = 2\n", encoding="utf-8")
        run = Runner(cwd=str(repo))
        ghflow.commit_round(run, self.st, self.reg, "bound", [self.fid], {})
        result = shippr.end_round(self.st, helpers.git(repo, "rev-parse", "HEAD").strip())
        self.assertEqual(result["next_round"], 2)
        self.assertEqual(self.st["prev_round_sha"], reviewed)
        diff = ghflow.collect_diff(run, self.st)
        self.assertIn("never_flagged.py", diff)
        self.assertIn("y = 2", diff)

    def test_sp16_inline_comment_uses_fresh_head(self):
        """SP-16."""
        run = pr_view_state(helpers.FakeRunner(), sha="fresh").on(("gh", "api"), out='{"id": 99}')
        result = ghflow.post_comment(run, self.st, self.fid)
        self.assertEqual((result["mode"], result["comment_id"], result["commit_id"]), ("inline", 99, "fresh"))
        call = run.called("gh", "api")[0]
        self.assertIn("commit_id=fresh", call)
        self.assertIn("line=4", call)
        self.assertIn("side=RIGHT", call)

    def test_sp16_fallback_422_then_pr_comment(self):
        """SP-16."""
        class Seq(helpers.FakeRunner):
            def __call__(inner, args, input=None):
                if args[:2] == ["gh", "api"]:
                    inner.calls.append(list(args))
                    if "subject_type=file" in args:
                        return helpers.Result(0, '{"id": 5}', "")
                    return helpers.Result(1, "", "HTTP 422: Unprocessable")
                return helpers.FakeRunner.__call__(inner, args, input)
        run = pr_view_state(Seq())
        result = ghflow.post_comment(run, self.st, self.fid)
        self.assertEqual((result["mode"], result["comment_id"]), ("file", 5))
        run = pr_view_state(helpers.FakeRunner()).on(("gh", "api"), code=1, err="HTTP 422")
        result = ghflow.post_comment(run, self.st, self.fid)
        self.assertEqual(result["mode"], "pr")
        self.assertTrue(run.called("gh", "pr", "comment"))

    def test_sp16_non_422_error_stops_without_fallback(self):
        """SP-16: the fallback exists only on HTTP 422; any other error stops."""
        run = pr_view_state(helpers.FakeRunner()).on(("gh", "api"), code=1, err="HTTP 502: Bad Gateway")
        with self.assertRaises(Stop) as ctx:
            ghflow.post_comment(run, self.st, self.fid)
        self.assertIn("502", str(ctx.exception))
        self.assertEqual(len(run.called("gh", "api")), 1)
        self.assertFalse(run.called("gh", "pr", "comment"))
        self.assertIsNone(self.st["candidates"][self.fid]["comment_id"])

    def test_sp16_bare_422_elsewhere_in_error_does_not_trigger_fallback(self):
        """SP-16: only "HTTP 422" triggers the fallback, not a 422 in an identifier
        or a response body; the real gh message ("(HTTP 422)") triggers it."""
        err = "HTTP 500: Internal Server Error (request 4220:1422AB, line 422)"
        run = pr_view_state(helpers.FakeRunner()).on(("gh", "api"), code=1, err=err)
        with self.assertRaises(Stop):
            ghflow.post_comment(run, self.st, self.fid)
        self.assertEqual(len(run.called("gh", "api")), 1)
        run = pr_view_state(helpers.FakeRunner()).on(("gh", "api"), code=1, out='{"id": 4221, "code": 422}',
                                                      err="HTTP 403: Forbidden")
        with self.assertRaises(Stop):
            ghflow.post_comment(run, self.st, self.fid)
        run = pr_view_state(helpers.FakeRunner()).on(
            ("gh", "api"), code=1, err="gh: Validation Failed (HTTP 422)")
        self.assertEqual(ghflow.post_comment(run, self.st, self.fid)["mode"], "pr")

    def test_sp17_one_commit_per_round_never_amend_or_force(self):
        """SP-17."""
        run = helpers.FakeRunner().on(("git", "rev-parse", "HEAD"), out="c0ffee\n")
        result = ghflow.commit_round(run, self.st, self.reg, "bound fixed", [self.fid], {})
        self.assertEqual(result["commit"], "c0ffee")
        self.assertEqual(run.called("git", "commit"),
                         [["git", "commit", "-m", "fix(review): round 1 fixes — bound fixed"]])
        flat = [a for call in run.calls for a in call]
        for forbidden in ("--amend", "--force", "-f", "--force-with-lease"):
            self.assertNotIn(forbidden, flat)
        self.assertEqual(self.reg.current(self.st["candidates"][self.fid]["key"]), "FIXED")

    def test_sp16_file_level_failure_other_than_422_stops_without_pr_comment(self):
        """SP-16 (BACKLOG section 7): 422 inline, then a different failure on the
        file-level fallback -> explicit stop, never the gh pr comment fallback."""
        class Seq(helpers.FakeRunner):
            def __call__(inner, args, input=None):
                if args[:2] == ["gh", "api"]:
                    inner.calls.append(list(args))
                    if "subject_type=file" in args:
                        return helpers.Result(1, "", "HTTP 502: Bad Gateway")
                    return helpers.Result(1, "", "gh: Validation Failed (HTTP 422)")
                return helpers.FakeRunner.__call__(inner, args, input)
        run = pr_view_state(Seq())
        with self.assertRaises(Stop) as ctx:
            ghflow.post_comment(run, self.st, self.fid)
        self.assertIn("502", str(ctx.exception))
        self.assertEqual(len(run.called("gh", "api")), 2)
        self.assertFalse(run.called("gh", "pr", "comment"))
        self.assertIsNone(self.st["candidates"][self.fid]["comment_id"])

    def test_sp17_trailers_added_after_a_blank_line(self):
        """SP-17 (BACKLOG section 7, attribution): each --trailer after a blank line."""
        run = helpers.FakeRunner().on(("git", "rev-parse", "HEAD"), out="c0ffee\n")
        trailer = "Co-Authored-By: Claude <noreply@anthropic.com>"
        ghflow.commit_round(run, self.st, self.reg, "bound fixed", [self.fid], {}, [trailer, "Refs: #7"])
        self.assertEqual(run.called("git", "commit"), [["git", "commit", "-m",
                         "fix(review): round 1 fixes — bound fixed\n\n%s\nRefs: #7" % trailer]])

    def test_sp17_failed_push_keeps_hash_and_relaunch_does_not_recommit(self):
        """SP-17 (BACKLOG section 7): the push fails after the commit -> the hash
        is recorded and saved; the relaunch only pushes, it never commits again."""
        saved = []
        run = helpers.FakeRunner().on(("git", "rev-parse", "HEAD"), out="c0ffee\n").on(
            ("git", "push"), code=1, err="network down")
        with self.assertRaises(Stop):
            ghflow.commit_round(run, self.st, self.reg, "bound", [self.fid], {},
                                checkpoint=lambda: saved.append(json.dumps(self.st)))
        self.assertTrue(saved)
        on_disk = json.loads(saved[-1])
        self.assertEqual(on_disk["unpushed"]["hash"], "c0ffee")
        self.assertEqual(on_disk["candidates"][self.fid]["outcome"], "FIXED")
        self.st = on_disk
        run = helpers.FakeRunner().on(("git", "rev-parse", "HEAD"), out="c0ffee\n")
        result = ghflow.commit_round(run, self.st, self.reg, "bound", [self.fid], {})
        self.assertEqual((result["commit"], result["resumed"]), ("c0ffee", True))
        self.assertEqual(run.called("git", "commit"), [])
        self.assertEqual(run.called("git", "push"), [["git", "push"]])
        self.assertEqual(self.st["commits"], [{"round": 1, "hash": "c0ffee", "summary": "bound"}])
        self.assertIsNone(self.st["unpushed"])

    def test_sp17_cli_failed_push_saved_then_resumed(self):
        """SP-17 through the CLI: exit 1 on the push, state saved, the relaunch
        pushes the same commit without a second one."""
        repo, bare = self.tmp / "repo", self.tmp / "origin.git"
        helpers.git(self.tmp, "init", "-q", "--bare", "-b", "main", str(bare))
        helpers.init_repo(repo)
        (repo / "a.txt").write_text("base\n", encoding="utf-8")
        helpers.git(repo, "add", "-A")
        helpers.git(repo, "commit", "-q", "-m", "base")
        helpers.git(repo, "remote", "add", "origin", bare.as_posix())
        helpers.git(repo, "push", "-q", "-u", "origin", "main")
        helpers.git(repo, "checkout", "-q", "-b", "feature")
        helpers.git(repo, "push", "-q", "-u", "origin", "feature")
        (repo / ".git" / "info" / "exclude").write_text(".harness/runs/\n", encoding="utf-8")
        self.st["root"] = str(repo)
        run = runs.create(repo, "ship-pr", self.st)
        (repo / "a.txt").write_text("fixed\n", encoding="utf-8")
        helpers.git(repo, "remote", "set-url", "origin", (self.tmp / "missing.git").as_posix())
        summary = helpers.write_output(repo, "summary.txt", "bound\n")
        args = ["ship-pr", "commit", "--run", str(run), "--summary-file", summary, "--fixed", self.fid]
        failed = helpers.harness(args, repo)
        self.assertEqual(failed.returncode, 1, failed.stderr)
        self.assertIn("git push", failed.stderr)
        # The transit file is consumed; the same command resumes without it.
        self.assertFalse((repo / summary).exists())
        head = helpers.git(repo, "rev-parse", "HEAD").strip()
        self.assertEqual(runs.load_state(run)["unpushed"]["hash"], head)
        helpers.git(repo, "remote", "set-url", "origin", bare.as_posix())
        resumed = helpers.harness(args, repo)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(json.loads(resumed.stdout)["commit"], head)
        self.assertEqual(helpers.git(repo, "rev-parse", "HEAD").strip(), head)
        self.assertEqual(helpers.git(repo, "rev-list", "--count", "main..HEAD").strip(), "1")
        self.assertEqual(helpers.git(repo, "rev-parse", "origin/feature").strip(), head)

    def test_sp09_not_fixed_given_twice_refused_by_the_cli(self):
        """SP-09 (BACKLOG section 7): --not-fixed A:r1 --not-fixed A:r2 no longer
        overwrite each other in a dict: refused (exit 2) before any git call."""
        self.st["root"] = str(self.tmp)
        run = runs.create(self.tmp, "ship-pr", self.st)
        res = helpers.harness(["ship-pr", "commit", "--run", str(run), "--not-fixed", "%s:r1" % self.fid,
                               "--not-fixed", "%s:r2" % self.fid], self.tmp)
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertIn("given twice", res.stderr)
        self.assertIsNone(runs.load_state(run)["candidates"][self.fid]["outcome"])

    def test_sp17_duplicate_id_refused_before_any_git_call(self):
        """SP-17, SP-09: an id recorded twice is refused before commit and push."""
        for fixed, not_fixed in (([self.fid, self.fid], {}), ([self.fid], {self.fid: "false positive"})):
            run = helpers.FakeRunner().on(("git", "rev-parse", "HEAD"), out="c0ffee\n")
            with self.assertRaises(Stop) as ctx:
                ghflow.commit_round(run, self.st, self.reg, "fix", fixed, not_fixed)
            self.assertIn(self.fid, str(ctx.exception))
            self.assertEqual(run.calls, [])
            self.assertIsNone(self.st["candidates"][self.fid]["outcome"])

    def test_sp18_threads_replied_and_resolved(self):
        """SP-18."""
        self.st["candidates"][self.fid]["comment_id"] = 99
        ghflow.commit_round(helpers.FakeRunner().on(("git", "rev-parse"), out="c0ffee\n"),
                            self.st, self.reg, "fix", [self.fid], {})
        graph = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [
            {"id": "T1", "isResolved": False, "comments": {"nodes": [{"databaseId": 99}]}}]}}}}}
        run = helpers.FakeRunner().on(("gh", "api", "graphql", "-f", "query=" + ghflow.THREADS_QUERY),
                                      out=json.dumps(graph))
        result = ghflow.handle_threads(run, self.st)
        self.assertEqual(result, {"resolved": [self.fid], "left_open": []})
        self.assertIn(["gh", "api", "repos/acme/toy/pulls/7/comments/99/replies", "-f", "body=Fixed in c0ffee."], run.calls)
        self.assertIn("t=T1", run.calls[-1])
        self.assertEqual(ghflow.handle_threads(run, self.st), {"resolved": [], "left_open": []})

    def test_sp18_interrupted_threads_never_repost_a_reply(self):
        """SP-18 (BACKLOG section 7): each thread is marked and saved before the
        next one; a relaunch after a failure posts no reply twice."""
        out = helpers.reviewer_block([helpers.finding(file="src/y.py", line=8, title="Second bug"),
                                      helpers.finding(file="src/z.py", line=9, title="Third bug")])
        self.st["themes"].append("b")
        others = [f["id"] for f in shippr.record_reviewer(self.st, self.reg, "b", out, False)[0]["to_validate"]]
        for i, fid in enumerate([self.fid] + others):
            if fid != self.fid:
                shippr.record_validator(self.st, self.reg, fid, helpers.validator_block("CONFIRMED"), False)
            self.st["candidates"][fid]["comment_id"] = 99 + i
        ghflow.commit_round(helpers.FakeRunner().on(("git", "rev-parse"), out="c0ffee\n"),
                            self.st, self.reg, "fix", [self.fid, others[0]], {others[1]: "out of scope"})
        graph = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [
            {"id": "T%d" % n, "isResolved": False, "comments": {"nodes": [{"databaseId": n}]}}
            for n in (99, 100, 101)]}}}}}
        saved = []
        checkpoint = lambda: saved.append(json.dumps(self.st))  # noqa: E731
        # The resolution of the second thread fails after its reply was posted.
        run = (helpers.FakeRunner().on(("gh", "api", "graphql", "-f", "query=" + ghflow.THREADS_QUERY),
                                       out=json.dumps(graph))
               .on(("gh", "api", "graphql", "-f", "query=" + ghflow.RESOLVE_MUTATION, "-f", "t=T100"),
                   code=1, err="HTTP 502"))
        with self.assertRaises(Stop):
            ghflow.handle_threads(run, self.st, checkpoint)
        first = [c for c in run.calls if c[2].endswith("/replies")]
        self.assertEqual(len(first), 2)
        self.st = json.loads(saved[-1])
        run = helpers.FakeRunner().on(("gh", "api", "graphql", "-f", "query=" + ghflow.THREADS_QUERY),
                                      out=json.dumps(graph))
        result = ghflow.handle_threads(run, self.st, checkpoint)
        self.assertEqual(result, {"resolved": [others[0]], "left_open": [others[1]]})
        again = [c for c in run.calls if c[2].endswith("/replies")]
        self.assertEqual([c[2] for c in again], ["repos/acme/toy/pulls/7/comments/101/replies"])
        self.assertIn("t=T100", [a for c in run.calls for a in c])

    def test_sp18_not_fixed_thread_left_open(self):
        """SP-18."""
        self.st["candidates"][self.fid]["comment_id"] = 99
        ghflow.commit_round(helpers.FakeRunner(), self.st, self.reg, "", [], {self.fid: "out of scope"})
        graph = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}}
        run = helpers.FakeRunner().on(("gh", "api", "graphql"), out=json.dumps(graph))
        result = ghflow.handle_threads(run, self.st)
        self.assertEqual(result["left_open"], [self.fid])
        self.assertFalse([c for c in run.calls if any("resolveReviewThread" in a for a in c)])

    def test_sp19_merge_refused_when_theme_uncovered(self):
        """SP-19."""
        self.st["exit"] = "exhaustion"
        self.st["uncovered"] = [{"round": 1, "theme": "a"}]
        run = helpers.FakeRunner()
        with self.assertRaises(Stop) as ctx:
            ghflow.merge(run, self.st, check_only=True)
        self.assertIn("not covered", str(ctx.exception))
        with self.assertRaises(Stop):
            ghflow.merge(run, self.st, check_only=False)
        self.assertFalse(run.called("gh", "pr", "merge"))

    def test_sp19_merge_commit_and_branch_deleted(self):
        """SP-19."""
        self.st["exit"] = "exhaustion"
        run = pr_view_state(helpers.FakeRunner())
        self.assertIn("rounds", ghflow.merge(run, self.st, check_only=True))
        self.assertFalse(run.called("gh", "pr", "merge"))
        ghflow.merge(run, self.st, check_only=False)
        self.assertEqual(run.called("gh", "pr", "merge"), [["gh", "pr", "merge", "7", "--merge", "--delete-branch"]])
        self.assertEqual(run.calls[-2:], [["git", "checkout", "main"], ["git", "pull"]])


class ExcludeTest(helpers.TempDirCase):
    def test_ca11_exclude_hides_journal_and_registry_idempotent(self):
        helpers.init_repo(self.tmp)
        run_cmd = Runner(cwd=str(self.tmp))
        self.assertTrue(ghflow.ensure_exclude(run_cmd, self.tmp))
        exclude = self.tmp / ".git" / "info" / "exclude"
        before = exclude.read_text(encoding="utf-8")
        self.assertFalse(ghflow.ensure_exclude(run_cmd, self.tmp))
        self.assertEqual(exclude.read_text(encoding="utf-8"), before)
        self.assertEqual(before.splitlines().count(".harness/runs/"), 1)
        run = runs.create(self.tmp, "ship-pr", {})
        journal.append(run, journal.make_record("reviewer", "inherit", 5, 1, "NO_FINDING"))
        registry.Registry(runs.registry_path(run)).add(helpers.finding(), "REJECTED")
        status = helpers.git(self.tmp, "status", "--porcelain", "--untracked-files=all")
        self.assertNotIn(".harness/runs", status)



class TestModeGuardTest(helpers.TempDirCase):
    """HCORE_GH / HCORE_GIT: honoured only with HCORE_TEST_MODE=1, and reported."""

    FAKE = json.dumps([sys.executable, "-c", "print('FAKE-GIT')"])

    def test_override_ignored_without_test_mode(self):
        env = {k: v for k, v in os.environ.items() if k != "HCORE_TEST_MODE"}
        env["HCORE_GIT"] = self.FAKE
        with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stderr(io.StringIO()) as err:
            res = Runner()(["git", "--version"])
        self.assertNotIn("FAKE-GIT", res.out)
        self.assertIn("git version", res.out)
        self.assertIn("HCORE_GIT ignored", err.getvalue())

    def test_override_honoured_and_signalled_in_test_mode(self):
        with mock.patch.dict(os.environ, {"HCORE_GIT": self.FAKE, "HCORE_TEST_MODE": "1"}):
            self.assertEqual(Runner()(["git", "--version"]).out.strip(), "FAKE-GIT")
        res = helpers.harness(["version"], self.tmp, env={"HCORE_GIT": self.FAKE, "HCORE_TEST_MODE": "1"})
        self.assertEqual(json.loads(res.stdout)["hcore_test_mode"]["overrides"]["git"][0], sys.executable)
        res = helpers.harness(["version"], self.tmp, env={"HCORE_GIT": self.FAKE, "HCORE_TEST_MODE": "0"})
        self.assertNotIn("hcore_test_mode", json.loads(res.stdout))


if __name__ == "__main__":
    unittest.main()
