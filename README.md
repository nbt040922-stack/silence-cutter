# Silence Cutter

Silence Cutter is a local video production pipeline. The production detector and renderer remain the source of truth; the desktop application only downloads sources, manages a persistent queue, and starts that existing pipeline as a subprocess.

## Desktop batch application

### Prerequisites

- Windows with NVIDIA driver/GPU support expected by the production pipeline
- FFmpeg and ffprobe available on `PATH`
- Python 3.11 environment with the project dependencies and `yt-dlp`
- Node.js, npm, and Rust for development/building

Install the Python desktop dependency and frontend packages:

```powershell
cd D:\Silence_cutter
.\.venv_asr_test\Scripts\python.exe -m pip install -e ".[high-recall,desktop]"
cd desktop
npm install
```

### Development

```powershell
cd D:\Silence_cutter\desktop
npm run tauri dev
```

### Production build

```powershell
cd D:\Silence_cutter\desktop
npm run tauri build
```

The compiled application and installer are written below `desktop\src-tauri\target\release`. If the repository or Python environment is moved, set `SILENCE_CUTTER_ROOT` and `SILENCE_CUTTER_PYTHON` before launching the application.

### Operation

Paste one URL per line and select **Add to queue**. One downloader and one processor may overlap, while each lane remains strictly sequential:

```text
QUEUED -> DOWNLOADING -> READY -> ANALYZING -> RENDERING -> DONE
```

Failures and cancellations are retained and can be retried. Successful downloads wait 55–70 seconds before the next download, without pausing analysis/rendering, and at most one READY source may wait ahead. Queue state, reports, and logs are stored under `workspace\jobs\<job_id>`. Finished videos default to `outputs\<title>_done.mp4`; both folders can be changed in Settings while no jobs are queued or active.

On restart, unfinished jobs become `INTERRUPTED` instead of being silently resumed. The Retry action places them back in the queue. Closing the app terminates the worker and its current subprocess tree.

The header health indicators verify GPU access, FFmpeg, yt-dlp, the Python environment, the production entry point, and the locally cached SenseVoice model. A missing requirement is shown as `NOT READY`; it is never simulated.

## Tests

```powershell
cd D:\Silence_cutter
.\.venv\Scripts\python.exe -m pytest -q
cd desktop
npm run build
cd src-tauri
cargo check
```
# ContentOps local routing

- `:8780`: Manual LAN API only (`origin=MANUAL_LAN`)
- `127.0.0.1:8790`: YTDOWNLOAD
- `127.0.0.1:8791`: shared Silence Scheduler, FIFO, one processing slot
- `127.0.0.1:8792`: Qwen Worker, owned by the scheduler

Manual jobs are downloaded through `:8790` and then submitted to `:8791`; the LAN API never calls Qwen directly.
