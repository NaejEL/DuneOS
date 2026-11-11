# Linter hook: HL-01 to HL-03.

import json
import sys
import unittest

import helpers
import phase4

LINTER_FAIL = ("import sys\nfor i in range(60):\n    print('error line %d ' % i + 'x' * 120)\nsys.exit(3)\n")


class LintTest(helpers.TempDirCase):
    def setUp(self):
        super().setUp()
        (self.tmp / "src").mkdir()
        (self.tmp / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        (self.tmp / "lint_fail.py").write_text(LINTER_FAIL, encoding="utf-8")
        (self.tmp / "lint_sleep.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        self.payload = {"tool_name": "Edit", "tool_input": {"file_path": "src/a.py"}, "session_id": "s1"}

    def configure(self, *entries):
        phase4.stamp(self.tmp, hooks={"lint": list(entries)})

    def test_hl03_without_configuration_the_hook_is_silent(self):
        """HL-03, CA-35: no hooks.lint -> exit 0 and an empty output."""
        res = phase4.hook("post-edit", self.payload, self.tmp)
        self.assertEqual((res.returncode, res.stdout.strip()), (0, ""))
        phase4.stamp(self.tmp, autonomy="gate")
        self.assertEqual(phase4.hook("post-edit", self.payload, self.tmp).stdout.strip(), "")

    def test_hl02_a_failing_linter_is_summarized_and_never_blocks(self):
        """HL-02, CA-35: exit 0, additionalContext with the exit code, 20 lines and 2000 characters at most."""
        self.configure({"glob": "**/*.py", "argv": [sys.executable, "lint_fail.py", "{file}"], "timeout_s": 20})
        res = phase4.hook("post-edit", self.payload, self.tmp)
        self.assertEqual(res.returncode, 0, res.stderr)
        context = json.loads(res.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(json.loads(res.stdout)["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertLessEqual(len(context), 2000)
        self.assertIn("exit 3", context)
        self.assertLessEqual(len(context.splitlines()), 21)

    def test_hook_reads_stdin_as_utf8_whatever_the_console_encoding(self):
        """HK-01: an accented path matches its glob even when the default encoding of stdin is not UTF-8."""
        (self.tmp / "dé").mkdir()
        (self.tmp / "dé" / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.configure({"glob": "dé/*.py", "argv": [sys.executable, "-c", "print('linted')"], "timeout_s": 20})
        payload = {"tool_name": "Edit", "tool_input": {"file_path": "dé/a.py"}, "session_id": "s1"}
        res = phase4.hook("post-edit", payload, self.tmp, env={"PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("linted", json.loads(res.stdout)["hookSpecificOutput"]["additionalContext"])

    def test_hl02_a_timeout_or_a_missing_linter_is_mentioned(self):
        """HL-02, CA-35: exit 0 with the mention."""
        self.configure({"glob": "src/*.py", "argv": [sys.executable, "lint_sleep.py", "{file}"], "timeout_s": 1})
        res = phase4.hook("post-edit", self.payload, self.tmp)
        self.assertEqual(res.returncode, 0)
        self.assertIn("timed out", json.loads(res.stdout)["hookSpecificOutput"]["additionalContext"])
        self.configure({"glob": "**/*.py", "argv": ["no-such-linter-anywhere", "{file}"], "timeout_s": 5})
        res = phase4.hook("post-edit", self.payload, self.tmp)
        self.assertEqual(res.returncode, 0)
        self.assertIn("not found", json.loads(res.stdout)["hookSpecificOutput"]["additionalContext"])

    def test_hl02_only_the_entries_that_match_the_file_run(self):
        """HL-02: a glob that does not match the edited file runs nothing."""
        self.configure({"glob": "**/*.js", "argv": [sys.executable, "lint_fail.py", "{file}"], "timeout_s": 5})
        self.assertEqual(phase4.hook("post-edit", self.payload, self.tmp).stdout.strip(), "")

    def test_hl01_entries_must_carry_the_file_placeholder(self):
        """HL-01: verification commands only: argv with {file}, never a rewrite."""
        from hcore import stamp
        with self.assertRaises(stamp.StampError):
            stamp.set_hooks_lint(self.tmp, [{"glob": "*.py", "argv": ["ruff", "check", "--fix"]}])
        self.assertTrue(stamp.set_hooks_lint(self.tmp, [{"glob": "*.py", "argv": ["ruff", "check", "{file}"]}])["written"])
        self.assertFalse(stamp.set_hooks_lint(self.tmp, [{"glob": "*.py", "argv": ["ruff", "check", "{file}"],
                                                         "timeout_s": 30}])["written"])


if __name__ == "__main__":
    unittest.main()
