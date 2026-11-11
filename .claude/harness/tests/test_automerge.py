# Automatic merge of ship-pr: SM-01 to SM-08 (the five conditions, the comment, the worktree merge).
#
# SM-08 (the rule of ship-pr/SKILL.md is rewritten) is a property of the prose: checked by the grep of CA-14.

import json
import unittest

import helpers
from hcore import automerge, ghflow, journal, runs, shippr
from hcore.proc import Result

STATE = {"pr": 7, "target": "main", "branch": "feat", "owner_repo": "acme/toy", "exit": "exhaustion",
         "uncovered": [], "candidates": {}, "merged": False}


def ok(out=""):
    return Result(0, out, "")


def runner(checks=None, threads=None, protected=False, mergeable="MERGEABLE", api_error=False, pages=None):
    """A FakeRunner answering gh for one scenario of the five conditions."""
    r = helpers.FakeRunner()
    if checks is None:
        r.on(("gh", "pr", "checks"), 1, "", "no checks reported on the 'feat' branch")
    else:
        r.on(("gh", "pr", "checks"), 0 if all(c["bucket"] == "pass" for c in checks) else 1, json.dumps(checks))
    nodes = threads if threads is not None else [{"id": "T1", "isResolved": True}]
    if pages:
        calls = {"n": 0}

        def graphql(args, input=None):
            r.calls.append(list(args))
            page = pages[min(calls["n"], len(pages) - 1)]
            calls["n"] += 1
            return ok(json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": page}}}}))
        r.graphql = graphql
    else:
        r.on(("gh", "api", "graphql"), 0, json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {
            "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}}}))
    if api_error:
        r.on(("gh", "api", "repos/acme/toy/branches/main"), 1, "", "HTTP 404")
    else:
        r.on(("gh", "api", "repos/acme/toy/branches/main"), 0, json.dumps({"protected": protected}))
    r.on(("gh", "pr", "view"), 0, json.dumps({"mergeable": mergeable}))
    r.on(("gh", "pr", "comment"), 0, "https://github.com/acme/toy/pull/7#issuecomment-555\n")
    r.on(("gh", "pr", "merge"), 0, "")
    r.on(("git",), 0, "")
    if pages:
        original = r.__call__

        def dispatch(args, input=None):
            return r.graphql(args) if args[:3] == ["gh", "api", "graphql"] else original(args, input)
        r.dispatch = dispatch
    return r


def call(r):
    return getattr(r, "dispatch", r)


def decide(r, state=None, protected=(), wait=0.0, sleeps=None):
    sleeps = sleeps if sleeps is not None else []
    clock = iter(range(0, 100000, 1))
    return automerge.evaluate(call(r), dict(STATE, **(state or {})), list(protected), wait, sleeps.append,
                              lambda: float(next(clock)) * 1000)


class ConditionsTest(unittest.TestCase):
    def test_sm01_all_true_merges_and_all_five_are_reported(self):
        """SM-01, CA-13: every condition true -> merge; five conditions with ok and detail."""
        d = decide(runner())
        self.assertTrue(d["merge"])
        self.assertEqual(sorted(d["conditions"]), ["C1", "C2", "C3", "C4", "C5"])
        self.assertTrue(all(c["ok"] and c["detail"] for c in d["conditions"].values()))
        self.assertEqual(d["conditions"]["C3"]["detail"], "no CI")

    def test_sm01_each_false_condition_blocks_alone_and_none_short_circuits(self):
        """SM-01: one false condition -> no merge, the other four still evaluated and reported."""
        cases = {
            "C1": dict(state={"candidates": {"R1-F1": {"id": "R1-F1", "status": "CONFIRMED", "outcome": "NOT_FIXED"}}}),
            "C2": dict(r=runner(threads=[{"id": "T1", "isResolved": False}, {"id": "T2", "isResolved": True}])),
            "C3": dict(r=runner(checks=[{"name": "build", "bucket": "fail"}])),
            "C4": dict(r=runner(protected=True)),
            "C5": dict(r=runner(mergeable="CONFLICTING")),
        }
        for label, case in cases.items():
            d = decide(case.get("r") or runner(), case.get("state"))
            self.assertFalse(d["merge"], label)
            self.assertEqual([k for k, c in d["conditions"].items() if not c["ok"]], [label], label)

    def test_sm01_c1_unvalidated_blocks_and_a_cap_exit_is_signalled(self):
        """SM-01 C1 (Q3, Q4): UNVALIDATED blocks; all CONFIRMED fixed with a cap exit passes, signalled."""
        bad = decide(runner(), {"candidates": {"R1-F1": {"id": "R1-F1", "status": "UNVALIDATED"}}})
        self.assertFalse(bad["conditions"]["C1"]["ok"])
        cap = decide(runner(), {"exit": "cap", "candidates": {"R1-F1": {"id": "R1-F1", "status": "CONFIRMED",
                                                                      "outcome": "FIXED"}}})
        self.assertTrue(cap["conditions"]["C1"]["ok"])
        self.assertIn("cap", cap["conditions"]["C1"]["detail"])

    def test_sm01_c2_reads_every_page(self):
        """SM-01 C2: more than 100 threads are read page by page; an unresolved one on page 2 is found."""
        p1 = {"pageInfo": {"hasNextPage": True, "endCursor": "c1"}, "nodes": [{"id": "A", "isResolved": True}]}
        p2 = {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [{"id": "B", "isResolved": False}]}
        d = decide(runner(pages=[p1, p2]))
        self.assertFalse(d["conditions"]["C2"]["ok"])
        self.assertIn("1 of 2", d["conditions"]["C2"]["detail"])

    def test_sm01_c3_pending_is_polled_then_false(self):
        """SM-01 C3 (Q5): pending is reread every 30 s for ci_wait_min, then false; skipping counts as green."""
        sleeps = []
        d = decide(runner(checks=[{"name": "build", "bucket": "pending"}]), wait=20.0, sleeps=sleeps)
        self.assertFalse(d["conditions"]["C3"]["ok"])
        self.assertIn("pending", d["conditions"]["C3"]["detail"])
        self.assertTrue(sleeps and set(sleeps) == {automerge.CI_POLL_S})
        green = decide(runner(checks=[{"name": "a", "bucket": "pass"}, {"name": "b", "bucket": "skipping"}]))
        self.assertTrue(green["conditions"]["C3"]["ok"])

    def test_sm01_c4_protection_unknown_and_listed(self):
        """SM-01 C4 (Q6): an API error is false (protection unknown); a target in ship_pr.protected is false."""
        d = decide(runner(api_error=True))
        self.assertEqual((d["conditions"]["C4"]["ok"], d["conditions"]["C4"]["detail"]), (False, "protection unknown"))
        d = decide(runner(), protected=["main"])
        self.assertFalse(d["conditions"]["C4"]["ok"])

    def test_sm01_c5_unknown_is_reread_five_times(self):
        """SM-01 C5: UNKNOWN is reread 5 times 3 s apart, then false."""
        sleeps = []
        r = runner(mergeable="UNKNOWN")
        d = decide(r, sleeps=sleeps)
        self.assertEqual((d["conditions"]["C5"]["ok"], sleeps.count(automerge.UNKNOWN_DELAY_S)), (False, 5))
        self.assertEqual(len(r.called("gh", "pr", "view")), 6)


class MergeFlowTest(helpers.TempDirCase):
    def state(self, **extra):
        return dict(STATE, rounds_data={}, **extra)

    def test_sm02_sm03_sm04_comment_first_then_merge_once(self):
        """SM-02, SM-03, SM-04: the comment is posted BEFORE the merge, once, with --body-file; a relaunch does not repost."""
        r = runner()
        state = self.state()
        result = automerge.run_auto(r, self.tmp, state, [], False, 0.0, lambda s: None)
        self.assertTrue(result["merged"])
        order = [c[1:3] for c in r.calls if c[:3] in (["gh", "pr", "comment"], ["gh", "pr", "merge"])]
        self.assertEqual(order, [["pr", "comment"], ["pr", "merge"]])
        comment = r.called("gh", "pr", "comment")[0]
        self.assertIn("--body-file", comment)
        body = (self.tmp / ".harness" / "runs" / "merge-comment-pr7.md").read_text(encoding="utf-8")
        self.assertTrue(body.startswith("Automatic merge: all conditions met"))
        self.assertEqual(body.count("- C"), 5)
        state["merged"] = False
        again = automerge.run_auto(r, self.tmp, state, [], False, 0.0, lambda s: None)
        self.assertFalse(again["comment"]["reposted"])
        self.assertEqual(len(r.called("gh", "pr", "comment")), 1)

    def test_sm03_a_false_condition_leaves_the_pr_open_with_the_reasons(self):
        """SM-03: no merge, a comment listing the false conditions, no exception, no question."""
        r = runner(protected=True)
        result = automerge.run_auto(r, self.tmp, self.state(), [], False, 0.0, lambda s: None)
        self.assertFalse(result["merged"])
        self.assertEqual(r.called("gh", "pr", "merge"), [])
        body = (self.tmp / ".harness" / "runs" / "merge-comment-pr7.md").read_text(encoding="utf-8")
        self.assertTrue(body.startswith("Automatic merge not performed:"))
        self.assertIn("C4", body)
        self.assertNotIn("- C1", body)

    def test_sm02_the_sp05_refusal_counts_as_a_false_condition(self):
        """SM-02, CA-14: an uncovered theme or an unfinished loop -> PR open, reason in the comment, conditions not run."""
        r = runner()
        uncovered = self.state(uncovered=[{"theme": "logic", "round": 1}])
        result = automerge.run_auto(r, self.tmp, uncovered, [], False, 0.0, lambda s: None)
        self.assertFalse(result["merged"])
        self.assertIn("not covered", result["refusal"])
        self.assertEqual(r.called("gh", "pr", "merge"), [])
        self.assertIn("review loop", (self.tmp / ".harness" / "runs" / "merge-comment-pr7.md").read_text(encoding="utf-8"))
        unfinished = automerge.decide(runner(), self.state(exit=None), [], 0.0)
        self.assertFalse(unfinished["merge"])

    def test_sm06_worktree_merge_never_checks_out_nor_pulls(self):
        """SM-06, CA-15: `gh pr merge --merge` without --delete-branch, remote branch deleted by the core, no checkout, no pull."""
        r = runner()
        state = self.state()
        automerge.run_auto(r, self.tmp, state, [], True, 0.0, lambda s: None)
        merge = r.called("gh", "pr", "merge")[0]
        self.assertEqual(merge, ["gh", "pr", "merge", "7", "--merge"])
        self.assertEqual(r.called("git", "push", "origin", "--delete", "feat"), [["git", "push", "origin", "--delete", "feat"]])
        self.assertEqual(r.called("git", "checkout"), [])
        self.assertEqual(r.called("git", "pull"), [])
        self.assertTrue(state["merged"])

    def test_sm06_outside_a_worktree_the_standard_merge_is_kept(self):
        """SM-06: the registered-worktree rule does not change the standard SP-19 merge."""
        r = runner()
        automerge.run_auto(r, self.tmp, self.state(), [], False, 0.0, lambda s: None)
        self.assertEqual(r.called("gh", "pr", "merge")[0], ["gh", "pr", "merge", "7", "--merge", "--delete-branch"])
        self.assertTrue(r.called("git", "checkout"))


class JournalAndSpecTest(helpers.TempDirCase):
    def test_sm03_merge_decision_event_schema(self):
        """SM-03: the `merge_decision` event carries merged and the conditions; invalid without them."""
        run = runs.create(self.tmp, "ship-pr", shippr.new_state("main", 3, ["t"]))
        journal.append(run, {"event": "merge_decision", "merged": False,
                             "conditions": {"C1": True, "C2": True, "C3": True, "C4": False, "C5": True}})
        self.assertEqual(journal.validate_file(run), [])
        self.assertNotEqual(journal.validate_record({"event": "merge_decision", "merged": "yes"}), [])

    def test_sm05_sm07_options_of_the_command_line(self):
        """SM-05: `merge --check --conditions` (gate policy, information); SM-07: `start --spec` (link to the queue)."""
        import harness
        args = harness.build_parser().parse_args(["ship-pr", "start", "--target", "main", "--spec", "specs/SPEC-a.md",
                                                  "--autonomy", "auto"])
        self.assertEqual((args.spec, args.autonomy), ("specs/SPEC-a.md", "auto"))
        merge = harness.build_parser().parse_args(["ship-pr", "merge", "--run", "r", "--auto"])
        self.assertTrue(merge.auto)
        info = harness.build_parser().parse_args(["ship-pr", "merge", "--run", "r", "--check", "--conditions"])
        self.assertTrue(info.check and info.conditions and not info.auto)


if __name__ == "__main__":
    unittest.main()
