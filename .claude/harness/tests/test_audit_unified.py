# /audit: profiles, lens selection, lens properties, single AUDIT.md (AD-01 to AD-12, LN-01, LN-02,
# SO-01 to SO-05) and the non-regression against the three old audits (NR-01 to NR-04).

import json
import re
import unittest
from pathlib import Path

import helpers
import test_audit
from hcore import audit, findings, gates, models, profiles, unified

SKILLS = helpers.CORE.parent
GAPS = helpers.FIXTURES / "audit-nonreg" / "expected-gaps.json"
SPEC = helpers.CORE.parent.parent / "specs" / "SPEC-phase2-profils-audit.md"


def frontmatter(path):
    text = Path(path).read_text(encoding="utf-8")
    assert text.startswith("---\n"), path
    return text.split("\n---\n", 1)[0].split("\n")[1:]


def plan(root, *args):
    return helpers.harness(["audit", "plan", "--root", str(root)] + list(args), root)


class SelectionTest(helpers.TempDirCase):
    def stamp(self, names):
        res = helpers.harness(["stamp", "add", "--root", str(self.tmp), "--profiles", names, "--by", "forced"], self.tmp)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_ad01_ca26_the_audit_skill_is_invocable_by_the_aliases(self):
        """AD-01, CA-26: name audit, context fork, user-invocable, and no disable-model-invocation (the
        aliases invoke it through the Skill tool); software-factory neither."""
        fm = frontmatter(SKILLS / "audit" / "SKILL.md")
        self.assertEqual(fm[0], "name: audit")
        self.assertIn("context: fork", fm)
        self.assertTrue(next(l for l in fm if l.startswith("description:")).split(":", 1)[1].strip())
        for name in ("audit", "software-factory"):
            self.assertFalse([l for l in frontmatter(SKILLS / name / "SKILL.md")
                              if l.startswith("disable-model-invocation")], name)
        for name in ("legacy-audit", "vibecode-audit", "ai-lab-audit", "ai-lab"):
            fm = frontmatter(SKILLS / name / "SKILL.md")
            self.assertEqual(fm[0], "name: %s" % name)
            self.assertNotIn("context: fork", fm)

    def test_ad02_ad03_ca18_lens_selection(self):
        """AD-02, AD-03, CA-18: stamp {software} -> 9 lenses; --profile wins over the stamp; the axis
        `securite` expands; --lenses outside the active profiles or unknown -> exit 2."""
        self.stamp("software")
        out = json.loads(plan(self.tmp).stdout)
        self.assertEqual(len(out["lenses"]), 9)
        out = json.loads(plan(self.tmp, "--profile", "research").stdout)
        self.assertEqual(out["lenses"], ["fidelite", "validite", "fuites", "bio", "route"])
        out = json.loads(plan(self.tmp, "--profile", "software;research", "--lenses", "securite;fuites").stdout)
        self.assertEqual(out["lenses"], ["acces", "injection", "metier", "fuites"])
        self.assertEqual(plan(self.tmp, "--profile", "research", "--lenses", "hygiene").returncode, 2)
        self.assertEqual(plan(self.tmp, "--profile", "research", "--lenses", "nope").returncode, 2)

    def test_ad04_waves_of_five_one_lens_per_auditor(self):
        """AD-04, CA-18, E-A10: 14 lenses go in waves of 5, 5 and 4, in the order profile -> set -> lens."""
        out = json.loads(plan(self.tmp, "--profile", "research;software").stdout)
        self.assertEqual([len(w) for w in out["waves"]], [5, 5, 4])
        self.assertEqual(out["waves"][0], ["fidelite", "validite", "fuites", "bio", "route"])
        flat = [l for w in out["waves"] for l in w]
        self.assertEqual(len(flat), len(set(flat)))

    def test_ad09_caps_per_set_and_an_option_of_an_inactive_set_is_an_error(self):
        """AD-09, CA-18: --max-specs min(N, 25) default 15 (legacy), --max-findings 30/15 (vibecode),
        --max-questions 12/6 (research); the option of a set that is not active -> exit 2."""
        out = json.loads(plan(self.tmp, "--profile", "software", "--max-specs", "99", "--max-findings", "3").stdout)
        self.assertEqual((out["caps"]["--max-specs"]["cap"], out["caps"]["--max-findings"]["cap"]), (25, 3))
        out = json.loads(plan(self.tmp, "--profile", "research").stdout)
        self.assertEqual(out["caps"]["--max-questions"]["cap"], 6)
        self.assertEqual(plan(self.tmp, "--profile", "software", "--max-questions", "3").returncode, 2)
        self.assertEqual(plan(self.tmp, "--profile", "research", "--max-questions", "0").returncode, 2)


class LensPropertiesTest(unittest.TestCase):
    def lenses(self):
        return {l["name"]: l for p in profiles.discover().values() for l in p["lenses"]}

    def test_ln01_the_six_lens_properties_match_the_engine(self):
        """LN-01: debt, shape, ordering, unauditable, no_run/no_protocol/allowed_exec and journal_check
        carry the ROADMAP 2.3 properties, and the engine of the lens set follows them."""
        lenses = self.lenses()
        self.assertEqual({l["debt"] for n, l in lenses.items() if n in ("hygiene", "portabilite", "qualite", "filet")},
                         {"accumulation"})
        self.assertEqual({l["debt"] for n, l in lenses.items() if l["shape"] == "pattern"}, {"generation"})
        self.assertEqual({n for n, l in lenses.items() if l["shape"] == "pattern"},
                         {"acces", "injection", "metier", "maintenabilite", "evolutivite"})
        self.assertEqual({l["ordering"] for n, l in lenses.items() if l["shape"] == "pattern"}, {"critical_first"})
        self.assertEqual(lenses["hygiene"]["ordering"], "severity_size")
        self.assertEqual(lenses["fidelite"]["ordering"], "severity_cost")
        # unauditable: finding for the vibecode set (engine: surface), mark for the other two (not audited).
        self.assertEqual({l["unauditable"] for n, l in lenses.items() if l["shape"] == "pattern"}, {"finding"})
        self.assertEqual(audit.PROFILES["vibecode-audit"].dead, audit.SURFACE)
        self.assertEqual(audit.PROFILES["legacy-audit"].dead, audit.NOT_AUDITED)
        self.assertEqual(audit.PROFILES["ai-lab-audit"].dead, audit.NOT_AUDITED)
        # no run, no protocol; only `fidelite` may execute (test-cpu).
        research = [l for l in lenses.values() if l["set"] == "research"]
        self.assertTrue(all(l["no_run"] and l["no_protocol"] for l in research))
        self.assertEqual({l["name"]: l["allowed_exec"] for l in research if l["allowed_exec"]}, {"fidelite": ["test-cpu"]})
        self.assertEqual({n for n, l in lenses.items() if l["journal_check"]}, {"validite", "route", "bio"})

    def test_ln02_the_other_properties_feed_the_engine(self):
        """LN-02: prefix, unit (axis), model role, dedup key and the validator fields are read from the
        profile: the engine's auditors, roles and keys are exactly the lens data."""
        legacy, vibe, lab = (audit.PROFILES[s] for s in ("legacy-audit", "vibecode-audit", "ai-lab-audit"))
        self.assertEqual([(a.name, a.unit, a.prefix) for a in vibe.auditors],
                         [("acces", "securite", "ACC"), ("injection", "securite", "INJ"), ("metier", "securite", "MET"),
                          ("maintenabilite", "maintenabilite", "MNT"), ("evolutivite", "evolutivite", "EVO")])
        self.assertEqual(legacy.units, ("hygiene", "portabilite", "qualite", "filet"))
        self.assertEqual(lab.auditor_roles["bio"], "lab.neuro")
        self.assertEqual(lab.auditor_roles["route"], "lab.director.cadrage")
        self.assertEqual(audit.subagent_type(lab, "fidelite", True), "lab-math")
        self.assertEqual(audit.subagent_type(lab, "fidelite", False), "Explore")
        lenses = self.lenses()
        self.assertEqual({lenses[a.name]["dedup_key"] for a in vibe.auditors}, {vibe.dedup})
        self.assertEqual({lenses[a.name]["dedup_key"] for a in lab.auditors}, {lab.dedup})
        self.assertEqual(lenses["acces"]["receives"], ["trust_map"])
        self.assertEqual(lenses["maintenabilite"]["receives"], ["reimpl_pairs"])
        self.assertEqual(lenses["validite"]["nature"], ["correction", "question"])
        self.assertEqual(lenses["validite"]["size_scale"], {"correction": "dev", "question": "gpu"})

    def test_ad06_ad07_ad08_ad10_ad11_ad12_engine_rules_follow_the_profile_data(self):
        """AD-06, AD-07, AD-08, AD-10, AD-11, AD-12: validators of research use lab.validator, the others
        audit.validator; the gates of each set exist with a headless fallback; the writes are AUDIT.md, the
        SPEC-leg/lab specs and .harness/runs/; the journal name is audit-*."""
        self.assertEqual(audit.PROFILES["ai-lab-audit"].validator_role, "lab.validator")
        self.assertEqual(audit.PROFILES["legacy-audit"].validator_role, "audit.validator")
        for prof in profiles.discover().values():
            for lset in prof["lens_sets"]:
                for gid in lset["gates"]:
                    gate = gates.get(gid)
                    self.assertIn(gate.headless, gates.HEADLESS, gid)
        self.assertEqual(gates.get("audit.software.assignment").repeat, 4)
        self.assertEqual(gates.get("audit.final").options[1], "Readjust")
        self.assertEqual(audit.allowed_writes("legacy-audit")[1], "specs/SPEC-leg-<nn>-<slug>.md")
        self.assertEqual(audit.allowed_writes("ai-lab-audit")[1], "specs/SPEC-lab-<nn>-<slug>.md")


@unittest.skipUnless(helpers.HAS_FIXTURES, helpers.SKIP_FIXTURES)
class UnifiedReplayTest(helpers.TempDirCase):
    def play(self, skill):
        fx = json.loads((test_audit.AUDIT_FIXTURES / ("%s.json" % skill)).read_text(encoding="utf-8"))
        return fx, test_audit.replay(self, skill, self.tmp, unified=True)

    def check(self, skill):
        fx, out = self.play(skill)
        repo = out["repo"]
        text = out["deliverable"]
        self.assertTrue(text.startswith("# Project audit\n\nGenerated by /audit on 2026-10-17. Profiles: p."))
        self.assertIn(unified.SET_START % skill, text)
        old = (test_audit.AUDIT_FIXTURES / ("%s.expected.md" % skill)).read_text(encoding="utf-8")
        block = unified.slice_of(text, skill)
        self.assertEqual(block, unified.strip_set_block(skill, old))
        for name in ("ROADMAP-legacy.md", "AUDIT-vibecode.md", "AUDIT-lab.md"):
            self.assertFalse((repo / name).exists(), name)
        self.assertEqual(findings.check_forbidden(text), [])
        porcelain = helpers.git(repo, "status", "--porcelain", "--untracked-files=all") if fx["git"] else ""
        for line in porcelain.splitlines():
            self.assertRegex(line[3:], r"^(AUDIT\.md|specs/|\.harness/runs/)", line)
        return out

    def test_so01_so05_nr01_nr02_the_software_sets_are_found_in_one_audit_md(self):
        """SO-01, SO-02, SO-04, SO-05, NR-01, NR-02, CA-20: /audit finds in AUDIT.md exactly the content of
        the legacy-audit and vibecode-audit replays (their deliverable minus head and Next), writes no old
        deliverable and nothing outside AUDIT.md, specs and .harness/runs/."""
        self.check("legacy-audit")

    def test_so01_nr02_the_vibecode_set(self):
        """SO-01, SO-02, NR-02: the vibecode replay keeps its attack table and its detailed patterns."""
        out = self.check("vibecode-audit")
        self.assertIn("## Where to attack", out["deliverable"])

    def test_so03_nr02_ca20_the_research_set_launches_through_factory_run(self):
        """SO-03, NR-02, E-A3, CA-20: ai-lab-audit's replay is found, with the Run line rewritten to
        `/factory-run --profile research "` and no `/lab-run` left."""
        out = self.check("ai-lab-audit")
        self.assertIn('**Run**: `/factory-run --profile research "', out["deliverable"])
        self.assertNotIn("/lab-run", out["deliverable"])

    def test_so01_the_blocks_follow_the_order_of_the_runs_and_a_foreign_block_is_kept(self):
        """SO-01, MG-05: assemble puts the blocks in the order given, keeps the block of a set this run did
        not audit, and refuses a forbidden status string (SO-04, FD-11) without writing."""
        repo = self.tmp / "r"
        repo.mkdir()
        unified.assemble(repo, [("legacy-audit", "## A\n"), ("ai-lab-audit", "## B\n")], "2026-10-31",
                         ["software", "research"], ["hygiene", "fidelite"], False, [])
        first = (repo / "AUDIT.md").read_text(encoding="utf-8")
        self.assertLess(first.index("harness:set legacy-audit"), first.index("harness:set ai-lab-audit"))
        unified.assemble(repo, [("legacy-audit", "## A2\n")], "2026-10-31", ["software"], ["hygiene"], True, [])
        second = (repo / "AUDIT.md").read_text(encoding="utf-8")
        self.assertIn("## B", second)
        self.assertIn("## A2", second)
        self.assertIn("sampled audit", second)
        with self.assertRaises(audit.AuditStop):
            unified.assemble(repo, [("legacy-audit", "Statut : APPROUVEE\n")], "2026-10-31", ["software"], ["x"],
                             False, [])
        self.assertEqual((repo / "AUDIT.md").read_text(encoding="utf-8"), second)

    def test_ad07_e_a4_a_defect_shared_by_two_sets_is_listed_once(self):
        """AD-07, E-A4: legacy-audit and vibecode-audit report the same RG-02 key in one /audit cycle: the
        second set drops it as a duplicate (no validation) and AUDIT.md lists it once."""
        title = "Aucun .gitignore"
        out = test_audit.replay(self, "legacy-audit", self.tmp, unified=True, cycle=True)
        repo, h = out["repo"], out["h"]
        fx = json.loads((test_audit.AUDIT_FIXTURES / "vibecode-audit.json").read_text(encoding="utf-8"))
        args = helpers.write_output(repo, "vib-arguments.txt", fx["arguments"])
        start = test_audit.check_ok(self, h("audit", "start", "--skill", "vibecode-audit", "--arguments-file", args,
                                            "--unified"))
        item = test_audit.vib_item("MET-01", pattern=title, places=(("api/a.js", 1), ("api/b.js", 2)))
        text = helpers.write_output(repo, "vib-metier.md", test_audit.block({"findings": [item]}))
        decision = test_audit.check_ok(self, h("audit", "auditor", "--run", start["run"], "--unit", "metier",
                                               "--duration-ms", "10", "--tokens", "100", "--output", text))
        self.assertEqual(decision["to_validate"], [])
        self.assertEqual(len(decision["duplicates"]), 1)
        self.assertIn("in this audit cycle", decision["duplicates"][0]["reason"])
        test_audit.check_ok(self, h("audit", "next", "--run", start["run"]))
        test_audit.check_ok(self, h("audit", "consolidate", "--run", start["run"]))
        data = helpers.write_output(repo, "vib-data.json", json.dumps(fx["data"], ensure_ascii=False))
        test_audit.check_ok(self, h("audit", "render", "--run", start["run"], "--data-file", data))
        test_audit.check_ok(self, h("audit", "assemble", "--set-run", start["run"], "--profiles", "p", "--lenses", "l",
                                    "--date", "2026-10-17"))
        self.assertEqual((repo / "AUDIT.md").read_text(encoding="utf-8").count(title), 1)

    def test_nr03_nr04_the_gaps_are_exactly_the_expected_list(self):
        """NR-03, NR-04, NR-05, CA-22: (NR-05, the gaps of a real LLM run, is the user recipe R9) the expected-gaps.json ids are exactly the E-A*/E-I* ids of the spec, each
        with a justification."""
        gaps = json.loads(GAPS.read_text(encoding="utf-8"))["gaps"]
        ids = [g["id"] for g in gaps]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(g["finding"].strip() and g["why"].strip() for g in gaps))
        if SPEC.is_file():
            spec_ids = set(re.findall(r"\bE-[AI][0-9]+\b", SPEC.read_text(encoding="utf-8")))
            self.assertEqual(set(ids), spec_ids)


if __name__ == "__main__":
    unittest.main()
