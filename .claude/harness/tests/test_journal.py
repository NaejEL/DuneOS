# Module hcore.journal: journal schema and file name (CA9, CA10).

import json
import unittest
from datetime import datetime

import helpers
from hcore import journal, runs

GOOD = {"agent": "factory-verifier", "model": "inherit", "duration_ms": 1200, "iteration": 1,
        "verdict": "APPROVED", "findings": {"produced": 0, "validated": 0, "rejected": 0}, "tokens": None}


class NameTest(unittest.TestCase):
    def test_ca10_name_pattern(self):
        name = journal.journal_name("ship-pr", datetime(2026, 10, 16, 9, 5, 7))
        self.assertEqual(name, "ship-pr-20261016-090507.jsonl")
        self.assertRegex(name, journal.NAME_RE)

    def test_unknown_skill_refused(self):
        with self.assertRaises(ValueError):
            journal.journal_name("unknown-skill", datetime.now())

    def test_jr01_the_ten_skill_names(self):
        """JR-01 (PF-05): the skill names admitted in journal names: the core ones plus
        the ones the profiles declare."""
        names = ("software-factory", "factory-run", "ship-pr", "code-index", "ai-lab", "lab-run",
                 "ai-lab-audit", "legacy-audit", "vibecode-audit", "brainstorm", "audit", "harness-doctor",   # JR-04
                 "factory-queue", "factory-batch")   # SX, FQ-10
        self.assertEqual(sorted(journal.SKILLS), sorted(names))
        ca09 = (r"^(software-factory|factory-run|ship-pr|code-index|ai-lab|lab-run|ai-lab-audit|"
                r"legacy-audit|vibecode-audit|brainstorm|audit|harness-doctor|factory-queue|factory-batch)"
                r"-[0-9]{8}-[0-9]{6}\.jsonl$")
        for skill in names:
            name = journal.journal_name(skill, datetime(2026, 10, 17, 9, 5, 7))
            self.assertRegex(name, journal.NAME_RE)
            self.assertRegex(name, ca09)
        self.assertNotRegex("ai-lab-x-20261017-090507.jsonl", journal.NAME_RE)


class ValidateTest(helpers.TempDirCase):
    def write(self, lines):
        path = self.tmp / "factory-run-20261016-120000.jsonl"
        path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        return path

    def test_ca9_valid_journal(self):
        path = self.write([json.dumps(GOOD), json.dumps(dict(GOOD, tokens=4200))])
        self.assertEqual(journal.validate_file(path), [])

    def test_ca9_line_without_verdict_rejected(self):
        bad = dict(GOOD)
        del bad["verdict"]
        errors = journal.validate_file(self.write([json.dumps(GOOD), json.dumps(bad)]))
        self.assertTrue(any("verdict" in e for e in errors))

    def test_ca9_non_json_line_rejected(self):
        errors = journal.validate_file(self.write([json.dumps(GOOD), "not json"]))
        self.assertTrue(any("invalid JSON" in e for e in errors))

    def test_ca9_field_types(self):
        cases = [dict(GOOD, findings={"produced": -1, "validated": 0, "rejected": 0}),
                 dict(GOOD, findings={"produced": 1, "validated": 0}),
                 dict(GOOD, tokens="12"), dict(GOOD, duration_ms=1.5), dict(GOOD, iteration=True)]
        for case in cases:
            self.assertTrue(journal.validate_record(case), case)

    def test_ca9_cli_exit_codes(self):
        good = self.write([json.dumps(GOOD)])
        self.assertEqual(helpers.harness(["journal", "validate", str(good)], self.tmp).returncode, 0)
        bad = dict(GOOD)
        del bad["verdict"]
        path = self.write([json.dumps(bad)])
        self.assertNotEqual(helpers.harness(["journal", "validate", str(path)], self.tmp).returncode, 0)
        path = self.write(["{"])
        self.assertNotEqual(helpers.harness(["journal", "validate", str(path)], self.tmp).returncode, 0)

    def test_jr02_ca09_agent_lines_and_gate_events_mixed(self):
        """JR-02, CA-09: a line without 'event' is an agent line; a gate event
        follows GT-07; a gate event without decision, or an unknown event, is refused."""
        from hcore import gates
        decision = gates.decide("ship-pr.merge", "gate")
        answer = gates.answer("ship-pr.merge", decision, "user", "Merge")
        path = self.write([json.dumps(GOOD), json.dumps(decision), json.dumps(answer)])
        self.assertEqual(journal.validate_file(path), [])
        self.assertEqual(helpers.harness(["journal", "validate", str(path)], self.tmp).returncode, 0)
        no_decision = dict(decision)
        del no_decision["decision"]
        path = self.write([json.dumps(GOOD), json.dumps(no_decision)])
        self.assertTrue(any("decision" in e for e in journal.validate_file(path)))
        self.assertNotEqual(helpers.harness(["journal", "validate", str(path)], self.tmp).returncode, 0)
        bad = [dict(decision, event="party"), dict(decision, kind="z"), dict(decision, answer="x"),
               dict(answer, source="robot"), dict(answer, answer=""), dict(decision, policy="yolo")]
        for case in bad:
            self.assertTrue(journal.validate_record(case), case)
        # An agent line keeps its own schema (backward compatible).
        self.assertTrue(journal.validate_record({"agent": "x"}))

    def test_append_refuses_invalid_record(self):
        with self.assertRaises(ValueError):
            journal.append(self.tmp / "x.jsonl", {"agent": "a"})


class RunFilesTest(helpers.TempDirCase):
    def test_ca10_run_created_under_claude_runs(self):
        run = runs.create(self.tmp, "factory-run", {}, now=datetime(2026, 10, 16, 12, 0, 0))
        twin = runs.create(self.tmp, "factory-run", {}, now=datetime(2026, 10, 16, 12, 0, 0))
        self.assertEqual(run.parent, self.tmp / ".harness" / "runs")
        self.assertNotEqual(run, twin)
        for path in (run, twin):
            self.assertRegex(path.name, journal.NAME_RE)
        self.assertTrue(runs.state_path(run).exists())
        self.assertTrue(runs.registry_path(run).name.endswith(".registry.jsonl"))

    def test_sanitize_replaces_every_invalid_field_and_reports_it(self):
        """No invalid field blocks: conforming fallback value, every fallback reported."""
        bad = {"agent": "", "model": " ", "duration_ms": -1, "iteration": "two", "verdict": None,
               "findings": {"produced": -2, "validated": "x", "rejected": True}, "tokens": 1.5, "theme": "t"}
        fixed, warnings = journal.sanitize(bad)
        self.assertEqual(journal.validate_record(fixed), [])
        self.assertEqual((fixed["agent"], fixed["model"], fixed["verdict"]), (journal.FALLBACK_TEXT,) * 3)
        self.assertEqual((fixed["duration_ms"], fixed["tokens"], fixed["iteration"]), (None, None, 0))
        self.assertEqual(fixed["findings"], {"produced": 0, "validated": 0, "rejected": 0})
        self.assertEqual(fixed["theme"], "t")
        self.assertEqual(len(warnings), 9)
        self.assertEqual(journal.sanitize(GOOD), (GOOD, []))
        self.assertEqual(journal.sanitize(dict(GOOD, findings="x"))[0]["findings"],
                         {"produced": 0, "validated": 0, "rejected": 0})

    def test_cli_invalid_caller_fields_never_stop_the_skill(self):
        """Every field supplied by the caller (--model, --duration-ms, --tokens):
        an invalid value is logged as a fallback and reported, exit 0."""
        start = json.loads(helpers.harness(["factory-run", "start"], self.tmp).stdout)
        run = start["run"]
        cases = [("--model", ""), ("--model", " "), ("--duration-ms", "abc"), ("--duration-ms", "-3"),
                 ("--tokens", "lots"), ("--tokens", "-1"), ("--tokens", "1.5")]
        for option, value in cases:
            args = ["factory-run", "record", "--run", run, "--agent", "factory-planner",
                    "--model", "inherit", "--duration-ms", "10", option, value]
            res = helpers.harness(args, self.tmp)
            self.assertEqual(res.returncode, 0, "%s %r: %s" % (option, value, res.stderr))
            warnings = " ".join(json.loads(res.stdout)["journal_warnings"])
            self.assertIn(option.lstrip("-"), warnings, (option, value))
        lines = [x for x in journal.read(self.tmp / run) if "event" not in x]   # agent lines (AP-03: + autonomy event)
        self.assertEqual(len(lines), len(cases))
        self.assertEqual([x["model"] for x in lines[:2]], [journal.FALLBACK_TEXT] * 2)
        self.assertEqual([x["duration_ms"] for x in lines[2:4]], [None, None])
        self.assertEqual([x["tokens"] for x in lines[4:]], [None, None, None])
        self.assertEqual(journal.validate_file(self.tmp / run), [])

    def test_journal_amend_completes_last_entry_of_the_slot(self):
        """JR-03 (BACKLOG section 7): duration and tokens known only after the call
        complete the last entry of that slot; a logged value is never overwritten."""
        from hcore import shippr
        run = runs.create(self.tmp, "ship-pr", shippr.new_state("main", 3, ["logic and edge cases", "b"]))
        out = helpers.write_output(self.tmp, "r.md", helpers.reviewer_block([helpers.finding()]))
        fid = None
        for theme in ("logic and edge cases", "b", "logic and edge cases"):
            if theme == "logic and edge cases" and fid is not None:
                args = ["ship-pr", "validator", "--run", str(run), "--finding", fid, "--model", "haiku", "--dead"]
            else:
                args = ["ship-pr", "reviewer", "--run", str(run), "--theme", theme, "--model", "haiku",
                        "--output", out]
            res = helpers.harness(args, self.tmp)
            self.assertEqual(res.returncode, 0, res.stderr)
            fid = fid or json.loads(res.stdout)["to_validate"][0]["id"]
        self.assertIn("journal amend", " ".join(json.loads(res.stdout)["journal_warnings"]))
        amend = ["journal", "amend", "--run", str(run)]
        res = helpers.harness(amend + ["--slot", "reviewer:logic and edge cases", "--duration-ms", "900",
                                       "--tokens", "1200"], self.tmp)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout)["amended"], ["duration_ms", "tokens"])
        res = helpers.harness(amend + ["--slot", "validator:%s" % fid, "--duration-ms", "30"], self.tmp)
        self.assertEqual(json.loads(res.stdout)["amended"], ["duration_ms"])
        lines = journal.read(run)
        self.assertEqual((lines[0]["duration_ms"], lines[0]["tokens"]), (900, 1200))
        self.assertEqual((lines[1]["duration_ms"], lines[1]["tokens"]), (None, None))
        self.assertEqual((lines[2]["duration_ms"], lines[2]["tokens"]), (30, None))
        # Already logged: kept and reported; unknown slot: reported; never blocking.
        res = helpers.harness(amend + ["--slot", "reviewer:logic and edge cases", "--duration-ms", "1"], self.tmp)
        self.assertEqual(res.returncode, 0)
        self.assertEqual(json.loads(res.stdout)["amended"], [])
        self.assertIn("kept", json.loads(res.stdout)["journal_warnings"][0])
        res = helpers.harness(amend + ["--slot", "reviewer:nope", "--tokens", "abc"], self.tmp)
        self.assertEqual(res.returncode, 0)
        self.assertEqual(len(json.loads(res.stdout)["journal_warnings"]), 2)
        self.assertEqual(journal.read(run)[0]["duration_ms"], 900)
        self.assertEqual(helpers.harness(amend + ["--slot", "b"], self.tmp).returncode, 2)
        self.assertEqual(journal.validate_file(run), [])
        # factory-run: the slot is the agent name.
        start = json.loads(helpers.harness(["factory-run", "start"], self.tmp).stdout)
        helpers.harness(["factory-run", "record", "--run", start["run"], "--agent", "factory-planner",
                         "--model", "inherit"], self.tmp)
        res = helpers.harness(["journal", "amend", "--run", start["run"], "--slot", "factory-planner",
                               "--duration-ms", "77", "--tokens", "5"], self.tmp)
        self.assertEqual(json.loads(res.stdout)["amended"], ["duration_ms", "tokens"])
        self.assertEqual([x for x in journal.read(self.tmp / start["run"]) if "event" not in x][0]["duration_ms"], 77)

    def test_duration_from_core_timestamps(self):
        state = {}
        runs.mark_begin(state, "x", clock=lambda: 10.0)
        self.assertEqual(runs.duration_ms(state, "x", None, clock=lambda: 12.5), 2500)
        with self.assertRaises(ValueError):
            runs.duration_ms(state, "x", None)
        self.assertEqual(runs.duration_ms(state, "y", 42), 42)


if __name__ == "__main__":
    unittest.main()
