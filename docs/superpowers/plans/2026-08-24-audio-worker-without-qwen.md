# Audio Worker Without Qwen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Qwen from the job-processing critical path and keep SenseVoiceSmall/FSMN-VAD warm in the long-lived Silence Scheduler on port 8791.

**Architecture:** Reuse one `production.pipeline.ProductionRuntime` instance inside the bridge instead of spawning `python -m production` per handoff. Its existing `SenseVoiceDetector` becomes the long-lived audio model holder. The bridge reports explicit audio warmup state and does not probe or require Qwen for readiness or job submission.

**Tech Stack:** Python 3.11, FunASR/ModelScope, existing `ProductionRuntime`, standard-library HTTP server, pytest.

**Spec:** `docs/superpowers/specs/2026-08-24-audio-worker-without-qwen-design.md`

## Global Constraints

- Do not call Qwen from the normal job pipeline.
- Keep processing concurrency at 1.
- Keep existing job/output/report contracts compatible.
- Keep Qwen model files on disk; do not delete them.
- Preserve renderer, formatter, title rewrite, output naming, and backend APIs except for readiness fields and Qwen-independent behavior.
- Do not modify UI structure.

---

### Task 1: Add failing tests for audio worker readiness and Qwen independence

**Files:**
- Modify: `tests/test_contentops_process_bridge.py`
- Modify: `tests/test_production_pipeline.py`

**Interfaces:**
- Tests will target `runtime_health`, `ContentOpsProcessBridge`, and the injected long-lived runtime boundary.

- [ ] **Step 1: Inspect existing bridge test fixtures and add a fake production runtime.**

  The fake must expose `warm() -> dict[str, Any]` and `process(source: Path, output_path: Path, report_path: Path) -> dict[str, Any]`, record `warm_calls` and `process_calls`, and write the requested report file when invoked.

- [ ] **Step 2: Write a failing test that health reports audio readiness without probing Qwen.**

```python
def test_health_is_ready_when_audio_worker_is_ready_even_if_qwen_is_down():
    health = runtime_health(
        host="127.0.0.1",
        port=8791,
        audio_probe=lambda: {"status": "READY", "error": None},
        qwen_probe=lambda: False,
    )
    assert health["status"] == "READY"
    assert health["audio_model_status"] == "READY"
    assert health["qwen_status"] == "DISABLED"
```

- [ ] **Step 3: Run the focused test and verify it fails because `runtime_health` has no audio probe contract.**

Run: `D:\Silence_cutter\.venv\Scripts\python.exe -m pytest tests/test_contentops_process_bridge.py::test_health_is_ready_when_audio_worker_is_ready_even_if_qwen_is_down -q`

Expected: FAIL with a missing `audio_probe` argument or missing `audio_model_status`.

- [ ] **Step 4: Write a failing test that the bridge reuses the same runtime for two jobs.**

```python
def test_bridge_reuses_warmed_audio_runtime_for_multiple_jobs(tmp_path):
    worker = FakeAudioAnalysisWorker()
    worker.warm()
    worker.process(Path("first.mp4"), tmp_path / "first.mp4", tmp_path / "first.json")
    worker.process(Path("second.mp4"), tmp_path / "second.mp4", tmp_path / "second.json")
    assert worker.warm_calls == 1
    assert worker.process_calls == 2
```

- [ ] **Step 5: Run the focused reuse test and verify it fails because the bridge has no persistent runtime injection.**

Run: `D:\Silence_cutter\.venv\Scripts\python.exe -m pytest tests/test_contentops_process_bridge.py::test_bridge_reuses_warmed_audio_runtime_for_multiple_jobs -q`

Expected: FAIL with the current subprocess-based implementation or missing constructor parameter.

---

### Task 2: Implement the long-lived audio runtime boundary

**Files:**
- Modify: `contentops_process_bridge.py:70-190, 205-225, 420-455`
- Modify: `production/pipeline.py:360-720` only where a reusable warm method is required
- Create: `audio_worker.py`

**Interfaces:**
- Produce `AudioAnalysisWorker.warm() -> dict[str, Any]`, `AudioAnalysisWorker.health() -> dict[str, Any]`, and `AudioAnalysisWorker.process(source: Path, report_path: Path, output_path: Path) -> dict[str, Any]`.
- The bridge owns exactly one worker instance and submits all processing through its single executor.

- [ ] **Step 1: Add the minimal warm/readiness adapter around the existing `ProductionRuntime` and its `SenseVoiceDetector`.**

  Warmup must instantiate `ProductionRuntime`, call the detector load path once, retain the runtime object, and record `READY` or `ERROR` with the exception text. Do not load Qwen.

- [ ] **Step 2: Make the bridge construct one worker at startup and expose its audio status.**

  The worker must be initialized before `restore()` accepts queued work. If warmup fails, keep the HTTP server available but do not process a job; return `AUDIO_MODEL_NOT_READY` with the recorded diagnostic.

- [ ] **Step 3: Replace `_pipeline` subprocess invocation in `_production_part_core` with the retained runtime call.**

  Preserve the existing report path, canonical job metadata, title rewrite, format planning, render, output list, and failure-stage mapping. Remove the `_apply_qwen_part_policy` call and mark enhanced selection as disabled/removed in the internal artifact without changing the public job response shape.

- [ ] **Step 4: Remove Qwen readiness from `runtime_health`, bridge constructor defaults, POST error mapping, and job submission validation.**

  Keep only a non-blocking `qwen_status: "DISABLED"` compatibility field if existing Monitor parsing expects it. The bridge must remain READY when 8792 is unavailable and audio is READY.

---

### Task 3: Complete the failing tests and add failure coverage

**Files:**
- Modify: `tests/test_contentops_process_bridge.py`
- Modify: `tests/test_production_pipeline.py`

**Interfaces:**
- Verify the public `/health` and `/api/process-jobs` behavior through the existing test HTTP handler.

- [ ] **Step 1: Run the tests from Task 1 after the implementation and verify they pass.**

Run: `D:\Silence_cutter\.venv\Scripts\python.exe -m pytest tests/test_contentops_process_bridge.py::test_health_is_ready_when_audio_worker_is_ready_even_if_qwen_is_down tests/test_contentops_process_bridge.py::test_bridge_reuses_warmed_audio_runtime_for_multiple_jobs -q`

Expected: PASS.

- [ ] **Step 2: Add and run a failing-then-passing test for warmup failure.**

Assert `audio_model_status == "ERROR"`, the health response is not falsely READY, and a submitted job returns `AUDIO_MODEL_NOT_READY` without calling Qwen.

- [ ] **Step 3: Add and run a test proving the report is created without Qwen.**

Use the fake runtime to write `pipeline_report.json`, make `qwen_probe` raise if called, and assert the bridge completes the handoff and returns all formatted output paths.

- [ ] **Step 4: Run the full focused Python suite.**

Run: `D:\Silence_cutter\.venv\Scripts\python.exe -m pytest tests/test_contentops_process_bridge.py tests/test_production_pipeline.py tests/test_job_runner.py -q`

Expected: zero failures.

---

### Task 4: Restart and verify the real runtime

**Files:**
- Modify: runtime state only; do not edit source files in this task

**Interfaces:**
- Real Silence Scheduler on `127.0.0.1:8791`; Qwen `8792` may be stopped and must not affect scheduler readiness.

- [ ] **Step 1: Stop only the current Silence Scheduler process tree and leave YT_NOTIFI/YTDOWNLOAD/LAN API untouched.**

- [ ] **Step 2: Start 8791 with `D:\Silence_cutter\.venv\Scripts\python.exe` and wait for `audio_model_status=READY`.**

- [ ] **Step 3: Verify `/health` reports audio READY, processing concurrency 1, and Qwen DISABLED.**

- [ ] **Step 4: Submit one short local-media smoke job and verify `pipeline_report.json` and output files exist.**

- [ ] **Step 5: Stop Qwen 8792 and repeat the health check; verify 8791 remains READY and a second smoke job still completes.**

- [ ] **Step 6: Run `git diff --check` and report exact test counts, health payload, and smoke-job artifacts.**
