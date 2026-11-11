# Logging proxy for git (not a test module): HCORE_GIT = [python, fake_git.py] (HCORE_TEST_MODE=1).
# Every call is appended to the file named by FAKE_GIT_LOG, then the real git runs.

import json
import os
import subprocess
import sys


def main(argv):
    path = os.environ.get("FAKE_GIT_LOG")
    if path:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0))
        try:
            os.write(fd, (json.dumps(argv) + "\n").encode("utf-8"))      # one write: concurrent callers do not interleave
        finally:
            os.close(fd)
    return subprocess.run(["git"] + argv).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
