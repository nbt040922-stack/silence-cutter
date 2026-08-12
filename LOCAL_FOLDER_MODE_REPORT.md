# Local Folder Mode Report

## Files changed

- `backend/job_runner.py`
- `desktop/index.html`
- `desktop/src/main.js`
- `desktop/src/styles.css`
- `tests/test_job_runner.py`
- `tests/test_downloader_manager.py`
- `LOCAL_FOLDER_MODE_REPORT.md`

## Default UI flow

`LOCAL_FOLDER` is the startup mode. Fresh settings use:

- Input: `D:\Vlog\Input`
- Output: `D:\Vlog\Output`

The main screen scans supported local files, shows their states, and queues stable `READY` files when the user selects **Start Processing**. YouTube URL and profile controls remain available only inside the collapsed **Advanced / Developer Tools** area.

## Folder watcher

Supported extensions are `.mp4`, `.mkv`, `.mov`, `.webm`, and `.m4v`. A file becomes `READY` only after:

1. size is unchanged across at least two scans;
2. modified time is unchanged across those scans;
3. both values remain stable for at least 7 seconds;
4. `ffprobe` returns a valid positive duration.

When `watch_input_folder` is enabled, the existing worker scans once per second and creates jobs only for newly stable files. Source files remain read-only and stay in the Input folder.

## Duplicate protection

Each source fingerprint is SHA-256 over:

`absolute normalized path + file size + modified-time nanoseconds`

The fingerprint is stored as `source_fingerprint` in `job.json`. Startup scans compare detected files with persisted jobs, so unchanged sources are not queued again. A genuinely replaced file at the same path receives a different fingerprint.

## Queue and state behavior

Local jobs start at `READY`; they never enter `DOWNLOADING`.

- `READY -> ANALYZING -> FORMATTING -> DONE`
- `READY -> ANALYZING -> NEEDS_REVIEW`
- `READY -> FAILED`

Detected files may temporarily show `STABILIZING` or `SKIPPED` before job creation. Unified ETA for local jobs contains analysis plus formatter render only.

## Persistence keys

Stored in `desktop-settings.json`:

- `input_mode = LOCAL_FOLDER`
- `input_folder`
- `output_folder`
- `watch_input_folder`
- `local_file_stability_seconds`

Scan observations are stored in `local-folder-scan.json`. Jobs retain `input_mode`, `original_source_path`, and `source_fingerprint`.

## Existing pipeline connection

Local ingestion creates a normal `READY` job whose `source_path` points at the original file. The existing worker sends it through the unchanged `_process_ready_job` entry point:

`SOURCE_PATH -> production analysis -> existing formatter planner -> Hybrid B renderer -> output folder`

No detector, timeline, splitter, formatter, crop, banner, audio, or output-naming logic is duplicated or changed by Local Folder mode.

## Validation

- Full Python regression suite: `285 passed, 1 skipped, 93 subtests passed`
- Desktop production build: PASS (`vite build`)
- Static Python compilation: PASS
- Git whitespace/error check: PASS
