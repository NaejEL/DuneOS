# The spec queue: FQ-01 to FQ-10.
#
# FQ-01 (the skill file and its frontmatter) is checked by the frontmatter gate of the verification (CA-38).

import json
import time
import unittest

import helpers
import phase4
from hcore import queue as queue_mod
from hcore import journal, runs
from hcore.proc import Runner


def specs():
    return {
        "SPEC-dep-a.md": phase4.spec_text("PROPOSEE", "A", ["Priorité : 2"]),
        "SPEC-dep-b.md": phase4.spec_text("PROPOSEE", "B", ["Dépend de : SPEC-dep-a", "Priorité : 1"]),
        "SPEC-solo.md": phase4.spec_text("PROPOSEE", "Solo", ["Priorité : 3"]),
    }


def out(test, res, code=0):
    test.assertEqual(res.returncode, code, "stdout=%s\nstderr=%s" % (res.stdout, res.stderr))
    return json.loads(res.stdout) if res.stdout.strip() else None


class ListTest(helpers.TempDirCase):
    def test_fq02_fq03_the_statuses_and_the_errors(self):
        """FQ-02, FQ-03, FQ-04, CA-21: merged, delivered, in progress (stale = interrupted), blocked, ready, proposed;
        a cycle and an unknown dependency are errors that make the specs ineligible."""
        work, _ = phase4.project(self.tmp, {
            "SPEC-merged.md": phase4.spec_text("LIVREE", "Merged"),
            "SPEC-proposed.md": phase4.spec_text("PROPOSEE", "Proposed"),
            "SPEC-running.md": phase4.spec_text("APPROUVEE", "Running"),
            "SPEC-blocked.md": phase4.spec_text("BLOQUEE", header=["Questions bloquantes : Q1"],
                                                questions=["- Q1 — a ?", "  Réponse :"]),
            "SPEC-ready.md": phase4.spec_text("BLOQUEE", header=["Questions bloquantes : Q1"],
                                              questions=["- Q1 — a ?", "  Réponse : yes"]),
            "SPEC-cycle-x.md": phase4.spec_text("PROPOSEE", header=["Dépend de : SPEC-cycle-y"]),
            "SPEC-cycle-y.md": phase4.spec_text("PROPOSEE", header=["Dépend de : SPEC-cycle-x"]),
            "SPEC-unknown-dep.md": phase4.spec_text("PROPOSEE", header=["Dépend de : SPEC-nowhere"]),
            "SPEC-approved.md": phase4.spec_text("APPROUVEE", "Approved"),
        })
        phase4.write_spec(work, "SPEC-delivered.md", phase4.spec_text("LIVREE", "Delivered"))     # local only: not pushed
        stale = runs.create(work, "factory-run", {"status": "running", "spec": "specs/SPEC-running.md"})
        old = time.time() - 3 * 3600
        import os
        os.utime(str(runs.state_path(stale)), (old, old))
        listing = queue_mod.scan(Runner(cwd=str(work)), work, "main")
        rows = {r["file"]: r for r in listing["specs"]}
        self.assertEqual(rows["SPEC-merged.md"]["status"], queue_mod.MERGED)
        self.assertEqual(rows["SPEC-delivered.md"]["status"], queue_mod.DELIVERED)
        self.assertEqual((rows["SPEC-running.md"]["status"], rows["SPEC-running.md"]["interrupted"]),
                         (queue_mod.IN_PROGRESS, True))
        self.assertEqual(rows["SPEC-blocked.md"]["status"], queue_mod.BLOCKED)
        self.assertEqual(rows["SPEC-ready.md"]["status"], queue_mod.READY)
        self.assertEqual(rows["SPEC-proposed.md"]["status"], queue_mod.PROPOSED)
        self.assertEqual((rows["SPEC-approved.md"]["status"], rows["SPEC-approved.md"]["approved"]), (queue_mod.PROPOSED, True))
        self.assertTrue(rows["SPEC-cycle-x.md"]["errors"] and rows["SPEC-cycle-y.md"]["errors"])
        self.assertTrue(any("unknown dependency SPEC-nowhere" in e for e in listing["errors"]))
        self.assertFalse(queue_mod.eligible(rows["SPEC-cycle-x.md"]))
        self.assertFalse(queue_mod.eligible(rows["SPEC-unknown-dep.md"]))
        self.assertTrue(queue_mod.eligible(rows["SPEC-proposed.md"]))
        self.assertTrue(queue_mod.eligible(rows["SPEC-ready.md"]))

    def test_fq05_topological_then_priority_then_name(self):
        """FQ-05, FQ-06: dependencies first, ties by Priorité ascending (absent last) then name."""
        work, _ = phase4.project(self.tmp, specs())
        listing = queue_mod.scan(Runner(cwd=str(work)), work, "main")
        self.assertEqual([r["file"] for r in listing["specs"]], ["SPEC-dep-a.md", "SPEC-dep-b.md", "SPEC-solo.md"])
        self.assertEqual(listing["specs"][1]["unsatisfied"], ["SPEC-dep-a.md"])
        self.assertEqual(queue_mod.next_spec(listing, set())["file"], "SPEC-dep-a.md")
        self.assertEqual(queue_mod.next_spec(listing, {"SPEC-dep-a.md"})["file"], "SPEC-solo.md")      # b waits for a: merged

    def test_fq02_a_spec_removed_during_the_scan_is_skipped(self):
        """FQ-02: a worktree cleaned up by a batch slot while the coordinator scans must not crash the scan."""
        from pathlib import Path
        from unittest import mock
        work, _ = phase4.project(self.tmp, specs())
        real = Path.read_text

        def vanishing(path, *a, **kw):
            if path.name == "SPEC-solo.md":
                raise FileNotFoundError(str(path))
            return real(path, *a, **kw)

        with mock.patch.object(Path, "read_text", vanishing):
            listing = queue_mod.scan(Runner(cwd=str(work)), work, "main")
        self.assertEqual([r["file"] for r in listing["specs"]], ["SPEC-dep-a.md", "SPEC-dep-b.md"])

    def test_fq02_the_cli_lists_and_proposes(self):
        """FQ-02: `queue list` and `queue next` answer JSON; a failed fetch is a warning that falls back to the local ref."""
        work, remote = phase4.project(self.tmp, specs())
        d = out(self, helpers.harness(["queue", "list", "--root", str(work), "--target", "main"], work))
        self.assertEqual(len(d["specs"]), 3)
        helpers.git(work, "remote", "set-url", "origin", str(self.tmp / "gone.git"))
        d = out(self, helpers.harness(["queue", "list", "--root", str(work), "--target", "main"], work))
        self.assertTrue(d["warnings"])
        n = out(self, helpers.harness(["queue", "next", "--root", str(work), "--target", "main", "--no-fetch"], work))
        self.assertEqual(n["next"]["file"], "SPEC-dep-a.md")


class RunTest(helpers.TempDirCase):
    def run_queue(self, work, *extra, autonomy="auto"):
        args = ["queue", "run", "--root", str(work), "--target", "main"] + (["--autonomy", autonomy] if autonomy else [])
        return helpers.harness(args + list(extra), work, env=phase4.env_for(self.tmp))

    def test_fq06_order_dependencies_and_failure(self):
        """FQ-06, CA-22: topological order; b waits for a to be merged; a failing child stops the queue (kind child)
        and the spec is not launched a second time."""
        work, _ = phase4.project(self.tmp, specs())
        phase4.scenario(self.tmp, {"SPEC-dep-a.md": {"action": "merge"}, "SPEC-solo.md": {"action": "fail"}})
        res = self.run_queue(work)
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn("STOP:", res.stderr)
        launched = [c["args"].split()[0] for c in phase4.claude_calls(self.tmp)]
        self.assertEqual(launched, ["specs/SPEC-dep-a.md", "specs/SPEC-dep-b.md", "specs/SPEC-solo.md"])
        stop = sorted((work / ".harness" / "runs").glob("STOP-*.md"))[-1].read_text(encoding="utf-8")
        self.assertIn("— child", stop)
        self.assertIn("/factory-run specs/SPEC-solo.md", stop)
        journal_file = sorted((work / ".harness" / "runs").glob("factory-queue-*.jsonl"))[0]
        events = [x["event"] for x in journal.read(journal_file)]
        self.assertEqual(events[0], "autonomy")
        self.assertEqual(events.count("chain"), 3)
        self.assertEqual(journal.validate_file(journal_file), [])
        for call in phase4.claude_calls(self.tmp):
            self.assertIn("--ship main", call["args"])

    def test_fq06_a_pr_left_open_does_not_stop_the_queue_but_blocks_its_dependents(self):
        """FQ-06: PR left open -> the queue continues; the dependents stay ineligible and are reported with the reason."""
        work, _ = phase4.project(self.tmp, specs())
        phase4.scenario(self.tmp, {"SPEC-dep-a.md": {"action": "open"}})
        res = self.run_queue(work)
        d = out(self, res)
        launched = [c["args"].split()[0] for c in phase4.claude_calls(self.tmp)]
        self.assertEqual(launched, ["specs/SPEC-dep-a.md", "specs/SPEC-solo.md"])
        reasons = {x["file"]: x["reason"] for x in d["left"]}
        self.assertIn("not merged", reasons["SPEC-dep-a.md"])
        self.assertIn("waits for SPEC-dep-a.md", reasons["SPEC-dep-b.md"])
        self.assertNotIn("SPEC-solo.md", reasons)

    def test_fq09_the_queue_requires_the_auto_policy(self):
        """FQ-09, CA-22: policy gate (the default here) -> exit 1, `requires --autonomy auto`; nothing launched."""
        work, _ = phase4.project(self.tmp, specs())
        res = self.run_queue(work, autonomy=None)
        self.assertEqual(res.returncode, 1)
        self.assertIn("requires --autonomy auto", res.stderr)
        self.assertEqual(phase4.claude_calls(self.tmp), [])

    def test_fq08_the_preconditions(self):
        """FQ-08: a dirty tree, or a project without the factory, stops with kind gate."""
        work, _ = phase4.project(self.tmp, specs())
        (work / "wip.txt").write_text("x", encoding="utf-8")
        res = self.run_queue(work)
        self.assertEqual(res.returncode, 1)
        self.assertIn("not clean", res.stderr)
        (work / "wip.txt").unlink()
        (work / ".claude" / "harness" / "harness.py").unlink()
        helpers.git(work, "add", "-A")
        helpers.git(work, "commit", "-q", "-m", "drop")
        res = self.run_queue(work)
        self.assertEqual(res.returncode, 1)
        self.assertIn("not installed", res.stderr)

    def test_fq07_a_second_coordinator_is_refused(self):
        """FQ-07, CA-22: the OS lock of the repository is exclusive."""
        work, _ = phase4.project(self.tmp, specs())
        with runs.locked_file(queue_mod.coordinator_lock(Runner(cwd=str(work)), work), 0):
            res = self.run_queue(work)
        self.assertEqual(res.returncode, 1)
        self.assertIn("locked by another", res.stderr)

    def test_fq10_the_journal_is_named_factory_queue(self):
        """FQ-10: .harness/runs/factory-queue-<ts>.jsonl."""
        work, _ = phase4.project(self.tmp, {"SPEC-solo.md": phase4.spec_text("PROPOSEE", "Solo")})
        out(self, self.run_queue(work))
        self.assertEqual(len(list((work / ".harness" / "runs").glob("factory-queue-*.jsonl"))), 1)


if __name__ == "__main__":
    unittest.main()
