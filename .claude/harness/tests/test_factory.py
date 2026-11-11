# Module hcore.factory: factory-run state machine (FR-01 to FR-04),
# sequences of definition (b) of the spec.

import unittest

import helpers
from hcore import factory

B, V = factory.BUILDER, factory.VERIFIER
CR = helpers.verifier_block("CHANGES_REQUESTED", False,
                            [{"file": "a.py", "severity": "major", "description": "empty case"}])
OK = helpers.verifier_block("APPROVED", True)


class EntryTest(helpers.TempDirCase):
    def spec(self, name, status_line):
        path = self.tmp / "specs" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("# SPEC\n\n%s\n\nBody.\n" % status_line, encoding="utf-8")
        return "specs/" + name

    def test_fr01_approved_spec_goes_to_build(self):
        """FR-01."""
        rel = self.spec("SPEC-a.md", "Statut : APPROUVEE")
        self.assertEqual(factory.entry(rel, self.tmp), {"mode": "build", "spec": rel})
        self.assertEqual(factory.entry("'%s'" % rel, self.tmp)["mode"], "build")

    def test_fr01_other_inputs_go_to_plan(self):
        """FR-01."""
        outside = self.tmp / "SPEC-c.md"
        outside.write_text("Statut : APPROUVEE\n", encoding="utf-8")
        for args in ("add the CSV export", "specs/missing.md", "SPEC-c.md", ""):
            self.assertEqual(factory.entry(args, self.tmp)["mode"], "plan", args)

    def test_bq07_e_p4_12_a_proposed_spec_goes_to_approve(self):
        """BQ-07, E-P4-12: a proposed spec goes to approve; a delivered one stops; an unknown status is refused."""
        proposed = self.spec("SPEC-b.md", "Statut : PROPOSEE")
        self.assertEqual(factory.entry(proposed, self.tmp)["mode"], "approve")
        delivered = factory.entry(self.spec("SPEC-d.md", "Statut : LIVREE"), self.tmp)
        self.assertEqual((delivered["mode"], delivered["stop"]), ("stop", "delivered"))
        with self.assertRaises(ValueError):
            factory.entry(self.spec("SPEC-e.md", "Statut : INCONNU"), self.tmp)

    def test_fr01_free_text_with_shell_characters_goes_to_plan(self):
        """FR-01: a free-text need (quotes, $, backtick, ':' or line break) is
        never a path and does not make the entry fail."""
        need = 'Add "export" : `rm -rf /` then $HOME and C:\\x\n<second line>|*?'
        self.assertEqual(factory.entry(need, self.tmp), {"mode": "plan", "spec": None})


class LoopTest(unittest.TestCase):
    def run_seq(self, steps):
        state = factory.new_state()
        out = []
        for agent, output, dead in steps:
            out.append(factory.record(state, agent, output, dead))
        return state, out

    def test_fr02_approved_and_tests_passed_is_success(self):
        """FR-02."""
        state, out = self.run_seq([(B, None, False), (V, OK, False)])
        self.assertEqual(out[-1][0]["action"], "success")
        self.assertEqual(state["status"], "success")

    def test_fr02_approved_with_failing_tests_is_not_success(self):
        """FR-02."""
        state, out = self.run_seq([(B, None, False), (V, helpers.verifier_block("APPROVED", False), False)])
        self.assertEqual(out[-1][0]["action"], "fix")
        self.assertNotEqual(state["status"], "success")

    def test_fr03_three_changes_requested_fail_with_issues(self):
        """FR-03."""
        steps = [(B, None, False), (V, CR, False)] * 3
        state, out = self.run_seq(steps)
        decision = out[-1][0]
        self.assertEqual(decision["action"], "fail")
        self.assertEqual(decision["issues"][0]["description"], "empty case")
        self.assertEqual(state["status"], "failed")
        self.assertEqual([o[1]["iteration"] for o in out], [1, 1, 2, 2, 3, 3])
        with self.assertRaises(factory.RunOver):
            factory.record(state, B, None, False)

    def test_fr04_dead_verifier_then_alive_keeps_counter(self):
        """FR-04."""
        state, out = self.run_seq([(B, None, False), (V, None, True), (V, OK, False)])
        self.assertEqual(out[1][0]["action"], "relaunch")
        self.assertEqual(out[1][1]["iteration"], out[2][1]["iteration"])
        self.assertEqual(state["iteration"], 1)
        self.assertEqual(out[-1][0]["action"], "success")

    def test_fr04_unusable_output_counts_as_dead(self):
        """FR-04."""
        state, out = self.run_seq([(B, None, False), (V, "I checked everything, it's fine", False)])
        self.assertEqual(out[-1][0]["action"], "relaunch")
        self.assertEqual(out[-1][1]["verdict"], "DEAD")

    def test_fr04_dead_twice_is_explicit_failure(self):
        """FR-04."""
        state, out = self.run_seq([(B, None, False), (V, None, True), (V, "no block", False)])
        self.assertEqual(out[-1][0]["action"], "fail")
        self.assertIn("died twice", out[-1][0]["reason"])
        state, out = self.run_seq([(B, None, True), (B, None, True)])
        self.assertEqual(out[-1][0]["action"], "fail")

    def test_fr04_failure_count_resets_after_success(self):
        """FR-04."""
        steps = [(B, None, True), (B, None, False), (V, CR, False), (B, None, True), (B, None, False), (V, OK, False)]
        state, out = self.run_seq(steps)
        self.assertEqual(out[-1][0]["action"], "success")

    def test_out_of_sequence_refused(self):
        state = factory.new_state()
        with self.assertRaises(ValueError):
            factory.record(state, V, OK, False)
        factory.record(state, factory.PLANNER, None, False)
        factory.record(state, B, None, False)
        with self.assertRaises(ValueError):
            factory.record(state, factory.PLANNER, None, False)


if __name__ == "__main__":
    unittest.main()
