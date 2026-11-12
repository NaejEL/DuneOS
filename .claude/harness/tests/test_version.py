"""Phase 3, VS-01 to VS-04: version of the repository and lag of an installation."""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

import helpers
import upgrade_fixtures as uf
from hcore import CORE_VERSION, version
from helpers import FakeRunner, git


@unittest.skipUnless(uf.REPO_OK, uf.SKIP_REPO)
class VersionTest(helpers.TempDirCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo, self.shas = uf.make_repo(self.tmp, 3)

    def run_version(self, harness: Path) -> dict:
        res = subprocess.run([os.sys.executable, "-B", str(harness), "version"], capture_output=True, text=True,
                             encoding="utf-8")
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout)

    def test_vs01_repo_commit_describe_and_source(self):
        """VS-01: K version = {core_version, repo: {commit, describe, dirty, path}, source: git}."""
        out = self.run_version(self.repo / "skills" / "_core" / "harness.py")
        self.assertEqual(out["source"], "git")
        self.assertEqual(out["repo"]["commit"], git(self.repo, "rev-parse", "HEAD").strip())
        self.assertEqual(out["repo"]["describe"], git(self.repo, "describe", "--tags", "--always", "--dirty").strip())
        self.assertIs(out["repo"]["dirty"], False)
        self.assertEqual(Path(out["repo"]["path"]).resolve(), self.repo.resolve())

    def test_vs01_link_resolves_to_the_real_repository(self):
        """VS-01: through a junction (Windows) or a symbolic link, the same commit is found."""
        link = self.tmp / "linked-skills"
        target = self.repo / "skills"
        if os.name == "nt":
            made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
            if made.returncode != 0:
                self.skipTest("cannot create a junction")
        else:
            os.symlink(str(target), str(link))
        out = self.run_version(link / "_core" / "harness.py")
        self.assertEqual(out["repo"]["commit"], git(self.repo, "rev-parse", "HEAD").strip())

    def test_vs01_git_unavailable_never_fails(self):
        """VS-01: no git (or no answer): commit null, describe core-<version>, dirty null."""
        info = version.repo_info(self.repo, FakeRunner().on(("git",), code=127))
        self.assertEqual((info["commit"], info["describe"], info["dirty"]),
                         (None, "core-%s" % CORE_VERSION, None))
        env = {"HCORE_TEST_MODE": "1", "HCORE_GIT": json.dumps(["no-such-git-binary"])}
        res = helpers.harness(["version"], self.tmp, env=env)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIsNone(json.loads(res.stdout)["repo"]["commit"])

    def test_vs02_a_project_copy_reads_its_manifest(self):
        """VS-02: .claude/harness/harness.py version reads MANIFEST.json (source manifest)."""
        info = uf.repo_info(self.repo, describe="v-copy")
        root = self.tmp / "proj"
        uf.fresh_install(root, uf.make_profiles(self.tmp), info)
        out = self.run_version(root / ".claude" / "harness" / "harness.py")
        self.assertEqual(out["source"], "manifest")
        self.assertEqual(out["repo"]["describe"], "v-copy")
        self.assertEqual(out["repo"]["commit"], info["commit"])

    def test_vs03_lag_statuses(self):
        """VS-03: behind (+ commits), up_to_date, unknown with semver fallback, ahead, diverged."""
        head = self.shas[-1]
        lag = lambda inst, **kw: version.lag(inst, kw.pop("head", head), self.repo, **kw)    # noqa: E731
        self.assertEqual(lag(self.shas[0]), {"status": "behind", "commits": 2})
        self.assertEqual(lag(head), {"status": "up_to_date", "commits": 0})
        self.assertEqual(lag("f" * 40, installed_core="2.0.0"), {"status": "behind", "commits": None})
        self.assertEqual(lag("f" * 40, installed_core=CORE_VERSION), {"status": "up_to_date", "commits": None})
        self.assertEqual(lag(None), {"status": "unknown", "commits": None})
        self.assertEqual(lag(head, head=self.shas[0]), {"status": "ahead", "commits": None})
        git(self.repo, "checkout", "-q", "-b", "side", self.shas[0])
        (self.repo / "side.txt").write_text("x", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "side")
        side = git(self.repo, "rev-parse", "HEAD").strip()
        self.assertEqual(lag(side, head=head)["status"], "diverged")

    def test_vs04_core_version_is_3(self):
        """VS-04: CORE_VERSION = 4.0.3 (Phases 4 and 5, then SC-09, DT-05, HK-07, SP-20, RD-08)."""
        self.assertEqual(CORE_VERSION, "4.0.3")


if __name__ == "__main__":
    unittest.main()
