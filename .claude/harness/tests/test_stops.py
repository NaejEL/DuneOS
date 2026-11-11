# STOP files: SX-01 to SX-07.

import json
import unittest
from datetime import datetime
from pathlib import Path

import helpers
from hcore import journal, runs, stops

FIXTURES = helpers.FIXTURES / "stop-expected"
SAMPLE = {"reason": "Something needs a human.\nSecond line of the reason.", "items": ["Q1 — first", "Q2 — second"],
          "spec": "specs/SPEC-a.md", "phase": "entry", "profile": "software", "batch_id": "20260101-000000",
          "child_command": "/factory-run specs/SPEC-a.md"}
WHERE = {"phase": "entry", "run": ".harness/runs/factory-run-20260101-000000.jsonl", "spec": "specs/SPEC-a.md",
         "branch": "main", "worktree": "/work"}


class RenderTest(unittest.TestCase):
    def test_sx01_the_kinds_are_the_closed_list(self):
        """SX-01."""
        self.assertEqual(set(stops.KINDS), {"destructive", "iterations", "agent", "blocked", "profile", "drift",
                                            "cost", "secret", "gate", "conflict", "child"})
        with self.assertRaises(stops.StopError):
            stops.render("factory-run", "other", SAMPLE, WHERE)

    def test_sx03_format_for_every_kind(self):
        """SX-03: title, then Where, Why, To resume, Command in that order; 40 lines at most; a command."""
        for kind in stops.KINDS:
            text = stops.render("factory-run", kind, SAMPLE, WHERE)
            lines = text.splitlines()
            self.assertEqual(lines[0], "# STOP — factory-run — %s" % kind)
            self.assertLessEqual(len(lines), 40, kind)
            heads = [l for l in lines if l.startswith("## ")]
            self.assertEqual(heads, ["## Where", "## Why", "## To resume", "## Command"], kind)
            block = text.split("```")[1].strip()
            self.assertTrue(block, kind)

    def test_sx03_limits_the_reason_and_the_items(self):
        """SX-03: 5 lines of reason, 10 items, 5 steps at most."""
        text = stops.render("factory-run", "gate", {"reason": "\n".join("r%d" % i for i in range(9)),
                                                     "items": ["i%d" % i for i in range(30)],
                                                     "steps": ["s%d" % i for i in range(9)]}, WHERE)
        self.assertEqual(text.count("\n- i"), 10)
        self.assertNotIn("r5", text)
        self.assertIn("5. s4", text)
        self.assertNotIn("6. s5", text)

    def test_sx04_the_resume_command_of_each_kind(self):
        """SX-04."""
        d = dict(SAMPLE)
        self.assertEqual(stops.resume_command("blocked", d), "/factory-run specs/SPEC-a.md")
        self.assertEqual(stops.resume_command("iterations", d), "/factory-run specs/SPEC-a.md")
        self.assertEqual(stops.resume_command("agent", d), "/factory-run specs/SPEC-a.md")
        self.assertEqual(stops.resume_command("drift", d), "/factory-run specs/SPEC-a.md")
        self.assertEqual(stops.resume_command("profile", d), "/software-factory --profile software")
        self.assertEqual(stops.resume_command("conflict", d), "/factory-batch --resume 20260101-000000")
        self.assertEqual(stops.resume_command("child", d), "/factory-run specs/SPEC-a.md")
        self.assertIn("yourself", " ".join(stops.RESUME["destructive"][0]))
        self.assertIn("allow_api_billing", stops.resume_command("cost", d))
        self.assertIn("--autonomy gate", stops.resume_command("gate", d))

    @unittest.skipUnless(FIXTURES.is_dir(), helpers.SKIP_FIXTURES)
    def test_sx03_expected_files(self):
        """SX-03, CA-12: the file of each kind equals stop-expected/<kind>.md."""
        for kind in stops.KINDS:
            expected = (FIXTURES / ("%s.md" % kind)).read_text(encoding="utf-8")
            self.assertEqual(stops.render("factory-run", kind, SAMPLE, WHERE), expected, kind)


class FilesTest(helpers.TempDirCase):
    def test_sx02_name_and_collision(self):
        """SX-02: STOP-<ts>.md; a collision adds a second; SX-07: under .harness/runs/."""
        now = datetime(2026, 10, 17, 9, 5, 7)
        first = stops.write(self.tmp, "factory-run", "gate", {"reason": "a"}, now=now, branch="main")
        second = stops.write(self.tmp, "factory-run", "gate", {"reason": "b"}, now=now, branch="main")
        self.assertEqual((first.name, second.name), ("STOP-20261017-090507.md", "STOP-20261017-090508.md"))
        self.assertEqual(first.parent, self.tmp / ".harness" / "runs")

    def test_sx05_the_three_outputs_and_sx06_the_refusal(self):
        """SX-05: file, `stop` event, exit 1 with `STOP: <reason> (see <path>)`; SX-06: later commands exit 1."""
        start = json.loads(helpers.harness(["factory-run", "start"], self.tmp).stdout)
        details = helpers.write_output(self.tmp, "stop-details.json", json.dumps(SAMPLE))
        res = helpers.harness(["stop", "write", "--skill", "factory-run", "--kind", "blocked", "--run", start["run"],
                               "--details-file", details], self.tmp)
        self.assertEqual(res.returncode, 1)
        self.assertRegex(res.stderr, r"STOP: Something needs a human\. \(see \.harness/runs/STOP-[0-9-]+\.md\)")
        self.assertFalse((self.tmp / details).exists())          # consumed like any transit file
        event = [x for x in journal.read(self.tmp / start["run"]) if x.get("event") == "stop"][0]
        self.assertEqual(event["kind"], "blocked")
        self.assertTrue((self.tmp / event["path"]).is_file())
        self.assertEqual(journal.validate_file(self.tmp / start["run"]), [])
        later = helpers.harness(["factory-run", "record", "--run", start["run"], "--agent", "factory-planner",
                                 "--model", "inherit", "--duration-ms", "1"], self.tmp)
        self.assertEqual(later.returncode, 1)
        self.assertIn("STOP-", later.stderr)
        self.assertEqual(runs.load_state(self.tmp / start["run"])["status"], "stopped")

    def test_sx05_an_event_without_a_path_is_invalid(self):
        """CA-37: a `stop` event without `path` is refused by the journal schema."""
        self.assertNotEqual(journal.validate_record({"event": "stop", "kind": "gate"}), [])
        self.assertEqual(journal.validate_record({"event": "stop", "kind": "gate", "path": "x"}), [])

    def test_sx07_stop_files_are_never_deleted_by_the_core(self):
        """SX-07: the module that writes the STOP files never removes any."""
        text = (helpers.CORE / "hcore" / "stops.py").read_text(encoding="utf-8")
        for removal in ("unlink", "os.remove", "rmtree"):
            self.assertNotIn(removal, text)


if __name__ == "__main__":
    unittest.main()
