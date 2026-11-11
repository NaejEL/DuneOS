# Module hcore.interp: interpreter detection (D5, CA14), with simulated
# executables (scripts launched by the tests' interpreter).

import os
import stat
import sys
import time
import unittest
from unittest import mock

import helpers
from hcore import interp


class InterpTest(helpers.TempDirCase):
    def stub(self, name, body):
        path = self.tmp / (name + ".py")
        path.write_text(body, encoding="utf-8")
        return [sys.executable, "-B", "-I", str(path)]

    def setUp(self):
        super().setUp()
        self.hang = self.stub("hang", "import time\ntime.sleep(30)\n")
        self.old = self.stub("old", "print('3.8.10')\n")
        self.ok = self.stub("ok", "print('3.9.13')\n")
        self.ok2 = self.stub("ok2", "print('3.12.1')\n")
        self.broken = self.stub("broken", "import sys\nsys.exit(9009)\n")

    def test_ca14_silent_candidate_skipped_without_blocking(self):
        start = time.monotonic()
        found = interp.detect([self.hang, self.ok], timeout=1.0)
        self.assertLess(time.monotonic() - start, 15)
        self.assertEqual(found["command"], self.ok)
        self.assertIsNone(found["tried"][0]["version"])

    def test_ca14_old_version_skipped(self):
        found = interp.detect([self.old, self.ok2], timeout=10)
        self.assertEqual(found["command"], self.ok2)
        self.assertEqual(found["version"], "3.12.1")

    def test_ca14_order_python_python3_py3(self):
        self.assertEqual(interp.CANDIDATES, [["python"], ["python3"], ["py", "-3"]])
        found = interp.detect([self.ok, self.ok2], timeout=10)
        self.assertEqual(found["command"], self.ok)
        found = interp.detect([self.broken, self.ok2, self.ok], timeout=10)
        self.assertEqual(found["command"], self.ok2)

    def test_ca14_default_order_probed(self):
        seen = []

        def fake_probe(cmd, timeout):
            seen.append(list(cmd))
            return {"python": None, "python3": (3, 8, 1), "py": (3, 9, 13)}[cmd[0]]

        with mock.patch.object(interp, "probe", side_effect=fake_probe):
            found = interp.detect()
        self.assertEqual(seen, [["python"], ["python3"], ["py", "-3"]])
        self.assertEqual(found["command"], ["py", "-3"])

        seen.clear()
        with mock.patch.object(interp, "probe", side_effect=lambda cmd, timeout: seen.append(cmd) or (3, 11, 0)):
            found = interp.detect()
        self.assertEqual(found["command"], ["python"])
        self.assertEqual(len(seen), 1)

    def fake_on_path(self, name, version):
        """Executable `name` placed in a dedicated directory, made the only PATH entry."""
        bindir = self.tmp / "bin"
        bindir.mkdir(exist_ok=True)
        if sys.platform == "win32":
            (bindir / (name + ".bat")).write_text("@echo %s\n" % version, encoding="ascii")
        else:
            path = bindir / name
            path.write_text("#!/bin/sh\necho %s\n" % version, encoding="ascii")
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return bindir

    def test_d5_candidate_resolved_by_path_like_command_v(self):
        """D5: same rule as the Bash loop of the SKILL.md files; the candidate is
        looked up in the PATH, never in the current interpreter's directory."""
        bindir = self.fake_on_path("python", "3.11.7")
        with mock.patch.dict(os.environ, {"PATH": str(bindir)}):
            found = interp.detect([["python"]], timeout=10)
        self.assertEqual(found["version"], "3.11.7")
        empty = self.tmp / "empty"
        empty.mkdir()
        with mock.patch.dict(os.environ, {"PATH": str(empty)}):
            self.assertIsNone(interp.detect([["python"], ["python3"]], timeout=10))
            self.assertIsNone(interp.resolve("python"))
        self.assertIsNone(interp.resolve("python", path=""))

    def test_missing_executable_and_none_found(self):
        self.assertIsNone(interp.probe(["hcore-absent-xyz"]))
        self.assertIsNone(interp.detect([self.old, ["hcore-absent-xyz"]], timeout=10))


if __name__ == "__main__":
    unittest.main()
