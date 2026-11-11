# Computed prioritization of the audits and the audit -> queue chain: PZ-01 to PZ-05, CH-04.

import json
import unittest

import helpers
import phase4
from hcore import prioritize

CASES = helpers.FIXTURES / "prioritization"


def f(fid, severity, size="S", deps=()):
    return {"id": fid, "severity": severity, "size": size, "depends_on": list(deps)}


class RankTest(unittest.TestCase):
    @unittest.skipUnless(CASES.is_dir(), helpers.SKIP_FIXTURES)
    def test_pz02_pz05_the_fixture_sets_give_the_expected_milestones_and_ranks(self):
        """PZ-02, PZ-05, CA-18: milestones, assignment and ranks of each set of findings."""
        for path in sorted(CASES.glob("*.json")):
            case = json.loads(path.read_text(encoding="utf-8"))
            got = prioritize.rank(case["findings"])
            self.assertEqual(got["milestones"], case["milestones"], path.name)
            self.assertEqual(got["assignment"], case["assignment"], path.name)
            self.assertEqual(got["ranks"], case["ranks"], path.name)
            self.assertEqual(got["priority"], got["ranks"], path.name)

    def test_pz02_a_minor_prerequisite_of_a_critical_moves_to_milestone_1(self):
        """PZ-02: dependency closure."""
        m = prioritize.milestones([f("X", "critical", deps=["Y"]), f("Y", "minor")])
        self.assertEqual(m, {"X": 1, "Y": 1})
        chain = prioritize.milestones([f("A", "critical", deps=["B"]), f("B", "major", deps=["C"]), f("C", "minor")])
        self.assertEqual(set(chain.values()), {1})

    def test_pz02_empty_milestones_are_removed_and_the_rest_renumbered(self):
        """PZ-02 (Q13): findings all major give one milestone; critical and minor give 1 and 2."""
        self.assertEqual(set(prioritize.milestones([f("A", "major"), f("B", "major")]).values()), {1})
        got = prioritize.rank([f("A", "critical"), f("B", "minor")])
        self.assertEqual([(m["number"], m["title"]) for m in got["milestones"]], [(1, "Critiques"), (2, "Mineurs")])

    def test_pz02_a_dependency_cycle_does_not_loop(self):
        """PZ-02: a cycle of dependencies still terminates, every finding ranked once."""
        got = prioritize.rank([f("A", "major", deps=["B"]), f("B", "major", deps=["A"])])
        self.assertEqual(sorted(got["ranks"]), ["A", "B"])
        self.assertEqual(sorted(got["ranks"].values()), [1, 2])

    def test_pz03_the_cap_keeps_the_first_in_milestone_order(self):
        """PZ-03: nothing is excluded; the cap keeps the first N and lists the excess."""
        got = prioritize.rank([f("A", "major"), f("B", "critical"), f("C", "minor")])
        order = sorted(got["ranks"], key=got["ranks"].get)
        self.assertEqual(prioritize.apply_cap(order, 2), {"kept": ["B", "A"], "excess": ["C"]})
        self.assertEqual(prioritize.apply_cap(order, None)["excess"], [])

    def test_pz01_pz04_framing_and_research_questions(self):
        """PZ-01: the reliable-first criteria per set; PZ-04: severity decreasing then cost increasing."""
        self.assertEqual(prioritize.FRAMING["vibecode"], {"order": "Severity first", "minor": "Detail them"})
        self.assertEqual(prioritize.FRAMING["legacy"]["order"], "risk first")
        qs = [f("Q1", "minor", "XS"), f("Q2", "critical", "L"), f("Q3", "critical", "S"), f("Q4", "major", "XS")]
        self.assertEqual(prioritize.rank_questions(qs), ["Q3", "Q2", "Q4", "Q1"])


class ChainPlanTest(helpers.TempDirCase):
    def test_ch04_pz05_specs_are_ordered_by_priority_then_name(self):
        """CH-04, PZ-05: the proposed specs, `Priorité` ascending (absent last), then the file name; --only restricts."""
        phase4.write_spec(self.tmp, "SPEC-leg-02-b.md", phase4.spec_text("PROPOSEE", header=["Priorité : 2"]))
        phase4.write_spec(self.tmp, "SPEC-leg-01-a.md", phase4.spec_text("PROPOSEE", header=["Priorité : 3"]))
        phase4.write_spec(self.tmp, "SPEC-leg-03-c.md", phase4.spec_text("PROPOSEE", header=["Priorité : 1"]))
        phase4.write_spec(self.tmp, "SPEC-leg-04-d.md", phase4.spec_text("PROPOSEE"))
        phase4.write_spec(self.tmp, "SPEC-leg-05-e.md", phase4.spec_text("LIVREE", header=["Priorité : 1"]))
        rows = prioritize.chain_plan(self.tmp)
        self.assertEqual([r["file"] for r in rows], ["specs/SPEC-leg-03-c.md", "specs/SPEC-leg-02-b.md",
                                                     "specs/SPEC-leg-01-a.md", "specs/SPEC-leg-04-d.md"])
        only = prioritize.chain_plan(self.tmp, ["SPEC-leg-01-a.md", "specs/SPEC-leg-02-b.md"])
        self.assertEqual([r["file"] for r in only], ["specs/SPEC-leg-02-b.md", "specs/SPEC-leg-01-a.md"])

    def test_ch04_audit_chain_decision_is_default_under_auto(self):
        """CH-04, CA-19: `audit chain-plan` under auto with a factory present chains the ordered list; `audit.chain` is default."""
        phase4.stamp(self.tmp, autonomy="auto")
        (self.tmp / ".claude" / "harness").mkdir(parents=True)
        (self.tmp / ".claude" / "harness" / "harness.py").write_text("#", encoding="utf-8")
        phase4.write_spec(self.tmp, "SPEC-leg-01-a.md", phase4.spec_text("PROPOSEE", header=["Priorité : 2"]))
        phase4.write_spec(self.tmp, "SPEC-leg-02-b.md", phase4.spec_text("PROPOSEE", header=["Priorité : 1"]))
        res = helpers.harness(["audit", "chain-plan", "--root", str(self.tmp)], self.tmp)
        self.assertEqual(res.returncode, 0, res.stderr)
        d = json.loads(res.stdout)
        self.assertEqual((d["chain"], d["decision"], d["only"]), (True, "default", "specs/SPEC-leg-02-b.md;specs/SPEC-leg-01-a.md"))
        gate = helpers.harness(["audit", "chain-plan", "--root", str(self.tmp), "--autonomy", "gate"], self.tmp)
        self.assertEqual(json.loads(gate.stdout)["decision"], "ask")
        (self.tmp / ".claude" / "harness" / "harness.py").unlink()
        none = json.loads(helpers.harness(["audit", "chain-plan", "--root", str(self.tmp)], self.tmp).stdout)
        self.assertFalse(none["chain"])


if __name__ == "__main__":
    unittest.main()
