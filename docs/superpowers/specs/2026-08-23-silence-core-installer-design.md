# Silence Core One-Click Installer Design

**Status:** Proposed for implementation

**Goal:** Build `Silence_Core_Setup.exe` that installs a background-only
Silence Core on a clean Windows x64 machine, automatically installs a valid
Qwen payload outside Program Files, and starts the owned services in the
order `8792 -> 8791 -> 8780`.

## Scope and locked architecture

- `8791` is the only processing scheduler and the only normal caller of Qwen.
- `8780` is manual LAN submission/status only and delegates to `8791`.
- YT_NOTIFI AUTO traffic goes directly to `8791`; it never calls `8780`.
- AUTO and MANUAL jobs share one durable queue.
- Maximum active Silence jobs: `1`.
- Maximum active Qwen jobs: `1`.
- Existing pipeline, semantic analysis, VAD/SenseVoice analysis, intro/outro/ad
  removal, enhanced selection, formatter, renderer, reports, dedupe, retry,
  and output verification remain authoritative.
- The Desktop UI, Electron, and Tauri runtime are excluded.

## Installed layout

Immutable application files:

```text
C:\Program Files\ContentOps\SilenceCore\
  supervisor\
  scheduler\
  qwen\
  lan\
  pipeline\
  formatter\
  tools\ffmpeg.exe
  tools\ffprobe.exe
  manifests\
```

Mutable files:

```text
C:\ProgramData\ContentOps\SilenceCore\
  config\
  state\
  queue\
  logs\supervisor\
  logs\qwen\
  logs\scheduler\
  logs\lan\
  logs\pipeline\
  logs\installer\
  workspace\
  models\qwen2.5-vl-7b\
```

The model directory is preserved during normal application upgrades and
normal uninstall. A separate full-clean maintenance action removes it.

## Qwen model payload

The installer never embeds the full Qwen weights in the setup executable.
Instead it performs a deterministic model-install transaction:

1. Probe Windows architecture, NVIDIA adapter/driver/VRAM, available disk,
   and required runtime compatibility.
2. Select the configured model profile. The production 7B profile is the
   default for supported hardware.
3. Fetch an authoritative HTTPS manifest from the configured trusted release
   source. The manifest contains model id/revision, every required file,
   byte size, SHA256, download URL, and total required/free space.
4. Compare the existing payload against the manifest. A complete, matching
   payload produces `MODEL DOWNLOAD = SKIPPED`.
5. Download missing or invalid files to `*.part` files using HTTP Range
   requests where the server supports them. Existing partial bytes are resumed
   only when the response and manifest still match.
6. Verify each file size and SHA256 before atomic rename into the model
   directory. A failed verification deletes only the invalid temporary file.
7. Write an installed-manifest record only after every required file passes.
8. Refuse startup when the model is incomplete, mismatched, or corrupt.

The installer must show the following fields on failure and persist them in
the installer log:

```text
QWEN MODEL INSTALL FAILED
Reason:
Downloaded:
Expected:
Resume supported:
Log:
```

No Python, pip, Git, Hugging Face CLI, manual browser download, or manual
archive extraction is required. The source URL and checksum manifest are
configuration, not secrets; authentication cookies/tokens are never logged.

## Startup supervisor

One installed supervisor owns exact child PIDs and working directories. It
does not kill arbitrary `python.exe`, `ffmpeg.exe`, `chrome.exe`, or shell
processes.

Startup sequence:

```text
validate runtime/GPU/disk/model/ports
  -> start Qwen 8792
  -> poll Qwen health until model_loaded + GPU active + READY
  -> start scheduler 8791
  -> poll scheduler health until READY and enhanced_ready
  -> start manual API 8780
  -> poll manual health until READY and scheduler connectivity is healthy
  -> write SILENCE CORE READY state
```

When a child exits unexpectedly, the supervisor restarts only that child with
bounded exponential backoff and records the component, PID, exit code, and
log path. A clean exit remains stopped. Startup is blocked while a required
downstream component is not ready.

Before binding ports `8780`, `8791`, and `8792`, the supervisor reports the
owning PID/process. A foreign owner yields `PORT_CONFLICT`; an owned stale
process is reconciled using the persisted supervisor identity.

## Service health contracts

`8792` reports service, owned PID, model, GPU/VRAM when available, and
`STARTING`, `LOADING`, `READY`, or `FAILED`.

`8791` reports build/runtime, owned PID, queue depth, active job, Qwen state,
formatter/resource readiness, and scheduler concurrency.

`8780` reports service, READY state, owned PID, scheduler connectivity, and
manual queue information. It never exposes secrets or calls Qwen directly.

## Packaging

Use Inno Setup 6 to produce the one user-facing artifact
`Silence_Core_Setup.exe`. The installer may unpack a frozen onedir runtime and
internal helper executables; the one-file requirement applies to the delivered
installer only. Desktop UI files are excluded from the payload.

The installer performs model setup and startup validation before reporting
success. Failure displays:

```text
SILENCE CORE STARTUP FAILED
Component:
Reason:
Log path:
```

Autostart uses one Scheduled Task or equivalent supervisor entry. It is
idempotent and uses absolute Program Files/ProgramData paths, never developer
repository paths or system PATH tools.

## Acceptance tests

- Build artifact exists and contains no Desktop UI runtime.
- Clean Windows x64 install requires no developer dependencies.
- Model download skips a valid existing payload.
- Interrupted download resumes and invalid checksum blocks READY.
- Normal uninstall preserves the model; full-clean removes it.
- `8792`, then `8791`, then `8780` become READY after install and reboot.
- A real AUTO-like job completes through `8791` with Qwen, reports, and output.
- A real MANUAL job submitted to `8780` queues behind an active AUTO job,
  completes only after scheduler success, and produces verified files.
- No AUTO request reaches `8780`; no client reaches `8792`; only `8791` calls
  `8792`.
- Active Silence and Qwen concurrency never exceed one.
- Startup, model, port, watchdog, and processing failures persist to the
  component-specific ProgramData logs.

The final report must include exact installer size/SHA256, model strategy,
runtime requirements, health-gate results, clean-machine and reboot results,
network call counts, changed files, tests, and `git diff --check`. The feature
is not marked PASS until the clean Windows GPU installation and real processing
acceptance have both completed.
