# Module hcore.fusion: software-factory merges (SF-01 to SF-04, CA12),
# git exclusion of the journal by the generated .gitignore (CA11), core copy (D1),
# generic engine (FU-01 to FU-06, CA-22).

import json
import os
import shutil
import subprocess
import sys
import unittest

import helpers
from hcore import CORE_VERSION, fusion, journal, jsonc, registry, runs


# The reference files predate the scratch folder: the generated block now ends with this line.
SCRATCH_LINE = ".harness/tmp/\n"


def ordered(path):
    return json.dumps(json.loads(path.read_text(encoding="utf-8")))


@unittest.skipIf(os.name == "nt", "POSIX permission bits")
class FileModeTest(helpers.TempDirCase):
    def test_a_rewritten_file_keeps_its_permissions(self):
        """An executable script rewritten by a merge or an upgrade stays executable; a plain file stays plain."""
        from hcore import upgrade
        for write in (fusion._write, upgrade._write_file):
            script, plain = self.tmp / "run.sh", self.tmp / "notes.md"
            script.write_text("#!/bin/sh\n", encoding="utf-8")
            plain.write_text("a\n", encoding="utf-8")
            os.chmod(str(script), 0o750)
            os.chmod(str(plain), 0o640)
            write(script, "#!/bin/sh\necho new\n")
            write(plain, "b\n")
            self.assertEqual(os.stat(str(script)).st_mode & 0o777, 0o750, write)
            self.assertEqual(os.stat(str(plain)).st_mode & 0o777, 0o640, write)

    def test_a_new_file_with_a_shebang_is_executable(self):
        """A created script is executable for whoever can read it; a created plain file is not."""
        fusion._write(self.tmp / "ci" / "factory.sh", "#!/usr/bin/env bash\n")
        fusion._write(self.tmp / "README.md", "text\n")
        self.assertTrue(os.access(str(self.tmp / "ci" / "factory.sh"), os.X_OK))
        self.assertFalse(os.stat(str(self.tmp / "README.md")).st_mode & 0o111)


class DenyRulesTest(unittest.TestCase):
    def test_sf01_stack_correspondence(self):
        """SF-01."""
        self.assertEqual(fusion.deny_rules([]), ["Read(./factory-logs/**)", "Read(./.git/objects/**)"])
        self.assertEqual(fusion.deny_rules(["node"])[:2], ["Read(./node_modules/**)", "Read(./dist/**)"])
        self.assertIn("Read(./**/obj/**)", fusion.deny_rules(["dotnet"]))
        self.assertIn("Read(./.pytest_cache/**)", fusion.deny_rules(["python"]))
        self.assertIn("Read(./build/**)", fusion.deny_rules(["cpp"]))
        self.assertIn("Read(./target/**)", fusion.deny_rules(["rust"]))
        self.assertNotIn("Read(./target/**)", fusion.deny_rules(["node"]))
        with self.assertRaises(fusion.FusionError):
            fusion.deny_rules(["cobol"])


@unittest.skipUnless(helpers.HAS_FIXTURES, helpers.SKIP_FIXTURES)
class FixtureMergeTest(helpers.TempDirCase):
    def prepare(self):
        src = helpers.FIXTURES / "merge-input"
        (self.tmp / ".claude").mkdir()
        (self.tmp / ".vscode").mkdir()
        shutil.copy(str(src / "settings.json"), str(self.tmp / ".claude" / "settings.json"))
        shutil.copy(str(src / "tasks.json"), str(self.tmp / ".vscode" / "tasks.json"))
        shutil.copy(str(src / "gitignore.txt"), str(self.tmp / ".gitignore"))

    def snapshot(self):
        return {p: (self.tmp / p).read_bytes() for p in (".claude/settings.json", ".vscode/tasks.json", ".gitignore")}

    def test_sf01_sf02_sf03_equal_expected_files(self):
        """SF-01 SF-02 SF-03."""
        self.prepare()
        states = fusion.apply(self.tmp, ["node"])
        expected = helpers.FIXTURES / "merge-expected"
        self.assertEqual(ordered(self.tmp / ".claude" / "settings.json"), ordered(expected / "settings.json"))
        self.assertEqual(ordered(self.tmp / ".vscode" / "tasks.json"), ordered(expected / "tasks.json"))
        self.assertEqual((self.tmp / ".gitignore").read_text(encoding="utf-8"),
                         (expected / "gitignore.txt").read_text(encoding="utf-8") + SCRATCH_LINE)
        self.assertEqual(set(states.values()), {fusion.MERGED})

    def test_sf01_third_party_key_kept_and_deny_without_duplicate(self):
        """SF-01."""
        self.prepare()
        fusion.apply(self.tmp, ["node"])
        data = json.loads((self.tmp / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(data["model"], "sonnet")
        self.assertEqual(data["env"], {"TOY": "1"})
        self.assertEqual(data["permissions"]["allow"], ["Bash(npm test)"])
        deny = data["permissions"]["deny"]
        self.assertEqual(len(deny), len(set(deny)))
        self.assertEqual(deny.count("Read(./node_modules/**)"), 1)

    def test_sf02_existing_kept_factory_entries_once(self):
        """SF-02."""
        self.prepare()
        fusion.apply(self.tmp, ["node"])
        fusion.apply(self.tmp, ["node"])
        data = json.loads((self.tmp / ".vscode" / "tasks.json").read_text(encoding="utf-8"))
        labels = [t["label"] for t in data["tasks"]]
        self.assertIn("npm: test", labels)
        for label in ("Factory: CI run", "Factory: CI run (YOLO)", "Factory: nouvelle spec (session)"):
            self.assertEqual(labels.count(label), 1)
        ids = [i["id"] for i in data["inputs"]]
        self.assertEqual(ids.count("factorySpec"), 1)
        self.assertIn("target", ids)

    def test_sf03_gitignore_line_added_once(self):
        """SF-03."""
        self.prepare()
        fusion.apply(self.tmp, ["node"])
        fusion.apply(self.tmp, ["node"])
        lines = (self.tmp / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines.count("factory-logs/"), 1)
        self.assertEqual(lines.count(".harness/runs/"), 1)

    def test_sf04_second_application_byte_identical(self):
        """SF-04."""
        self.prepare()
        fusion.apply(self.tmp, ["node"])
        before = self.snapshot()
        states = fusion.apply(self.tmp, ["node"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(set(states.values()), {fusion.IDENTICAL})

    def test_sf04_fresh_project_matches_reference_output(self):
        """SF-04."""
        states = fusion.apply(self.tmp, [])
        expected = helpers.FIXTURES / "merge-expected"
        self.assertEqual(ordered(self.tmp / ".claude" / "settings.json"), ordered(expected / "fresh-settings.json"))
        self.assertEqual(ordered(self.tmp / ".vscode" / "tasks.json"), ordered(expected / "fresh-tasks.json"))
        self.assertEqual((self.tmp / ".gitignore").read_text(encoding="utf-8"),
                         (expected / "fresh-gitignore.txt").read_text(encoding="utf-8") + SCRATCH_LINE)
        self.assertEqual(set(states.values()), {fusion.CREATED})

    def test_sf01_invalid_json_not_overwritten(self):
        """SF-01."""
        (self.tmp / ".claude").mkdir()
        (self.tmp / ".claude" / "settings.json").write_text("{ broken", encoding="utf-8")
        with self.assertRaises(fusion.FusionError):
            fusion.apply(self.tmp, [])
        self.assertEqual((self.tmp / ".claude" / "settings.json").read_text(encoding="utf-8"), "{ broken")


VSCODE_DEFAULT_TASKS = (
    "{\n"
    "\t// See https://go.microsoft.com/fwlink/?LinkId=733558\n"
    "\t// for the documentation about the tasks.json format\n"
    "\t\"version\": \"2.0.0\",\n"
    "\t\"tasks\": [\n"
    "\t\t{\n"
    "\t\t\t\"label\": \"echo\", /* sample task */\n"
    "\t\t\t\"type\": \"shell\",\n"
    "\t\t\t\"command\": \"echo // not a comment\",\n"
    "\t\t},\n"
    "\t]\n"
    "}\n"
)


class TolerantReadTest(helpers.TempDirCase):
    """SF-01 / SF-02: VS Code JSONC and BOM from Windows tools, all or nothing."""

    def put(self, rel, text, newline="\n"):
        path = self.tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline=newline) as fh:
            fh.write(text)
        return path

    def test_sf02_vscode_default_jsonc_merged_comments_kept(self):
        """SF-02."""
        path = self.put(".vscode/tasks.json", VSCODE_DEFAULT_TASKS)
        states = fusion.apply(self.tmp, [])
        self.assertEqual(states[".vscode/tasks.json"], fusion.MERGED)
        text = path.read_text(encoding="utf-8")
        for kept in ("// See https://go.microsoft.com", "/* sample task */", "echo // not a comment"):
            self.assertIn(kept, text)
        data = jsonc.loads(text).value
        self.assertEqual([t["label"] for t in data["tasks"]],
                         ["echo", "Factory: CI run", "Factory: CI run (YOLO)", "Factory: nouvelle spec (session)"])
        self.assertEqual([i["id"] for i in data["inputs"]], ["factorySpec"])
        self.assertIn('\n\t\t{\n\t\t\t"label": "Factory: CI run"', text)
        before = path.read_bytes()
        self.assertEqual(fusion.apply(self.tmp, [])[".vscode/tasks.json"], fusion.IDENTICAL)
        self.assertEqual(path.read_bytes(), before)

    def test_sf01_bom_and_crlf_preserved(self):
        """SF-01."""
        path = self.put(".claude/settings.json", '﻿{\r\n  "model": "sonnet"\r\n}\r\n', newline="")
        self.assertEqual(fusion.apply(self.tmp, ["rust"])[".claude/settings.json"], fusion.MERGED)
        raw = path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        data = json.loads(raw.decode("utf-8-sig"))
        self.assertEqual(data["model"], "sonnet")
        self.assertIn("Read(./target/**)", data["permissions"]["deny"])
        self.assertEqual(fusion.apply(self.tmp, ["rust"])[".claude/settings.json"], fusion.IDENTICAL)
        self.assertEqual(path.read_bytes(), raw)

    def test_sf01_jsonc_settings_with_trailing_commas(self):
        """SF-01."""
        path = self.put(".claude/settings.json",
                        '{\n  // settings\n  "permissions": {\n    "deny": ["Read(./x/**)",],\n  },\n}\n')
        fusion.apply(self.tmp, [])
        text = path.read_text(encoding="utf-8")
        self.assertIn("// settings", text)
        deny = jsonc.loads(text).value["permissions"]["deny"]
        self.assertEqual(deny, ["Read(./x/**)", "Read(./factory-logs/**)", "Read(./.git/objects/**)"])

    def test_sf03_gitignore_crlf_kept(self):
        """SF-03."""
        path = self.put(".gitignore", "node_modules/\r\ndist/\r\n", newline="")
        expected = b"node_modules/\r\ndist/\r\nfactory-logs/\r\n.harness/runs/\r\n.harness/tmp/\r\n"
        fusion.apply(self.tmp, [])
        self.assertEqual(path.read_bytes(), expected)
        fusion.apply(self.tmp, [])
        self.assertEqual(path.read_bytes(), expected)

    def test_sf04_all_or_nothing_when_one_file_is_invalid(self):
        """SF-04: unreadable tasks.json -> neither settings.json nor .gitignore touched."""
        settings = self.put(".claude/settings.json", '{"model": "sonnet"}\n')
        self.put(".vscode/tasks.json", "{ broken")
        with self.assertRaises(fusion.FusionError):
            fusion.apply(self.tmp, ["node"])
        self.assertEqual(settings.read_text(encoding="utf-8"), '{"model": "sonnet"}\n')
        self.assertFalse((self.tmp / ".gitignore").exists())
        self.assertEqual(sorted(p.name for p in self.tmp.rglob("*") if p.is_file()), ["settings.json", "tasks.json"])

    def test_jsonc_parser_errors_and_strings(self):
        for bad in ("{ broken", '{"a": 1 /* ouvert', '{"a" 1}', "[1,, 2]", '{"a": 1} x'):
            with self.assertRaises(ValueError, msg=bad):
                jsonc.loads(bad)
        doc = jsonc.loads('{"u": "a/*b*/c//d", "e": "\\"x\\""}')
        self.assertEqual(doc.value, {"u": "a/*b*/c//d", "e": '"x"'})
        self.assertFalse(doc.loose)

    def test_sf04_non_utf8_file_named_and_all_or_nothing(self):
        """SF-04: an existing non-UTF-8 file (UTF-16 from a PowerShell tool) is
        refused by a message naming it with the expected encoding; nothing is written."""
        tasks = self.tmp / ".vscode" / "tasks.json"
        tasks.parent.mkdir(parents=True)
        tasks.write_bytes('{"version": "2.0.0"}\n'.encode("utf-16"))
        before = tasks.read_bytes()
        with self.assertRaises(fusion.FusionError) as ctx:
            fusion.apply(self.tmp, ["node"])
        self.assertIn("tasks.json", str(ctx.exception))
        self.assertIn("UTF-8", str(ctx.exception))
        self.assertEqual(tasks.read_bytes(), before)
        self.assertEqual([p.name for p in self.tmp.rglob("*") if p.is_file()], ["tasks.json"])
        res = helpers.harness(["sf", "apply", "--stacks", "node"], self.tmp)
        self.assertEqual(res.returncode, 2)
        self.assertIn("tasks.json", res.stderr)
        self.assertIn("UTF-8", res.stderr)
        self.assertNotIn("codec", res.stderr)
        gitignore = self.tmp / ".gitignore"
        gitignore.write_bytes("node_modules/\n".encode("utf-16"))
        tasks.unlink()
        with self.assertRaises(fusion.FusionError) as ctx:
            fusion.apply(self.tmp, [])
        self.assertIn(".gitignore", str(ctx.exception))
        agent = self.tmp / ".claude" / "agents" / "a.md"
        agent.parent.mkdir(parents=True)
        agent.write_bytes("prompt\n".encode("utf-16"))
        with self.assertRaises(fusion.FusionError) as ctx:
            fusion.install_file(agent, "prompt\n")
        self.assertIn("a.md", str(ctx.exception))

    def test_sf01_end_of_line_comment_stays_with_its_element(self):
        """SF-01: a // comment after the last item stays attached to that item;
        valid JSONC result and identical second application."""
        cases = [
            ('{\n  "permissions": {\n    "deny": [\n      "Read(./x)", // keep\n    ]\n  }\n}\n',
             '      "Read(./x)", // keep\n      "Read(./factory-logs/**)",\n'),
            ('{\n  "permissions": {\n    "deny": [\n      "Read(./x)" // keep\n    ]\n  }\n}\n',
             '      "Read(./x)", // keep\n      "Read(./factory-logs/**)",\n'),
            ('{\r\n  "permissions": {\r\n    "deny": [\r\n      "Read(./x)" /* a */, // keep\r\n    ]\r\n  }\r\n}\r\n',
             '      "Read(./x)" /* a */, // keep\r\n      "Read(./factory-logs/**)",\r\n'),
        ]
        for raw, expected in cases:
            path = self.put(".claude/settings.json", raw, newline="")
            fusion.apply(self.tmp, [])
            text = path.read_bytes().decode("utf-8")
            self.assertIn(expected, text, text)
            self.assertEqual(text.count("// keep"), 1)
            self.assertEqual(jsonc.loads(text).value["permissions"]["deny"],
                             ["Read(./x)", "Read(./factory-logs/**)", "Read(./.git/objects/**)"])
            before = path.read_bytes()
            self.assertEqual(fusion.apply(self.tmp, [])[".claude/settings.json"], fusion.IDENTICAL)
            self.assertEqual(path.read_bytes(), before)
        path = self.put(".claude/settings.json", '{\n  "model": "sonnet" // choice\n}\n')
        fusion.apply(self.tmp, [])
        text = path.read_text(encoding="utf-8")
        self.assertIn('  "model": "sonnet", // choice\n  "permissions": {', text)
        self.assertEqual(jsonc.loads(text).value["model"], "sonnet")


class InstallFileTest(helpers.TempDirCase):
    def test_sf04_states(self):
        """SF-04."""
        dest = self.tmp / ".claude" / "agents" / "factory-builder.md"
        self.assertEqual(fusion.install_file(dest, "prompt\n"), fusion.CREATED)
        self.assertEqual(fusion.install_file(dest, "prompt\n"), fusion.IDENTICAL)
        dest.write_text("customized prompt\n", encoding="utf-8")
        self.assertEqual(fusion.install_file(dest, "prompt\n"), fusion.KEPT)
        self.assertEqual(dest.read_text(encoding="utf-8"), "customized prompt\n")

    def test_d1_copy_core_with_version_and_no_bytecode(self):
        result = fusion.copy_core(helpers.CORE, self.tmp)
        states = result["files"]
        self.assertTrue(result["copied"])
        self.assertEqual(set(states.values()), {fusion.CREATED})
        harness_dir = self.tmp / ".claude" / "harness"
        self.assertTrue((harness_dir / "harness.py").is_file())
        init = (harness_dir / "hcore" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn('CORE_VERSION = "%s"' % CORE_VERSION, init)
        self.assertFalse(any("__pycache__" in k or k.endswith(".pyc") for k in states))
        self.assertEqual(set(fusion.copy_core(helpers.CORE, self.tmp)["files"].values()), {fusion.IDENTICAL})
        res = helpers.harness(["version"], self.tmp)
        self.assertEqual(res.returncode, 0)
        copied = subprocess.run([helpers.sys.executable, "-B", str(harness_dir / "harness.py"), "version"],
                                capture_output=True, text=True)
        self.assertEqual(json.loads(copied.stdout)["core_version"], CORE_VERSION)

    def test_d1_copied_core_run_without_B_leaves_no_bytecode(self):
        """D1: the versioned copy of the core, called without -B or PYTHONDONTWRITEBYTECODE
        (factory-run template), creates no __pycache__ in the repository."""
        helpers.init_repo(self.tmp)
        fusion.copy_core(helpers.CORE, self.tmp)
        helpers.git(self.tmp, "add", "-A")
        helpers.git(self.tmp, "commit", "-q", "-m", "core")
        env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}
        res = subprocess.run([sys.executable, str(self.tmp / ".claude" / "harness" / "harness.py"), "version"],
                             cwd=str(self.tmp), capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(helpers.git(self.tmp, "status", "--porcelain", "--untracked-files=all"), "")
        self.assertEqual(list(self.tmp.rglob("__pycache__")), [])

    def test_d1_copy_core_never_mixes_two_versions(self):
        """SF-04 / D1: a different existing core is neither overwritten nor completed."""
        fusion.copy_core(helpers.CORE, self.tmp)
        harness_dir = self.tmp / ".claude" / "harness"
        init = harness_dir / "hcore" / "__init__.py"
        old = init.read_text(encoding="utf-8").replace(CORE_VERSION, "0.9.0")
        init.write_text(old, encoding="utf-8")
        (harness_dir / "hcore" / "shippr.py").unlink()   # file that appeared in the new version
        result = fusion.copy_core(helpers.CORE, self.tmp)
        self.assertFalse(result["copied"])
        self.assertEqual(result["blocked_by"], [".claude/harness/hcore/__init__.py"])
        self.assertEqual(result["not_copied"], [".claude/harness/hcore/shippr.py"])
        self.assertEqual(result["files"][".claude/harness/hcore/__init__.py"], fusion.KEPT)
        self.assertFalse((harness_dir / "hcore" / "shippr.py").exists())
        self.assertEqual(init.read_text(encoding="utf-8"), old)


class BacklogFixesTest(helpers.TempDirCase):
    def test_d1_cli_blocked_copy_core_is_an_explicit_stop(self):
        """D1 (BACKLOG section 7): a blocked copy-core exits 1 with a clear
        message naming the files at fault, nothing copied."""
        fusion.copy_core(helpers.CORE, self.tmp)
        init = self.tmp / ".claude" / "harness" / "hcore" / "__init__.py"
        init.write_text(init.read_text(encoding="utf-8").replace(CORE_VERSION, "0.9.0"), encoding="utf-8")
        res = helpers.harness(["sf", "copy-core", "--root", "."], self.tmp)
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn("STOP: core not copied", res.stderr)
        self.assertIn(".claude/harness/hcore/__init__.py", res.stderr)
        self.assertFalse(json.loads(res.stdout)["copied"])

    def test_sf04_transit_file_removed_once_read_even_on_failure(self):
        """SF-04 (BACKLOG section 7): the transit file of sf install does not stay
        in .harness/runs/, install succeeded or not; a file elsewhere is kept."""
        src = helpers.write_output(self.tmp, "factory-planner.md", "prompt\n")
        res = helpers.harness(["sf", "install", "--dest", ".claude/agents/factory-planner.md", "--source", src],
                              self.tmp)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertFalse((self.tmp / src).exists())
        (self.tmp / ".claude" / "agents" / "blocked.md").mkdir(parents=True)
        src = helpers.write_output(self.tmp, "blocked.md", "prompt\n")
        res = helpers.harness(["sf", "install", "--dest", ".claude/agents/blocked.md", "--source", src], self.tmp)
        self.assertNotEqual(res.returncode, 0)
        self.assertFalse((self.tmp / src).exists())
        self.assertEqual(list((self.tmp / ".harness" / "runs").iterdir()), [])
        outside = self.tmp / "keep.md"
        outside.write_text("mine\n", encoding="utf-8")
        res = helpers.harness(["sf", "install", "--dest", "ci/keep.md", "--source", "keep.md"], self.tmp)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(outside.exists())


class CliTest(helpers.TempDirCase):
    def test_sf04_cli_apply_install_copy_core(self):
        """SF-01 SF-04: the CLI called by software-factory."""
        res = helpers.harness(["sf", "apply", "--root", ".", "--stacks", "node,python"], self.tmp)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout)[".claude/settings.json"], fusion.CREATED)
        deny = json.loads((self.tmp / ".claude" / "settings.json").read_text(encoding="utf-8"))["permissions"]["deny"]
        self.assertIn("Read(./.venv/**)", deny)
        args = ["sf", "install", "--root", ".", "--dest", "ci/factory.sh", "--source", "-"]
        self.assertEqual(json.loads(helpers.harness(args, self.tmp, stdin="#!/usr/bin/env bash\n").stdout)["ci/factory.sh"],
                         fusion.CREATED)
        self.assertEqual(json.loads(helpers.harness(args, self.tmp, stdin="other\n").stdout)["ci/factory.sh"],
                         fusion.KEPT)
        res = helpers.harness(["sf", "copy-core", "--root", "."], self.tmp)
        self.assertEqual(json.loads(res.stdout)["core_version"], CORE_VERSION)
        bad = helpers.harness(["sf", "apply", "--stacks", "cobol"], self.tmp)
        self.assertEqual(bad.returncode, 2)


class GitignoreExcludesRunsTest(helpers.TempDirCase):
    def test_ca11_generated_gitignore_hides_runs(self):
        helpers.init_repo(self.tmp)
        fusion.apply(self.tmp, [])
        run = runs.create(self.tmp, "factory-run", {})
        journal.append(run, journal.make_record("factory-builder", "inherit", 1, 1, "DONE"))
        registry.Registry(runs.registry_path(run)).add(helpers.finding(), "REJECTED")
        status = helpers.git(self.tmp, "status", "--porcelain", "--untracked-files=all")
        self.assertNotIn(".harness/runs", status)
        self.assertIn(".gitignore", status)


RGKB_READ = ["mcp__rgkb__search", "mcp__rgkb__definition", "mcp__rgkb__symbols"]
SETTINGS_JSONC = ('{\r\n  // project settings\r\n  "permissions": {\r\n    "allow": ["Bash(npm test)"], // kept\r\n'
                  '  },\r\n  "enabledMcpjsonServers": [],\r\n}\r\n')


class GenericEngineTest(helpers.TempDirCase):
    def ops(self):
        return [fusion.op_array_union(("permissions", "allow"), ["Read(./x)"] + ["Bash(py .claude/rgkb/rgkb.py *)"]),
                fusion.op_array_union(("enabledMcpjsonServers",), ["rgkb"]),
                fusion.op_add_key(("mcpServers",), "rgkb", {"command": "py", "args": ["-3", "rgkb.py"]})]

    def test_fu01_array_union_add_key_tasks(self):
        """FU-01: array union at a path, key added if absent (never overwritten),
        tasks deduplicated by label and inputs by id."""
        data, kept = fusion.merge_json({"a": {"list": [1]}, "k": {"x": 1}}, [
            fusion.op_array_union(("a", "list"), [1, 2, 2, 3]),
            fusion.op_add_key(("k",), "x", 2), fusion.op_add_key(("k",), "y", 3)])
        self.assertEqual(data, {"a": {"list": [1, 2, 3]}, "k": {"x": 1, "y": 3}})
        self.assertEqual(kept, ["k.x"])
        tasks = [{"label": "Index: search", "type": "shell", "command": "a"}]
        data, _ = fusion.merge_json({"tasks": [{"label": "Index: search", "command": "mine"}]}, [
            fusion.op_tasks([{"id": "rgkbQuery", "type": "promptString"}], tasks)])
        self.assertEqual(data["tasks"], [{"label": "Index: search", "command": "mine"}])
        self.assertEqual([i["id"] for i in data["inputs"]], ["rgkbQuery"])
        with self.assertRaises(fusion.FusionError):
            fusion.merge_json({"a": 1}, [fusion.op_array_union(("a", "b"), [1])])
        with self.assertRaises(fusion.FusionError):
            fusion.merge_json({"a": {"b": 1}}, [fusion.op_array_union(("a", "b"), [1])])

    def test_fu01_ca22_jsonc_kept_second_application_identical_and_kept_state(self):
        """FU-01, CA-22: JSONC comments, BOM and CRLF kept; a second application is
        byte for byte identical; an existing different 'rgkb' entry -> customized-kept."""
        path = self.tmp / ".claude" / "settings.json"
        path.parent.mkdir()
        path.write_bytes((jsonc.BOM + SETTINGS_JSONC).encode("utf-8"))
        plan = fusion.plan_json(path, self.ops())
        self.assertEqual(plan.state, fusion.MERGED)
        fusion.write_plans(self.tmp, {".claude/settings.json": plan})
        text = path.read_bytes().decode("utf-8")
        self.assertTrue(text.startswith(jsonc.BOM))
        self.assertIn("// project settings", text)
        self.assertIn("// kept", text)
        self.assertNotIn("\n", text.replace("\r\n", ""))
        data = jsonc.loads(text).value
        self.assertEqual(data["enabledMcpjsonServers"], ["rgkb"])
        self.assertEqual(data["permissions"]["allow"][0], "Bash(npm test)")
        before = path.read_bytes()
        self.assertEqual(fusion.plan_json(path, self.ops()), fusion.Plan(fusion.IDENTICAL, None, ()))
        self.assertEqual(path.read_bytes(), before)
        mcp = self.tmp / ".mcp.json"
        mcp.write_text('{"mcpServers": {"rgkb": {"command": "python3", "args": ["mine.py"]}}}\n', encoding="utf-8")
        kept = fusion.plan_json(mcp, [fusion.op_add_key(("mcpServers",), "rgkb", {"command": "py"})])
        self.assertEqual((kept.state, kept.text, kept.kept), (fusion.KEPT, None, ("mcpServers.rgkb",)))

    def test_fu01_all_or_nothing(self):
        """FU-01: an unreadable file fails the plan before anything is written."""
        (self.tmp / "good.json").write_text("{}", encoding="utf-8")
        (self.tmp / "bad.json").write_text("{", encoding="utf-8")
        with self.assertRaises(fusion.FusionError):
            {rel: fusion.plan_json(self.tmp / rel, self.ops()) for rel in ("good.json", "bad.json")}
        self.assertEqual((self.tmp / "good.json").read_text(encoding="utf-8"), "{}")

    @unittest.skipUnless(helpers.HAS_FIXTURES, helpers.SKIP_FIXTURES)
    def test_fu02_ca22_sf_on_fu01_output_unchanged_since_base1(self):
        """FU-02, CA-22: SF-01 to SF-03 expressed on FU-01; tests/fixtures/merge-expected
        unchanged since BASE1 (031df12) when git and that commit are available."""
        self.assertEqual(fusion.merge_settings(None, ["node"]),
                         {"permissions": {"deny": ["Read(./node_modules/**)", "Read(./dist/**)",
                                                   "Read(./factory-logs/**)", "Read(./.git/objects/**)"]}})
        self.assertEqual(list(fusion.merge_tasks(None)), ["version", "inputs", "tasks"])

    def test_fu03_generic_gitignore_lines(self):
        """FU-03: any line list, CRLF kept, no duplicate."""
        path = self.tmp / ".gitignore"
        path.write_bytes(b"lab-logs/\r\n")
        plan = fusion.plan_lines(path, ["lab-logs/", "experiments/results/", ".harness/runs/"])
        self.assertEqual((plan.state, plan.text), (fusion.MERGED, "lab-logs/\r\nexperiments/results/\r\n.harness/runs/\r\n"))
        self.assertEqual(fusion.plan_lines(self.tmp / "none", ["a/"]).state, fusion.CREATED)

    def test_fu04_install_file_for_every_generator(self):
        """FU-04: install_file creates, recognizes the identical, never overwrites."""
        dest = self.tmp / ".claude" / "skills" / "lab-run" / "SKILL.md"
        self.assertEqual(fusion.install_file(dest, "lab\n"), fusion.CREATED)
        self.assertEqual(fusion.install_file(dest, "lab\r\n"), fusion.IDENTICAL)
        self.assertEqual(fusion.install_file(dest, "other\n"), fusion.KEPT)
        self.assertEqual(dest.read_text(encoding="utf-8"), "lab\n")

    def test_fu05_tools_union_never_removes(self):
        """FU-05: tools of a limiting frontmatter completed, none removed; inline,
        bracket and block forms; no tools line = inherits all, unchanged."""
        agent = "---\nname: factory-planner\ntools: Read, Glob, Grep\n---\n\nBody tools: none\n"
        done = fusion.union_tools(agent, ["Grep"] + RGKB_READ)
        self.assertIn("tools: Read, Glob, Grep, mcp__rgkb__search, mcp__rgkb__definition, mcp__rgkb__symbols\n", done)
        self.assertTrue(done.endswith("Body tools: none\n"))
        self.assertEqual(fusion.union_tools(done, RGKB_READ), done)
        self.assertIn("tools: [Read, mcp__rgkb__search]",
                      fusion.union_tools("---\ntools: [Read]\n---\n", RGKB_READ[:1]))
        block = "---\r\nname: x\r\ntools:\r\n  - Read\r\n  - Grep\r\nmodel: haiku\r\n---\r\n"
        self.assertEqual(fusion.union_tools(block, ["Grep", "mcp__rgkb__search"]),
                         "---\r\nname: x\r\ntools:\r\n  - Read\r\n  - Grep\r\n  - mcp__rgkb__search\r\nmodel: haiku\r\n---\r\n")
        builder = "---\nname: factory-builder\n---\nBody\n"
        self.assertEqual(fusion.union_tools(builder, RGKB_READ), builder)
        with self.assertRaises(fusion.FusionError):
            fusion.union_tools("no frontmatter\n", RGKB_READ)

    def test_fu06_allowed_tools_union_bash_and_powershell(self):
        """FU-06: --allowedTools of ci/* scripts completed, none removed."""
        sh = ('#!/usr/bin/env bash\nset -euo pipefail\ncmd=(claude -p x)\n'
              'cmd+=(--allowedTools "Read,Glob,Bash(git *)")\n"${cmd[@]}"\n')
        ps1 = "$claudeArgs += @('--allowedTools', 'Read,Glob,Bash(git *)')\n"
        extra = ["Bash(git *)", "mcp__rgkb__search", "Bash(python .claude/rgkb/rgkb.py *)"]
        done, warnings = fusion.union_allowed_tools(sh, extra)
        self.assertEqual(warnings, [])
        self.assertIn('--allowedTools "Read,Glob,Bash(git *),mcp__rgkb__search,Bash(python .claude/rgkb/rgkb.py *)"',
                      done)
        self.assertEqual(fusion.union_allowed_tools(done, extra), (done, []))
        self.assertIn("'--allowedTools', 'Read,Glob,Bash(git *),mcp__rgkb__search,",
                      fusion.union_allowed_tools(ps1, extra)[0])
        self.assertEqual(fusion.union_allowed_tools("echo none\n", extra), ("echo none\n", []))
        with self.assertRaises(fusion.FusionError):
            fusion.union_allowed_tools(sh, ["Bash(x),Read"])
        script = self.tmp / "factory.sh"
        script.write_text(done, encoding="utf-8")
        plan = fusion.plan_text(script, lambda t: fusion.union_allowed_tools(t, extra)[0])
        self.assertEqual(plan.state, fusion.IDENTICAL)

    def test_fu06_value_holding_the_other_quote_is_reported(self):
        """FU-06, iteration 2: a value holding the other quote character, or an
        --allowedTools without a quoted value, is left unchanged and reported."""
        sh = 'cmd+=(--allowedTools "Read,Bash(echo \'x\')")\nother --allowedTools $TOOLS\n'
        ps1 = "$a += @('--allowedTools', 'Read,Bash(echo \"x\")')\n"
        for text in (sh, ps1):
            new, warnings = fusion.union_allowed_tools(text, ["mcp__rgkb__search"])
            self.assertEqual(new, text)
            self.assertTrue(any("quote character" in w for w in warnings), warnings)
        self.assertTrue(any("line 2" in w and "without a quoted value" in w
                            for w in fusion.union_allowed_tools(sh, ["x"])[1]))

    def test_fu05_empty_tools_quoted_value_and_trailing_comment(self):
        """FU-05, iteration 2: an empty tools: (YAML null, inherits every tool) stays
        empty; a quoted value is completed inside its quotes; a trailing YAML comment
        is kept after the list."""
        empty = "---\nname: x\ntools:\nmodel: haiku\n---\nBody\n"
        self.assertEqual(fusion.union_tools(empty, RGKB_READ), empty)
        self.assertEqual(fusion.union_tools("---\ntools:   \n---\n", RGKB_READ), "---\ntools:   \n---\n")
        self.assertEqual(fusion.union_tools('---\ntools: "Read, Grep"\n---\n', ["Grep", "mcp__rgkb__search"]),
                         '---\ntools: "Read, Grep, mcp__rgkb__search"\n---\n')
        self.assertEqual(fusion.union_tools("---\ntools: ['Read']\n---\n", ["mcp__rgkb__search"]),
                         "---\ntools: ['Read', 'mcp__rgkb__search']\n---\n")
        self.assertEqual(fusion.union_tools("---\ntools: Read, Grep  # read only\n---\n", ["mcp__rgkb__search"]),
                         "---\ntools: Read, Grep, mcp__rgkb__search  # read only\n---\n")
        self.assertEqual(fusion.union_tools("---\ntools: # inherits\n---\n", ["x"]), "---\ntools: # inherits\n---\n")
        block = "---\ntools:\n  - \"Read\"  # first\n---\n"
        self.assertEqual(fusion.union_tools(block, ["Read", "Grep"]),
                         "---\ntools:\n  - \"Read\"  # first\n  - \"Grep\"\n---\n")


if __name__ == "__main__":
    unittest.main()
