from __future__ import annotations

import os
import subprocess
import sys
import time


def restart_decision(exit_code: int, restart_count: int) -> tuple[bool, float]:
    """Return whether a crashed worker should restart and its backoff."""
    if exit_code == 0:
        return False, 0.0
    return True, min(30.0, float(2 ** min(max(restart_count, 0), 5)))


def main() -> None:
    command = [sys.executable, "-m", "qwen_worker.server"]
    flags = 0x08000000 if os.name == "nt" else 0
    restarts = 0
    while True:
        process = subprocess.Popen(command, creationflags=flags)
        code = process.wait()
        should_restart, delay = restart_decision(code, restarts)
        if not should_restart:
            raise SystemExit(code)
        restarts += 1
        time.sleep(delay)


if __name__ == "__main__":
    main()
