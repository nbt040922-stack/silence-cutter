from __future__ import annotations

import os
import subprocess
import sys
import time


def main() -> None:
    command = [sys.executable, "-m", "qwen_worker.server"]
    flags = 0x08000000 if os.name == "nt" else 0
    restarts = 0
    while True:
        process = subprocess.Popen(command, creationflags=flags)
        code = process.wait()
        if code == 0 or restarts >= 1:
            raise SystemExit(code)
        restarts += 1
        time.sleep(1.0)


if __name__ == "__main__":
    main()
