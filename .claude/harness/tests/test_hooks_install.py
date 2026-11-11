# Installation of the hooks in .claude/settings.json: HK-01, HK-02, HK-03, HK-06.
#
# HK-06 (software-factory installs the hooks and --upgrade regenerates them) is a property of the prose; the
# command it calls, `hooks install`, is tested here.

import json
import os
import subprocess
import sys
import unittest

import helpers
import phase4
from hcore import fusion, hooks_install

HOOKS = helpers.FIXTURES / "hooks"


def find_bash():
    for exe in ("C:/Program Files/Git/bin/bash.exe", "C:/Program Files (x86)/Git/bin/bash.exe", "bash"):
        try:
            if subprocess.run([exe, "-c", "exit 0"], capture_output=True, timeout=30).returncode == 0:
                return exe
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


class InstallTest(helpers.TempDirCase):
    def settings(self, text=None):
        path = self.tmp / ".claude" / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if text is not None:
            path.write_bytes(text.encode("utf-8"))
        return path

    @unittest.skipUnless(HOOKS.is_dir(), helpers.SKIP_FIXTURES)
    def test_hk02_merge_equals_the_expected_file_and_keeps_user_hooks_and_comments(self):
        """HK-02, CA-26: result == settings-expected.json, user hook and JSONC comments kept; second pass byte for byte."""
        path = self.settings((HOOKS / "settings-input.json").read_text(encoding="utf-8"))
        result = hooks_install.apply(self.tmp, "py -3", with_post=True)
        self.assertEqual(result[".claude/settings.json"], fusion.MERGED)
        text = path.read_text(encoding="utf-8")
        expected = (HOOKS / "settings-expected.json").read_text(encoding="utf-8")
        self.assertEqual(json.loads(text.replace("// permissions of the team", "")
                                    .replace("// generated", "")), json.loads(expected.replace("// permissions of the team", "")
                                                                               .replace("// generated", "")))
        self.assertEqual(list(json.loads(expected.replace("// permissions of the team", "").replace("// generated", ""))["hooks"]),
                         ["PreToolUse", "PostToolUse"])
        self.assertIn("// permissions of the team", text)
        self.assertIn("echo user-hook", text)
        again = hooks_install.apply(self.tmp, "py -3", with_post=True)
        self.assertEqual(again[".claude/settings.json"], fusion.IDENTICAL)
        self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_hk02_post_edit_only_when_a_linter_is_configured(self):
        """HK-02, HL-01: the PostToolUse entry exists only with `hooks.lint` in harness.json."""
        hooks_install.apply(self.tmp, "python")
        self.assertNotIn("PostToolUse", json.loads(self.settings().read_text(encoding="utf-8"))["hooks"])
        phase4.stamp(self.tmp, hooks={"lint": [{"glob": "**/*.py", "argv": ["ruff", "check", "{file}"], "timeout_s": 20}]})
        result = hooks_install.apply(self.tmp, "python")
        self.assertEqual(result["entries"]["post-edit"], fusion.CREATED)
        self.assertIn("PostToolUse", json.loads(self.settings().read_text(encoding="utf-8"))["hooks"])

    def test_hk02_a_harness_entry_edited_by_the_user_is_kept(self):
        """HK-02, CA-26: a different entry of the harness is `personnalisé-conservé`, never rewritten."""
        hooks_install.apply(self.tmp, "python")
        path = self.settings()
        data = json.loads(path.read_text(encoding="utf-8"))
        data["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] = 99
        path.write_text(json.dumps(data), encoding="utf-8")
        before = path.read_bytes()
        result = hooks_install.apply(self.tmp, "python")
        self.assertEqual(result["entries"]["pre-edit"], fusion.KEPT)
        self.assertEqual(path.read_bytes(), before)

    def test_hk07_the_command_is_the_same_on_every_machine(self):
        """HK-07: the interpreter is picked when the hook runs; installing from another D5 interpreter changes nothing."""
        hooks_install.apply(self.tmp, "python")
        before = self.settings().read_bytes()
        for py in ("py -3", "python3"):
            result = hooks_install.apply(self.tmp, py)
            self.assertEqual(result["entries"]["pre-bash"], fusion.IDENTICAL, py)
        self.assertEqual(self.settings().read_bytes(), before)
        command = hooks_install.command_for("py -3", "pre-bash")
        self.assertIn('for c in python python3 py; do a=; [ "$c" = py ] && a=-3; ', command)
        self.assertIn('sys.exit(sys.version_info < (3, 9))', command)
        self.assertIn('for c in /opt/venv/bin/python python python3 py; do',
                      hooks_install.command_for("/opt/venv/bin/python", "pre-bash"))
        self.assertIn("for c in '/c/My Venv/python.exe' python python3 py; do",
                      hooks_install.command_for('"/c/My Venv/python.exe"', "pre-bash"))

    def legacy_settings(self, py, timeout=10):
        def entry(matcher, name):
            command = ('f="$CLAUDE_PROJECT_DIR/.claude/harness/hooks/hook.py"; [ -f "$f" ] || exit 0; %s "$f" %s; '
                       'rc=$?; [ "$rc" -eq 2 ] && exit 2; exit 0' % (py, name))
            return {"matcher": matcher, "hooks": [{"type": "command", "command": command, "timeout": timeout}]}
        data = {"hooks": {"PreToolUse": [entry(hooks_install.EDIT_TOOLS, "pre-edit"),
                                         entry(hooks_install.SHELL_TOOLS, "pre-bash"),
                                         {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user-hook"}]}]}}
        return self.settings(json.dumps(data, indent=2))

    def test_hk07_an_entry_generated_with_a_fixed_interpreter_is_updated_in_place(self):
        """HK-07: the earlier generated entry (`py -3` written in it) is rewritten at its place; the user hook stays."""
        path = self.legacy_settings("py -3")
        result = hooks_install.apply(self.tmp, "python")
        self.assertEqual(result["entries"], {"pre-edit": hooks_install.UPDATED, "pre-bash": hooks_install.UPDATED})
        groups = json.loads(path.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
        self.assertEqual([g["matcher"] for g in groups], [hooks_install.EDIT_TOOLS, hooks_install.SHELL_TOOLS, "Bash"])
        self.assertEqual(groups[1]["hooks"][0]["command"], hooks_install.command_for("python", "pre-bash"))
        self.assertEqual(groups[2]["hooks"][0]["command"], "echo user-hook")
        self.assertEqual(hooks_install.apply(self.tmp, "py -3")[".claude/settings.json"], fusion.IDENTICAL)

    def test_hk07_an_earlier_entry_edited_by_the_user_is_still_kept(self):
        """HK-07, HK-02: an earlier entry with another change than its interpreter stays `personnalisé-conservé`."""
        path = self.legacy_settings("py -3", timeout=30)
        before = path.read_bytes()
        result = hooks_install.apply(self.tmp, "python")
        self.assertEqual(result["entries"]["pre-bash"], fusion.KEPT)
        self.assertEqual(path.read_bytes(), before)
        edited = self.legacy_settings("py -3 -X dev; echo")
        before = edited.read_bytes()
        self.assertEqual(hooks_install.apply(self.tmp, "python")["entries"]["pre-bash"], fusion.KEPT)
        self.assertEqual(edited.read_bytes(), before)

    def test_hk02_cli_with_a_lint_file(self):
        """HK-02, HL-01: `hooks install --lint-file` writes hooks.lint (consumed file) then merges the three entries."""
        src = helpers.write_output(self.tmp, "lint.json", json.dumps([{"glob": "**/*.py", "argv": ["ruff", "check", "{file}"],
                                                                      "timeout_s": 20}]))
        res = helpers.harness(["hooks", "install", "--root", str(self.tmp), "--py", "py -3", "--lint-file", src], self.tmp)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout)["entries"]["post-edit"], fusion.CREATED)
        self.assertFalse((self.tmp / src).exists())
        stamp = json.loads((self.tmp / ".claude" / "harness.json").read_text(encoding="utf-8"))
        self.assertEqual(stamp["hooks"]["lint"][0]["argv"], ["ruff", "check", "{file}"])
        bad = helpers.write_output(self.tmp, "lint.json", json.dumps([{"glob": "*.py", "argv": ["ruff"]}]))
        self.assertEqual(helpers.harness(["hooks", "install", "--root", str(self.tmp), "--py", "py", "--lint-file", bad],
                                         self.tmp).returncode, 2)

    def test_hk01_the_hook_script_is_part_of_the_core_copy(self):
        """HK-01: hooks/hook.py is copied under .claude/harness/hooks/ by copy-core."""
        res = helpers.harness(["sf", "copy-core", "--root", str(self.tmp)], self.tmp)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue((self.tmp / ".claude" / "harness" / "hooks" / "hook.py").is_file())


@unittest.skipUnless(find_bash(), "no working bash")
class CommandTest(helpers.TempDirCase):
    def run_command(self, name, payload, project, py):
        command = hooks_install.command_for(py, name)
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project))
        return subprocess.run([find_bash(), "-c", command], input=json.dumps(payload), capture_output=True, text=True,
                              env=env, timeout=60)

    def test_hk03_never_exit_2_by_accident(self):
        """HK-03, CA-27: no script in the project, or an interpreter that does not exist -> exit code other than 2."""
        payload = {"tool_name": "Bash", "tool_input": {"command": "git status"}, "session_id": "s"}
        empty = self.tmp / "empty"
        empty.mkdir()
        for name in hooks_install_names():
            res = self.run_command(name, payload, empty, "python")
            self.assertNotEqual(res.returncode, 2, name)
            res = self.run_command(name, payload, empty, "definitely-not-an-interpreter")
            self.assertNotEqual(res.returncode, 2, name)

    def test_hk03_a_wanted_blocking_still_exits_2(self):
        """HK-03: the exit code 2 of the hook is kept for a blocked command."""
        project = self.tmp / "p"
        (project / ".claude" / "harness").mkdir(parents=True)
        for item in ("hooks", "hcore"):
            src = helpers.CORE / item
            dest = project / ".claude" / "harness" / item
            import shutil
            shutil.copytree(str(src), str(dest), ignore=shutil.ignore_patterns("__pycache__"))
        payload = {"tool_name": "Bash", "tool_input": {"command": "git push --force"}, "session_id": "s"}
        res = self.run_command("pre-bash", payload, project, '"%s"' % sys.executable.replace("\\", "/"))
        self.assertEqual(res.returncode, 2, res.stderr)

    def guarded_project(self):
        project = self.tmp / "p"
        (project / ".claude" / "harness").mkdir(parents=True)
        import shutil
        for item in ("hooks", "hcore"):
            shutil.copytree(str(helpers.CORE / item), str(project / ".claude" / "harness" / item),
                            ignore=shutil.ignore_patterns("__pycache__"))
        return project

    def link(self, target, link):
        try:
            os.symlink(target, str(link))
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")

    def test_hk07_an_interpreter_below_the_floor_is_skipped(self):
        """HK-07, D5: a `python` that answers but fails the 3.9 floor is skipped for the next candidate."""
        project = self.guarded_project()
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        old = bin_dir / "python"
        old.write_text('#!/bin/sh\ncase "$*" in *version_info*) exit 1;; esac\nexit 0\n', encoding="utf-8")
        old.chmod(0o755)
        self.link(sys.executable, bin_dir / "python3")
        payload = {"tool_name": "Bash", "tool_input": {"command": "git push --force"}, "session_id": "s"}
        command = hooks_install.command_for("python3", "pre-bash")
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project), PATH=str(bin_dir) + os.pathsep + os.environ["PATH"])
        res = subprocess.run([find_bash(), "-c", command], input=json.dumps(payload), capture_output=True, text=True,
                             env=env, timeout=60)
        self.assertEqual(res.returncode, 2, res.stderr)

    def test_hk07_an_interpreter_path_with_spaces_stays_whole(self):
        """HK-07: a venv path containing a space is probed and run as one word."""
        project = self.guarded_project()
        spaced = self.tmp / "my venv"
        spaced.mkdir()
        self.link(sys.executable, spaced / "python")
        payload = {"tool_name": "Bash", "tool_input": {"command": "git push --force"}, "session_id": "s"}
        command = hooks_install.command_for(str(spaced / "python").replace("\\", "/"), "pre-bash")
        self.assertIn("'%s'" % str(spaced / "python").replace("\\", "/"), command)
        empty = self.tmp / "empty-path"
        empty.mkdir()
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project), PATH=str(empty))  # no fallback interpreter
        import shutil
        res = subprocess.run([shutil.which(find_bash()) or find_bash(), "-c", command], input=json.dumps(payload), capture_output=True, text=True,
                             env=env, timeout=60)
        self.assertEqual(res.returncode, 2, res.stderr)


def hooks_install_names():
    return [name for _event, _matcher, name, _timeout in hooks_install.SPECS]


if __name__ == "__main__":
    unittest.main()
