# Module hcore.runs: inter-process lock of a run and atomic state writes
# (BACKLOG section 7, major defect 1: concurrent K reviewer / K validator calls
# lost updates, gave colliding ids and raised PermissionError on os.replace).

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

import helpers
from hcore import registry, runs, shippr

THEMES = ["logic", "regressions", "consistency", "naming"]

# Starts harness.py only once the gate file exists, so that every process
# reaches the state at the same moment (maximal contention).
GATED = (
    "import os, runpy, sys, time\n"
    "gate, harness = sys.argv[1], sys.argv[2]\n"
    "while not os.path.exists(gate):\n"
    "    time.sleep(0.001)\n"
    "sys.argv = [harness] + sys.argv[3:]\n"
    "runpy.run_path(harness, run_name='__main__')\n"
)

HOLD = (
    "import sys, time\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from hcore import runs\n"
    "with runs.locked(sys.argv[2]):\n"
    "    print('locked', flush=True)\n"
    "    time.sleep(float(sys.argv[3]))\n"
)


def _env():
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    return env


class ConcurrencyTest(helpers.TempDirCase):
    def parallel(self, calls):
        """Runs every harness call in its own process, all released together."""
        gate = self.tmp / ("gate-%d" % time.monotonic_ns())
        procs = [subprocess.Popen([sys.executable, "-B", "-c", GATED, str(gate), str(helpers.HARNESS)] + args,
                                  cwd=str(self.tmp), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, encoding="utf-8", env=_env())
                 for args in calls]
        time.sleep(0.5)
        gate.write_text("go", encoding="utf-8")
        results = []
        for proc in procs:
            out, err = proc.communicate(timeout=180)
            results.append((proc.returncode, out, err))
        return results

    def test_parallel_reviewers_and_validators_lose_nothing(self):
        """LK-01, CA-17: N parallel 'ship-pr reviewer' then 'ship-pr validator' processes on the
        same run: no colliding id, no lost theme, no exception (several trials)."""
        for trial in range(3):
            run = runs.create(self.tmp, "ship-pr", shippr.new_state("main", 3, THEMES))
            calls = []
            for i, theme in enumerate(THEMES):
                findings = [helpers.finding(file="src/%s.py" % theme, line=n, title="Bug %d of %s" % (n, theme))
                            for n in range(1, 4)]
                out = helpers.write_output(self.tmp, "reviewer-%d-%d.md" % (trial, i), helpers.reviewer_block(findings))
                calls.append(["ship-pr", "reviewer", "--run", str(run), "--theme", theme, "--model", "haiku",
                              "--duration-ms", "10", "--output", out])
            results = self.parallel(calls)
            ids = []
            for code, out, err in results:
                self.assertEqual(code, 0, err)
                self.assertNotIn("Traceback", err)
                ids.extend(f["id"] for f in json.loads(out)["to_validate"])
            self.assertEqual(len(ids), 12)
            self.assertEqual(len(set(ids)), 12, ids)
            state = runs.load_state(run)
            self.assertEqual(sorted(state["candidates"]), sorted(ids))
            self.assertEqual(state["rounds_data"]["1"]["themes"], {t: "ok" for t in THEMES})
            self.assertEqual(state["rounds_data"]["1"]["raw"], 12)
            out = helpers.write_output(self.tmp, "validator-%d.md" % trial, helpers.validator_block("REJECTED"))
            results = self.parallel([["ship-pr", "validator", "--run", str(run), "--finding", fid, "--model", "haiku",
                                      "--duration-ms", "5", "--output", out] for fid in ids])
            for code, _, err in results:
                self.assertEqual(code, 0, err)
                self.assertNotIn("Traceback", err)
            state = runs.load_state(run)
            self.assertEqual({c["status"] for c in state["candidates"].values()}, {"REJECTED"})
            self.assertEqual(state["rounds_data"]["1"]["rejected"], 12)
            reg = registry.Registry(runs.registry_path(run))
            self.assertEqual(len(reg.status), 12)
            self.assertEqual(len(runs.registry_path(run).read_text(encoding="utf-8").splitlines()), 12)
            self.assertEqual(len(run.read_text(encoding="utf-8").splitlines()), 4 + 12)


class LockTest(helpers.TempDirCase):
    def hold(self, run, seconds):
        proc = subprocess.Popen([sys.executable, "-B", "-c", HOLD, str(helpers.CORE), str(run), str(seconds)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_env())
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.communicate()))
        self.assertEqual(proc.stdout.readline().strip(), "locked")
        return proc

    def test_lock_held_by_another_process_times_out(self):
        """LK-01: beyond the timeout, an explicit stop (LockTimeout)."""
        run = runs.create(self.tmp, "ship-pr", {})
        self.hold(run, 30)
        with self.assertRaises(runs.LockTimeout):
            with runs.locked(run, timeout=0.3):
                pass

    def test_orphan_lock_released_when_its_holder_dies(self):
        """LK-01: a command killed while holding the lock never blocks the next one."""
        run = runs.create(self.tmp, "ship-pr", {})
        proc = self.hold(run, 60)
        proc.kill()
        proc.wait(timeout=30)
        started = time.monotonic()
        with runs.locked(run, timeout=10):
            pass
        self.assertLess(time.monotonic() - started, 5)

    def test_cli_waits_then_stops_with_exit_1_on_lock_timeout(self):
        run = runs.create(self.tmp, "ship-pr", shippr.new_state("main", 3, THEMES))
        self.hold(run, 30)
        with mock.patch.object(runs, "LOCK_TIMEOUT", 0.3):
            import harness
            with mock.patch.object(sys, "stderr", new=__import__("io").StringIO()) as err:
                code = harness.main(["begin", "--run", str(run), "--slot", "reviewer:logic"])
        self.assertEqual(code, 1)
        self.assertIn("locked", err.getvalue())

    def test_replace_retried_on_permission_error(self):
        """LK-02: under Windows, os.replace refused while the destination is open
        elsewhere is retried."""
        run = runs.create(self.tmp, "ship-pr", {"n": 0})
        real = os.replace
        failures = {"left": 3}

        def flaky(src, dst):
            if failures["left"]:
                failures["left"] -= 1
                raise PermissionError(13, "Access is denied", dst)
            return real(src, dst)

        with mock.patch.object(runs, "RETRY_REPLACE", True), mock.patch.object(runs, "REPLACE_DELAY", 0), \
                mock.patch.object(runs.os, "replace", side_effect=flaky):
            runs.save_state(run, dict(runs.load_state(run), n=1))
        self.assertEqual(runs.load_state(run)["n"], 1)
        with mock.patch.object(runs, "RETRY_REPLACE", True), mock.patch.object(runs, "REPLACE_DELAY", 0), \
                mock.patch.object(runs.os, "replace", side_effect=PermissionError(13, "denied")):
            with self.assertRaises(PermissionError):
                runs.save_state(run, {"n": 2})
        self.assertEqual(runs.load_state(run)["n"], 1)
        self.assertEqual(sorted(p.name for p in run.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
