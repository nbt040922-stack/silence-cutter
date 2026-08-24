# ContentOps Monitor API Adapter Map

Discovered from the running services and their source contracts on 2026-08-24. The monitor uses only the paths listed here and displays `--` when a field is not exposed.

| Service | Port | Endpoint | Purpose | Response fields | Used by Monitor |
|---|---:|---|---|---|---|
| YT_NOTIFI | 8787 | `GET /health` | Health | `status`, `service`, `enabled_channels` | Service status |
| YT_NOTIFI | 8787 | `GET /api/status` | Watcher/config summary | `watcher`, `ytdlp`, `telegram`, `enabled_channels`, `last_new_video`, `config_error` | Dashboard summary |
| YT_NOTIFI | 8787 | `GET /api/jobs` | Processing jobs | array: `id`, `video_title`, `video_url`, `status`, `channel_name`, `created_at`, `updated_at`, download fields, error | Jobs and metrics |
| YT_NOTIFI | 8787 | `GET /api/channels` | Watched channel state | array: `channel_id`, `name`, `enabled`, `status`, `last_poll_at`, `last_success_at`, `failures` | Channels page |
| YT_NOTIFI | 8787 | `PATCH /api/channels/{channel_id}` | Enable/disable channel | channel payload | Channel actions |
| YTDOWNLOAD | 8790 | `GET /health` | Bridge health | `status` | Service status |
| YTDOWNLOAD | 8790 | `POST /api/download-jobs` | Submit download bridge job | job payload / error | Future job enrichment only; Monitor does not process jobs |
| YTDOWNLOAD | 8790 | `GET /api/download-jobs/{id}` | Read download job | job payload / error | Future job enrichment |
| Silence Scheduler | 8791 | `GET /health` | Runtime health | `status`, `readiness`, `qwen_health`, `active_job`, `waiting_jobs`, `queue`, runtime fields | Service status and degradation |
| Silence Scheduler | 8791 | `GET /status` | Queue status | active/queued processing state | Active jobs |
| Silence Scheduler | 8791 | `GET /api/process-jobs/{id}` | Read process job | process record | Job enrichment |
| Qwen | 8792 | `GET /health` | Worker readiness | `status`, `model_loaded`, `warmed_up`, error fields | Qwen service status |
| Manual LAN API | 8780 | `GET /health` | API health | `status`, `port` | Service status |
| Manual LAN API | 8780 | `GET /jobs` | Manual jobs | `{ "jobs": [...] }`, bearer token when remote auth applies | Jobs page |
| Manual LAN API | 8780 | `POST /jobs` | Create manual job | `{ "job_id": ... }` or job record, bearer token | Manual Job page |
| Manual LAN API | 8780 | `POST /discover-jobs` | Scan channels and enqueue one unseen high-view video per channel | `created`, `skipped`, `errors`, `total` | Jobs → Tạo Job mới |
| Manual LAN API | 8780 | `GET /jobs/{id}` | Read manual job | job record | Job enrichment |

## Notes

- All default URLs use `http://127.0.0.1:<port>`.
- Health requests use a short timeout and are independent; one unavailable service does not prevent the rest of the dashboard from updating.
- YT_NOTIFI `/api/jobs` and `/api/channels` return arrays directly. LAN `/jobs` wraps jobs in an object.
- No endpoint currently exposes a historical seven-day aggregate, so the dashboard hides that chart rather than generating values.
- No confirmed service start/stop ownership contract exists. Destructive service controls therefore remain disabled and show `UNMANAGED SERVICE`.
