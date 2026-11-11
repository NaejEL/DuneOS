# code-index rules (CX-01 to CX-16, BK-02) and the rgkb.py data-loss fix (CX-09, D8).

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import helpers
from hcore import blocks, cx, fusion, gates, interp

ASSET = helpers.CORE.parent / "code-index" / "assets" / "rgkb.py"
HAS_ASSET = ASSET.is_file()
SKIP_ASSET = "code-index asset absent (core copied into a project)"
PY = ["python"]


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


def read(path: Path) -> str:
    with open(str(path), encoding="utf-8", newline="") as fh:
        return fh.read()


class ArgumentsTest(unittest.TestCase):
    def test_cx01_reindex_no_wire_and_the_rest_handed_back(self):
        """CX-01: only --reindex and --no-wire are interpreted; the rest is returned untouched."""
        self.assertEqual(cx.parse_arguments("--reindex --no-wire"),
                         {"reindex": True, "no_wire": True, "extra": ""})
        self.assertEqual(cx.parse_arguments("")["reindex"], False)
        got = cx.parse_arguments("--no-wire fais ça --vite")
        self.assertTrue(got["no_wire"])
        self.assertEqual(got["extra"], "fais ça --vite")


class RgInstallTest(unittest.TestCase):
    def test_cx02_install_command_by_package_manager(self):
        """CX-02: the first available manager of the system wins; none -> local fallback."""
        self.assertIn("winget install --id BurntSushi.ripgrep.MSVC", cx.rg_install("windows", ["scoop", "winget"])["command"])
        self.assertEqual(cx.rg_install("windows", ["choco"])["command"], "choco install ripgrep -y")
        self.assertEqual(cx.rg_install("macos", ["brew"])["command"], "brew install ripgrep")
        self.assertEqual(cx.rg_install("linux", ["apt-get", "cargo"])["command"], "apt-get install -y ripgrep")
        self.assertEqual(cx.rg_install("linux", ["pacman", "yay"])["manager"], "yay")
        self.assertEqual(cx.rg_install("linux", ["cargo"])["command"], "cargo install ripgrep")
        self.assertIsNone(cx.rg_install("linux", []))
        self.assertIsNone(cx.rg_install("macos", ["apt-get"]))

    def test_cx02_system_names(self):
        """CX-02: platform names map to the three systems."""
        self.assertEqual([cx.system_name(p) for p in ("win32", "darwin", "linux")], ["windows", "macos", "linux"])


class InterpreterTest(unittest.TestCase):
    def test_cx03_path_command_only_with_a_version_floor(self):
        """CX-03: the interpreter comes from interp.detect (PATH, floor 3.9); an absolute path is refused."""
        self.assertEqual(interp.FLOOR, (3, 9))
        self.assertEqual(cx.interpreter(["py"]), ["py", "-3"])
        self.assertEqual(cx.interpreter(["python3"]), ["python3"])
        with self.assertRaises(cx.CxError):
            cx.interpreter(["/home/me/venv/bin/python"])
        with self.assertRaises(cx.CxError):
            cx.interpreter(["C:\\venv\\python.exe"])
        with self.assertRaises(cx.CxError):
            cx.interpreter([])


class SurveyTest(helpers.TempDirCase):
    def test_cx04_survey_reports_what_is_there(self):
        """CX-04: git, factory, lab, earlier install, markers and bulky folders; nothing is modified."""
        write(self.tmp / ".git" / "HEAD", "ref")
        write(self.tmp / "package.json", "{}")
        write(self.tmp / "node_modules" / "x" / "i.js", "")
        write(self.tmp / ".claude" / "agents" / "factory-planner.md", "---\nname: a\n---\n")
        write(self.tmp / ".claude" / "agents" / "lab-math.md", "---\nname: a\n---\n")
        write(self.tmp / ".mcp.json", '{"mcpServers": {"rgkb": {}}}')
        write(self.tmp / ".gitignore", "*.sqlite\n")
        write(self.tmp / "ci" / "factory.sh", "x")
        before = sorted(str(p) for p in self.tmp.rglob("*"))
        got = cx.survey(self.tmp)
        self.assertTrue(got["git"])
        self.assertEqual(got["factory"], [".claude/agents/factory-planner.md"])
        self.assertEqual(got["lab"], [".claude/agents/lab-math.md"])
        self.assertEqual(got["ci_scripts"], ["ci/factory.sh"])
        self.assertTrue(got["previous"]["mcp"])
        self.assertFalse(got["previous"]["server"])
        self.assertIn("package.json", got["markers"])
        self.assertEqual(got["heavy_dirs"], ["node_modules/"])
        self.assertTrue(got["knowledge_excluded"])
        self.assertEqual(before, sorted(str(p) for p in self.tmp.rglob("*")))


class ServerCopyTest(helpers.TempDirCase):
    def asset(self, version: str) -> Path:
        return write(self.tmp / "asset" / "rgkb.py", 'TOOL_VERSION = "%s"\nprint(1)\n' % version)

    def test_cx05_absent_identical_newer_asset_and_newer_project(self):
        """CX-05: created; identical -> nothing; asset >= -> overwritten and announced; project > -> kept."""
        dest = self.tmp / "proj" / ".claude" / "rgkb" / "rgkb.py"
        self.assertEqual(cx.install_server(self.asset("1.0.1"), self.tmp / "proj")["state"], fusion.CREATED)
        self.assertEqual(cx.install_server(self.asset("1.0.1"), self.tmp / "proj")["state"], fusion.IDENTICAL)
        write(dest, 'TOOL_VERSION = "1.0.0"\nold\n')
        got = cx.install_server(self.asset("1.0.1"), self.tmp / "proj")
        self.assertEqual(got["state"], cx.UPDATED)
        self.assertIn("announce", got)
        self.assertEqual(read(dest), read(self.asset("1.0.1")))
        write(dest, 'TOOL_VERSION = "1.0.10"\nmine\n')
        got = cx.install_server(self.asset("1.0.9"), self.tmp / "proj")
        self.assertEqual(got["state"], fusion.KEPT)
        self.assertEqual(read(dest), 'TOOL_VERSION = "1.0.10"\nmine\n')

    def test_cx05_equal_version_overwrites(self):
        """CX-05: an equal version that differs is overwritten (>=)."""
        write(self.tmp / "p" / ".claude" / "rgkb" / "rgkb.py", 'TOOL_VERSION = "1.0.1"\nedited\n')
        self.assertEqual(cx.install_server(self.asset("1.0.1"), self.tmp / "p")["state"], cx.UPDATED)


class RegisterTest(helpers.TempDirCase):
    def test_cx06_cx07_cx15_files_created_then_identical(self):
        """CX-06, CX-07, CX-15, CX-16: three files created, no absolute path, second application identical."""
        states = cx.register(self.tmp, ["py", "-3"])
        self.assertEqual(set(states.values()), {fusion.CREATED})
        mcp = json.loads(read(self.tmp / ".mcp.json"))
        self.assertEqual(mcp["mcpServers"]["rgkb"], {"command": "py", "args": ["-3", ".claude/rgkb/rgkb.py", "mcp"]})
        settings = json.loads(read(self.tmp / ".claude" / "settings.json"))
        self.assertEqual(settings["enabledMcpjsonServers"], ["rgkb"])
        allow = settings["permissions"]["allow"]
        self.assertIn("mcp__rgkb__file_card", allow)
        self.assertIn("Bash(py -3 .claude/rgkb/rgkb.py *)", allow)
        self.assertFalse([a for a in allow if "note_add" in a or "reindex" in a])
        self.assertEqual(settings["permissions"]["deny"], cx.DENY_RULES)
        tasks = json.loads(read(self.tmp / ".vscode" / "tasks.json"))
        self.assertEqual([i["id"] for i in tasks["inputs"]], ["rgkbQuery"])
        self.assertEqual(len(tasks["tasks"]), 5)
        self.assertEqual(tasks["tasks"][0]["windows"]["command"], "py -3 .claude/rgkb/rgkb.py index")
        self.assertEqual(tasks["tasks"][0]["command"], "python .claude/rgkb/rgkb.py index")
        snapshot = {p: read(self.tmp / p) for p in (".mcp.json", ".claude/settings.json", ".vscode/tasks.json")}
        self.assertEqual(set(cx.register(self.tmp, ["py", "-3"]).values()), {fusion.IDENTICAL})
        self.assertEqual(snapshot, {p: read(self.tmp / p) for p in snapshot})

    def test_cx06_existing_server_entry_is_customized_kept(self):
        """CX-06: an rgkb entry that differs is never replaced; other servers and keys are preserved."""
        write(self.tmp / ".mcp.json", '{"mcpServers": {"rgkb": {"command": "mine"}, "other": {}}}')
        write(self.tmp / ".claude" / "settings.json",
              '{\n  // keep me\n  "model": "x",\n  "permissions": {"allow": ["Read"]}\n}\n')
        states = cx.register(self.tmp, ["python"])
        self.assertEqual(states[".mcp.json"], fusion.KEPT)
        self.assertEqual(json.loads(read(self.tmp / ".mcp.json"))["mcpServers"]["rgkb"], {"command": "mine"})
        self.assertEqual(states[".claude/settings.json"], fusion.MERGED)
        text = read(self.tmp / ".claude" / "settings.json")
        self.assertIn("// keep me", text)
        self.assertIn('"model": "x"', text)
        self.assertIn('"Read"', text)

    def test_cx06_invalid_json_changes_nothing(self):
        """CX-06: an unreadable file fails the whole registration (all or nothing)."""
        write(self.tmp / ".claude" / "settings.json", "{not json")
        with self.assertRaises(fusion.FusionError):
            cx.register(self.tmp, ["python"])
        self.assertFalse((self.tmp / ".mcp.json").exists())


class ScopeTest(helpers.TempDirCase):
    CONFIG = '{\n  "schema": 1,\n  "exclude": ["!.git/**", "!**/node_modules/**"],\n  "hooks": "commit",\n  "rg_path": null\n}\n'

    def test_cx08_exclude_completed_nothing_removed(self):
        """CX-08: '!<dir>/**' per detected bulky folder not already covered; rest untouched; idempotent."""
        write(self.tmp / ".knowledge" / "config.json", self.CONFIG)
        (self.tmp / "node_modules").mkdir()
        (self.tmp / "data").mkdir()
        (self.tmp / "lab-logs").mkdir()
        got = cx.scope(self.tmp)
        cfg = json.loads(read(self.tmp / ".knowledge" / "config.json"))
        self.assertEqual(cfg["exclude"], ["!.git/**", "!**/node_modules/**", "!data/**", "!lab-logs/**"])
        self.assertEqual(cfg["hooks"], "commit")
        self.assertIsNone(cfg["rg_path"])
        self.assertEqual(got["states"][".knowledge/config.json"], fusion.MERGED)
        again = cx.scope(self.tmp)
        self.assertEqual(again["states"][".knowledge/config.json"], fusion.IDENTICAL)

    def test_cx08_missing_config_asks_for_init(self):
        """CX-08: no config.json -> explicit error, nothing created."""
        with self.assertRaises(cx.CxError):
            cx.scope(self.tmp)

    def test_cx08_missing_exclude_key_is_not_replaced_by_a_short_list(self):
        """CX-08: without an exclude key the defaults apply; completing would replace them."""
        write(self.tmp / ".knowledge" / "config.json", '{"schema": 1}\n')
        (self.tmp / "data").mkdir()
        got = cx.scope(self.tmp)
        self.assertEqual(got["states"][".knowledge/config.json"], fusion.KEPT)
        self.assertNotIn("exclude", json.loads(read(self.tmp / ".knowledge" / "config.json")))

    def test_cx10_counter_rule_after_one_comment_and_nothing_else(self):
        """CX-10: an excluding rule gets '!.knowledge/**' at the end, after a one-line comment."""
        write(self.tmp / ".knowledge" / "config.json", self.CONFIG)
        write(self.tmp / ".gitignore", "*.jsonl\ndist/\n")
        got = cx.scope(self.tmp)
        self.assertEqual(read(self.tmp / ".gitignore"), "*.jsonl\ndist/\n%s\n!.knowledge/**\n" % cx.GITIGNORE_NOTE)
        self.assertTrue(got["gitignore_counter_rule"])
        cx.scope(self.tmp)
        self.assertEqual(read(self.tmp / ".gitignore").count("!.knowledge/**"), 1)

    def test_cx10_no_exclusion_no_change(self):
        """CX-10: a .gitignore that does not exclude .knowledge/ is left as it is; a later counter-rule counts."""
        write(self.tmp / ".knowledge" / "config.json", self.CONFIG)
        write(self.tmp / ".gitignore", "dist/\ndata/\n")
        self.assertFalse(cx.scope(self.tmp)["gitignore_counter_rule"])
        self.assertEqual(read(self.tmp / ".gitignore"), "dist/\ndata/\n")
        for rules in (".knowledge\n", ".knowledge/\n", "*.sqlite\n", "*.db\n", ".knowledge/*\n"):
            self.assertTrue(cx.knowledge_excluded(rules), rules)
            self.assertFalse(cx.knowledge_excluded(rules + "!.knowledge/**\n"), rules)


class WireTest(helpers.TempDirCase):
    PLANNER = "---\nname: factory-planner\ntools: Read, Glob, Grep\n---\nBody.\n"
    BUILDER = "---\nname: factory-builder\n---\nBody.\n"
    RUN = "---\nname: factory-run\n---\n1. Plan\n"
    CI_SH = 'cmd+=(--allowedTools "Read,Glob,Bash(git *)")\n'
    CI_PS = "$a += @('--allowedTools', 'Read,Glob,Bash(git *)')\n"

    def setup_factory(self):
        write(self.tmp / ".claude" / "agents" / "factory-planner.md", self.PLANNER)
        write(self.tmp / ".claude" / "agents" / "factory-builder.md", self.BUILDER)
        write(self.tmp / ".claude" / "skills" / "factory-run" / "SKILL.md", self.RUN)
        write(self.tmp / "ci" / "factory.sh", self.CI_SH)
        write(self.tmp / "ci" / "factory.ps1", self.CI_PS)

    CONTENTS = {"factory-planner": "read-only block", "factory-builder": "builder block",
                "factory-run": "orchestrator block"}

    def test_cx12_cx13_cx14_cx16_wire_and_idempotence(self):
        """CX-12, CX-13, CX-14, CX-16: one block per target, tools completed only where limiting, CI lists completed."""
        self.setup_factory()
        got = cx.wire(self.tmp, self.CONTENTS, ["python"])["states"]
        self.assertEqual(set(got.values()), {fusion.MERGED})
        planner = read(self.tmp / ".claude" / "agents" / "factory-planner.md")
        self.assertTrue(planner.startswith("---\nname: factory-planner\ntools: Read, Glob, Grep, mcp__rgkb__search"))
        self.assertEqual(planner.count("<!-- rgkb:debut -->"), 1)
        builder = read(self.tmp / ".claude" / "agents" / "factory-builder.md")
        self.assertNotIn("tools:", builder)
        self.assertIn("builder block", builder)
        sh = read(self.tmp / "ci" / "factory.sh")
        self.assertIn('"Read,Glob,Bash(git *),mcp__rgkb__search', sh)
        self.assertIn("Bash(python .claude/rgkb/rgkb.py *)", sh)
        self.assertIn("mcp__rgkb__status", read(self.tmp / "ci" / "factory.ps1"))
        snapshot = {p.name: read(p) for p in self.tmp.rglob("*") if p.is_file()}
        again = cx.wire(self.tmp, dict(self.CONTENTS), ["python"])["states"]
        self.assertEqual(set(again.values()), {fusion.IDENTICAL})
        self.assertEqual(snapshot, {p.name: read(p) for p in self.tmp.rglob("*") if p.is_file()})

    def test_cx12_changed_block_is_replaced_not_duplicated(self):
        """CX-12: a second application with another text replaces the block in place."""
        self.setup_factory()
        cx.wire(self.tmp, self.CONTENTS, ["python"])
        cx.wire(self.tmp, dict(self.CONTENTS, **{"factory-builder": "builder block v2"}), ["python"])
        builder = read(self.tmp / ".claude" / "agents" / "factory-builder.md")
        self.assertEqual(builder.count("<!-- rgkb:debut -->"), 1)
        self.assertIn("v2", builder)

    def test_cx12_missing_content_for_a_present_target_is_refused(self):
        """CX-12: a detected target without block text is an error, nothing written."""
        self.setup_factory()
        with self.assertRaises(cx.CxError):
            cx.wire(self.tmp, {"factory-planner": "x"}, ["python"])
        self.assertEqual(read(self.tmp / ".claude" / "agents" / "factory-planner.md"), self.PLANNER)

    def test_cx13_inheriting_agent_gets_no_tools(self):
        """CX-13: an agent that inherits every tool (no tools: line) is never restricted."""
        write(self.tmp / ".claude" / "agents" / "lab-math.md", "---\nname: lab-math\n---\nx\n")
        cx.wire(self.tmp, {"lab-math": "b"}, ["python"])
        self.assertNotIn("tools", read(self.tmp / ".claude" / "agents" / "lab-math.md").split("---")[1])


class GateTest(unittest.TestCase):
    def test_cx11_hooks_gate_is_in_the_catalog(self):
        """CX-11: gate code-index.hooks, headless fallback skip, three options."""
        gate = gates.get("code-index.hooks")
        self.assertEqual(gate.headless, "skip")
        self.assertEqual(len(gate.options), 3)


@unittest.skipUnless(HAS_ASSET, SKIP_ASSET)
class RgkbTest(helpers.TempDirCase):
    def rgkb(self, *args):
        env = dict(os.environ, RGKB_ROOT=str(self.tmp), PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        return subprocess.run([sys.executable, "-B", str(ASSET)] + list(args), cwd=str(self.tmp),
                              capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)

    def load_module(self):
        os.environ["RGKB_ROOT"] = str(self.tmp)
        self.addCleanup(os.environ.pop, "RGKB_ROOT", None)
        spec = importlib.util.spec_from_file_location("rgkb_under_test", str(ASSET))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_cx09_init_never_rewrites_config(self):
        """CX-09 (D8): init, edit config.json, init again -> config.json identical byte for byte."""
        self.assertEqual(self.rgkb("init").returncode, 0)
        config = self.tmp / ".knowledge" / "config.json"
        cfg = json.loads(read(config))
        cfg["exclude"].append("!mine/**")
        cfg["hooks"] = "commit"
        write(config, json.dumps(cfg, indent=4) + "\n")
        before = config.read_bytes()
        self.assertEqual(self.rgkb("init").returncode, 0)
        self.assertEqual(config.read_bytes(), before)

    def test_cx09_tool_version_is_above_1_0_0(self):
        """CX-09: TOOL_VERSION incremented."""
        module = self.load_module()
        self.assertGreater(tuple(int(x) for x in module.TOOL_VERSION.split(".")), (1, 0, 0))

    def test_cx09_unreadable_config_is_not_rewritten_by_hooks_install(self):
        """CX-09 (Q-K): hooks install never replaces an unreadable config.json by the defaults."""
        helpers.init_repo(self.tmp)
        self.assertEqual(self.rgkb("init").returncode, 0)
        config = self.tmp / ".knowledge" / "config.json"
        write(config, '{"exclude": [')
        before = config.read_bytes()
        res = self.rgkb("hooks", "install")
        self.assertEqual(config.read_bytes(), before)
        self.assertIn("illisible", res.stdout + res.stderr)
        self.assertFalse((self.tmp / ".git" / "hooks" / "post-merge").exists())

    HOOK_TEXTS = ("#!/bin/sh\necho one\n", "#!/bin/sh\n\necho one\necho two\n", "echo only\n", "#!/bin/sh\nset -e\n")

    def test_bk02_rgkb_and_blocks_agree_on_well_formed_hooks(self):
        """BK-02: insertion and removal of the hook block give the same output in rgkb.py and blocks.py (style hash)."""
        module = self.load_module()
        block = module.hook_block("post-merge")
        inner = block[len(module.HOOK_BEGIN):block.index(module.HOOK_END)].strip("\n")
        for text in self.HOOK_TEXTS:
            inserted_rgkb = module.insert_hook_block(text, block)
            inserted_core = blocks.upsert_block(text, "rgkb", inner, "hash")
            self.assertEqual(inserted_rgkb, inserted_core, repr(text))
            self.assertEqual(module.strip_hook_block(inserted_rgkb), blocks.remove_block(inserted_core, "rgkb", "hash"),
                             repr(text))
            self.assertEqual(module.strip_hook_block(inserted_rgkb).rstrip("\n"), text.rstrip("\n"))

    def test_bk02_missing_end_marker_is_refused_like_the_core(self):
        """BK-02 (X3): a missing end marker raises instead of truncating the file."""
        module = self.load_module()
        broken = "#!/bin/sh\necho keep\n" + module.HOOK_BEGIN + "\nstuff\n"
        with self.assertRaises(ValueError):
            module.strip_hook_block(broken)
        with self.assertRaises(blocks.BlockError):
            blocks.remove_block(broken, "rgkb", "hash")


if __name__ == "__main__":
    unittest.main()
