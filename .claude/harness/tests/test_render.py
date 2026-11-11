"""Phase 3, RD-01 to RD-07 and EN-01 to EN-04: deterministic rendering, layout and version header."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest

import helpers
import upgrade_fixtures as uf
from hcore import blocks, profiles, render, upgrade


class PureRenderTest(unittest.TestCase):
    def test_rd02_same_inputs_same_bytes(self):
        """RD-02: render(fragment, params, contributions) is a pure function."""
        prof = profiles.get("software")
        args = ({"interpreter": "py -3", "stacks": ["python"]}, ["mcp__rgkb__search"], {}, render.header("v1", "3.0.0"))
        one, two = render.profile_layouts(prof, *args), render.profile_layouts(prof, *args)
        self.assertEqual(one, two)
        self.assertEqual(render.render_new(one[".claude/agents/factory-planner.md"]),
                         render.render_new(two[".claude/agents/factory-planner.md"]))

    def test_rd02_a_missing_required_parameter_is_an_error(self):
        """RD-02, RD-03: `{{name}}` without value or default fails, `{{name|default}}` renders the default."""
        with self.assertRaises(render.RenderError):
            render.substitute("x {{interpreter}}", {})
        self.assertEqual(render.substitute("{{a|dflt}} {{b}}", {"b": ["x", "y"]}), "dflt x, y")

    def test_rd03_params_are_validated_by_pf03(self):
        """RD-03: `params[]` = {name, source: detected|judgement, required}, checked by the profile validation."""
        data = dict(profiles.get("software"))
        data["params"] = [{"name": "x", "source": "guess", "required": True}]
        errors = profiles.validate_manifest(data, data["_dir"])
        self.assertTrue(any("params" in e for e in errors), errors)
        self.assertEqual(profiles.validate_manifest(profiles.get("software"), profiles.get("software")["_dir"]), [])

    def test_rd05_union_of_profile_tools_and_contributions(self):
        """RD-05: profile tools first, then the contributions in order, no duplicate; tools null stays unrestricted."""
        prof = profiles.get("software")
        layouts = render.profile_layouts(prof, {"interpreter": "python"}, ["mcp__rgkb__search", "Read"], {},
                                         render.header("v", "3.0.0"))
        planner = dict((n, c) for n, _s, c in layouts[".claude/agents/factory-planner.md"].blocks)
        self.assertIn("tools: Read, Glob, Grep, mcp__rgkb__search\n", planner[render.FRONTMATTER] + "\n")
        builder = dict((n, c) for n, _s, c in layouts[".claude/agents/factory-builder.md"].blocks)
        self.assertNotIn("tools:", builder[render.FRONTMATTER])
        ci = layouts["ci/factory.sh"].blocks[0][2]
        self.assertIn("mcp__rgkb__search", ci)

    def test_rd06_rgkb_blocks_are_templates(self):
        """RD-06: a rgkb block depends on the interpreter and the agent name only."""
        tpl = (uf.ASSETS / "blocks" / "read-only.md").read_text(encoding="utf-8")
        one = render.rgkb_block(tpl, "factory-planner", "py -3", "v1", "1.0.1")
        self.assertEqual(one, render.rgkb_block(tpl, "factory-planner", "py -3", "v1", "1.0.1"))
        self.assertTrue(one.startswith("<!-- rgkb: claude-skills v1 (rgkb 1.0.1) -->"))
        self.assertIn("py -3 .claude/rgkb/rgkb.py", one)
        self.assertIn("factory-planner", one)

    def test_rd07_strip_blocks(self):
        """RD-07: the out-of-block text = the file without the harness blocks, the rgkb blocks and the header."""
        text = ("---\n# harness: claude-skills v (core 3.0.0)\n# >>> harness.frontmatter >>>\nname: a\n"
                "# <<< harness.frontmatter <<<\n---\n\n<!-- harness.body:debut -->\nB\n<!-- harness.body:fin -->\n"
                "\nmine\n<!-- rgkb:debut -->\nR\n<!-- rgkb:fin -->\n")
        self.assertEqual(blocks.strip_blocks(text), "---\n---\n\n\nmine\n")

    def test_en01_header_line(self):
        """EN-01, EN-04: one English line, no date."""
        line = render.header("abc123", "3.0.0")
        self.assertEqual(line, "# harness: claude-skills abc123 (core 3.0.0)")
        self.assertNotRegex(line, r"\d{4}-\d{2}-\d{2}")


@unittest.skipUnless(uf.REPO_OK, uf.SKIP_REPO)
class LayoutTest(helpers.TempDirCase):
    def setUp(self) -> None:
        super().setUp()
        repo, _ = uf.make_repo(self.tmp, 2)
        self.info = uf.repo_info(repo, describe="v-test")
        self.root = self.tmp / "proj"
        self.pv1 = uf.make_profiles(self.tmp)
        uf.fresh_install(self.root, self.pv1, self.info)

    def lines(self, rel):
        return uf.read(self.root, rel).split("\n")

    def test_rd01_en01_en03_layout_and_header_of_every_generated_file(self):
        """RD-01, EN-01, EN-03, EP-11: header, blocks, free zones; header lines per file type; none in JSON/copies."""
        planner = self.lines(".claude/agents/factory-planner.md")
        self.assertEqual(planner[0], "---")
        self.assertTrue(planner[1].startswith("# harness: claude-skills "))
        text = uf.read(self.root, ".claude/agents/factory-planner.md")
        self.assertEqual(blocks.harness_block_names(text), ["harness.body", "harness.frontmatter"])
        self.assertIn("# >>> harness.frontmatter >>>", text)
        orch = uf.read(self.root, ".claude/skills/factory-run/SKILL.md")
        self.assertIn("<!-- harness.profile.software:debut -->", orch)
        sh = self.lines("ci/factory.sh")
        self.assertEqual(sh[0], "#!/usr/bin/env bash")
        self.assertTrue(sh[1].startswith("# harness: claude-skills "))
        self.assertTrue(self.lines("ci/factory.ps1")[0].startswith("# harness: claude-skills "))
        for rel in (".claude/settings.json", ".vscode/tasks.json", ".claude/harness.json",
                    ".claude/harness/MANIFEST.json", ".claude/harness/harness.py"):
            self.assertNotIn("harness: claude-skills", uf.read(self.root, rel), rel)    # EN-03

    def test_ci_script_is_valid_bash(self):
        """RD-01: the rendered ci/factory.sh passes `bash -n`."""
        ok = helpers.bash_check(uf.read(self.root, "ci/factory.sh"))
        if ok is None:
            self.skipTest("no working bash")
        self.assertTrue(ok)

    def test_en02_the_header_is_replaced_by_an_upgrade_and_classed_header(self):
        """EN-02, EN-04: a new version changes only the header line (class header); idempotent afterwards."""
        newer = dict(self.info, describe="v-next")
        res = uf.plan_and_apply(self.root, upgrade.SF, newer, self.pv1)
        kinds = {c["kind"] for c in res["changes"] if c["file"].startswith(".claude/agents/")}
        self.assertEqual(kinds, {"header"})
        self.assertIn("v-next", uf.read(self.root, ".claude/agents/factory-planner.md"))
        self.assertEqual(uf.plan_and_apply(self.root, upgrade.SF, newer, self.pv1)["changes"], [])

    def test_rd04_missing_required_parameter_asks_to_fill_it_and_writes_nothing(self):
        """RD-04, UP-11: action fill_params, rien d'écrit; une fois fourni, le paramètre n'est plus redemandé."""
        profs = self.tmp / "profiles-need"
        shutil.copytree(str(self.pv1), str(profs))
        path = profs / "software" / "profile.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["params"].append({"name": "house_rule", "source": "judgement", "required": True})
        path.write_text(json.dumps(data), encoding="utf-8")
        before = uf.tree(self.root)
        res = uf.plan_and_apply(self.root, upgrade.SF, self.info, profs)
        self.assertEqual((res["action"], res["missing"]), ("fill_params", ["house_rule"]))
        self.assertEqual(uf.tree(self.root), before)
        res = uf.plan_and_apply(self.root, upgrade.SF, self.info, profs, params={"house_rule": "x"})
        self.assertEqual(res["action"], "plan")
        stamped = json.loads(uf.read(self.root, ".claude/harness.json"))["skills"]["software-factory"]["params"]
        self.assertEqual(stamped, {"house_rule": "x"})
        self.assertEqual(uf.plan_and_apply(self.root, upgrade.SF, self.info, profs)["changes"], [])


    def test_rd02_the_generated_orchestrator_is_the_single_source_fragment(self):
        """RD-02, AP-03, HK-05: the factory-run body of a fresh install is bodies/orchestrator.md, rendered, and it
        carries the commands the orchestrator must run (binding, settling, scope hooks)."""
        text = uf.read(self.root, ".claude/skills/factory-run/SKILL.md")
        start, end = blocks.markers("harness.profile.software", "md")
        body = text.split(start + "\n", 1)[1].split("\n" + end, 1)[0]
        source = (uf.PROFILES / "software" / "bodies" / "orchestrator.md").read_text(encoding="utf-8")
        rendered = render.substitute(source, {"interpreter": uf.INTERPRETER})
        self.assertEqual(body.strip("\n"), rendered.strip("\n"))
        for command in ("factory-run bind", "spec settle", "activate --run", "deactivate --run",
                        "factory-run start", "spec set-status", "factory-run deliver"):
            self.assertIn(command, body)
        self.assertNotIn("{{", body)


if __name__ == "__main__":
    unittest.main()
