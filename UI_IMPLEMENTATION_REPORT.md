# Silence Cutter Desktop Batch UI — Implementation Report

## Delivered

- Single-window dark Tauri 2 desktop application with a vanilla HTML/CSS/JavaScript frontend.
- Multi-URL input and persistent sequential queue (`max_concurrent_jobs = 1`).
- Statuses: `QUEUED`, `DOWNLOADING`, `ANALYZING`, `RENDERING`, `DONE`, `FAILED`, `CANCELLED`, and restart-only `INTERRUPTED`.
- Per-job Play, Open Folder, Retry, Cancel, Remove, and concise/advanced log actions.
- Real yt-dlp percentage during download. Analysis and render use indeterminate progress because the locked production CLI does not expose a trustworthy percentage.
- Per-job source, rendered intermediate, production report, command log, process log, and atomic `job.json` persistence under `workspace/jobs/<job_id>`.
- Configurable workspace and output folders. Concurrency is deliberately fixed at one.
- Runtime health indicators for NVIDIA GPU access, FFmpeg/ffprobe, yt-dlp, Python, the production entry point, and the cached SenseVoice model.
- Restart recovery marks active jobs `INTERRUPTED`. App shutdown terminates the worker subprocess tree.
- A worker process lock prevents two app instances from processing jobs concurrently.

## Backend boundary

`backend/job_runner.py` is the stable UI/backend boundary. The Tauri shell communicates with it using JSON requests over a short-lived stdin/stdout RPC process. A separate long-running Python worker claims queued jobs and invokes:

```text
python -m production <downloaded-source> -o <job>/rendered.mp4 --report <job>/pipeline_report.json --debug
```

The desktop layer does not import, duplicate, or alter the detector, fusion, content-boundary, timeline, or renderer algorithms. It only orchestrates the existing CLI.

## Reliability behavior

- JSON is written atomically to avoid partially persisted queue state.
- Output filenames are Windows-safe and collision-resistant.
- Cancel terminates the current process tree, including yt-dlp/FFmpeg descendants.
- Remove is disabled for active jobs and requires user confirmation.
- Folder settings cannot change while a job is queued or active.
- A completed job is marked `DONE` only after both production output and report exist and the final output copy succeeds.

## Acceptance coverage

The job-runner test submits three valid HTTP URLs, uses deterministic downloader/production substitutes at the orchestration boundary, verifies strictly sequential processing, verifies all three outputs, and reloads queue state from disk. Separate tests cover restart recovery and retry, invalid URL failure/removal, and the hard single-job concurrency setting. This keeps CI independent of network access, model inference, and a large media download.

Final verification on 2026-08-10:

- Python final full suite: `185 passed, 1 skipped, 57 subtests passed in 11.98s`.
- Frontend production build: passed (Vite 7.3.6).
- Rust compile check: passed.
- Tauri debug executable build: passed at `desktop/src-tauri/target/debug/silence-cutter-desktop.exe`.
- Tauri optimized Windows/NSIS build: passed at `desktop/src-tauri/target/release/bundle/nsis/Silence Cutter_0.1.0_x64-setup.exe`.
- Runtime health: GPU, FFmpeg, yt-dlp, Python pipeline, and SenseVoice cache all ready.
- Visual smoke test: main window and Settings dialog opened successfully; queue and health state rendered correctly.
- Windows UTF-8 regression: Japanese, Vietnamese, Korean, emoji, and mixed-script titles pass through yt-dlp metadata, JSON persistence, logs, RPC, and Windows-safe filename generation without `charmap`/`UnicodeEncodeError`.
- Downloader Manager V1: independent single-concurrency download/process lanes, one READY prefetch, persistent 55–70s cooldown, classified bounded retries, and anonymous downloads.

## Scope preserved

No production speech detector, model, threshold, fusion rule, intro/outro rule, tight2 timing policy, KEEP interval logic, or renderer setting was changed for this desktop phase.
