# Internal RC Packaging Audit

This release candidate is self-contained for the four NVIDIA team PCs. It does not use developer paths or require coworkers to install Python, FFmpeg, packages, or models.

## Included

- Tauri desktop executable, compiled web UI, and hidden backend worker startup.
- Python 3.11.9 embedded runtime.
- Production Python modules: `backend`, `production`, `silence_cutter`, and `speech_detector`.
- Frozen `.venv_asr_test` site-packages, including PyTorch 2.11.0+cu130, torchaudio, FunASR 1.4.1, ModelScope, Silero VAD 6.2.1, NumPy, and yt-dlp.
- PyTorch CUDA runtime DLL dependencies supplied by its Windows wheel.
- SenseVoiceSmall model files and FSMN-VAD model files.
- FFmpeg and ffprobe 8.1.2.
- Fixed 240-second H.264/AAC hardware benchmark video.
- Microsoft Edge WebView2 offline installer.
- One-click Inno Setup installer plus SHA256 manifest for core payload files.

## External prerequisite

- A compatible NVIDIA display driver. It is intentionally not bundled.

## Runtime locations

- Installed code and dependencies resolve relative to `Silence Cutter.exe` under `resources`.
- Writable settings, logs, jobs, benchmark reports, and outputs live under `%LOCALAPPDATA%\SilenceCutter`.
- No source-tree, user-profile model-cache, or machine-specific FFmpeg path is used by the installed app.

## Release gate

This is an internal release candidate, not the final installer. The final installer remains blocked until one RTX 2080 Super and one RTX 3060 machine both pass the fixed benchmark and produce matching timeline hashes.

## One-click RC result

- File: `release/SilenceCutter-Internal-RC-Setup.exe`
- Size: 3,280,468,289 bytes (3.055 GiB)
- SHA256: `3f1ce947a3b0a060136e2865c63e9425b0a39b025aff234a668914362c79c868`
- Silent installation smoke test: PASS
- Installed Tauri UI launch: PASS
- Embedded Python backend worker launch: PASS
- Backend termination with app exit: PASS
- WebView2 handled inside installer: PASS
