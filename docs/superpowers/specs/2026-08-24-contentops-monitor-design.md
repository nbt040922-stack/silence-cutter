# ContentOps Monitor Phase 1 Design

## Goal

Create a lightweight native Windows monitoring/control client named ContentOps Monitor. It observes and calls the existing localhost ContentOps services without embedding processing logic or stopping those services when the monitor exits.

## Scope

Phase 1 includes Dashboard, Jobs, Watched Channels, Manual Job, Services, Alerts, Logs, local configuration, system tray, Windows notifications, and safe service-control affordances. Installer creation and backend pipeline changes are out of scope.

## Architecture

Use a separate .NET 8 WPF project under `ContentOpsMonitor/`. The existing `desktop/` Tauri application remains untouched.

The application is organized around a small set of native components:

- `ApiClient`: timeout-safe JSON HTTP calls over `HttpClient`.
- Service adapters: endpoint-specific mapping for YT_NOTIFI, YTDOWNLOAD, Silence, Qwen, and Manual LAN.
- `HealthMonitor`: asynchronous 5-second polling, state transitions, and derived READY/DEGRADED/DOWN/UNKNOWN status.
- `MonitorStore`: local configuration and alert persistence under `%LOCALAPPDATA%\\ContentOps\\Monitor\\`.
- `MainViewModel`: dashboard/page state and commands.
- WPF views/styles: dark navy control-center UI with sidebar, metric cards, compact tables, and status colors.
- Tray/notification layer: minimize-to-tray, status icon, transition-only notifications, pause toggle, and exit without backend shutdown.

No third-party UI framework is required. Use WPF controls, resource dictionaries, `HttpClient`, `System.Text.Json`, `Process`, and Shell APIs where practical.

## Data flow

On startup, the monitor loads local config, renders all services as UNKNOWN, and begins polling immediately. Each poll independently calls service health endpoints. Failures are isolated per service and never crash the UI. Successful responses are mapped to health details, PID/CPU/RAM/uptime only when the backend or positively-owned process exposes them, and otherwise `--`.

YT_NOTIFI jobs and channels are refreshed when the dashboard/jobs/channels pages are active and on a modest refresh cadence. Manual Job posts to the existing LAN API only. No job processing or channel storage is duplicated locally.

## Confirmed API adapter map

| Service | Port | Endpoint | Purpose | Monitor use |
|---|---:|---|---|---|
| YT_NOTIFI | 8787 | `GET /health` | Service health | Service status |
| YT_NOTIFI | 8787 | `GET /api/status` | Watcher/config summary | Dashboard/system summary |
| YT_NOTIFI | 8787 | `GET /api/jobs` | Processing jobs | Jobs/dashboard metrics |
| YT_NOTIFI | 8787 | `GET /api/channels` | Watched channel state | Channels page |
| YT_NOTIFI | 8787 | `PATCH /api/channels/{channel_id}` | Enable/disable channel | Channel actions |
| YTDOWNLOAD | 8790 | `GET /health` | Bridge health | Service status |
| YTDOWNLOAD | 8790 | `POST /api/download-jobs` | Submit download bridge job | Reserved adapter/status details |
| YTDOWNLOAD | 8790 | `GET /api/download-jobs/{id}` | Read download job | Job enrichment when IDs match |
| Silence Scheduler | 8791 | `GET /health` | Scheduler/runtime health | Service status and degradation |
| Silence Scheduler | 8791 | `GET /status` | Queue/active job state | Active jobs/status |
| Silence Scheduler | 8791 | `GET /api/process-jobs/{id}` | Read process job | Job enrichment |
| Qwen | 8792 | `GET /health` | Worker readiness/model state | Service status |
| Manual LAN API | 8780 | `GET /health` | API health | Service status |
| Manual LAN API | 8780 | `GET /jobs` | Manual jobs | Jobs page; local auth when needed |
| Manual LAN API | 8780 | `POST /jobs` | Create manual job | Manual Job page |
| Manual LAN API | 8780 | `GET /jobs/{id}` | Read manual job | Job enrichment |

The API map will also explicitly record that `8787` returns a JSON array for jobs/channels, `8780 /jobs` returns `{ jobs: [...] }`, and unknown fields remain unavailable rather than being fabricated.

## Health and safety

TCP reachability alone is never treated as READY when `/health` exists. Qwen and Silence responses are interpreted using their actual readiness fields. A service can be reachable but DEGRADED.

Start/stop/restart controls are disabled by default and show `UNMANAGED SERVICE` unless a configured, positively identified ContentOps process can be matched by executable path and command line. The monitor never kills by port and never broadly terminates Python, Node, Electron, PowerShell, or FFmpeg processes.

Alerts are local records generated only on meaningful transitions or failed jobs. Notifications are emitted once for READY-to-DOWN and DOWN-to-READY transitions, subject to the pause/enable setting. Poll cycles do not create repeated notifications.

## UI

The main window uses a dark navy layout: fixed left navigation, top title/refresh area, metric cards, service table, active jobs, recent alerts, and watched channels. Pages reuse the same data services and show `--`/`UNKNOWN` for unavailable data. The chart is hidden unless historical data is actually exposed.

## Verification

Verify with build/tests plus a live smoke test against the five configured localhost ports: startup with services unavailable, health transitions, manual-job request validation, channel/job reads, and clean close/minimize behavior. Run `git diff --check`. Do not modify backend files and do not commit or push.
