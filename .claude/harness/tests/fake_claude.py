# Simulated `claude -p` child for the chaining tests (not a test module).
#
# HCORE_CLAUDE = [python, fake_claude.py] (HCORE_TEST_MODE=1). Every call is
# appended to the JSON-lines log named by FAKE_CLAUDE_LOG: argv, the relevant
# environment, the cwd and the start and end times. The behaviour comes from the
# scenario file named by FAKE_CLAUDE_SCENARIO: {"<spec file name>": {"action": ...}}
# with the actions merge | open | fail | stop | sleep | deliver (and "ship" keys
# for a child `ship-pr`). The git operations are real, on the repositories of the test.

import json
import os
import subprocess
import sys
import time
from datetime import datetime


def git(*args):
    return subprocess.run(["git"] + list(args), capture_output=True, text=True, encoding="utf-8")


def set_status(path, word):
    with open(path, "rb") as fh:
        raw = fh.read().decode("utf-8")
    lines = raw.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("Statut"):
            lines[i] = "Statut : " + word
            break
    with open(path, "wb") as fh:
        fh.write("\n".join(lines).encode("utf-8"))


def log(entry):
    path = os.environ.get("FAKE_CLAUDE_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")


def scenario_for(name):
    path = os.environ.get("FAKE_CLAUDE_SCENARIO")
    data = json.load(open(path, encoding="utf-8")) if path and os.path.exists(path) else {}
    return data.get(name) or data.get("*") or {"action": "merge"}


def write_stop():
    folder = os.path.join(os.getcwd(), ".harness", "runs")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "STOP-%s.md" % datetime.now().strftime("%Y%m%d-%H%M%S")), "w", encoding="utf-8") as fh:
        fh.write("# STOP — factory-run — gate\n\n## Why\nsimulated\n")


def main(argv):
    prompt = argv[argv.index("-p") + 1]
    skill, _, rest = prompt[1:].partition(" ")
    words = rest.split()
    spec = next((w for w in words if w.endswith(".md")), None)
    name = os.path.basename(spec) if spec else (words[0] if words else "")
    entry = {"argv": argv, "skill": skill, "args": rest, "cwd": os.getcwd(), "start": time.time(),
             "env": {k: os.environ.get(k) for k in ("CLAUDECODE", "HARNESS_CHAIN_ID", "HARNESS_PARENT_RUN")}}
    scenario = scenario_for(name)
    action = scenario.get("action", "merge")
    code = 0
    if action == "sleep":
        time.sleep(float(scenario.get("seconds", 30)))
    elif action == "fail":
        code = 1
    elif action == "stop":
        write_stop()
        code = 1
    elif skill == "factory-run":
        target = words[words.index("--ship") + 1] if "--ship" in words else "main"
        slug = os.path.splitext(name)[0].lower()
        time.sleep(float(scenario.get("seconds", 0)))
        for fname, content in scenario.get("files", {}).items():
            with open(fname, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
        if "--deliver-only" in words:
            set_status(spec, "LIVREE")
            with open("built-%s.txt" % slug, "w", encoding="utf-8") as fh:
                fh.write("built\n")
            git("add", "-A")
            git("commit", "-m", "feat(factory): " + slug)
        else:
            git("checkout", "-b", "factory/" + slug)
            set_status(spec, "LIVREE")
            git("add", "-A")
            git("commit", "-m", "feat(factory): " + slug)
            if action == "merge":
                git("push", "origin", "HEAD:" + target)
    elif skill == "ship-pr":
        target = words[0] if words else "main"
        time.sleep(float(scenario.get("seconds", 0)))
        if scenario.get("ship", action) == "merge":
            git("push", "origin", "HEAD:" + target)
    runs_dir = os.path.join(os.getcwd(), ".harness", "runs")      # the logs a real child leaves in its tree
    os.makedirs(runs_dir, exist_ok=True)
    with open(os.path.join(runs_dir, "%s-simulated.jsonl" % skill), "a", encoding="utf-8") as fh:
        fh.write("{}\n")
    entry["end"] = time.time()
    entry["exit"] = code
    log(entry)
    sys.stdout.write(json.dumps({"result": "simulated", "is_error": code != 0}))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
