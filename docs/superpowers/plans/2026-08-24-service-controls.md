# ContentOps Service Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe, independent start/stop/restart/health controls for the five local ContentOps services and connect the existing Dashboard buttons.

**Architecture:** A loopback-only Python supervisor on port 8794 owns/adopts only verified service processes and exposes JSON health/control endpoints. Port 8793 is reserved by Windows HTTP API on this machine. The WPF Monitor calls that API; its existing health poll remains authoritative for service readiness and the Dashboard layout remains unchanged.

**Tech Stack:** Python standard library HTTP server and subprocess management; .NET 8 WPF, existing ApiClient, xUnit tests.

**Spec:** `docs/superpowers/specs/2026-08-24-contentops-monitor-design.md`

## Global Constraints

- Keep service control loopback-only on `127.0.0.1:8794`.
- Do not kill a process unless its PID, start time, and command marker match owned/adopted state.
- Do not change existing service APIs, job routes, or Dashboard layout.
- Do not commit or push.

---

### Task 1: Supervisor contracts and safety tests

**Files:**
- Create: `contentops_service_control.py`
- Create: `tests/test_contentops_service_control.py`

- [ ] Write failing tests for five definitions, ownership guard, and start/stop state transitions.
- [ ] Run the focused tests and verify they fail because the supervisor module is absent.
- [ ] Implement definitions, injected process/health dependencies, and JSON-safe state transitions.
- [ ] Run the focused tests and verify they pass.

### Task 2: Loopback HTTP service

**Files:**
- Modify: `contentops_service_control.py`
- Test: `tests/test_contentops_service_control.py`

- [ ] Add `GET /health`, `GET /api/services`, and `POST /api/services/{name}/{action}`.
- [ ] Return 404 for unknown services, 409 for unowned stop/restart, and structured state payloads.
- [ ] Add the Windows process identity check and owned process-tree termination.
- [ ] Run Python tests and `python -m py_compile contentops_service_control.py`.

### Task 3: Monitor API adapter and commands

**Files:**
- Modify: `ContentOpsMonitor/src/ContentOps.Monitor/Models/MonitorModels.cs`
- Modify: `ContentOpsMonitor/src/ContentOps.Monitor/Services/Api/ApiClient.cs`
- Create: `ContentOpsMonitor/src/ContentOps.Monitor/Services/Api/ServiceControlClient.cs`
- Modify: `ContentOpsMonitor/src/ContentOps.Monitor/ViewModels/MainViewModel.cs`
- Modify: `ContentOpsMonitor/src/ContentOps.Monitor/MainWindow.xaml`
- Test: `ContentOpsMonitor/tests/ContentOps.Monitor.Tests/ServiceControlClientTests.cs`

- [ ] Write failing client tests for start, restart, and health URL construction.
- [ ] Implement the client and ViewModel commands with refresh after actions.
- [ ] Bind the existing three Dashboard buttons to start, restart, and open health.
- [ ] Run the focused .NET tests.

### Task 4: Runtime startup and verification

**Files:**
- Modify: `ContentOpsMonitor/src/ContentOps.Monitor/ViewModels/MainViewModel.cs`
- Create: `scripts/start_contentops_service_control.ps1`

- [ ] Start or reuse the loopback supervisor when the Monitor initializes.
- [ ] Rebuild, publish, and verify the supervisor health plus all five service health probes.
- [ ] Run the complete Monitor test suite and `git diff --check`.
