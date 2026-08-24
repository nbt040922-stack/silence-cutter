# Bulk Channel Job Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manual Jobs-page workflow that scans multiple YouTube channels, selects one unseen high-view video per channel, and queues the resulting MANUAL jobs.

**Architecture:** Keep discovery in `lan_job_api.py` so the existing `yt-dlp` executable, job store, dedupe rules, and LAN queue remain authoritative. Add one Monitor adapter endpoint and a second mode in the existing manual-job view; Channels page remains untouched.

**Tech Stack:** Python standard library, existing `yt-dlp`, WPF/XAML, .NET 8, xUnit.

**Spec:** `D:/Silence_cutter/docs/superpowers/specs/2026-08-24-bulk-channel-job-discovery.md`

## Global Constraints

- Only change Jobs → Tạo Job mới and Manual LAN API.
- Do not change the Channels page or AUTO poller.
- Select at most one video per channel per scan.
- Scan only the last two years through the current time.
- Exclude video IDs already selected or already represented by a MANUAL job.
- Submit selected URLs sequentially through the existing `/jobs` behavior.
- Do not commit or push unless explicitly requested.

---

### Task 1: Backend discovery primitives

**Files:**
- Modify: `D:/Silence_cutter/lan_job_api.py`
- Test: `D:/Silence_cutter/tests/test_lan_job_api.py`

**Interfaces:**
- Add `discover_channel_jobs(channel_urls, now=None)` returning a JSON-safe batch result.
- Add `fetch_channel_candidates(channel_url, *, since, until)` returning normalized candidate records.
- Persist history under `SILENCE_CUTTER_DATA_DIR` as an atomic JSON file.

- [ ] Write tests for one-channel ranking, history exclusion, one-result-per-channel, and per-channel failure isolation.
- [ ] Run `pytest tests/test_lan_job_api.py -q` and confirm the new tests fail because the discovery functions are absent.
- [ ] Implement yt-dlp flat channel enumeration plus bounded metadata enrichment for candidates in the two-year window.
- [ ] Implement history load/save and MANUAL job dedupe checks.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Manual LAN discovery endpoint

**Files:**
- Modify: `D:/Silence_cutter/lan_job_api.py`
- Test: `D:/Silence_cutter/tests/test_lan_job_api.py`

**Interfaces:**
- Add `POST /discover-jobs` with `{ "channels": ["..."] }`.
- Return `{ "created": [...], "skipped": [...], "errors": [...], "total": n }`.

- [ ] Write a handler test for authenticated batch discovery and invalid payloads.
- [ ] Run the handler tests and confirm the endpoint test fails before routing exists.
- [ ] Route the endpoint through `discover_channel_jobs`, submitting URLs one at a time via `create_remote_job`.
- [ ] Ensure failures return per-channel details and never expose local command secrets.
- [ ] Run all Python tests.

### Task 3: Monitor adapter and view-model mode

**Files:**
- Modify: `D:/Silence_cutter/ContentOpsMonitor/src/ContentOps.Monitor/Services/Api/ServiceAdapters.cs`
- Modify: `D:/Silence_cutter/ContentOpsMonitor/src/ContentOps.Monitor/ViewModels/MainViewModel.cs`
- Test: `D:/Silence_cutter/ContentOpsMonitor/tests/ContentOps.Monitor.Tests/ManualJobViewModelTests.cs`
- Test: `D:/Silence_cutter/ContentOpsMonitor/tests/ContentOps.Monitor.Tests/ApiAdapterTests.cs`

**Interfaces:**
- Add `ManualLanAdapter.DiscoverJobsAsync(IReadOnlyList<string> channels, CancellationToken ct)`.
- Add view-model state for `ManualMode`, multiline `ChannelScoutInput`, result message, and `DiscoverChannelJobsCommand`.

- [ ] Add failing tests for command validation, multiline channel parsing, and adapter payload/response mapping.
- [ ] Run the focused .NET tests and confirm they fail before the new command and adapter method exist.
- [ ] Implement the minimal adapter and sequential UI command, preserving existing manual URL submission.
- [ ] Run the focused .NET tests and confirm they pass.

### Task 4: Jobs → Tạo Job mới UI

**Files:**
- Modify: `D:/Silence_cutter/ContentOpsMonitor/src/ContentOps.Monitor/MainWindow.xaml`
- Test: `D:/Silence_cutter/ContentOpsMonitor/tests/ContentOps.Monitor.Tests/ManualJobViewModelTests.cs`

- [ ] Add a compact mode switch for **Thủ công** and **Săn video theo kênh**.
- [ ] Add one multiline channel textbox and the manual scan button; do not add any UI to Channels page.
- [ ] Bind busy state, results, errors, and queued count without replacing the current preview card.
- [ ] Build and run the full .NET test suite.

### Task 5: Verification and packaging

**Files:**
- Modify: `D:/Silence_cutter/docs/API_ADAPTER_MAP.md`
- Modify: `D:/Silence_cutter/ContentOpsMonitor/docs/API_ADAPTER_MAP.md`

- [ ] Run Python tests and .NET tests with their exact counts.
- [ ] Run `git diff --check`.
- [ ] Build the WPF app and verify the exact EXE path.
- [ ] Confirm existing manual URL flow and Channels page behavior remain intact.
