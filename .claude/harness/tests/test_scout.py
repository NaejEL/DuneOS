# Profile scout (SC-01 to SC-08), arbitration of the profile gates (AR-01 to AR-04), model of the scout.

import json
import unittest
from pathlib import Path

import helpers
import phase2
from hcore import gates, models, profiles, scout


def h(root, *args):
    return helpers.harness(list(args), root)


def evidence(file, start, end, excerpt):
    return {"file": file, "lines": {"start": start, "end": end}, "excerpt": excerpt}


@unittest.skipUnless(helpers.HAS_FIXTURES, helpers.SKIP_FIXTURES)
class VerdictTest(helpers.TempDirCase):
    def research_agent(self):
        return {"signals": [
            {"profile": "research", "criterion": "scientific-corpus", "found": True,
             "evidence": evidence("docs/JOURNAL.md", 3, 3, "## 2026-09-01 — Baseline")},
            {"profile": "research", "criterion": "gpu-declared", "found": True,
             "evidence": evidence("CLAUDE.md", 9, 9, "PoC de mémoire associative, GPU requis (CUDA).")}],
            "uncertainties": []}

    def names(self, result, status):
        return [p["name"] for p in result["profiles"] if p["status"] == status]

    def test_sc01_sc02_deterministic_signals_have_proofs_and_skip_artifact_folders(self):
        """SC-01, SC-02: glob, dir, dependency and content criteria are evaluated with proofs
        {file, line, excerpt}; artifact folders are excluded (ST-03)."""
        root = phase2.project(self.tmp / "p", "profile-research")
        (root / "node_modules" / "x").mkdir(parents=True)
        (root / "node_modules" / "x" / "package.json").write_text("{}", encoding="utf-8")
        found = scout.signals(root)["profiles"]
        by_id = {c["id"]: c for c in found["research"]["criteria"]}
        self.assertTrue(by_id["ml-dependency"]["found"])
        self.assertEqual(by_id["ml-dependency"]["proofs"][0]["file"], "requirements.txt")
        self.assertEqual(by_id["dated-journal"]["proofs"][0]["line"], 3)
        software = {c["id"]: c for c in found["software"]["criteria"]}
        self.assertFalse(software["manifest-node"]["found"])
        res = h(root, "scout", "signals", "--root", str(root))
        self.assertEqual(sorted(json.loads(res.stdout)["profiles"]), ["research", "software"])

    def test_sc09_test_data_folders_are_not_signals_of_the_project(self):
        """SC-09: a dated journal or a manifest under fixtures/, __fixtures__/ or testdata/ proves nothing."""
        root = self.tmp / "harness-like"
        for folder in ("tests/fixtures/mixed/docs", "ci/fixtures/toy/docs", "pkg/testdata/docs", "src/__fixtures__/docs"):
            (root / folder).mkdir(parents=True)
            (root / folder / "JOURNAL.md").write_text("# Journal\n\n## 2026-09-01 — Baseline\n", encoding="utf-8")
            (root / folder.rsplit("/", 1)[0] / "pyproject.toml").write_text("[project]\nname = \"toy\"\n", encoding="utf-8")
        found = scout.signals(root)["profiles"]
        research = {c["id"]: c for c in found["research"]["criteria"]}
        software = {c["id"]: c for c in found["software"]["criteria"]}
        self.assertFalse(research["dated-journal"]["found"], research["dated-journal"]["proofs"])
        self.assertFalse(software["manifest-python-package"]["found"], software["manifest-python-package"]["proofs"])
        (root / "docs").mkdir()
        (root / "docs" / "JOURNAL.md").write_text("## 2026-09-01 — Baseline\n", encoding="utf-8")
        found = scout.signals(root)["profiles"]
        proofs = {c["id"]: c for c in found["research"]["criteria"]}["dated-journal"]["proofs"]
        self.assertEqual([p["file"] for p in proofs], ["docs/JOURNAL.md"])

    def test_sc03_sc05_the_agent_only_receives_judgement_criteria(self):
        """SC-03: the criteria handed to the Explore agent are the judgement ones, with a French question."""
        got = scout.judgement_criteria()
        self.assertEqual({(j["profile"], j["criterion"]) for j in got},
                         {("research", "scientific-corpus"), ("research", "gpu-declared"),
                          ("software", "software-package")})
        self.assertTrue(all(j["question"] for j in got))

    def test_sc05_ca08_verdicts_on_the_fixture_projects(self):
        """SC-05, SC-07, CA-08: profile-software -> software retained, research rejected, clear;
        profile-research -> research retained, software rejected; profile-mixed -> both; an empty
        project -> the fallback profile (by fallback, clear); a calibrated project -> uncertain."""
        soft = phase2.project(self.tmp / "s", "profile-software")
        v = scout.verdict(soft)
        self.assertEqual((self.names(v, "retained"), self.names(v, "rejected"), v["clear"]),
                         (["software"], ["research"], True))
        res = phase2.project(self.tmp / "r", "profile-research")
        v = scout.verdict(res, self.research_agent())
        self.assertEqual((self.names(v, "retained"), self.names(v, "rejected"), v["proposal"], v["by"]),
                         (["research"], ["software"], ["research"], "scout"))
        mixed = phase2.project(self.tmp / "m", "profile-mixed")
        v = scout.verdict(mixed, self.research_agent())
        self.assertEqual((self.names(v, "retained"), v["clear"]), (["research", "software"], True))
        empty = self.tmp / "e"
        empty.mkdir()
        v = scout.verdict(empty)
        self.assertEqual((v["proposal"], v["by"], v["clear"]), (["software"], "fallback", True))
        grey = self.tmp / "g"
        (grey / "src").mkdir(parents=True)
        (grey / "test").mkdir()
        v = scout.verdict(grey)
        self.assertEqual((self.names(v, "uncertain"), v["clear"]), (["software"], False))

    def test_sc05_ca09_an_unverifiable_proof_is_rejected_and_does_not_count(self):
        """SC-05, CA-09: a judgement signal citing an absent file, lines outside the file or an excerpt not
        at those lines is listed in rejected_signals and adds nothing to the score."""
        root = phase2.project(self.tmp / "r", "profile-research")
        base = scout.verdict(root)
        for bad in (evidence("docs/NOPE.md", 1, 1, "x"), evidence("docs/JOURNAL.md", 90, 91, "x"),
                    evidence("docs/JOURNAL.md", 3, 3, "this text is not in the file")):
            agent = {"signals": [{"profile": "research", "criterion": "scientific-corpus", "found": True,
                                  "evidence": bad}], "uncertainties": []}
            v = scout.verdict(root, agent)
            self.assertEqual(len(v["rejected_signals"]), 1, bad)
            self.assertEqual([p["score"] for p in v["profiles"]], [p["score"] for p in base["profiles"]])
        good = scout.verdict(root, self.research_agent())
        self.assertEqual(good["rejected_signals"], [])
        self.assertGreater(good["profiles"][0]["score"], base["profiles"][0]["score"])

    def test_sc06_a_scout_dead_twice_leaves_the_verdict_unclear(self):
        """SC-06, CA-09: a silent scout is not an agreeing scout: clear false and `scout non recueilli`."""
        root = phase2.project(self.tmp / "r", "profile-research")
        v = scout.verdict(root, None, scout_failed=True)
        self.assertFalse(v["clear"])
        self.assertIn("scout non recueilli", v["uncertainties"])

    def test_sc08_the_verdict_is_written_under_runs_and_journaled(self):
        """SC-04, SC-08, CA-24: the CLI writes .harness/runs/scout-*.json, journals `harness-scout`
        with the haiku model of the role and a valid line."""
        root = phase2.project(self.tmp / "r", "profile-research")
        src = phase2.signals_for(root, self.research_agent())
        res = h(root, "scout", "verdict", "--root", str(root), "--signals", src)
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout)
        self.assertTrue((root / out["verdict_file"]).is_file())
        self.assertRegex(out["verdict_file"], r"^\.harness/runs/scout-\d{8}-\d{6}\.json$")
        self.assertFalse((root / src).exists())
        journals = sorted((root / ".harness" / "runs").glob("software-factory-*.jsonl"))
        line = json.loads(journals[0].read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual((line["agent"], line["model"]), ("harness-scout", "haiku"))
        self.assertEqual(h(root, "journal", "validate", str(journals[0])).returncode, 0)

    def test_sc04_ca25_the_scout_model_defaults_to_haiku(self):
        """SC-04, CA-25: models.json absent -> harness.scout is haiku with pass_param; --models inherit
        replaces it; the other roles stay inherit (MD-02)."""
        res = models.resolve_root(self.tmp, ("harness.scout", "factory.planner"))
        self.assertEqual(res["roles"]["harness.scout"], {"model": "haiku", "pass_param": True})
        self.assertEqual(res["roles"]["factory.planner"], {"model": "inherit", "pass_param": False})
        res = models.resolve_root(self.tmp, ("harness.scout",), "harness.scout=inherit")
        self.assertEqual(res["roles"]["harness.scout"]["pass_param"], False)
        out = json.loads(h(self.tmp, "models", "resolve", "--root", str(self.tmp), "--roles", "harness.scout").stdout)
        self.assertEqual(out["roles"]["harness.scout"]["model"], "haiku")


class ArbitrationTest(helpers.TempDirCase):
    def decide(self, gid, policy=None, *extra):
        if policy:
            (self.tmp / ".claude").mkdir(exist_ok=True)
            (self.tmp / ".claude" / "harness.json").write_text(json.dumps({"autonomy": policy}), encoding="utf-8")
        return h(self.tmp, "gate", "decide", "--root", str(self.tmp), "--id", gid, *extra)

    def test_ar01_the_profile_gate_is_a_kind_b_with_a_default_and_a_headless_fallback(self):
        """AR-01, CA-23: software-factory.profiles is kind b, default = the proposal, headless fallback
        default; with autonomy absent the decision is ask."""
        gate = gates.get("software-factory.profiles")
        self.assertEqual((gate.kind, gate.headless, gate.proposal), ("b", "default", True))
        res = self.decide("software-factory.profiles")
        self.assertEqual(json.loads(res.stdout)["decision"], "ask")

    def test_ar02_auto_with_a_clear_proposal_takes_the_default_logged_twice(self):
        """AR-02, CA-10: autonomy auto and a clear proposal -> `default`, then a `default` answer; two gate
        events in the journal, the second with source default."""
        res = self.decide("software-factory.profiles", "auto")
        out = json.loads(res.stdout)
        self.assertEqual((out["decision"], out["policy"]), ("default", "auto"))
        ans = h(self.tmp, "gate", "answer", "--root", str(self.tmp), "--run", out["run"], "--id",
                "software-factory.profiles", "--default")
        self.assertEqual(ans.returncode, 0, ans.stderr)
        events = [json.loads(l) for l in (self.tmp / out["run"]).read_text(encoding="utf-8").splitlines()]
        events = [e for e in events if e.get("event") == "gate"]
        self.assertEqual([(e["phase"], e["source"]) for e in events], [("decision", None), ("answer", "default")])

    def test_ar02_auto_with_an_ambiguous_proposal_stops_with_the_resume_command(self):
        """AR-02, GT-05, CA-10: ambiguous under auto -> exit 1 naming the verdict file and --profile; a
        headless fallback on an ambiguous decision is a stop as well."""
        res = self.decide("software-factory.profiles", "auto", "--ambiguous", "--verdict",
                          ".harness/runs/scout-20261031-120000.json")
        self.assertEqual(res.returncode, 1)
        self.assertIn("scout-20261031-120000.json", res.stderr)
        self.assertIn("--profile", res.stderr)
        res = self.decide("software-factory.profiles", "gate", "--ambiguous")
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout)
        ans = h(self.tmp, "gate", "answer", "--root", str(self.tmp), "--run", out["run"], "--id",
                "software-factory.profiles", "--headless")
        self.assertEqual(ans.returncode, 1, ans.stdout)
        self.assertIn("headless fallback: stop", ans.stderr)
        clear = json.loads(self.decide("software-factory.profiles").stdout)
        ans = h(self.tmp, "gate", "answer", "--root", str(self.tmp), "--run", clear["run"], "--id",
                "software-factory.profiles", "--headless")
        self.assertEqual((ans.returncode, json.loads(ans.stdout)["answer"]), (0, "proposal"))

    def test_ar03_ap02_the_audit_prioritisation_gates_take_their_default_under_auto(self):
        """AR-03 replaced by AP-02: the phase 1 guard is gone, an audit prioritisation gate under auto takes
        its default; only the scout proposal gates stop on an ambiguous proposal."""
        res = self.decide("audit.software.framing", "auto")
        out = json.loads(res.stdout)
        self.assertEqual((out["decision"], out["policy"]), ("default", "auto"))
        self.assertNotIn("warning", out)
        self.assertEqual(sorted(g.id for g in gates.all_gates().values() if g.proposal),
                         ["audit.profiles", "software-factory.profiles"])

    def test_ca23_the_phase2_gates_have_a_default_and_a_headless_fallback(self):
        """CA-23: the nine gates of Phase 2 are in the catalog (`gate list`) with a rationale and a fallback."""
        listed = {g["id"]: g for g in json.loads(h(self.tmp, "gate", "list").stdout)["gates"]}
        for gid in ("software-factory.profiles", "factory-run.profile", "audit.profiles", "audit.drop-security",
                    "audit.software.framing", "audit.software.assignment", "audit.research.framing",
                    "audit.research.selection", "audit.final"):
            self.assertIn(gid, listed)
            self.assertTrue(listed[gid]["rationale"].strip(), gid)
            self.assertIn(listed[gid]["headless"], gates.HEADLESS, gid)
        self.assertEqual(listed["audit.software.framing"]["headless"], "stop")
        self.assertEqual(listed["audit.software.order"]["headless"], "default")

    def test_ar04_ep01_forcing_a_profile_skips_the_scout_unknown_is_an_error(self):
        """AR-04, EP-01: `--profile` on the entry point is stamped by=forced; an unknown profile -> exit 2."""
        ok = h(self.tmp, "stamp", "add", "--root", str(self.tmp), "--profiles", "software;research", "--by", "forced")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual([p["by"] for p in json.loads((self.tmp / ".claude" / "harness.json")
                                                      .read_text(encoding="utf-8"))["profiles"]], ["forced", "forced"])
        self.assertEqual(h(self.tmp, "stamp", "add", "--root", str(self.tmp), "--profiles", "zzz", "--by",
                           "forced").returncode, 2)


if __name__ == "__main__":
    unittest.main()
