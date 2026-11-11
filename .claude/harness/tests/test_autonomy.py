# Autonomy policy: AP-01 to AP-10 (decision table, override, installed default, journal, catalog).
#
# AP-09 (each SKILL.md writes the default of its gates with the justification of the catalog) is a
# property of the prose: it is checked by the grep of CA-07 at verification time, not by a test here.

import json
import unittest
from pathlib import Path

import helpers
from hcore import gates, journal

REPO = helpers.CORE.parent.parent


def out(test, res, code=0):
    test.assertEqual(res.returncode, code, "stdout=%s\nstderr=%s" % (res.stdout, res.stderr))
    return json.loads(res.stdout) if res.stdout.strip() else None


class DecisionTableTest(unittest.TestCase):
    def test_ap01_gate_asks_for_a_b_and_c(self):
        """AP-01: policy gate -> ask for the three kinds."""
        for gid in ("ship-pr.dirty", "ship-pr.conflict", "ship-pr.target", "factory-run.approval"):
            self.assertEqual(gates.decide(gid, "gate")["decision"], gates.ASK, gid)

    def test_ap01_auto_takes_the_default_for_b_and_c(self):
        """AP-01: policy auto -> default for b and c; b or c without a default -> stop."""
        self.assertEqual(gates.decide("ship-pr.conflict", "auto")["decision"], gates.DEFAULT)         # b
        self.assertEqual(gates.decide("factory-run.approval", "auto")["decision"], gates.DEFAULT)     # c
        self.assertEqual(gates.decide("factory-run.need", "auto")["decision"], gates.STOP)            # c, no default
        self.assertEqual(gates.decide("lab-run.pi-questions", "auto")["decision"], gates.STOP)        # b, no default

    def test_ap01_kind_a_evaluates_stop_when(self):
        """AP-01: auto and kind a: stop_when true -> stop, false -> default; the result is logged (AP-06)."""
        always = gates.decide("ship-pr.dirty", "auto")
        self.assertEqual((always["decision"], always["stop_when"], always["stop_result"]), ("stop", "always", True))
        never = gates.decide("ship-pr.merge", "auto")
        self.assertEqual((never["decision"], never["stop_result"]), ("default", False))
        pre = "lab-run.preregistration"
        self.assertEqual(gates.decide(pre, "auto", inputs={"blocking_unanswered": True})["decision"], gates.STOP)
        self.assertEqual(gates.decide(pre, "auto", inputs={"blocking_unanswered": False})["decision"], gates.DEFAULT)
        self.assertEqual(gates.decide(pre, "gate", inputs={"blocking_unanswered": True})["decision"], gates.ASK)

    def test_ap01_dialogue_gates_always_ask_and_nothing_else_does_under_auto(self):
        """AP-01: dialogue -> ask; AP-07: no other auto decision is ask, AskUserQuestion or not."""
        for gate in gates.all_gates().values():
            decision = gates.decide(gate.id, "auto")["decision"]
            if gate.dialogue:
                self.assertEqual(decision, gates.ASK, gate.id)
            else:
                self.assertNotEqual(decision, gates.ASK, gate.id)

    def test_ap02_no_phase_mark_left_in_the_core(self):
        """AP-02: the Phase 1 guard and the mark of Phase 2 are gone from the core code."""
        mark = "phase2" + "_auto"
        hits = [p.name for p in list(helpers.CORE.rglob("*.py")) + list(helpers.CORE.rglob("*.json"))
                if mark in p.read_text(encoding="utf-8") and p.name != Path(__file__).name]
        self.assertEqual(hits, [])
        self.assertFalse(hasattr(gates, "AUTO_APPLIED_AS_GATE"))

    def test_ap06_the_decision_event_carries_what_was_decided(self):
        """AP-06: policy, decision, default and justification are in the event, valid for the journal."""
        event = gates.decide("ship-pr.conflict", "auto")
        for key in ("policy", "decision", "default", "justification"):
            self.assertTrue(event[key], key)
        self.assertEqual(gates.validate_event(event), [])

    def test_ap10_catalog_is_complete(self):
        """AP-10: every gate has a kind and a justification, every kind a gate a stop_when; the new gates exist."""
        for gate in gates.all_gates().values():
            self.assertTrue(gate.justification.strip(), gate.id)
            if gate.kind == "a":
                self.assertIn(gate.stop_when, ("always", "never") + ("input:blocking_unanswered",), gate.id)
        for gid in ("factory-run.ship", "audit.chain", "harness-doctor.upgrade"):
            self.assertIn(gid, gates.all_gates())

    def test_ap10_ship_target_default_comes_from_the_stamp(self):
        """AP-10: ship-pr.target takes ship_pr.target of the stamp under auto, otherwise it stops."""
        self.assertEqual(gates.decide("ship-pr.target", "auto")["decision"], gates.STOP)
        event = gates.decide("ship-pr.target", "auto", default="release")
        self.assertEqual((event["decision"], event["default"]), (gates.DEFAULT, "release"))


class CliTest(helpers.TempDirCase):
    def stamp(self, text):
        path = self.tmp / ".claude" / "harness.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_ap03_the_flag_wins_and_never_writes_the_stamp(self):
        """AP-03, CA-05: --autonomy auto on a gate stamp -> default, stamp byte for byte identical, source flag."""
        path = self.stamp('{"autonomy": "gate"}')
        before = path.read_bytes()
        d = out(self, helpers.harness(["gate", "decide", "--id", "factory-run.approval", "--autonomy", "auto"], self.tmp))
        self.assertEqual((d["decision"], d["policy"]), ("default", "auto"))
        self.assertEqual(path.read_bytes(), before)
        events = [x for x in journal.read(self.tmp / d["run"]) if x.get("event") == "autonomy"]
        self.assertEqual([(e["value"], e["source"]) for e in events], [("auto", "flag")])
        bad = helpers.harness(["gate", "decide", "--id", "factory-run.approval", "--autonomy", "maybe"], self.tmp)
        self.assertEqual(bad.returncode, 2)

    def test_ap03_priority_flag_then_stamp_then_absent(self):
        """AP-03: option > stamp > absent (= gate); the source is journaled."""
        sources = []
        for text, flag in ((None, None), ('{"autonomy": "auto"}', None), ('{"autonomy": "auto"}', "gate")):
            if text:
                self.stamp(text)
            args = ["gate", "decide", "--id", "ship-pr.conflict"] + (["--autonomy", flag] if flag else [])
            d = out(self, helpers.harness(args, self.tmp))
            ev = [x for x in journal.read(self.tmp / d["run"]) if x.get("event") == "autonomy"][0]
            sources.append((d["decision"], ev["source"]))
        self.assertEqual(sources, [("ask", "absent"), ("default", "stamp"), ("ask", "flag")])

    def test_ap01_stop_decision_writes_a_stop_file_and_exits_1(self):
        """AP-01, SX-05: a kind a gate whose stop_when holds stops: exit 1, a STOP file of kind gate."""
        res = helpers.harness(["gate", "decide", "--id", "ship-pr.dirty", "--autonomy", "auto"], self.tmp)
        self.assertEqual(res.returncode, 1)
        self.assertIn("STOP:", res.stderr)
        files = list((self.tmp / ".harness" / "runs").glob("STOP-*.md"))
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].read_text(encoding="utf-8").startswith("# STOP — ship-pr — gate"))

    def test_ap05_installed_default(self):
        """AP-05, CA-06: absent -> auto; an existing value is kept (file identical); --autonomy gate on a fresh stamp."""
        d = out(self, helpers.harness(["stamp", "autonomy", "--root", str(self.tmp)], self.tmp))
        self.assertEqual((d["value"], d["written"]), ("auto", True))
        self.assertEqual(json.loads((self.tmp / ".claude" / "harness.json").read_text(encoding="utf-8"))["autonomy"],
                         "auto")
        path = self.stamp('{"autonomy": "gate"}')
        before = path.read_bytes()
        d = out(self, helpers.harness(["stamp", "autonomy", "--root", str(self.tmp)], self.tmp))
        self.assertEqual((d["value"], d["written"]), ("gate", False))
        self.assertEqual(path.read_bytes(), before)
        path.unlink()
        d = out(self, helpers.harness(["stamp", "autonomy", "--root", str(self.tmp), "--autonomy", "gate"], self.tmp))
        self.assertEqual(d["value"], "gate")

    def test_ap07_catalog_lists_justification_and_stop_when(self):
        """AP-10, CA-07: `gate list` gives kind, justification and, for kind a, stop_when."""
        listed = out(self, helpers.harness(["gate", "list"], self.tmp))["gates"]
        for g in listed:
            self.assertTrue(g["justification"].strip(), g["id"])
            if g["kind"] == "a":
                self.assertTrue(g["stop_when"], g["id"])

    @unittest.skipUnless((REPO / ".claude" / "harness.json").is_file(), "repository stamp absent")
    def test_ap08_this_repository_stays_in_gate(self):
        """AP-08, CA-08: the harness does not modify itself under autonomy."""
        data = json.loads((REPO / ".claude" / "harness.json").read_text(encoding="utf-8"))
        self.assertEqual(data["autonomy"], "gate")


if __name__ == "__main__":
    unittest.main()
