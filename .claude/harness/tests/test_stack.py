# Module hcore.stack and the 'stack' CLI: markers, manifests, core stacks,
# artifact folders, size bounds (ST-01 to ST-05, CA-20).

import json
import unittest

import helpers
from hcore import fusion, stack

# One concrete file (or folder) name per ST-01 marker.
SAMPLES = {"*.sln": "App.sln", "*.csproj": "App.csproj", "pyproject.toml": "pyproject.toml",
           "requirements.txt": "requirements.txt", "setup.py": "setup.py", "environment.yml": "environment.yml",
           "*.ipynb": "explore.ipynb", "package.json": "package.json", "CMakeLists.txt": "CMakeLists.txt",
           "Makefile": "Makefile", "go.mod": "go.mod", "Cargo.toml": "Cargo.toml", "Dockerfile": "Dockerfile",
           "docker-compose*.yml": "docker-compose.dev.yml", "compose*.yml": "compose.yml",
           "ansible.cfg": "ansible.cfg", "playbooks/": "playbooks/", "roles/": "roles/", "*.tf": "main.tf"}


def make(root, rel):
    path = root / rel.rstrip("/")
    if rel.endswith("/"):
        path.mkdir(parents=True, exist_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")


class DetectTest(helpers.TempDirCase):
    def test_st01_ca20_every_marker_at_the_root_and_one_level_down(self):
        """ST-01, CA-20: each marker of the union is detected at the root and one
        level of subfolders, never deeper."""
        self.assertEqual(sorted(p for p, _, _ in stack.MARKERS), sorted(SAMPLES))
        for pattern, sample in SAMPLES.items():
            for prefix in ("", "sub/"):
                root = self.tmp / ("%s-%d" % (pattern.replace("*", "x").replace("/", ""), len(prefix)))
                make(root, prefix + sample)
                found = stack.detect(root)
                self.assertEqual(found["markers"], [pattern], (pattern, prefix))
                self.assertEqual(found["manifests"], [prefix + sample], (pattern, prefix))
            deep = self.tmp / ("deep-%s" % pattern.replace("*", "x").replace("/", ""))
            make(deep, "a/b/" + sample)
            self.assertEqual(stack.detect(deep)["markers"], [], pattern)

    def test_st02_core_stacks_feed_sf01(self):
        """ST-02: core stacks (node, dotnet, python, cpp, rust) in SF-01 order, families, manifests."""
        for rel in ("web/package.json", "api/App.csproj", "pyproject.toml", "Cargo.toml", "go.mod",
                    "infra/main.tf", "node_modules/pkg/package.json"):
            make(self.tmp, rel)
        found = stack.detect(self.tmp)
        self.assertEqual(found["stacks"], ["node", "dotnet", "python", "rust"])
        self.assertTrue(set(found["stacks"]) <= set(fusion.DENY_BY_STACK))
        self.assertEqual(sorted(found["families"]), ["dotnet", "go", "node", "python", "rust", "terraform"])
        self.assertNotIn("node_modules/pkg/package.json", found["manifests"])
        self.assertIn("api/App.csproj", found["manifests"])

    def test_st03_artifact_and_bulky_folders(self):
        """ST-03: union of legacy-audit:37 and code-index:49; those present are reported."""
        for name in ("node_modules", ".venv", "venv", "bin", "obj", "build", "dist", "target", "__pycache__",
                     ".git", "data", "checkpoints", "models", "wandb", "runs", "experiments/results",
                     "factory-logs", "lab-logs"):
            self.assertIn(name, stack.ARTIFACT_DIRS)
        for rel in ("node_modules/", "data/", "experiments/results/", "src/bin/", "src/main.py"):
            make(self.tmp, rel)
        self.assertEqual(stack.detect(self.tmp)["artifact_dirs"],
                         ["data/", "experiments/results/", "node_modules/", "src/bin/"])

    def test_st02_core_stacks_of_software_factory_as_at_base1(self):
        """ST-02, iteration 2: Makefile -> cpp, down to the SF-01 deny rules;
        environment.yml and *.ipynb give the python family but not the python
        stack (software-factory:31 at BASE1: pyproject, requirements, setup.py)."""
        make(self.tmp / "c", "Makefile")
        found = stack.detect(self.tmp / "c")
        self.assertEqual(found["stacks"], ["cpp"])
        self.assertIn("Read(./build/**)", fusion.deny_rules(found["stacks"]))
        for sample in ("environment.yml", "notebooks/explore.ipynb"):
            root = self.tmp / sample.replace("/", "-")
            make(root, sample)
            found = stack.detect(root)
            self.assertEqual((found["families"], found["stacks"]), (["python"], []), sample)
            self.assertNotIn("Read(./.venv/**)", fusion.deny_rules(found["stacks"]))
        for sample in ("pyproject.toml", "requirements.txt", "setup.py"):
            root = self.tmp / ("py-" + sample)
            make(root, sample)
            self.assertEqual(stack.detect(root)["stacks"], ["python"], sample)

    def test_st01_first_level_folders_with_an_artifact_name_are_searched(self):
        """ST-01, iteration 2: every first-level folder is searched for markers,
        an artifact name included (software-factory:31)."""
        for rel in ("build/package.json", "bin/App.csproj", "data/pyproject.toml"):
            make(self.tmp, rel)
        found = stack.detect(self.tmp)
        self.assertEqual(sorted(found["manifests"]), ["bin/App.csproj", "build/package.json", "data/pyproject.toml"])
        self.assertEqual(found["stacks"], ["node", "dotnet", "python"])


class BoundsTest(helpers.TempDirCase):
    def fill(self, n):
        for i in range(n):
            (self.tmp / ("d%d" % (i % 20))).mkdir(exist_ok=True)
            (self.tmp / ("d%d" % (i % 20)) / ("f%d.txt" % i)).write_bytes(b"")

    def test_st04_ca20_2000_files_not_sampled(self):
        """ST-04, CA-20: 2000 files outside artifacts -> sampled false (artifacts not counted)."""
        self.fill(2000)
        make(self.tmp, "node_modules/x/y.js")
        make(self.tmp, "experiments/results/r.json")
        self.assertEqual(stack.bounds(self.tmp), {"files": 2000, "sampled": False, "tree_depth": None,
                                                  "files_per_auditor": None})

    def test_st04_ca20_2001_files_sampled(self):
        """ST-04, CA-20: 2001 files -> sampled true, 3 levels, 40 files per auditor; CLI."""
        self.fill(2001)
        res = helpers.harness(["stack", "detect", "--root", str(self.tmp)], self.tmp)
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout)
        self.assertEqual((out["files"], out["sampled"], out["tree_depth"], out["files_per_auditor"]),
                         (2001, True, 3, 40))
        missing = helpers.harness(["stack", "detect", "--root", str(self.tmp / "nope")], self.tmp)
        self.assertEqual(missing.returncode, 1)
        self.assertIn("does not exist", missing.stderr)

    def test_st04_exclusions_any_depth_and_root_only(self):
        """ST-04, iteration 2: the folders of legacy-audit:37 are excluded at any
        depth; the bulky folders added by code-index:49 (models, data, runs...) at
        the root only."""
        for rel in ("node_modules/a.js", "src/node_modules/b.js", "src/__pycache__/c.pyc", "pkg/bin/d",
                    "models/m.bin", "data/d.csv", "runs/r.log", "experiments/results/r.json",
                    "src/models/user.py", "src/data/schema.sql", "src/runs/job.py", "src/main.py"):
            make(self.tmp, rel)
        self.assertEqual(stack.count_files(self.tmp), 4)

    def test_st05_long_journal_sampled(self):
        """ST-05: a journal of more than 1500 lines -> sampled, 15 last entries."""
        path = self.tmp / "JOURNAL.md"
        path.write_text("line\n" * 1500, encoding="utf-8")
        self.assertEqual(stack.journal_bounds(path)["sampled"], False)
        path.write_text("line\n" * 1501, encoding="utf-8")
        res = helpers.harness(["stack", "journal", "--file", str(path)], self.tmp)
        out = json.loads(res.stdout)
        self.assertEqual((out["lines"], out["sampled"], out["last_entries"]), (1501, True, 15))


if __name__ == "__main__":
    unittest.main()
