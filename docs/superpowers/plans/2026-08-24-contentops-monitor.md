# ContentOps Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a native .NET 8 WPF ContentOps Monitor client at `D:\Silence_cutter\ContentOpsMonitor\` that monitors and safely calls the existing localhost services without owning their processing pipelines.

**Architecture:** A single WPF executable uses focused C# classes for configuration, HTTP adapters, health polling, local alerts, and view-model state. The UI uses native WPF controls and resource styles; service calls are asynchronous and resilient to timeouts, invalid JSON, and unavailable backends. Tray and notification behavior stays in the monitor process and never shuts down backend services.

**Tech Stack:** .NET 8, WPF, C#, `HttpClient`, `System.Text.Json`, native Windows notification/tray APIs where available, no Electron and no third-party UI framework.

**Spec:** `D:\Silence_cutter\docs\superpowers\specs\2026-08-24-contentops-monitor-design.md`

## Global Constraints

- Source root: `D:\Silence_cutter\ContentOpsMonitor\`.
- Target: Windows x64, .NET 8 WPF.
- Existing `desktop/` Tauri app and all backend service files remain untouched.
- Default ports are 8787, 8790, 8791, 8792, and 8780 on `127.0.0.1`.
- Poll health every 5 seconds asynchronously; never block the UI thread.
- Use real API data only; unavailable values render `--` or `UNKNOWN`.
- Never kill arbitrary processes by port or broadly terminate Python, Node, Electron, PowerShell, or FFmpeg.
- Store monitor state under `%LOCALAPPDATA%\\ContentOps\\Monitor\\`.
- Do not create an installer, commit, or push.

### Task 1: Scaffold the native WPF project and testable domain models

**Files:**
- Create: `D:\Silence_cutter\ContentOpsMonitor\ContentOpsMonitor.sln`
- Create: `D:\Silence_cutter\ContentOpsMonitor\src\ContentOps.Monitor\ContentOps.Monitor.csproj`
- Create: `D:\Silence_cutter\ContentOpsMonitor\src\ContentOps.Monitor\App.xaml`
- Create: `D:\Silence_cutter\ContentOpsMonitor\src\ContentOps.Monitor\App.xaml.cs`
- Create: `D:\Silence_cutter\ContentOpsMonitor\src\ContentOps.Monitor\Models\MonitorModels.cs`
- Create: `D:\Silence_cutter\ContentOpsMonitor\tests\ContentOps.Monitor.Tests\ContentOps.Monitor.Tests.csproj`
- Create: `D:\Silence_cutter\ContentOpsMonitor\tests\ContentOps.Monitor.Tests\ModelTests.cs`

**Interfaces:**
- Produces `ServiceDefinition`, `ServiceSnapshot`, `ServiceState`, `MonitorAlert`, `JobRecord`, `ChannelRecord`, and `MonitorConfig` types used by every later task.

- [ ] **Step 1: Create the solution and project files** with `net8.0-windows`, `UseWPF=true`, `EnableWindowsTargeting=true`, nullable enabled, and x64-capable build settings.
- [ ] **Step 2: Write model tests** covering status enum values, default endpoint ports, and `MonitorConfig` default local-app-data path behavior.
- [ ] **Step 3: Implement the minimal models** as records/enums with JSON-friendly nullable properties and no backend-specific business logic.
- [ ] **Step 4: Run the model test project** and confirm it passes.

### Task 2: Implement configuration, API clients, and the API adapter map

**Files:**
- Create: `D:\Silence_cutter\ContentOpsMonitor\src\ContentOps.Monitor\Configuration\MonitorConfigStore.cs`
- Create: `D:\Silence_cutter\ContentOpsMonitor\src\ContentOps.Monitor\Services\Api\ApiClient.cs`
- Create: `D:\Silence_cutter\ContentOpsMonitor\src\ContentOps.Monitor\Services\Api\ServiceAdapters.cs`
- Create: `D:\Silence_cutter\ContentOpsMonitor\docs\API_ADAPTER_MAP.md`
- Create: `D:\Silence_cutter\ContentOpsMonitor\tests\ContentOps.Monitor.Tests\ApiAdapterTests.cs`

**Interfaces:**
- `ApiClient.GetJsonAsync<T>(Uri, CancellationToken)` returns `ApiResult<T>` without throwing for connection, timeout, HTTP, or JSON failures.
- `ServiceAdapters.GetHealthAsync(ServiceDefinition, CancellationToken)` returns `ServiceSnapshot`.
- `YtNotifiAdapter.GetJobsAsync`, `YtNotifiAdapter.GetChannelsAsync`, and `YtNotifiAdapter.SetChannelEnabledAsync` map confirmed endpoints.
- `ManualLanAdapter.GetJobsAsync` and `ManualLanAdapter.CreateJobAsync(string token, string url)` call the confirmed LAN API.

- [ ] **Step 1: Write adapter tests** using a fake `HttpMessageHandler` for successful health JSON, timeout/error mapping, YT_NOTIFI arrays, LAN `{jobs: [...]}`, and bearer-token POST.
- [ ] **Step 2: Run tests** and verify they fail because adapters do not exist.
- [ ] **Step 3: Implement `ApiClient`** with a 3-second default timeout, cancellation propagation, `System.Text.Json` case-insensitive parsing, and structured error results.
- [ ] **Step 4: Implement service adapters** using only confirmed endpoint paths and response fields; preserve raw unknown fields only when useful for display.
- [ ] **Step 5: Write `API_ADAPTER_MAP.md`** documenting service, port, endpoint, purpose, response fields, auth behavior, and monitor usage.
- [ ] **Step 6: Run adapter tests** and confirm all pass.

### Task 3: Add asynchronous health polling, transition alerts, and safe process metadata

**Files:**
- Create: `D:\Silence_cutter\ContentOpsMonitor\src\ContentOps.Monitor\Services\Health\HealthMonitor.cs`
- Create: `D:\Silence_cutter\ContentOpsMonitor\src\ContentOps.Monitor\Services\Alerts\AlertStore.cs`
- Create: `D:\Silence_cutter\ContentOpsMonitor\src\ContentOps.Monitor\Services\ProcessControl\OwnedProcessInspector.cs`
- Create: `D:\Silence_cutter\ContentOpsMonitor\tests\ContentOps.Monitor.Tests\HealthMonitorTests.cs`

**Interfaces:**
- `HealthMonitor.StartAsync()` begins a cancellable 5-second loop; `StopAsync()` cancels it.
- `HealthMonitor.SnapshotChanged` publishes `IReadOnlyList<ServiceSnapshot>`.
- `HealthMonitor.AlertRaised` publishes a `MonitorAlert` only for READY→DOWN, DOWN→READY, and backend-specific degradation transitions.
- `OwnedProcessInspector.TryGetOwnedProcess(ServiceDefinition)` returns nullable `OwnedProcessInfo`; it never matches by port alone.

- [ ] **Step 1: Write tests** for initial UNKNOWN state, health success → READY, connection failure → DOWN, recovery alert exactly once, and no repeated alert on unchanged DOWN.
- [ ] **Step 2: Run tests** and verify failure.
- [ ] **Step 3: Implement `HealthMonitor`** with independent per-service probes, linked cancellation, and status mapping that distinguishes reachable/degraded from ready.
- [ ] **Step 4: Implement `AlertStore`** as bounded local JSON persistence with severity/component/message/timestamp/resolved fields.
- [ ] **Step 5: Implement ownership checks** from explicit configured executable path/command-line identity only; expose unmanaged state otherwise and leave controls disabled.
- [ ] **Step 6: Run tests** and confirm transition behavior passes.

### Task 4: Build the dark navy WPF shell and dashboard pages

**Files:**
- Create: `D:\Silence_cutter\ContentOpsMonitor\src\ContentOps.Monitor\MainWindow.xaml`
- Create: `D:\Silence_cutter\ContentOpsMonitor\src\ContentOps.Monitor\MainWindow.xaml.cs`
- Create: `D:\Silence_cutter\ContentOpsMonitor\src\ContentOps.Monitor\ViewModels\MainViewModel.cs`
- Create: `D:\Silence_cutter\ContentOpsMonitor\src\ContentOps.Monitor\ViewModels\ViewModelBase.cs`
- Create: `D:\Silence_cutter\ContentOpsMonitor\src\ContentOps.Monitor\Styles\Theme.xaml`
- Create: `D:\Silence_cutter\ContentOpsMonitor\Views\DashboardView.xaml`
- Create: `D:\Silence_cutter\ContentOpsMonitor\Views\JobsView.xaml`
- Create: `D:\Silence_cutter\ContentOpsMonitor\Views\ChannelsView.xaml`
- Create: `D:\Silence_cutter\ContentOpsMonitor\Views\ManualJobView.xaml`
- Create: `D:\Silence_cutter\ContentOpsMonitor\Views\ServicesView.xaml`
- Create: `D:\Silence_cutter\ContentOpsMonitor\Views\AlertsView.xaml`
- Create: `D:\Silence_cutter\ContentOpsMonitor\Views\LogsView.xaml`

**Interfaces:**
- `MainViewModel.Navigate(string page)` changes the active page.
- `MainViewModel.RefreshAsync()` refreshes health and page data without fake fallbacks.
- View models expose `ObservableCollection<ServiceSnapshot> Services`, `Jobs`, `Channels`, and `Alerts`, plus metric strings.

- [ ] **Step 1: Add view-model tests** for metric aggregation from real job statuses and channel enabled mapping.
- [ ] **Step 2: Implement bindings and commands** around the adapter and health interfaces.
- [ ] **Step 3: Implement the shared WPF theme** with navy background, sidebar, cards, compact typography, and green/yellow/red status brushes.
- [ ] **Step 4: Implement dashboard layout** with five metric cards, service table, active jobs, recent alerts, and watched channels; hide the historical chart when no historical API exists.
- [ ] **Step 5: Implement Jobs, Channels, Manual Job, Services, Alerts, and Logs pages** with empty/error states, filters, safe actions, copy/open-folder affordances, and `--` for unavailable fields.
- [ ] **Step 6: Build the WPF project** and visually smoke-test startup with all services unavailable.

### Task 5: Add tray, Windows notifications, configuration UI, and safe service links

**Files:**
- Create: `D:\Silence_cutter\ContentOpsMonitor\src\ContentOps.Monitor\Services\Notifications\WindowsNotificationService.cs`
- Create: `D:\Silence_cutter\ContentOpsMonitor\src\ContentOps.Monitor\Services\Tray\TrayService.cs`
- Create: `D:\Silence_cutter\ContentOpsMonitor\src\ContentOps.Monitor\Views\ConfigurationView.xaml`
- Modify: `D:\Silence_cutter\ContentOpsMonitor\src\ContentOps.Monitor\MainWindow.xaml.cs`
- Modify: `D:\Silence_cutter\ContentOpsMonitor\src\ContentOps.Monitor\Services\Health\HealthMonitor.cs`

**Interfaces:**
- `WindowsNotificationService.NotifyTransition(ServiceSnapshot previous, ServiceSnapshot current)` suppresses unchanged-state notifications and respects `MonitorConfig.NotificationsEnabled`.
- `TrayService.SetStatus(IReadOnlyList<ServiceSnapshot>)` selects green/yellow/red and exposes Open, Services status, Pause notifications, and Exit Monitor commands.

- [ ] **Step 1: Test notification transition filtering** without requiring a live Windows toast session.
- [ ] **Step 2: Implement notification service** with the exact ContentOps Alert/Recovered titles and component/port text.
- [ ] **Step 3: Implement tray lifecycle** and window close-to-tray behavior; explicit Exit only closes Monitor.
- [ ] **Step 4: Add configuration controls** for poll interval, notification toggle, endpoints, and startup/minimize preferences; persist through `MonitorConfigStore`.
- [ ] **Step 5: Add original dashboard buttons** only for confirmed URLs `http://127.0.0.1:8787` and `http://127.0.0.1:8780`.
- [ ] **Step 6: Build and run a minimize/restore/exit smoke test** confirming backend processes remain untouched.

### Task 6: Verification and Phase 1 report

**Files:**
- Create: `D:\Silence_cutter\ContentOpsMonitor\CONTENTOPS_MONITOR_PHASE1_REPORT.md`
- Create: `D:\Silence_cutter\ContentOpsMonitor\scripts\smoke-test.ps1`

- [ ] **Step 1: Run all .NET tests** from `D:\Silence_cutter\ContentOpsMonitor`.
- [ ] **Step 2: Build Release x64** and record the output path.
- [ ] **Step 3: Run the smoke test** against ports 8787, 8790, 8791, 8792, and 8780, recording READY/DOWN/DEGRADED observations without changing backend state.
- [ ] **Step 4: Test manual-job validation** against the existing LAN API only when a token and URL are supplied; do not submit a fake job.
- [ ] **Step 5: Run `git diff --check`** from `D:\Silence_cutter` and confirm unrelated files are unchanged.
- [ ] **Step 6: Fill the Phase 1 report** with UI, API map, service health, transition notification, manual job/channel tests, resource observations, created/changed files, and PASS/BLOCKED/FAIL.
