# Cycle context of the hooks: HK-05, HK-07, HP-01 to HP-03, HD-01, HD-02, HR-01, HR-02 and the secret files (Q15).

import json
import os
import time
import unittest

import helpers
import phase4
from hcore import journal, runs, scope


class PathsTest(unittest.TestCase):
    def test_hk07_separators_case_and_dotdot(self):
        """HK-07: `\\` and `/`, case ignored for Windows paths, `..` resolved, relative to the root; outside -> None."""
        self.assertEqual(scope.normalize("C:\\Proj\\src\\a.py", "C:\\proj"), "src/a.py")
        self.assertEqual(scope.normalize("c:/PROJ/src/../lib/b.py", "C:\\Proj"), "lib/b.py")
        self.assertEqual(scope.normalize("src\\a.py", "C:\\proj"), "src/a.py")
        self.assertEqual(scope.normalize("/work/app/src/a.py", "/work/app"), "src/a.py")
        self.assertEqual(scope.normalize("src/./x/../a.py", "/work/app"), "src/a.py")
        self.assertIsNone(scope.normalize("C:\\other\\a.py", "C:\\proj"))
        self.assertIsNone(scope.normalize("/etc/passwd", "/work/app"))
        self.assertIsNone(scope.normalize("../outside.py", "/work/app"))

    def test_hk07_globs(self):
        """HK-07: `**` crosses folders, `*` does not; the folder itself matches `dir/**`."""
        self.assertTrue(scope.matches("src/**", "src/a/b.py"))
        self.assertTrue(scope.matches("src/**", "src"))
        self.assertTrue(scope.matches("tests/*.py", "tests/a.py"))
        self.assertFalse(scope.matches("tests/*.py", "tests/sub/a.py"))
        self.assertTrue(scope.matches("**/*.md", "docs/a.md"))
        self.assertFalse(scope.matches("src/**", "lib/a.py"))

    def test_q15_secret_files(self):
        """Q15: .env, .env.*, *.pem, *.key, id_rsa*, *.p12, *.pfx; .env.example is allowed."""
        for name in (".env", ".env.local", "config/server.pem", "a/b/app.key", "id_rsa", "id_rsa.pub", "x.p12", "x.pfx"):
            self.assertTrue(scope.secret_hit(name), name)
        for name in (".env.example", "src/env.py", "keys.md"):
            self.assertFalse(scope.secret_hit(name), name)


class ContextTest(helpers.TempDirCase):
    def start(self, session="s1", skill="factory-run", spec="specs/SPEC-a.md"):
        run = runs.create(self.tmp, skill if skill != "ship-pr" else "ship-pr", {"status": "running", "spec": spec,
                                                                                 "target": "main"})
        rel = run.relative_to(self.tmp).as_posix()
        scope.activate(self.tmp, skill, rel, session, "build", spec, "main")
        return run, rel

    def test_hk05_the_marker_is_per_session(self):
        """HK-05, CA-32: another session's marker does not apply; no marker -> nothing applies."""
        self.start("s1")
        self.assertIsNotNone(scope.current(self.tmp, "s1"))
        self.assertIsNone(scope.current(self.tmp, "s2"))
        self.assertIsNone(scope.current(self.tmp / "elsewhere", "s1"))

    def test_hk05_stale_markers_are_ignored_and_removed_by_the_next_activate(self):
        """HK-05, CA-32: a run no longer `running`, or a marker older than 12 h, is ignored."""
        run, rel = self.start("s1")
        state = runs.load_state(run)
        state["status"] = "success"
        runs.save_state(run, state)
        self.assertIsNone(scope.current(self.tmp, "s1"))
        run2 = runs.create(self.tmp, "factory-run", {"status": "running"})
        scope.activate(self.tmp, "factory-run", run2.relative_to(self.tmp).as_posix(), "s1")
        marker = json.loads((self.tmp / ".harness" / "runs" / "active" / "s1.json").read_text(encoding="utf-8"))
        self.assertEqual(len(marker["stack"]), 1)                      # the stale entry was dropped
        old = scope.current(self.tmp, "s1", now=time.time() + 13 * 3600)
        self.assertIsNone(old)

    def test_hk05_the_stack_top_applies_and_deactivate_pops(self):
        """HK-05, CA-32: ship-pr on top of factory-run -> ship-pr applies; deactivate removes the entry of the run."""
        run1, rel1 = self.start("s1", "factory-run")
        run2, rel2 = self.start("s1", "ship-pr")
        self.assertEqual(scope.current(self.tmp, "s1")["skill"], "ship-pr")
        scope.deactivate(self.tmp, rel2, "s1")
        self.assertEqual(scope.current(self.tmp, "s1")["skill"], "factory-run")
        scope.deactivate(self.tmp, rel1, "s1")
        self.assertFalse((self.tmp / ".harness" / "runs" / "active" / "s1.json").exists())

    def test_hk05_a_literal_placeholder_falls_back_to_the_project_marker_with_a_warning(self):
        """HK-05 (Q17), CA-32: `${CLAUDE_SESSION_ID}` not substituted -> active/project.json, applies to any session."""
        run = runs.create(self.tmp, "factory-run", {"status": "running"})
        d = scope.activate(self.tmp, "factory-run", run.relative_to(self.tmp).as_posix(), "${CLAUDE_SESSION_ID}")
        self.assertTrue(d["warnings"])
        self.assertTrue((self.tmp / ".harness" / "runs" / "active" / "project.json").is_file())
        self.assertIsNotNone(scope.current(self.tmp, "any-session"))

    def test_hk05_the_cli_activates_and_deactivates(self):
        """HK-05: `activate` and `deactivate` commands, marker under .harness/runs/active/."""
        run = runs.create(self.tmp, "factory-run", {"status": "running", "spec": "specs/SPEC-a.md"})
        res = helpers.harness(["activate", "--run", str(run), "--skill", "factory-run", "--session", "abc"], self.tmp)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue((self.tmp / ".harness" / "runs" / "active" / "abc.json").is_file())
        helpers.harness(["deactivate", "--run", str(run), "--session", "abc"], self.tmp)
        self.assertFalse((self.tmp / ".harness" / "runs" / "active" / "abc.json").exists())


class HooksTest(helpers.TempDirCase):
    def setUp(self):
        super().setUp()
        self.work, _ = phase4.project(self.tmp, {"SPEC-a.md": phase4.spec_text(
            "APPROUVEE", header=["Périmètre fichiers : src/**, tests/**"])})
        self.run = runs.create(self.work, "factory-run", {"status": "running", "spec": "specs/SPEC-a.md"})
        self.rel = self.run.relative_to(self.work).as_posix()
        scope.activate(self.work, "factory-run", self.rel, "s1", "build", "specs/SPEC-a.md")

    def edit(self, path, session="s1", tool="Edit"):
        return phase4.hook("pre-edit", {"tool_name": tool, "tool_input": {"file_path": path}, "session_id": session}, self.work)

    def test_hp01_hp02_inside_the_perimeter_and_the_admitted_paths(self):
        """HP-01, HP-02, CA-30: perimeter files, .harness/runs/**, the spec, outside-root paths and Windows variants pass."""
        for path in ("src/a.py", "tests/sub/b.py", ".harness/runs/x.md", "specs/SPEC-a.md",
                     str(self.work / "src" / "c.py"), "src\\d.py", "/elsewhere/else.py", "C:\\elsewhere\\x.py"):
            self.assertEqual(self.edit(path).returncode, 0, path)

    def test_hp03_outside_the_perimeter_blocks_with_the_message(self):
        """HP-03, CA-30: exit 2 and the message naming the path, the spec and the globs."""
        res = self.edit("docs/readme.md")
        self.assertEqual(res.returncode, 2)
        self.assertEqual(res.stderr.strip(), "Blocked: docs/readme.md is outside the scope declared by specs/SPEC-a.md "
                         "(Files scope: src/**, tests/**). Stay within the spec; if the spec is wrong, stop and report it.")

    def test_hp01_no_perimeter_or_no_marker_lets_everything_pass(self):
        """HP-01, CA-30: a spec without the field, or no marker, or another session -> exit 0."""
        phase4.write_spec(self.work, "SPEC-a.md", phase4.spec_text("APPROUVEE"))
        self.assertEqual(self.edit("docs/readme.md").returncode, 0)
        phase4.write_spec(self.work, "SPEC-a.md", phase4.spec_text("APPROUVEE", header=["Périmètre fichiers : src/**"]))
        self.assertEqual(self.edit("docs/readme.md", session="other").returncode, 0)
        scope.deactivate(self.work, self.rel, "s1")
        self.assertEqual(self.edit("docs/readme.md").returncode, 0)

    def test_hr01_hr02_the_second_drift_stops_the_cycle(self):
        """HR-01, HR-02, CA-33: 1st drift -> blocked, no STOP; 2nd -> blocked, STOP drift, continue false; record -> exit 1."""
        first = self.edit("docs/a.md")
        self.assertEqual(first.returncode, 2)
        self.assertEqual(list((self.work / ".harness" / "runs").glob("STOP-*.md")), [])
        second = self.edit("docs/b.md")
        self.assertEqual(second.returncode, 2)
        self.assertIn('"continue": false', second.stdout)
        self.assertIn("second scope drift in this cycle: cycle stopped (see .harness/runs/STOP-", second.stderr)
        stop = list((self.work / ".harness" / "runs").glob("STOP-*.md"))
        self.assertEqual(len(stop), 1)
        self.assertIn("— drift", stop[0].read_text(encoding="utf-8"))
        events = [x for x in journal.read(self.run) if x.get("event") == "drift"]
        self.assertEqual([e["count"] for e in events], [1, 2])
        self.assertEqual(journal.validate_file(self.run), [])
        record = helpers.harness(["factory-run", "record", "--run", str(self.run), "--agent", "factory-builder"], self.work)
        self.assertEqual(record.returncode, 1)
        self.assertIn("STOP-", record.stderr)

    def test_hr01_a_new_run_starts_at_zero(self):
        """HR-01, CA-33: the counter is per run."""
        self.edit("docs/a.md")
        run2 = runs.create(self.work, "factory-run", {"status": "running", "spec": "specs/SPEC-a.md"})
        scope.activate(self.work, "factory-run", run2.relative_to(self.work).as_posix(), "s2", "build", "specs/SPEC-a.md")
        res = self.edit("docs/a.md", session="s2")
        self.assertEqual(res.returncode, 2)
        self.assertEqual(list((self.work / ".harness" / "runs").glob("STOP-*.md")), [])

    def test_q15_a_secret_file_is_refused_in_a_cycle_with_a_stop(self):
        """Q15: Edit and Write on .env -> exit 2 and a STOP of kind secret; outside a cycle nothing happens."""
        res = self.edit(".env", tool="Write")
        self.assertEqual(res.returncode, 2)
        self.assertIn("— secret", next((self.work / ".harness" / "runs").glob("STOP-*.md")).read_text(encoding="utf-8"))
        self.assertEqual(self.edit(".env", session="nobody").returncode, 0)


class DiffTest(helpers.TempDirCase):
    def test_hd01_only_the_files_of_the_pr_diff(self):
        """HD-01, CA-31: a file of `git diff origin/<target>...HEAD` passes, any other is blocked in English."""
        work, _ = phase4.project(self.tmp)
        helpers.git(work, "checkout", "-q", "-b", "feat")
        (work / "pr.txt").write_text("in the PR", encoding="utf-8")
        helpers.git(work, "add", "-A")
        helpers.git(work, "commit", "-q", "-m", "pr")
        run = runs.create(work, "ship-pr", {"status": "running", "target": "main"})
        scope.activate(work, "ship-pr", run.relative_to(work).as_posix(), "s1", "review", None, "main")
        edit = lambda p: phase4.hook("pre-edit", {"tool_name": "Edit", "tool_input": {"file_path": p}, "session_id": "s1"}, work)
        self.assertEqual(edit("pr.txt").returncode, 0)
        self.assertEqual(edit(".harness/runs/x.md").returncode, 0)
        res = edit("README.md")
        self.assertEqual(res.returncode, 2)
        self.assertIn("scope limited to the PR diff", res.stderr)
        self.assertIn("origin/main...HEAD", res.stderr)

    def test_hd02_a_git_failure_lets_the_edit_pass_and_is_reported(self):
        """HD-02: no origin/<target> (git diff fails) -> exit 0, systemMessage, hook-errors.jsonl."""
        work, _ = phase4.project(self.tmp)
        run = runs.create(work, "ship-pr", {"status": "running", "target": "nonexistent"})
        scope.activate(work, "ship-pr", run.relative_to(work).as_posix(), "s1", "review", None, "nonexistent")
        res = phase4.hook("pre-edit", {"tool_name": "Edit", "tool_input": {"file_path": "README.md"}, "session_id": "s1"}, work)
        self.assertEqual(res.returncode, 0)
        self.assertIn("systemMessage", res.stdout)
        self.assertTrue((work / ".harness" / "runs" / "hook-errors.jsonl").is_file())

    def test_cp32_in_a_stack_the_ship_pr_context_wins_over_factory_run(self):
        """CA-32: ship-pr stacked over factory-run -> the diff rule applies, not the perimeter."""
        work, _ = phase4.project(self.tmp, {"SPEC-a.md": phase4.spec_text("APPROUVEE", header=["Périmètre fichiers : src/**"])})
        run1 = runs.create(work, "factory-run", {"status": "running", "spec": "specs/SPEC-a.md"})
        scope.activate(work, "factory-run", run1.relative_to(work).as_posix(), "s1", "build", "specs/SPEC-a.md")
        run2 = runs.create(work, "ship-pr", {"status": "running", "target": "main"})
        scope.activate(work, "ship-pr", run2.relative_to(work).as_posix(), "s1", "review", None, "main")
        res = phase4.hook("pre-edit", {"tool_name": "Edit", "tool_input": {"file_path": "src/a.py"}, "session_id": "s1"}, work)
        self.assertEqual(res.returncode, 2)
        self.assertIn("PR diff", res.stderr)


if __name__ == "__main__":
    unittest.main()
