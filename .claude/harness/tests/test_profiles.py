# Profiles (PF-01 to PF-06) and the lossless extraction of the skills into them (PX-01 to PX-05).

import filecmp
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

import helpers
from hcore import findings, gates, journal, models, profiles

PROFILES = helpers.CORE / "profiles"
HCORE = helpers.CORE / "hcore"
SKILLS = helpers.CORE.parent
BASE2 = "b3c5bf09b066f02151112fc6f7f57586c165b20b"


def run_validate(path):
    return helpers.harness(["profile", "validate", str(path)], helpers.CORE)


class FormatTest(unittest.TestCase):
    def test_pf01_pf02_the_two_profiles_are_conformant_folders_without_code(self):
        """PF-01, PF-02, PX-01, PX-02: a profile is a folder with a strict JSON manifest and Markdown
        fragments, no .py file; software is the fallback and carries two lens sets, research one."""
        for name in ("software", "research"):
            folder = PROFILES / name
            data = json.loads((folder / "profile.json").read_text(encoding="utf-8"))
            self.assertEqual(data["name"], name)
            self.assertEqual(list(folder.rglob("*.py")), [])
            self.assertEqual(profiles.validate_manifest(data, folder), [])
            self.assertEqual(run_validate(folder).returncode, 0)
        soft, res = profiles.get("software"), profiles.get("research")
        self.assertEqual([s["name"] for s in soft["lens_sets"]], ["legacy", "vibecode"])
        self.assertEqual([l["name"] for l in soft["lenses"]],
                         ["hygiene", "portabilite", "qualite", "filet", "acces", "injection", "metier",
                          "maintenabilite", "evolutivite"])
        self.assertEqual([l["name"] for l in res["lenses"]], ["fidelite", "validite", "fuites", "bio", "route"])
        self.assertEqual([p for p in profiles.discover().values() if p["fallback"]], [soft])

    def test_pf03_each_invalid_fixture_is_refused_with_its_error(self):
        """PF-03: missing key, missing fragment, unknown phase kind, gate id outside the namespaces,
        lens already declared by another profile, two fallbacks, model role declared twice -> exit 2
        and the message names the error."""
        if not helpers.HAS_FIXTURES:
            self.skipTest(helpers.SKIP_FIXTURES)
        expected = {"missing-key": "missing required key: loop", "missing-fragment": "fragment not found",
                    "unknown-phase": "unknown phase kind 'deploy'", "gate-namespace": "outside the namespaces",
                    "duplicate-lens": "is declared by alpha and beta", "two-fallbacks": "two profiles are fallback",
                    "duplicate-model-role": "model role 'alpha.worker' is declared by"}
        cases = sorted(p.name for p in (helpers.FIXTURES / "profile-invalid").iterdir())
        self.assertEqual(cases, sorted(expected))
        for case, message in expected.items():
            res = run_validate(helpers.FIXTURES / "profile-invalid" / case)
            self.assertEqual(res.returncode, 2, (case, res.stdout, res.stderr))
            self.assertIn(message, res.stderr, case)

    def test_pf04_list_is_sorted_with_lenses_and_roles(self):
        """PF-04: `profile list` renders the profiles sorted by name with their lenses and roles."""
        res = helpers.harness(["profile", "list"], helpers.CORE)
        self.assertEqual(res.returncode, 0, res.stderr)
        listed = json.loads(res.stdout)["profiles"]
        self.assertEqual([p["name"] for p in listed], ["research", "software"])
        self.assertEqual(listed[0]["roles"][0], "lab-director")
        self.assertIn("evolutivite", listed[1]["lenses"])


class ClosedSetsTest(unittest.TestCase):
    def test_pf05_no_profile_name_in_the_core_code(self):
        """PF-05, CA-05: no name proper to a profile remains in hcore/*.py nor harness.py."""
        pattern = re.compile(r"factory-(planner|builder|verifier)|lab-(director|neuro|math|builder|verifier)|"
                             r"PRE-ENREGISTRE|APPROUVEE|hygiene|portabilite|fidelite|validite|STACK_DENY|FACTORY_TASKS")
        hits = [p.name for p in list(HCORE.glob("*.py")) + [helpers.CORE / "harness.py"]
                if pattern.search(p.read_text(encoding="utf-8"))]
        self.assertEqual(hits, [])

    def test_pf05_the_closed_sets_are_core_union_profiles(self):
        """PF-05: model roles, journal names, gates, forbidden status lines and deny rules include what the
        profiles declare."""
        for role in ("factory.planner", "lab.math", "harness.scout"):
            self.assertIn(role, models.ROLES)
        for name in ("factory-run", "lab-run", "audit"):
            self.assertIn(name, journal.SKILLS)
        self.assertIn("audit.software.assignment", gates.all_gates())
        self.assertIn("audit.research.selection", gates.all_gates())
        self.assertNotIn("audit.research.selection", gates.CATALOG)
        for line in ("Statut : APPROUVEE", "Statut : PRE-ENREGISTRE", "Statut : PRÉ-ENREGISTRÉE"):
            self.assertTrue(findings.check_forbidden(line + "\n"), line)
        self.assertEqual(findings.check_forbidden("Statut : PROPOSEE\n"), [])
        rules = profiles.deny_rules(profiles.get("software"), ["node"])
        self.assertEqual(rules, ["Read(./node_modules/**)", "Read(./dist/**)", "Read(./factory-logs/**)",
                                 "Read(./.git/objects/**)"])

    def test_pf06_ship_pr_brainstorm_code_index_are_not_profiles(self):
        """PF-06: ship-pr, brainstorm and code-index keep their gates and roles in the core catalog."""
        self.assertEqual(sorted(profiles.discover()), ["research", "software"])
        for gid in ("ship-pr.merge", "brainstorm.round", "code-index.hooks"):
            self.assertIn(gid, gates.CATALOG)
        self.assertIn("shippr.reviewer", models.ROLES)


@unittest.skipUnless(helpers.HAS_FIXTURES, helpers.SKIP_FIXTURES)
class AddProfileTest(helpers.TempDirCase):
    def test_ca06_adding_a_profile_is_adding_a_folder(self):
        """PF-05, CA-06: a copy of the core plus one folder under profiles/ sees the profile everywhere
        (list, scout signals, audit plan, factory-run start); no other file differs from the core."""
        core = self.tmp / "core"
        shutil.copytree(str(helpers.CORE), str(core), ignore=shutil.ignore_patterns("__pycache__", "tests"))
        shutil.copytree(str(helpers.FIXTURES / "profile-toy"), str(core / "profiles" / "profile-toy"))
        diff = filecmp.dircmp(str(helpers.CORE / "profiles"), str(core / "profiles"))
        self.assertEqual((diff.left_only, diff.diff_files), ([], []))
        self.assertEqual(diff.right_only, ["profile-toy"])
        h = lambda *a: subprocess.run([__import__("sys").executable, "-B", str(core / "harness.py")] + list(a),  # noqa: E731
                                      cwd=str(self.tmp), capture_output=True, text=True, encoding="utf-8")
        listed = json.loads(h("profile", "list").stdout)["profiles"]
        self.assertIn("profile-toy", [p["name"] for p in listed])
        project = self.tmp / "p"
        project.mkdir()
        (project / "toy.marker").write_text("x", encoding="utf-8")
        signals = json.loads(h("scout", "signals", "--root", str(project)).stdout)
        self.assertTrue(signals["profiles"]["profile-toy"]["criteria"][0]["found"])
        plan = json.loads(h("audit", "plan", "--root", str(project), "--profile", "profile-toy").stdout)
        self.assertEqual(plan["lenses"], ["profile-toy-lens"])
        self.assertEqual(h("stamp", "add", "--root", str(project), "--profiles", "profile-toy", "--by",
                           "forced").returncode, 0)
        start = h("factory-run", "start", "--root", str(project), "--profile", "profile-toy")
        self.assertEqual(json.loads(start.stdout)["profile"], "profile-toy", start.stderr)


@unittest.skipUnless(helpers.HAS_FIXTURES, helpers.SKIP_FIXTURES)
class ExtractionTest(unittest.TestCase):
    EXTRACTION = helpers.FIXTURES / "profile-extraction"

    @staticmethod
    def sentences(text):
        parts = []
        for chunk in re.split(r"\. |;", " ".join(text.split())):
            chunk = chunk.strip()
            if len(chunk) > 3:
                parts.append(chunk)
        return parts

    def test_px03_base2_copies_are_the_files_at_base2(self):
        """PX-03, CA-07: the SKILL.md copies under base2/ are the files at BASE2, byte for byte."""
        for path in sorted((self.EXTRACTION / "base2").glob("*.md")):
            shown = subprocess.run(["git", "show", "%s:skills/%s/SKILL.md" % (BASE2, path.stem)],
                                   cwd=str(SKILLS), capture_output=True)
            if shown.returncode != 0:
                self.skipTest("BASE2 not in this clone")
            self.assertEqual(shown.stdout, path.read_bytes(), path.name)

    def test_px03_the_extraction_inventory_is_well_formed(self):
        """PX-01, PX-02, PX-03: every skill of sections.json has its BASE2 copy and valid line ranges; every
        retired phrase names the ROADMAP rule that contradicts it. (The sentence-by-sentence comparison of the
        French BASE2 prose with the current files ended with the translation to English: the BASE2 copies are
        legacy fixtures and are not translated.)"""
        sections = json.loads((self.EXTRACTION / "sections.json").read_text(encoding="utf-8"))["sections"]
        retired = json.loads((self.EXTRACTION / "retired.json").read_text(encoding="utf-8"))["retired"]
        for entry in retired:
            self.assertTrue(entry["roadmap"].strip())
            self.assertTrue(entry["phrase"].strip())
        for skill, ranges in sections.items():
            lines = (self.EXTRACTION / "base2" / (skill + ".md")).read_text(encoding="utf-8").split("\n")
            for first, last in ranges:
                self.assertTrue(1 <= first <= last <= len(lines), (skill, first, last))
                self.assertTrue(self.sentences("\n".join(lines[first - 1:last])), (skill, first, last))

    def test_px04_data_moved_into_profiles_unchanged(self):
        """PX-04: STACK_DENY, GENERIC_DENY, GITIGNORE_LINES, FACTORY_INPUT/TASKS, the AL-01 to AL-03
        rules and the MD-08 presets are now profile data, with the Phase 1 values."""
        soft, res = profiles.get("software"), profiles.get("research")
        self.assertEqual(soft["settings"]["deny_by_stack"]["python"],
                         ["Read(./.venv/**)", "Read(./**/__pycache__/**)", "Read(./.pytest_cache/**)"])
        self.assertEqual(soft["gitignore"], ["factory-logs/", ".harness/runs/", ".harness/tmp/"])
        self.assertEqual([t["label"] for t in soft["tasks"]["tasks"]],
                         ["Factory: CI run", "Factory: CI run (YOLO)", "Factory: nouvelle spec (session)"])
        self.assertEqual(res["gitignore"], ["lab-logs/", "experiments/results/", ".harness/runs/", ".harness/tmp/"])
        self.assertEqual(res["model_presets"]["lab-experts"], {"lab.neuro": "fable", "lab.math": "fable"})
        self.assertEqual(models.PRESETS["lab-experts"]["lab.math"], "fable")

    def test_px05_the_audit_templates_stay_the_source_of_the_sections(self):
        """PX-05: the profile references the deliverable sections, the French templates stay in templates/."""
        self.assertEqual(profiles.get("software")["audit_sections"], ["roadmap", "attack", "detail"])
        self.assertTrue((helpers.CORE / "templates" / "audits.json").is_file())
        self.assertEqual(list(PROFILES.rglob("*.json")), [PROFILES / "research" / "profile.json",
                                                         PROFILES / "software" / "profile.json"])


if __name__ == "__main__":
    unittest.main()
