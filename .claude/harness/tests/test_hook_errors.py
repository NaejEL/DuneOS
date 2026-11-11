# Internal errors of the hooks and portability: HK-01, HK-04 (and the stdlib-only rule of CA-36).

import json
import os
import re
import unittest

import helpers
import phase4

STDLIB = {"importlib", "json", "os", "subprocess", "sys", "threading", "datetime"}


class ErrorTest(helpers.TempDirCase):
    PAYLOADS = {
        "pre-edit": {"tool_name": "Edit", "tool_input": {"file_path": "src/a.py"}, "session_id": "s1"},
        "pre-bash": {"tool_name": "Bash", "tool_input": {"command": "git status"}, "session_id": "s1"},
        "post-edit": {"tool_name": "Edit", "tool_input": {"file_path": "src/a.py"}, "session_id": "s1"},
    }

    def assert_reported(self, name, res, before=0):
        self.assertEqual(res.returncode, 0, "%s: %s" % (name, res.stderr))
        self.assertIn("systemMessage", res.stdout, name)
        self.assertIn("harness hook %s: internal error" % name, json.loads(res.stdout.splitlines()[0])["systemMessage"])
        log = (self.tmp / ".harness" / "runs" / "hook-errors.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertGreater(len(log), before, name)
        entry = json.loads(log[-1])
        self.assertEqual(entry["hook"], name)
        self.assertEqual(set(entry), {"ts", "hook", "tool", "error"})

    def test_hk04_stdin_that_is_not_json(self):
        """HK-04, CA-34: exit 0, systemMessage on stdout, a line in hook-errors.jsonl."""
        for name in self.PAYLOADS:
            self.assert_reported(name, phase4.hook(name, None, self.tmp, raw="this is not json"))

    def test_hk04_a_corrupt_marker(self):
        """HK-04, CA-34: an unreadable activation marker lets the action pass and is reported."""
        marker = self.tmp / ".harness" / "runs" / "active"
        marker.mkdir(parents=True)
        (marker / "s1.json").write_text("{corrupt", encoding="utf-8")
        for name, payload in self.PAYLOADS.items():
            if name == "post-edit":
                continue           # the linter hook reads no marker
            self.assert_reported(name, phase4.hook(name, payload, self.tmp))

    def test_hk04_git_absent(self):
        """HK-04, CA-34: git cannot run during a ship-pr context -> exit 0, reported."""
        from hcore import runs, scope
        run = runs.create(self.tmp, "ship-pr", {"status": "running", "target": "main"})
        scope.activate(self.tmp, "ship-pr", run.relative_to(self.tmp).as_posix(), "s1", "review", None, "main")
        res = phase4.hook("pre-edit", self.PAYLOADS["pre-edit"], self.tmp, env={"PATH": "", "HCORE_TEST_MODE": "1",
                                                                              "HCORE_GIT": json.dumps(["no-such-git-binary"])})
        self.assert_reported("pre-edit", res)

    def test_hk04_injected_failure(self):
        """HK-04, CA-34: HCORE_TEST_MODE=1 HCORE_HOOK_FAIL=1 on each hook."""
        for name, payload in self.PAYLOADS.items():
            res = phase4.hook(name, payload, self.tmp, env={"HCORE_TEST_MODE": "1", "HCORE_HOOK_FAIL": "1"})
            self.assert_reported(name, res)
        # The injection needs the test mode: without it the hook works normally.
        ok = phase4.hook("pre-bash", self.PAYLOADS["pre-bash"], self.tmp, env={"HCORE_HOOK_FAIL": "1"})
        self.assertEqual((ok.returncode, ok.stdout.strip()), (0, ""))

    def test_hk04_a_missing_core_is_an_internal_error_not_a_crash(self):
        """HK-04: a hook copied without hcore/ lets the action pass and reports (never a blocking traceback)."""
        import shutil
        project = self.tmp / "p"
        shutil.copytree(str(helpers.CORE / "hooks"), str(project / ".claude" / "harness" / "hooks"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        import subprocess
        import sys
        res = subprocess.run([sys.executable, "-B", str(project / ".claude" / "harness" / "hooks" / "hook.py"), "pre-bash"],
                             input=json.dumps(self.PAYLOADS["pre-bash"]), capture_output=True, text=True, timeout=60,
                             env=dict(os.environ, CLAUDE_PROJECT_DIR=str(project)))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("internal error", res.stdout)
        self.assertNotIn("Traceback", res.stderr)

    def test_hk01_the_subcommands_are_the_three_documented(self):
        """HK-01: pre-edit, pre-bash and post-edit; an unknown sub-command never blocks a tool."""
        res = phase4.hook("pre-nothing", {}, self.tmp)
        self.assertEqual(res.returncode, 0)


class PortabilityTest(unittest.TestCase):
    def test_ca36_the_hook_imports_only_the_standard_library(self):
        """CA-36: every import of hook.py is on the frozen list of standard modules."""
        text = phase4.HOOK.read_text(encoding="utf-8")
        found = set()
        for line in text.splitlines():
            m = re.match(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", line)
            if m and m.group(1) != "__future__":
                found.add(m.group(1).split(".")[0])
        self.assertEqual(found - STDLIB, set())
        self.assertTrue(found)

    def test_ca36_the_hook_does_not_use_syntax_above_python_39(self):
        """CA-36: compiles with the 3.9 grammar (no match statement, no `X | Y` annotations evaluated)."""
        compile(phase4.HOOK.read_text(encoding="utf-8"), str(phase4.HOOK), "exec")
        self.assertNotRegex(phase4.HOOK.read_text(encoding="utf-8"), r"(?m)^\s*match\s+\w+:")


if __name__ == "__main__":
    unittest.main()
