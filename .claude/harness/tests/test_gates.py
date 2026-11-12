# Module hcore.gates and the 'gate' CLI: policy, decision, headless
# fallback, journal events (GT-01 to GT-08, CA-10, CA-11).

import json
import re
import unittest
from pathlib import Path

import helpers
from hcore import gates, journal, runs, shippr
from hcore.gates import GateError

SKILLS_DIR = helpers.CORE.parent


def check_ok(test, res):
    test.assertEqual(res.returncode, 0, "stdout=%s\nstderr=%s" % (res.stdout, res.stderr))
    return json.loads(res.stdout)


class PolicyTest(helpers.TempDirCase):
    def policy(self, text):
        path = self.tmp / ".claude" / "harness.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_gt01_missing_file_or_key_means_gate(self):
        """GT-01: missing file or key -> gate."""
        self.assertEqual(gates.read_policy(self.tmp), "gate")
        self.policy('{"other": 1}')
        self.assertEqual(gates.read_policy(self.tmp), "gate")
        self.policy('{"autonomy": "auto"}')
        self.assertEqual(gates.read_policy(self.tmp), "auto")

    def test_gt01_invalid_value_or_unreadable_json_is_exit_2(self):
        """GT-01: invalid value or unreadable JSON -> exit 2."""
        for text in ('{"autonomy": "yolo"}', "{", "[1]"):
            self.policy(text)
            with self.assertRaises(GateError):
                gates.read_policy(self.tmp)
            res = helpers.harness(["gate", "decide", "--id", "ship-pr.merge"], self.tmp)
            self.assertEqual(res.returncode, 2, text)

    def test_ap01_decision_table(self):
        """AP-01 replaces GT-02: gate -> ask for a, b, c; auto -> default for b and c; kind a goes through stop_when."""
        for kind in gates.KINDS:
            self.assertEqual(gates.decision(kind, "gate"), gates.ASK)
        self.assertEqual(gates.decision("b", "auto"), gates.DEFAULT)
        self.assertEqual(gates.decision("c", "auto"), gates.DEFAULT)
        with self.assertRaises(GateError):
            gates.decision("d", "gate")

    def test_ca10_absent_file_asks_for_a_b_and_c(self):
        for gid in ("ship-pr.merge", "ship-pr.conflict", "factory-run.approval"):
            event = gates.decide(gid, gates.read_policy(self.tmp))
            self.assertEqual(event["decision"], gates.ASK, gid)
            self.assertNotIn("warning", event)

    def test_ap02_the_phase1_guard_is_gone(self):
        """AP-02 removes GT-03: auto is no longer applied as gate; the decision carries its justification."""
        event = gates.decide("ship-pr.conflict", "auto")
        self.assertEqual((event["decision"], event["policy"]), (gates.DEFAULT, "auto"))
        self.assertNotIn("warning", event)
        self.assertTrue(event["justification"])

    def test_gt04_dialogue_gate_always_asks(self):
        """GT-04: a dialogue gate (brainstorm.*) always returns ask."""
        for gid in ("brainstorm.subject", "brainstorm.round", "brainstorm.write"):
            self.assertTrue(gates.get(gid).dialogue)
            self.assertEqual(gates.decide(gid, "auto")["decision"], gates.ASK, gid)

    def test_gt05_no_default_and_decision_default_is_stop(self):
        """GT-05: no default and decision default -> stop."""
        event = gates.decide("factory-run.need", "auto")
        self.assertEqual(event["decision"], gates.STOP)
        self.assertEqual(gates.decide("factory-run.approval", "auto")["decision"],
                         gates.DEFAULT)
        with self.assertRaises(GateError):
            gates.answer("factory-run.need", event, "user", "x")


class CatalogTest(unittest.TestCase):
    def test_gt08_catalog_has_the_35_core_gates(self):
        """GT-08: the catalog (27 gates of Phase 1, 5 of Phase 2 in the core, the rest declared by
        the profiles, PF-05), every entry complete."""
        self.assertEqual(len(gates.CATALOG), 38)   # GT-09: + 3 gates of Phase 3; AP-10: + 3 gates of Phase 4
        for gate in gates.all_gates().values():
            self.assertIn(gate.kind, gates.KINDS, gate.id)
            self.assertIn(gate.headless, gates.HEADLESS, gate.id)
            self.assertTrue(gate.options and all(gate.options), gate.id)
            self.assertTrue(gate.justification.strip(), gate.id)
            self.assertIn(gate.skill, journal.SKILLS, gate.id)
            if gate.headless == gates.DEFAULT:
                self.assertIsNotNone(gate.default, gate.id)

    def test_gt08_unknown_id_refused(self):
        """GT-08: unknown id refused."""
        with self.assertRaises(GateError):
            gates.get("ship-pr.nope")

    def test_gt06_every_gate_returns_its_headless_fallback(self):
        """GT-06: the decision returns the headless fallback of the gate."""
        expected = {"ship-pr.target": "stop", "ship-pr.dirty": "default", "ship-pr.conflict": "default",
                    "ship-pr.merge": "default", "software-factory.code-index": "skip",
                    "factory-run.questions": "stop", "factory-run.approval": "stop"}
        for gid, fallback in expected.items():
            self.assertEqual(gates.decide(gid, "gate")["headless"], fallback, gid)

    @unittest.skipUnless((SKILLS_DIR / "ship-pr" / "SKILL.md").is_file(), "SKILL.md files absent")
    def test_ca11_partial_gates_of_software_factory_and_ship_pr_are_in_their_skill(self):
        """CA-11 for the L2 skills: id and every option found in the source SKILL.md."""
        files = {"software-factory": "software-factory", "factory-run": "software-factory", "ship-pr": "ship-pr"}
        for gate in gates.CATALOG.values():
            if gate.skill not in files or gate.id == "factory-run.need":
                continue
            text = (SKILLS_DIR / files[gate.skill] / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn(gate.id, text)
            for option in gate.options:
                self.assertIn(option, text, (gate.id, option))


class CliTest(helpers.TempDirCase):
    def h(self, *args):
        return helpers.harness(list(args), self.tmp)

    def test_gt08_cli_unknown_id_is_exit_2(self):
        res = self.h("gate", "decide", "--id", "ship-pr.nope")
        self.assertEqual(res.returncode, 2)
        self.assertIn("unknown gate", res.stderr)
        self.assertFalse((self.tmp / ".harness" / "runs").exists())

    def test_gt07_decision_then_answer_logged(self):
        """GT-07, CA-10: both events in the run journal, valid schema (JR-02)."""
        d = check_ok(self, self.h("gate", "decide", "--id", "software-factory.code-index"))
        self.assertEqual((d["decision"], d["headless"], d["options"]), ("ask", "skip", ["install", "refuse"]))
        self.assertRegex(Path(d["run"]).name, r"^software-factory-[0-9]{8}-[0-9]{6}\.jsonl$")
        a = check_ok(self, self.h("gate", "answer", "--run", d["run"], "--id", "software-factory.code-index",
                                  "--answer", "install"))
        self.assertEqual((a["answer"], a["source"], a["apply"]), ("install", "user", "answer"))
        lines = [x for x in journal.read(self.tmp / d["run"]) if x.get("event") == "gate"]
        self.assertEqual([x["phase"] for x in lines], ["decision", "answer"])
        self.assertEqual((lines[0]["answer"], lines[0]["source"]), (None, None))
        self.assertEqual((lines[1]["answer"], lines[1]["source"], lines[1]["policy"]), ("install", "user", "gate"))
        check_ok(self, self.h("journal", "validate", d["run"]))
        # An answer needs a pending decision.
        again = self.h("gate", "answer", "--run", d["run"], "--id", "software-factory.code-index", "--answer", "x")
        self.assertEqual(again.returncode, 2)

    def test_ap02_cli_auto_takes_the_default(self):
        (self.tmp / ".claude").mkdir()
        (self.tmp / ".claude" / "harness.json").write_text('{"autonomy": "auto"}', encoding="utf-8")
        d = check_ok(self, self.h("gate", "decide", "--id", "software-factory.code-index"))
        self.assertEqual((d["decision"], d["policy"]), ("default", "auto"))
        self.assertNotIn("warning", journal.read(self.tmp / d["run"])[1])

    def test_gt06_headless_fallbacks_applied_and_declared(self):
        run = runs.create(self.tmp, "ship-pr", shippr.new_state("main", 3, ["t"]))
        for gid, apply, answer in (("ship-pr.merge", "default", "Leave the PR open"),
                                   ("ship-pr.dirty", "default", "abandon")):
            check_ok(self, self.h("gate", "decide", "--run", str(run), "--id", gid))
            a = check_ok(self, self.h("gate", "answer", "--run", str(run), "--id", gid, "--headless"))
            self.assertEqual((a["apply"], a["answer"], a["source"]), (apply, answer, "headless"))
        check_ok(self, self.h("gate", "decide", "--run", str(run), "--id", "ship-pr.target"))
        stop = self.h("gate", "answer", "--run", str(run), "--id", "ship-pr.target", "--headless")
        self.assertEqual(stop.returncode, 1)
        self.assertIn("STOP: gate ship-pr.target", stop.stderr)
        sf = check_ok(self, self.h("gate", "decide", "--id", "software-factory.code-index"))
        skip = check_ok(self, self.h("gate", "answer", "--run", sf["run"], "--id", "software-factory.code-index",
                                     "--headless"))
        self.assertEqual((skip["apply"], skip["answer"]), ("skip", "skip"))
        lines = journal.read(run)
        self.assertEqual([x["source"] for x in lines if x.get("phase") == "answer"], ["headless"] * 3)
        self.assertEqual(journal.validate_file(run), [])

    def test_gate_of_another_skill_and_answer_without_run_refused(self):
        run = runs.create(self.tmp, "ship-pr", shippr.new_state("main", 3, ["t"]))
        self.assertEqual(self.h("gate", "decide", "--run", str(run), "--id", "factory-run.approval").returncode, 2)
        self.assertEqual(self.h("gate", "answer", "--id", "ship-pr.merge", "--answer", "Merge").returncode, 2)
        check_ok(self, self.h("gate", "decide", "--run", str(run), "--id", "ship-pr.merge"))
        both = self.h("gate", "answer", "--run", str(run), "--id", "ship-pr.merge", "--answer", "Merge", "--headless")
        self.assertEqual(both.returncode, 2)
        default = self.h("gate", "answer", "--run", str(run), "--id", "ship-pr.merge", "--default")
        self.assertEqual(default.returncode, 2)

    def test_free_answer_travels_by_file_and_is_consumed(self):
        d = check_ok(self, self.h("gate", "decide", "--id", "ship-pr.target"))
        answer = helpers.write_output(self.tmp, "gate-answer.txt", "release/`date`-$(whoami)\n")
        a = check_ok(self, self.h("gate", "answer", "--run", d["run"], "--id", "ship-pr.target",
                                  "--answer-file", answer))
        self.assertEqual(a["answer"], "release/`date`-$(whoami)")
        self.assertFalse((self.tmp / answer).exists())

    def test_gt05_stop_decision_expects_no_answer(self):
        """A logged stop decision (GT-05) accepts no answer (exit 2)."""
        run = runs.create(self.tmp, "factory-run", {})
        event = gates.decide("factory-run.need", "auto")
        self.assertEqual(event["decision"], gates.STOP)
        journal.append(run, event)
        self.assertEqual(journal.validate_file(run), [])
        res = self.h("gate", "answer", "--run", str(run), "--id", "factory-run.need", "--answer", "x")
        self.assertEqual(res.returncode, 2)
        self.assertIn("decision was stop", res.stderr)


class VerifierDefectsTest(helpers.TempDirCase):
    """Minor defects found by the Verifier on L2 and L3."""

    def h(self, *args):
        return helpers.harness(list(args), self.tmp)

    def test_gt01_invalid_policy_leaves_no_orphan_run(self):
        """GT-01, defect 1: 'gate decide' without --run validates .claude/harness.json
        BEFORE opening the run: an exit 2 leaves no run behind."""
        (self.tmp / ".claude").mkdir()
        for text in ('{"autonomy": "yolo"}', "{"):
            (self.tmp / ".claude" / "harness.json").write_text(text, encoding="utf-8")
            res = self.h("gate", "decide", "--id", "software-factory.code-index")
            self.assertEqual(res.returncode, 2, text)
            self.assertFalse((self.tmp / ".harness" / "runs").exists(), text)

    def test_gt07_off_option_answer_asks_again_and_never_kills_the_run(self):
        """GT-07, defect 2 (iteration 2): a user answer outside the closed options
        (a free "Other" text) is not logged and returns action "reask" (exit 0):
        the decision stays pending and the same gate is answered again; a free
        gate keeps its free answer."""
        run = runs.create(self.tmp, "ship-pr", shippr.new_state("main", 3, ["t"]))
        check_ok(self, self.h("gate", "decide", "--run", str(run), "--id", "ship-pr.merge"))
        for bad in ("Merge it now", "merger", "Merge / Leave the PR open"):
            res = check_ok(self, self.h("gate", "answer", "--run", str(run), "--id", "ship-pr.merge",
                                        "--answer", bad))
            self.assertEqual(res["action"], "reask", bad)
            self.assertIn("not one of the options", res["reason"])
            self.assertEqual(res["options"], ["Merge", "Leave the PR open"])
        self.assertEqual([x["phase"] for x in journal.read(run) if x.get("event") == "gate"], ["decision"])
        a = check_ok(self, self.h("gate", "answer", "--run", str(run), "--id", "ship-pr.merge", "--answer", "Merge"))
        self.assertEqual((a["action"], a["answer"]), ("apply", "Merge"))
        self.assertEqual([x["phase"] for x in journal.read(run) if x.get("event") == "gate"], ["decision", "answer"])
        free = gates.answer("factory-run.need", gates.decide("factory-run.need", "gate"), "user", "Any need at all")
        self.assertEqual(free["answer"], "Any need at all")

    def test_gt07_multi_question_gate_one_valid_option_per_question_in_order(self):
        """GT-07, iteration 2: a gate asking several questions takes exactly one
        valid option per question, in order; a per-theme gate takes 1 to 4 options
        of its single list."""
        order = gates.decide("vibecode-audit.order", "gate")
        ok = gates.answer("vibecode-audit.order", order, "user", "Quick wins / Omit them")
        self.assertEqual(ok["answer"], "Quick wins / Omit them")
        for bad in ("Quick wins / everything", "Omit them / Quick wins", "Quick wins",
                    "Quick wins / Keep the order", "Quick wins / Omit them / Detail them"):
            with self.assertRaises(gates.OffOptions, msg=bad):
                gates.answer("vibecode-audit.order", order, "user", bad)
        framing = gates.decide("legacy-audit.framing", "gate")
        self.assertEqual(gates.answer("legacy-audit.framing", framing, "user", "2 / foundations")["answer"],
                         "2 / foundations")
        with self.assertRaises(gates.OffOptions):
            gates.answer("legacy-audit.framing", framing, "user", "foundations / 2")
        assign = gates.decide("legacy-audit.assignment", "gate")
        self.assertEqual(gates.answer("legacy-audit.assignment", assign, "user",
                                      "Milestone 1 / Milestone 1 / Exclude from roadmap / Milestone 2")["source"], "user")
        for bad in ("Milestone 1 / Milestone 1 / Milestone 1 / Milestone 1 / Milestone 2", "Milestone 3"):
            with self.assertRaises(gates.OffOptions, msg=bad):
                gates.answer("legacy-audit.assignment", assign, "user", bad)
        self.assertEqual(gates.get("vibecode-audit.order").questions[1],
                         ("Detail them", "List them in one line", "Omit them"))

    def test_gt07_a_french_token_gives_the_same_decision_as_the_english_one(self):
        """GT-07: the former French tokens are still accepted and recorded as the English ones."""
        for gid, french, english in (
                ("factory-run.approval", "approbation", "approval"),
                ("factory-run.approval", "itérer", "iterate"),
                ("ship-pr.merge", "Merger", "Merge"),
                ("legacy-audit.assignment", "Jalon 1 / Jalon dernier / Exclure de la roadmap",
                 "Milestone 1 / Last milestone / Exclude from roadmap"),
                ("vibecode-audit.order", "Gains rapides / Les omettre", "Quick wins / Omit them")):
            events = []
            for token in (french, english):
                decision = gates.decide(gid, "gate")
                events.append((decision["decision"], gates.answer(gid, decision, "user", token)))
            self.assertEqual(events[0][0], events[1][0], gid)
            self.assertEqual(events[0][1]["answer"], english, gid)
            self.assertEqual(events[0][1], events[1][1], gid)
        decision = gates.decide("factory-run.approval", "gate")
        with self.assertRaises(gates.OffOptions):
            gates.answer("factory-run.approval", decision, "user", "approbation / refuse")

    def test_gt08_free_and_multi_gates_declared_and_defaults_are_options(self):
        """GT-08: the free-answer gates are declared in the catalog; every declared
        default is itself a valid answer of its gate."""
        free = sorted(g.id for g in gates.CATALOG.values() if g.free)
        self.assertEqual(free, ["audit.profiles", "brainstorm.round", "brainstorm.subject", "factory-run.need",
                                "factory-run.profile", "factory-run.questions", "lab-run.pi-questions",
                                "ship-pr.target", "software-factory.profiles"])
        for gate in gates.all_gates().values():
            if gate.default is not None:
                self.assertEqual(gates.check_option(gate, gate.default), gate.default, gate.id)


class SkillWiringTest(unittest.TestCase):
    @unittest.skipUnless((SKILLS_DIR / "ship-pr" / "SKILL.md").is_file(), "SKILL.md files absent")
    def test_gates_wired_in_ship_pr_and_software_factory(self):
        """L2: every gate of the two skills goes through 'gate decide' / 'gate answer'."""
        ship = (SKILLS_DIR / "ship-pr" / "SKILL.md").read_text(encoding="utf-8")
        sf = (SKILLS_DIR / "software-factory" / "SKILL.md").read_text(encoding="utf-8")
        for text in (ship, sf):
            # Iteration 2: an off-option answer returns action reask, the skill asks again.
            self.assertIn("`reask`", text)
            self.assertRegex(text, r"ask (the|the same) question again")
            self.assertIn("gate decide", text)
            self.assertIn("gate answer", text)
            self.assertIn("--headless", text)
        for gid in ("ship-pr.target", "ship-pr.dirty", "ship-pr.conflict", "ship-pr.merge"):
            self.assertRegex(ship, r"--id %s\b" % re.escape(gid))
        for gid in ("software-factory.code-index", "factory-run.questions", "factory-run.approval"):
            self.assertRegex(sf, r"(--id |gate `)%s\b" % re.escape(gid))


if __name__ == "__main__":
    unittest.main()
