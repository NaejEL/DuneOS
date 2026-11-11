# Single loop (LP-01 to LP-07): machine selection, routing of the orchestrator input, instances.

import json
import unittest

import helpers
from hcore import factory, labrun, loop, profiles


def h(root, *args):
    return helpers.harness(list(args), root)


class MachineTest(unittest.TestCase):
    def test_lp01_lp03_lp04_the_loops_are_declared_by_the_profiles_and_the_facades_follow(self):
        """LP-01, LP-03, LP-04: software declares plan -> gate -> build -> verify -> record, research the
        consult/interpret loop with the Phase 1 gate ids; factory.py and labrun.py take their agents from
        them (the FR-* and LR-* tests are unchanged)."""
        soft, res = profiles.get("software"), profiles.get("research")
        self.assertEqual([p["kind"] for p in soft["loop"]["phases"]], ["plan", "gate", "build", "verify", "record"])
        self.assertEqual([p["kind"] for p in res["loop"]["phases"]],
                         ["plan", "consult", "consolidate", "gate", "build", "verify", "interpret", "gate", "record"])
        gate_ids = [g for p in res["loop"]["phases"] if p["kind"] == "gate" for g in p["ids"]]
        self.assertEqual(gate_ids, ["lab-run.pi-questions", "lab-run.preregistration", "lab-run.consignation"])
        self.assertEqual((loop.machine_of(soft), loop.machine_of(res)), ("linear", "lab"))
        self.assertEqual((factory.PLANNER, factory.APPROVED_LINE), ("factory-planner", "Status: APPROVED"))
        self.assertEqual((labrun.DIRECTOR, labrun.EXPERTS), ("lab-director", {"lab-neuro": "neuro", "lab-math": "math"}))
        self.assertEqual(labrun.role_of("lab-director", "interpretation"), "lab.director.interpretation")

    def test_lp02_a_profile_declaring_an_unknown_phase_kind_is_refused(self):
        """LP-02, CA-14: a profile with a `deploy` phase -> `profile validate` exit 2."""
        if not helpers.HAS_FIXTURES:
            self.skipTest(helpers.SKIP_FIXTURES)
        res = helpers.harness(["profile", "validate", str(helpers.FIXTURES / "profile-invalid" / "unknown-phase")],
                              helpers.CORE)
        self.assertEqual(res.returncode, 2)
        self.assertIn("unknown phase kind 'deploy'", res.stderr)
        self.assertEqual(set(profiles.PHASE_KINDS), {"plan", "consult", "consolidate", "gate", "build", "verify",
                                                     "interpret", "record"})


class RoutingTest(helpers.TempDirCase):
    def setUp(self):
        super().setUp()
        (self.tmp / "specs").mkdir()
        (self.tmp / "specs" / "SPEC-a.md").write_text("Statut : APPROUVEE\n\n# A\n", encoding="utf-8")
        (self.tmp / "specs" / "SPEC-p.md").write_text("Statut : PROPOSEE\n\n# P\n", encoding="utf-8")
        (self.tmp / "experiments").mkdir()
        (self.tmp / "experiments" / "EXP-a.md").write_text("Statut : PRE-ENREGISTRE\n", encoding="utf-8")

    def stamp(self, names):
        res = h(self.tmp, "stamp", "add", "--root", str(self.tmp), "--profiles", names, "--by", "forced")
        self.assertEqual(res.returncode, 0, res.stderr)

    def start(self, text, *extra):
        src = helpers.write_output(self.tmp, "factory-run-arguments.txt", text)
        return h(self.tmp, "factory-run", "start", "--root", str(self.tmp), "--arguments-file", src, *extra)

    def test_lp05_ca15_the_input_is_routed_to_the_owner_profile(self):
        """LP-05, CA-15: a ready artefact -> its profile in build mode; `suivant` -> research/plan; free
        text with one active profile -> that profile; two active -> gate factory-run.profile with the
        first profile of the stamp as default."""
        self.stamp("software;research")
        out = json.loads(self.start("specs/SPEC-a.md").stdout)
        self.assertEqual((out["profile"], out["mode"]), ("software", "build"))
        out = json.loads(self.start("experiments/EXP-a.md").stdout)
        self.assertEqual((out["profile"], out["mode"]), ("research", "build"))
        out = json.loads(self.start("suivant").stdout)
        self.assertEqual((out["profile"], out["mode"]), ("research", "plan"))
        # BQ-07, E-P4-12: a proposed spec is routed to its profile and goes to approve.
        out = json.loads(self.start("specs/SPEC-p.md").stdout)
        self.assertEqual((out["profile"], out["mode"]), ("software", "approve"))
        out = json.loads(self.start("un besoin libre à arbitrer").stdout)
        self.assertEqual((out["profile"], out["gate"], out["default"]), (None, "factory-run.profile", "software"))
        out = json.loads(self.start("--profile research un besoin libre").stdout)
        self.assertEqual((out["profile"], out["need"]), ("research", "un besoin libre"))

    def test_lp05_one_active_profile_takes_free_text_and_refuses_the_inactive_one(self):
        """LP-05, CA-15: free text with a single active profile; --profile of an inactive profile ->
        exit 1 (`profile <p> is not active`); an unknown one -> exit 2."""
        self.stamp("software")
        self.assertEqual(json.loads(self.start("ajouter un bouton").stdout)["profile"], "software")
        res = self.start("x", "--profile", "research")
        self.assertEqual(res.returncode, 1)
        self.assertIn("profile research is not active", res.stderr)
        self.assertEqual(self.start("x", "--profile", "zzz").returncode, 2)

    def test_lp06_lp07_the_second_instance_forces_research_and_names_the_journal(self):
        """LP-06, LP-07, CA-15, CA-24: `--orchestrator lab-run` is the research instance of the template:
        journal lab-run-*, profile research imposed; every recorded line carries its profile."""
        self.stamp("software;research")
        res = self.start("une question", "--orchestrator", "lab-run")
        out = json.loads(res.stdout)
        self.assertEqual((out["profile"], out["mode"], out["orchestrator"]), ("research", "plan", "lab-run"))
        self.assertRegex(out["run"], r"lab-run-\d{8}-\d{6}\.jsonl$")
        brief = helpers.write_output(self.tmp, "director.md", "# cadrage\n")
        rec = h(self.tmp, "factory-run", "record", "--run", out["run"], "--agent", "lab-director", "--mode", "cadrage",
                "--output", brief, "--duration-ms", "5", "--tokens", "9")
        self.assertEqual(rec.returncode, 0, rec.stderr)
        line = [json.loads(x) for x in (self.tmp / out["run"]).read_text(encoding="utf-8").splitlines()
                if "event" not in json.loads(x)][0]
        self.assertEqual((line["agent"], line["profile"]), ("lab-director", "research"))
        soft = json.loads(self.start("specs/SPEC-a.md").stdout)
        self.assertRegex(soft["run"], r"factory-run-\d{8}-\d{6}\.jsonl$")
        rec = h(self.tmp, "factory-run", "record", "--run", soft["run"], "--agent", "factory-builder",
                "--duration-ms", "5", "--tokens", "9")
        self.assertEqual(rec.returncode, 0, rec.stderr)
        line = [json.loads(x) for x in (self.tmp / soft["run"]).read_text(encoding="utf-8").splitlines()
                if "event" not in json.loads(x)][0]
        self.assertEqual(line["profile"], "software")
        self.assertEqual(h(self.tmp, "journal", "validate", str(self.tmp / soft["run"])).returncode, 0)


if __name__ == "__main__":
    unittest.main()
