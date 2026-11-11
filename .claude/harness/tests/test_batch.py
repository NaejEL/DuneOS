# /factory-batch: parallel worktrees and a single ship at a time: FB-01 to FB-08.
#
# FB-01 (the skill file, disable-model-invocation) is checked by the frontmatter gate of the verification (CA-38).

import json
import unittest
from pathlib import Path

import helpers
import phase4
from hcore import batch, journal, queue as queue_mod, runs, worktrees
from hcore.proc import Runner


def out(test, res, code=0):
    test.assertEqual(res.returncode, code, "stdout=%s\nstderr=%s" % (res.stdout, res.stderr))
    return json.loads(res.stdout) if res.stdout.strip() else None


class OptionsTest(unittest.TestCase):
    def test_fb03_parallel_default_cap_and_errors(self):
        """FB-03, CA-24: default 2; 9 -> 4; 0 and x are usage errors."""
        self.assertEqual(batch.parse_parallel(None), 2)
        self.assertEqual(batch.parse_parallel("3"), 3)
        self.assertEqual(batch.parse_parallel("9"), 4)
        for bad in ("0", "x", "-1", "1.5", ""):
            with self.assertRaises(batch.BatchUsage):
                batch.parse_parallel(bad)

    def test_fb03_the_cli_refuses_a_bad_value_with_exit_2(self):
        """FB-03, CA-24."""
        for bad in ("0", "x"):
            res = helpers.harness(["batch", "run", "specs/*.md", "--parallel", bad], helpers.CORE)
            self.assertEqual(res.returncode, 2, bad)

    def test_fb02_the_globs_are_expanded_and_sorted_by_the_core(self):
        """FB-02: markdown files only, sorted, no duplicates, relative to the root."""
        with helpers.tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("SPEC-b.md", "SPEC-a.md", "notes.txt"):
                phase4.write_spec(root, name, "x")
            self.assertEqual(batch.expand(root, ["specs/*", "specs/SPEC-a.md"]), ["specs/SPEC-a.md", "specs/SPEC-b.md"])


class RunTest(helpers.TempDirCase):
    def project(self, specs, shared=False):
        work, remote = phase4.project(self.tmp, specs)
        if shared:
            (work / "shared.txt").write_text("base\n", encoding="utf-8")
            helpers.git(work, "add", "-A")
            helpers.git(work, "commit", "-q", "-m", "shared")
            helpers.git(work, "push", "-q", "origin", "main")
        return work

    def run_batch(self, work, *args, autonomy="auto"):
        cmd = ["batch", "run", "--root", str(work), "--target", "main"] + (["--autonomy", autonomy] if autonomy else [])
        return helpers.harness(cmd + list(args), work, env=phase4.env_for(self.tmp))

    def test_fb04_fb05_slots_single_ship_cleanup_and_collection(self):
        """FB-04, FB-05, FB-06, FB-08, CA-24: at most N builds at once, never two ships at once, every PR merged,
        the logs of the worktrees collected under .harness/runs/wt-<slug>/ before the removal."""
        work = self.project({"SPEC-a.md": phase4.spec_text("PROPOSEE", "A", ["Priorité : 1"]),
                             "SPEC-b.md": phase4.spec_text("PROPOSEE", "B", ["Priorité : 2"]),
                             "SPEC-c.md": phase4.spec_text("PROPOSEE", "C", ["Priorité : 3"])})
        phase4.scenario(self.tmp, {"*": {"action": "merge", "seconds": 1.2}})
        # the two first builds must overlap whatever the speed of the machine: the worktrees are created one at a
        # time (git_lock), so the second build starts late; the builds of a, b hold a 6 s window instead of 1.2 s
        phase4.scenario(self.tmp, {"SPEC-a.md": {"action": "merge", "seconds": 6}, "SPEC-b.md": {"action": "merge", "seconds": 6},
                                   "SPEC-c.md": {"action": "merge", "seconds": 0.2}})
        res = self.run_batch(work, "specs/*.md", "--parallel", "2")
        d = out(self, res)
        self.assertEqual(sorted(d["status"].values()), ["merged"] * 3)
        calls = phase4.claude_calls(self.tmp)
        builds = [c for c in calls if c["skill"] == "factory-run"]
        ships = sorted((c for c in calls if c["skill"] == "ship-pr"), key=lambda c: c["start"])
        self.assertEqual(len(builds), 3)
        self.assertEqual(len(ships), 3)
        self.assertTrue(all("--deliver-only" in c["args"] for c in builds))
        overlap = max(sum(1 for o in builds if o["start"] < c["end"] and c["start"] < o["end"]) for c in builds)
        self.assertLessEqual(overlap, 2)
        self.assertGreaterEqual(overlap, 2)                                  # the slots were really used in parallel
        for first, second in zip(ships, ships[1:]):
            self.assertLessEqual(first["end"], second["start"] + 0.05)       # one ship at a time
        for call in ships:
            self.assertIn("--spec specs/", call["args"])
        self.assertEqual(worktrees.load(worktrees.common_dir(Runner(cwd=str(work)), work)), [])
        self.assertEqual(len(list((work / ".harness" / "runs").glob("wt-*/factory-run-simulated.jsonl"))), 3)
        batch_journal = sorted((work / ".harness" / "runs").glob("factory-batch-*.jsonl"))[0]
        self.assertEqual(journal.validate_file(batch_journal), [])
        state = runs.load_state(batch_journal)
        self.assertEqual(state["parallel"], 2)
        self.assertEqual(helpers.git(work, "status", "--porcelain").strip(), "")

    def test_fb04_the_scheduler_fetches_at_most_once_per_interval(self):
        """FB-04: the scheduling scans fetch origin every 30 s, not at every 0.1 s turn."""
        b = batch.Batch(self.tmp, "main", 2, None, [], "auto")
        now = [100.0]
        b.clock = lambda: now[0]
        self.assertTrue(b.fetch_due())
        for _ in range(50):
            now[0] += 0.1
            self.assertFalse(b.fetch_due())
        now[0] += batch.FETCH_EVERY_S
        self.assertTrue(b.fetch_due())
        self.assertFalse(b.fetch_due())

    def test_fb06_resume_rebuilds_a_failed_spec_whose_original_was_moved_to_handoff(self):
        """FB-06, WT-05: a spec transferred then failed is recovered from handoff/ on --resume and built again."""
        work = self.project({})
        (work / ".git" / "info" / "exclude").write_text(
            ".harness/runs/\n.claude/findings.jsonl\n.claude/findings.lock\nspecs/\n", encoding="utf-8")
        phase4.write_spec(work, "SPEC-a.md", phase4.spec_text("PROPOSEE", "A"))      # local only: transferred, original moved
        phase4.scenario(self.tmp, {"*": {"action": "fail"}})
        res = self.run_batch(work, "specs/*.md")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertFalse((work / "specs" / "SPEC-a.md").exists())
        self.assertEqual(len(list((work / ".harness" / "runs" / "handoff").glob("*-SPEC-a.md"))), 1)
        batch_id = sorted((work / ".harness" / "runs").glob("factory-batch-*.jsonl"))[0].name[len("factory-batch-"):-len(".jsonl")]
        phase4.scenario(self.tmp, {"*": {"action": "open"}})
        d = out(self, self.run_batch(work, "--resume", batch_id))
        self.assertEqual(d["status"]["specs/SPEC-a.md"], "open")
        self.assertEqual(len([c for c in phase4.claude_calls(self.tmp) if c["skill"] == "factory-run"]), 2)

    def test_fb05_a_non_trivial_conflict_aborts_the_rebase_and_stops_with_the_worktree_kept(self):
        """FB-05, CA-24: rebase conflict -> `rebase --abort`, stop `conflict`, the worktree is kept, the first PR merged."""
        work = self.project({"SPEC-a.md": phase4.spec_text("PROPOSEE", "A", ["Priorité : 1"]),
                             "SPEC-b.md": phase4.spec_text("PROPOSEE", "B", ["Priorité : 2"])}, shared=True)
        phase4.scenario(self.tmp, {"SPEC-a.md": {"action": "merge", "files": {"shared.txt": "from a\n"}},
                                   "SPEC-b.md": {"action": "merge", "files": {"shared.txt": "from b\n"}}})
        res = self.run_batch(work, "specs/*.md", "--parallel", "2")
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn("STOP:", res.stderr)
        stop = sorted((work / ".harness" / "runs").glob("STOP-*.md"))[-1].read_text(encoding="utf-8")
        self.assertIn("— conflict", stop)
        self.assertIn("/factory-batch --resume", stop)
        self.assertIn("shared.txt", stop)
        calls = [c for c in phase4.claude_calls(self.tmp) if c["skill"] == "ship-pr"]
        self.assertEqual(len(calls), 1)                                      # b was never shipped
        entries = worktrees.load(worktrees.common_dir(Runner(cwd=str(work)), work))
        self.assertEqual([e["branch"] for e in entries], ["factory/b"])
        wt = entries[0]["path"]
        self.assertEqual(helpers.git(Path(wt), "status", "--porcelain").strip(), "")       # the rebase was aborted
        self.assertTrue(any(c[:1] == ["-C"] and "rebase" in c and "--abort" in c for c in phase4.git_calls(self.tmp)))

    def test_fb04_a_spec_whose_dependency_is_in_the_batch_waits_for_its_merge(self):
        """FB-04: b depends on a: b is built only after a is merged."""
        work = self.project({"SPEC-a.md": phase4.spec_text("PROPOSEE", "A"),
                             "SPEC-b.md": phase4.spec_text("PROPOSEE", "B", ["Dépend de : SPEC-a"])})
        phase4.scenario(self.tmp, {"*": {"action": "merge", "seconds": 0.2}})
        d = out(self, self.run_batch(work, "specs/*.md", "--parallel", "2"))
        self.assertEqual(sorted(d["status"].values()), ["merged", "merged"])
        calls = phase4.claude_calls(self.tmp)
        build_a = next(c for c in calls if c["skill"] == "factory-run" and "SPEC-a.md" in c["args"])
        build_b = next(c for c in calls if c["skill"] == "factory-run" and "SPEC-b.md" in c["args"])
        ship_a = next(c for c in calls if c["skill"] == "ship-pr" and "SPEC-a.md" in c["args"])
        self.assertGreaterEqual(build_b["start"], ship_a["end"])
        self.assertGreater(build_b["start"], build_a["end"])

    def test_fb02_the_specs_that_are_not_eligible_are_reported(self):
        """FB-02: a blocked spec is not launched and appears in `not_eligible`."""
        work = self.project({"SPEC-a.md": phase4.spec_text("PROPOSEE", "A"),
                             "SPEC-z.md": phase4.spec_text("BLOQUEE", header=["Questions bloquantes : Q1"],
                                                          questions=["- Q1 — a ?", "  Réponse :"])})
        phase4.scenario(self.tmp, {"*": {"action": "merge"}})
        d = out(self, self.run_batch(work, "specs/*.md"))
        self.assertEqual([x["file"] for x in d["not_eligible"]], ["specs/SPEC-z.md"])
        self.assertEqual(d["status"]["specs/SPEC-a.md"], "merged")

    def test_fb07_the_batch_requires_the_auto_policy_and_one_coordinator(self):
        """FB-07: FQ-09 and FQ-07 apply to the batch."""
        work = self.project({"SPEC-a.md": phase4.spec_text("PROPOSEE", "A")})
        res = self.run_batch(work, "specs/*.md", autonomy=None)
        self.assertEqual(res.returncode, 1)
        self.assertIn("requires --autonomy auto", res.stderr)
        with runs.locked_file(queue_mod.coordinator_lock(Runner(cwd=str(work)), work), 0):
            res = self.run_batch(work, "specs/*.md")
        self.assertEqual(res.returncode, 1)
        self.assertIn("locked by another", res.stderr)

    def test_fb06_resume_of_an_unknown_batch_is_a_usage_error(self):
        """FB-06: --resume <id> needs an existing state."""
        work = self.project({"SPEC-a.md": phase4.spec_text("PROPOSEE", "A")})
        res = self.run_batch(work, "--resume", "20200101-000000")
        self.assertEqual(res.returncode, 2)
        self.assertIn("unknown batch", res.stderr)


if __name__ == "__main__":
    unittest.main()
