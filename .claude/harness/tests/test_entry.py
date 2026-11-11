# Single entry point /software-factory (EP-01 to EP-07) and the /ai-lab alias (AX-01 to AX-03).

import filecmp
import json
import shutil
import unittest

import helpers
import phase2
from hcore import profiles, scout, stamp


def h(root, *args):
    return helpers.harness(list(args), root)


class ArgumentsTest(helpers.TempDirCase):
    def args(self, text):
        src = helpers.write_output(self.tmp, "sf-arguments.txt", text)
        return h(self.tmp, "sf", "args", "--root", str(self.tmp), "--arguments-file", src)

    def test_ep01_options_and_first_need(self):
        """EP-01, AR-04: `--profile p[;p]` or `--rescan` then the first need, kept verbatim; both options
        together, or an unknown profile -> exit 2."""
        out = json.loads(self.args('--profile "software;research" un besoin "avec guillemets"').stdout)
        self.assertEqual(out, {"profiles": ["software", "research"], "rescan": False,
                               "need": 'un besoin "avec guillemets"'})
        out = json.loads(self.args("--rescan").stdout)
        self.assertEqual((out["rescan"], out["profiles"], out["need"]), (True, [], ""))
        self.assertEqual(json.loads(self.args("seulement un besoin").stdout)["need"], "seulement un besoin")
        self.assertEqual(self.args("--profile software --rescan").returncode, 2)
        self.assertEqual(self.args("--profile zzz").returncode, 2)
        self.assertFalse((self.tmp / ".harness" / "runs" / "sf-arguments.txt").exists())


@unittest.skipUnless(helpers.HAS_FIXTURES, helpers.SKIP_FIXTURES)
class RescanTest(helpers.TempDirCase):
    def test_ep02_ep03_ca12_rescan_proposes_only_additions_and_reports_vanished(self):
        """EP-02, EP-03, EP-05, CA-12: a project stamped software, then a research corpus appears: compare
        gives added [research]; applying the union keeps the earlier settings; the stamp gains research
        and loses nothing; with the corpus removed, the profile is `vanished` and still stamped."""
        root = phase2.project(self.tmp / "p", "profile-software")
        self.assertEqual(h(root, "sf", "apply", "--root", str(root), "--stacks", "node", "--profiles",
                           "software").returncode, 0)
        stamp.add(root, ["software"], "scout", "2026-10-31")
        settings = root / ".claude" / "settings.json"
        before = json.loads(settings.read_text(encoding="utf-8"))["permissions"]["deny"]
        self.assertEqual(stamp.resolve(root), {"source": "stamp", "profiles": ["software"]})
        phase2.project(root, "profile-research")
        verdict = scout.verdict(root)
        verdict_file = root / ".harness" / "runs" / "verdict.json"
        verdict_file.parent.mkdir(parents=True, exist_ok=True)
        verdict_file.write_text(json.dumps(verdict), encoding="utf-8")
        cmp = json.loads(h(root, "stamp", "compare", "--root", str(root), "--verdict", str(verdict_file)).stdout)
        self.assertEqual(cmp, {"added": ["research"], "kept": ["software"], "vanished": []})
        self.assertEqual(h(root, "sf", "apply", "--root", str(root), "--stacks", "node", "--profiles",
                           "software;research").returncode, 0)
        after = json.loads(settings.read_text(encoding="utf-8"))["permissions"]["deny"]
        self.assertEqual(after[:len(before)], before)
        self.assertIn("Read(./experiments/results/**)", after)
        stamp.add(root, ["research"], "scout", "2026-11-01")
        for rel in ("requirements.txt", "docs", "eval", "model", "CLAUDE.md"):
            target = root / rel
            shutil.rmtree(str(target)) if target.is_dir() else target.unlink()
        verdict_file.write_text(json.dumps(scout.verdict(root)), encoding="utf-8")
        cmp = json.loads(h(root, "stamp", "compare", "--root", str(root), "--verdict", str(verdict_file)).stdout)
        self.assertEqual((cmp["added"], cmp["vanished"]), ([], ["research"]))
        self.assertEqual(stamp.names(root), ["software", "research"])

    def test_ep04_the_profile_data_drives_the_generation(self):
        """EP-04: `profile show` hands the generation what the skill needs: roles with their mission,
        model role and tools, the orchestrator, install and scout fragments."""
        out = json.loads(h(self.tmp, "profile", "show", "research").stdout)
        self.assertEqual([r["name"] for r in out["roles"]],
                         ["lab-director", "lab-neuro", "lab-math", "lab-builder", "lab-verifier"])
        director = out["roles"][0]
        self.assertEqual((director["model_role"], director["read_only"]), ("lab.director.cadrage", True))
        self.assertIn("Research director", director["mission_text"])
        self.assertTrue(out["orchestrator_text"].strip() and out["install_text"].strip())
        self.assertEqual(out["ci"]["max_turns"], 150)
        self.assertEqual(json.loads(h(self.tmp, "profile", "show", "software").stdout)["ci"]["max_turns"], 100)

    def test_ca13_generation_in_a_single_profile_is_unchanged(self):
        """CA-13, PX-04: `sf apply --stacks node` on the merge fixture still equals merge-expected, and the
        `--profiles software` form gives the same files as the legacy form."""
        a, b = self.tmp / "a", self.tmp / "b"
        for root in (a, b):
            shutil.copytree(str(helpers.FIXTURES / "merge-input"), str(root))
            (root / ".claude").mkdir(exist_ok=True)
            shutil.move(str(root / "settings.json"), str(root / ".claude" / "settings.json")) \
                if (root / "settings.json").is_file() else None
        h(a, "sf", "apply", "--root", str(a), "--stacks", "node")
        h(b, "sf", "apply", "--root", str(b), "--stacks", "node", "--profiles", "software")
        for rel in (".claude/settings.json", ".gitignore"):
            self.assertEqual((a / rel).read_bytes(), (b / rel).read_bytes(), rel)

    def test_px04_the_research_profile_merges_like_the_lab_rules(self):
        """PX-04, CA-13: `sf apply --profiles research` writes the same settings, tasks and .gitignore as the
        AL-01 to AL-03 rules (`lab apply`) on a research project, byte for byte."""
        a = phase2.project(self.tmp / "a", "profile-research")
        b = phase2.project(self.tmp / "b", "profile-research")
        (a / "data").mkdir()
        (b / "data").mkdir()
        h(a, "lab", "apply", "--root", str(a), "--test-command", "pytest -q")
        h(b, "sf", "apply", "--root", str(b), "--stacks", "python", "--profiles", "research", "--test-command",
          "pytest -q")
        for rel in (".claude/settings.json", ".vscode/tasks.json", ".gitignore"):
            self.assertEqual((a / rel).read_bytes(), (b / rel).read_bytes(), rel)

    def test_ep07_ca31_copy_core_carries_the_profiles(self):
        """EP-07, CA-31: `sf copy-core` copies profiles/ with the core; a second run is identical."""
        res = h(self.tmp, "sf", "copy-core", "--root", str(self.tmp))
        self.assertEqual(res.returncode, 0, res.stderr)
        diff = filecmp.dircmp(str(helpers.CORE / "profiles"), str(self.tmp / ".claude" / "harness" / "profiles"))
        self.assertEqual((diff.left_only, diff.right_only, diff.diff_files), ([], [], []))
        self.assertEqual(h(self.tmp, "sf", "copy-core", "--root", str(self.tmp)).returncode, 0)

    def test_ax01_ax02_ax03_ai_lab_adds_research_to_an_equipped_project(self):
        """AX-01, AX-02, AX-03: /ai-lab is `/software-factory --profile research`: on a project stamped
        software it adds research and removes nothing; it carries no retirement notice (no expiry)."""
        stamp.add(self.tmp, ["software"], "scout", "2026-10-01")
        added = stamp.add(self.tmp, ["research"], "forced", "2026-10-31")
        self.assertEqual((added["added"], stamp.names(self.tmp)), (["research"], ["software", "research"]))
        alias = (helpers.CORE.parent / "ai-lab" / "SKILL.md").read_text(encoding="utf-8").split("\n")
        self.assertEqual(alias[1], "name: ai-lab")
        self.assertNotIn("context: fork", alias[:8])


if __name__ == "__main__":
    unittest.main()
