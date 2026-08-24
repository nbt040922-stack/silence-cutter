# CONTENTOPS MONITOR — IMAGE 2 FIDELITY REPORT

## Dashboard

- Macro layout: fixed 3-row Image 2 grid.
- KPI alignment: exactly 5 equal cards with shared height, icon treatment, and spacing.
- Services/chart row: compact Services summary at approximately 55% with a 7-day history panel at approximately 45%.
- Lower 3-panel row: Active Jobs approximately 46%, Recent Alerts and Watched Channels approximately 27% each.
- Spacing: shared 16px major rhythm with compact 8px inner gaps.
- Density: all core operational state fits in the normal desktop viewport.
- Empty space: intentional empty states only; no full-width raw tables.
- Visual hierarchy: layered page, sidebar, elevated cards, inner rows, badges.

## Sidebar

- 8 primary items: Dashboard, Kênh theo dõi, Jobs, Tạo Job mới, Services, Cảnh báo, Nhật ký, Cấu hình.
- Icons: consistent Segoe Fluent Icons family.
- Active state: rounded blue surface with left accent bar and brighter foreground.
- Jobs submenu removed: yes.
- System health: derived from live service snapshots and colored READY/DEGRADED/DOWN.

## Dashboard components

- KPI cards: real job counts, no fabricated trends.
- Services: all five live services, compact status/PID/CPU/RAM/uptime columns.
- 7-day chart: designed empty state because the current backend exposes no historical endpoint.
- Active Jobs: real active/queued jobs only.
- Recent Alerts: live alerts or designed empty state.
- Watched Channels: live channels with compact recent list.

## Other pages

- Channels: premium list rows with channel ID, subscription state, last event, error and action.
- Jobs: filters remain inside the page as a horizontal toolbar.
- Services: richer live service cards with secondary UNMANAGED metadata.
- Alerts: designed empty state and severity rows.
- Logs: wide viewer surface with toolbar and centered empty state.
- Settings: visual endpoint and monitor configuration summary.

## Real data and performance

- Real data preserved: services, jobs, channels, alerts, ports, PIDs and status values.
- Backend changes: none; API contracts frozen.
- CPU/RAM: no new continuous animations or heavy effects; existing polling remains unchanged.
- Idle metrics: `--` remains visible when the adapter does not expose process metrics.

## Visual QA

- Dashboard normal window: inspected live.
- Dashboard 1440-class desktop layout: inspected live at the current 1480×900 shell.
- Channels, Jobs, Services, Alerts, Logs, Settings: existing pages retained and build-verified after shell/layout changes.
- Sidebar accessibility tree: exactly 8 navigation items, no Jobs children.

## Verification

- Build: passed, 0 warnings, 0 errors.
- Tests: passed, 11/11.
- `git diff --check`: passed; existing line-ending warning only for `requirements-production.txt`.
- Final: PASS.

No commit or push performed.
