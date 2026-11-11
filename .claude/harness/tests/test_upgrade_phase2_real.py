"""Phase 3, RD-01, MI-04, CV-01 to CV-04, UP-06: a real Phase 1/2 project (the generated files of this
very repository at the end of Phase 2) goes through the conversion, not through the block update."""

from __future__ import annotations

import re
import shutil
import unittest

import helpers
import upgrade_fixtures as uf
from hcore import blocks, upgrade

FIXTURE = helpers.CORE.parents[1] / "tests" / "fixtures" / "upgrade-phase2-real"
AGENTS = [".claude/agents/factory-%s.md" % r for r in ("planner", "builder", "verifier")]
SKILL = ".claude/skills/factory-run/SKILL.md"


def keys(text: str):
    """Top-level YAML keys of the frontmatter, in order (block markers are comments)."""
    lines = text.split("\n")
    assert lines[0].strip() == "---", text[:80]
    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    return [m.group(1) for m in (re.match(r"^([A-Za-z][A-Za-z0-9_-]*):", l) for l in lines[1:end]) if m]


def frontmatter_value(text: str, key: str):
    match = re.search(r"^%s: *(.*)$" % re.escape(key), text, re.M)
    return match.group(1).strip() if match else None


class Phase2RealTest(helpers.TempDirCase):
    def setUp(self):
        super().setUp()
        repo_dir, _ = uf.make_repo(self.tmp, 2)
        self.info = uf.repo_info(repo_dir)
        self.root = self.tmp / "proj"
        for src, dst in (("claude/agents", ".claude/agents"), ("claude/models.json", ".claude/models.json"),
                         ("claude/skills-factory-run/SKILL.md", SKILL), ("ci/factory.sh", "ci/factory.sh"),
                         ("ci/factory.ps1", "ci/factory.ps1")):
            path = FIXTURE / src
            if path.is_dir():
                for f in sorted(path.iterdir()):
                    uf.write(self.root, "%s/%s" % (dst, f.name), f.read_bytes().decode("utf-8"))
            else:
                uf.write(self.root, dst, path.read_bytes().decode("utf-8"))
        # Phase 2 (EP-06): the orchestrator body sits in a harness.profile.software block, no frontmatter block.
        head, body = uf.read(self.root, SKILL).split("\n---\n", 1)
        uf.write(self.root, SKILL, "%s\n---\n<!-- harness.profile.software:debut -->\n%s\n"
                                   "<!-- harness.profile.software:fin -->\n" % (head, body.strip("\n")))
        helpers.init_repo(self.root)
        helpers.git(self.root, "add", "-A", "-f")
        helpers.git(self.root, "commit", "-q", "-m", "phase 2 install")

    def plan(self):
        return upgrade.build_plan(self.root, upgrade.SF, self.info, uf.CORE, uf.PROFILES, interpreter=uf.INTERPRETER,
                                  requested=["software"], now=uf.NOW, assets=uf.ASSETS)

    def after(self, plan, rel):
        return next((c["after"] for c in plan["changes"] if c["file"] == rel), None)

    def test_rd01_phase2_files_are_not_layout_files(self):
        """RD-01: harness.model and harness.profile.* alone do not make the RD-01 layout."""
        for rel in AGENTS + [SKILL]:
            text = uf.read(self.root, rel)
            self.assertTrue(blocks.harness_block_names(text), rel)
            self.assertFalse(blocks.has_layout_blocks(text), rel)

    def test_mi04_cv_no_duplicate_key_and_models_survive(self):
        """MI-04, CV-02, X1: no duplicate frontmatter key, one model per agent, the models.json choice kept."""
        uf.write(self.root, ".claude/models.json",
                 '{\n  "factory.planner": "opus",\n  "factory.builder": "haiku",\n  "factory.verifier": "sonnet"\n}\n')
        helpers.git(self.root, "commit", "-qam", "user model choices")
        plan = self.plan()
        self.assertEqual(plan["action"], "plan")
        expected = {"planner": "opus", "builder": "haiku", "verifier": "sonnet"}
        for rel in AGENTS + [SKILL]:
            after = self.after(plan, rel)
            self.assertIsNotNone(after, rel)
            found = keys(after)
            self.assertEqual(len(found), len(set(found)), (rel, found))
        for role, model in expected.items():
            after = self.after(plan, ".claude/agents/factory-%s.md" % role)
            self.assertEqual(frontmatter_value(after, "model"), model, role)
            self.assertEqual(after.count("model:"), 1)
        self.assertEqual(self.after(plan, SKILL).count("name:"), 1)

    def test_cv_pristine_files_are_converted_and_the_second_plan_is_empty(self):
        """CV-02, UP-06: Phase 2 files are `convert` (not blocks-only), and a second plan after apply is empty."""
        plan = self.plan()
        kinds = {c["file"]: c["kind"] for c in plan["changes"]}
        for rel in AGENTS + [SKILL]:
            self.assertEqual(kinds[rel], "convert", rel)
        self.assertFalse(plan["blocks_only"])
        files = upgrade.write_plan(self.root, plan, uf.NOW)
        upgrade.apply_plan(self.root, self.root / files["plan"], None, uf.NOW)
        for rel in AGENTS + [SKILL]:
            found = keys(uf.read(self.root, rel))
            self.assertEqual(len(found), len(set(found)), rel)
        again = self.plan()
        self.assertEqual(again["changes"], [])

    def test_cv04_user_prose_is_never_blocks_only(self):
        """CV-03, CV-04: an appended suffix is kept after the blocks; edited prose is a candidate, not a change."""
        rel = AGENTS[0]
        uf.write(self.root, rel, uf.read(self.root, rel) + "\n## Règles maison\n\nToujours citer la spec.\n")
        edited = AGENTS[1]
        uf.write(self.root, edited, uf.read(self.root, edited).replace("Tu es", "Vous êtes", 1))
        plan = self.plan()
        self.assertFalse(plan["blocks_only"])
        after = self.after(plan, rel)
        self.assertIn("Toujours citer la spec.", after)
        self.assertEqual(len(keys(after)), len(set(keys(after))))
        self.assertIsNone(self.after(plan, edited))
        self.assertIn(edited, [c["file"] for c in plan["candidates"]])


if __name__ == "__main__":
    unittest.main()
