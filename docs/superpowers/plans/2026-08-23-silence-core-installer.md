# Silence Core One-Click Installer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce `Silence_Core_Setup.exe` that installs a UI-free, self-contained Silence Core, automatically installs a verified Qwen payload, and starts `8792 -> 8791 -> 8780` with one shared queue.

**Architecture:** Reuse the existing `contentops_process_bridge.py`, `lan_job_api.py`, `backend.job_runner`, pipeline, formatter, and Qwen implementation. Add a small ProgramData-owned runtime/bootstrap layer around them: model payload management, owned-child supervision, health gates, and an Inno Setup package that contains a frozen onedir runtime but not Qwen weights. Keep AUTO on `8791`, MANUAL on `8780 -> 8791`, and expose Qwen only to `8791`.

**Tech Stack:** Python 3.11 frozen onedir runtime, standard-library HTTP/range/checksum code, existing PyTorch/Qwen/Silero/SenseVoice/FFmpeg/NVENC stack, Inno Setup 6, pytest.

**Spec:** `docs/superpowers/specs/2026-08-23-silence-core-installer-design.md`

## Global Constraints

- Do not package the Silence Cutter Desktop UI.
- Do not embed full Qwen weights in the installer.
- Qwen model path is `C:\ProgramData\ContentOps\SilenceCore\models\qwen2.5-vl-7b`.
- `8790` is not part of Silence Core; `8791` is the shared scheduler; `8792` is Qwen; `8780` is manual LAN only.
- AUTO and MANUAL share one queue; maximum active Silence jobs and Qwen jobs are both `1`.
- Only `8791` may call `8792`; clients and `8780` must never call Qwen directly.
- Normal uninstall preserves models; full-clean maintenance removes them.
- No Python, pip, Git, Node/npm, system FFmpeg, or manual Hugging Face steps on the target machine.
- Do not change YT_NOTIFI, YTDOWNLOAD, TikTok Publisher, or Desktop UI packaging.
- Do not commit or push generated installer binaries or model weights.

---

### Task 1: ProgramData runtime paths and immutable resource resolution

**Files:**
- Create: `silence_core/runtime_paths.py`
- Create: `silence_core/__init__.py`
- Modify: `silence_cutter/runtime_paths.py`
- Test: `tests/test_silence_core_runtime_paths.py`

**Interfaces:**
- `CorePaths.from_environment() -> CorePaths` returns immutable `install_root`,
  `data_root`, `model_root`, `log_root`, `state_root`, and bundled `ffmpeg`/
  `ffprobe` paths.
- `CorePaths.ensure_data_layout() -> None` creates only ProgramData directories.
- Existing pipeline path resolution continues to work in development and uses
  the frozen install root only when `SILENCE_CORE_PACKAGED=1`.

- [ ] **Step 1: Write failing tests** asserting packaged paths never contain the developer repo, state is under ProgramData, and bundled tools resolve before PATH tools.
- [ ] **Step 2: Run `python -m pytest tests/test_silence_core_runtime_paths.py -q` and verify the new API is missing.
- [ ] **Step 3: Implement `CorePaths` and the packaged-mode bridge with standard-library `pathlib` only.
- [ ] **Step 4: Re-run the focused tests and `python -m py_compile silence_core/runtime_paths.py`.

### Task 2: Manifest-driven resumable Qwen model installer

**Files:**
- Create: `silence_core/model_manager.py`
- Create: `installer/core_model_manifest.json`
- Modify: `installer/model_manifest.json`
- Test: `tests/test_silence_core_model_manager.py`

**Interfaces:**
- `ModelManifest.from_file(path) -> ModelManifest` validates model id, revision,
  required files, byte sizes, SHA256, and HTTPS URLs.
- `ModelManager.ensure_model(manifest) -> ModelInstallResult` returns
  `SKIPPED`, `DOWNLOADED`, or `FAILED` with downloaded/expected bytes,
  resume support, and log path.
- `ModelManager.check_model(manifest) -> ModelCheckResult` never downloads.
- `ModelManager.repair_model(manifest) -> ModelInstallResult` replaces only
  missing/invalid files.

- [ ] **Step 1: Write tests for valid-payload skip, partial `.part` resume, size/hash rejection, atomic completion, and disk-space refusal.
- [ ] **Step 2: Run the focused tests and verify they fail because the manager does not exist.
- [ ] **Step 3: Implement range download with `urllib.request`, `Content-Range` validation, streamed SHA256, and atomic rename; never log tokens/cookies.
- [ ] **Step 4: Add the user-facing failure fields exactly: `QWEN MODEL INSTALL FAILED`, `Reason`, `Downloaded`, `Expected`, `Resume supported`, `Log`.
- [ ] **Step 5: Run focused tests and verify an existing valid model returns `MODEL DOWNLOAD = SKIPPED`.

### Task 3: GPU/disk/port/runtime readiness checker

**Files:**
- Create: `silence_core/readiness.py`
- Modify: `backend/hardware.py`
- Test: `tests/test_silence_core_readiness.py`

**Interfaces:**
- `readiness_report(paths, manifest) -> dict` returns PASS/WARN/FAIL for Windows
  x64, GPU/driver/VRAM, CUDA/Torch, disk, bundled FFmpeg/FFprobe, model,
  formatter imports/resources, and ports `8780/8791/8792`.
- `find_port_owner(port) -> PortOwner` identifies PID/process without killing it.
- `require_startup_ready(report) -> None` raises a structured startup error.

- [ ] **Step 1: Add tests for missing GPU, insufficient disk, missing model, foreign occupied port, and all-pass report using injected probes.
- [ ] **Step 2: Run tests red.
- [ ] **Step 3: Implement probes using existing hardware helpers plus Windows `Get-NetTCPConnection`/socket fallback and bundled executable paths.
- [ ] **Step 4: Run focused tests and serialize a redacted report to ProgramData logs.

### Task 4: Owned Core supervisor and ordered startup

**Files:**
- Create: `silence_core/supervisor.py`
- Create: `silence_core/health_gate.py`
- Create: `silence_core/launcher.py`
- Test: `tests/test_silence_core_supervisor.py`

**Interfaces:**
- `CoreSupervisor.start() -> StartupResult` starts Qwen, waits for Qwen READY,
  starts `8791`, waits for scheduler READY, then starts `8780` and waits for
  scheduler connectivity.
- `CoreSupervisor.watch_once() -> list[RestartEvent]` reconciles exact owned
  PIDs and restarts only crashed children with bounded backoff.
- `CoreSupervisor.stop_owned() -> None` stops only children recorded by the
  supervisor identity file.
- `GET /health` contracts remain backward-compatible while adding PID,
  queue/Qwen connectivity, and readiness fields.

- [ ] **Step 1: Write tests for startup order, downstream-not-ready blocking, foreign port conflict, single-child restart, clean exit handling, and duplicate-start prevention.
- [ ] **Step 2: Run tests red.
- [ ] **Step 3: Implement exact command specs using `subprocess.Popen`, hidden Windows creation flags, per-component logs, identity/state JSON, and exponential backoff capped at 30 seconds.
- [ ] **Step 4: Add a `silence_core` CLI with `install-check`, `start`, `stop`, `status`, `check-model`, `repair-model`, and `update-model` subcommands.
- [ ] **Step 5: Run focused tests and a local dry-run with fake health providers.

### Task 5: Shared queue and manual API contract hardening

**Files:**
- Modify: `contentops_process_bridge.py`
- Modify: `lan_job_api.py`
- Modify: `backend/job_runner.py`
- Test: `tests/test_silence_core_queue_contract.py`

**Interfaces:**
- `8791` accepts `AUTO_YT_NOTIFI` and `MANUAL_LAN` origins into one durable
  scheduler queue.
- `8780` creates/reuses a deterministic manual job key and delegates to the
  scheduler; it never runs processing itself or calls Qwen.
- `SchedulerLimits(active_silence=1, active_qwen=1)` is enforced across both
  origins.

- [ ] **Step 1: Add tests for duplicate manual POST reuse, terminal-job no-reenqueue, explicit retry only, AUTO/MANUAL FIFO queueing, and max concurrency one.
- [ ] **Step 2: Run the new queue tests red against the current behavior.
- [ ] **Step 3: Implement the smallest contract changes while preserving existing state files, dedupe, retry, reports, and output verification.
- [ ] **Step 4: Assert call boundaries with an HTTP test: YT_NOTIFI uses `8791`, `8780` delegates to scheduler, and no client invokes `8792`.
- [ ] **Step 5: Run existing bridge/job-runner/LAN API tests plus the new contract suite.

### Task 6: Frozen Core runtime and Inno Setup installer

**Files:**
- Create: `installer/SilenceCore.iss`
- Create: `scripts/build_silence_core_installer.ps1`
- Create: `scripts/install_silence_core_autostart.ps1`
- Create: `scripts/uninstall_silence_core.ps1`
- Create: `scripts/silence_core_setup.py`
- Modify: `installer_setup/inventory.py`
- Test: `tests/test_silence_core_packaging.py`

**Interfaces:**
- Build output: `release/Silence_Core_Setup.exe`.
- Installed entrypoint: `silence_core\launcher.exe` or frozen launcher with
  `silence_core\launcher\` runtime, no Desktop UI files.
- Installer post-install invokes the model manager and startup health gate;
  it returns nonzero and displays component/log details on failure.

- [ ] **Step 1: Add packaging tests that inspect the staged payload for Desktop UI, developer paths, `.venv`, system Python, and model-weight inclusion.
- [ ] **Step 2: Run tests red against the existing Desktop installer layout.
- [ ] **Step 3: Build an onedir frozen runtime with PyInstaller/spec files and copy only production modules, bundled FFmpeg/FFprobe, fonts/resources, and runtime DLLs.
- [ ] **Step 4: Write Inno Setup entries for Program Files immutable files, ProgramData model/state/log paths, normal/full-clean uninstall behavior, and idempotent Scheduled Task autostart.
- [ ] **Step 5: Build the installer and record exact size/SHA256; do not commit the generated installer or model payload.

### Task 7: Windows acceptance and final report

**Files:**
- Create: `docs/SILENCE_CORE_INSTALLER_REPORT.md`
- Create: `scripts/validate_silence_core_install.ps1`
- Test: `tests/test_silence_core_acceptance.py`

- [ ] **Step 1: Run `git diff --check` and the complete relevant pytest suite.
- [ ] **Step 2: Install the generated EXE on a clean Windows x64 GPU machine with no Python/Git/Node/FFmpeg setup.
- [ ] **Step 3: Verify model download/skip, Qwen warm health, ordered `8792/8791/8780` readiness, and reboot autostart.
- [ ] **Step 4: Run one real AUTO-like job through `8791` and one real MANUAL job through `8780` while the first is active; verify queueing, single concurrency, processed files, reports, and final DONE authority.
- [ ] **Step 5: Write the report with installer hash, model strategy, health results, network call counts, path-leak audit, tests, and PASS/BLOCKED/FAIL. PASS is not allowed without clean-machine and reboot evidence.
