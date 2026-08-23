from __future__ import annotations

import time
import signal

from silence_core.runtime_paths import CorePaths
from silence_core.supervisor import CoreSupervisor


def main() -> int:
    paths = CorePaths.from_environment()
    paths.ensure_data_layout()
    supervisor = CoreSupervisor(paths=paths)
    def stop(_signum, _frame):
        supervisor.stop_owned()
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, stop)
    result = supervisor.start()
    if not result.ready:
        # Keep the watchdog alive so scheduler/LAN health endpoints remain
        # available and failed children can be retried independently.
        (paths.log_root / "supervisor").mkdir(parents=True, exist_ok=True)
        (paths.log_root / "supervisor" / "startup.json").write_text(
            '{"status":"DEGRADED","failed_component":%r,"reason":%r}\n'
            % (result.failed_component, result.reason), encoding="utf-8",
        )
    while True:
        supervisor.watch_once()
        time.sleep(2.0)


if __name__ == "__main__":
    raise SystemExit(main())
