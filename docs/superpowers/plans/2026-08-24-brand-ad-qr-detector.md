# Brand, Sponsor, QR and Advertisement Detector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a recall-first visual scan stage that detects personal branding, sponsor marks, QR codes and advertisement visuals, converts confirmed intervals into CUT regions, and reports incomplete scans explicitly.

**Architecture:** Keep the existing semantic cleaner and Qwen worker as the model path. Add a focused `brand_scan` package for typed detections, QR confirmation, temporal merging and artifact generation; production will invoke it after speech/content timeline creation and subtract its cut intervals from KEEP without changing Silero, SenseVoice, fusion or renderer behavior.

**Tech Stack:** Python 3.11+, dataclasses, Pillow/FFmpeg frame extraction, optional OpenCV QR detector, existing persistent Qwen worker, unittest/pytest.

**Spec:** `docs/superpowers/specs/2026-08-24-brand-ad-qr-detector-design.md`

## Global Constraints

- `recall_first=true` is the default.
- Do not add Whisper, ASR or another semantic model.
- Reuse the existing Qwen worker for visual context.
- Do not modify Silero, SenseVoice, speech fusion or renderer behavior.
- Never report a successful complete scan when Qwen, frame extraction or QR confirmation failed.
- Every detection and CUT interval must be within source duration and non-overlapping.
- Speech inside an advertisement interval must not suppress that interval.

---

### Task 1: Define detection models and deterministic interval utilities

**Files:**
- Create: `brand_scan/__init__.py`
- Create: `brand_scan/models.py`
- Create: `brand_scan/intervals.py`
- Test: `tests/test_brand_scan_models.py`

**Interfaces:**
- `Detection(type: str, start: float, end: float, confidence: float, detectors: tuple[str, ...], reason: str)` validates type, finite bounds and confidence.
- `BrandScanResult(status: str, detections: list[Detection], cut_intervals: list[dict[str, float]], ...)` serializes to JSON-safe dictionaries.
- `merge_intervals(intervals: Iterable[Mapping[str, float]], padding: float, duration: float) -> list[dict[str, float]]` clamps, pads, sorts and merges intervals.
- `expand_temporal_evidence(detections: Iterable[Detection], duration: float, before: float, after: float) -> list[Detection]` preserves source bounds.

- [ ] **Step 1: Write failing tests** for invalid types/timestamps, confidence bounds, interval overlap merging, duration clamping, and serialization fields.
- [ ] **Step 2: Run `pytest tests/test_brand_scan_models.py -q` and verify the new tests fail because the package is absent.**
- [ ] **Step 3: Implement the dataclasses and interval functions with no model or FFmpeg dependency.**
- [ ] **Step 4: Run `pytest tests/test_brand_scan_models.py -q`; all tests must pass.**
- [ ] **Step 5: Run `git diff --check`; do not stage user-owned files such as `hardware_probe.json`.**

### Task 2: Add independent QR confirmation and frame evidence grouping

**Files:**
- Create: `brand_scan/qr.py`
- Create: `brand_scan/evidence.py`
- Test: `tests/test_brand_scan_qr.py`

**Interfaces:**
- `detect_qr(frame: Image.Image) -> bool` uses OpenCV `QRCodeDetector` when available and raises a typed `QRDetectorUnavailable` only when the dependency cannot be loaded.
- `group_frame_evidence(frames: Iterable[Mapping[str, Any]], duration: float, min_consecutive: int, padding: float) -> list[Detection]` groups consecutive QR/logo evidence into one bounded interval.
- `combine_detector_evidence(qwen: Iterable[Detection], qr: Iterable[Detection], duration: float) -> list[Detection]` keeps QR-supported medium-confidence candidates and merges duplicate evidence without dropping a QR detection.

- [ ] **Step 1: Write failing tests** with fake QR detector results for a short QR burst, consecutive logo frames, separated bursts, and medium-confidence Qwen evidence supported by QR.
- [ ] **Step 2: Run `pytest tests/test_brand_scan_qr.py -q` and verify failure before implementation.**
- [ ] **Step 3: Implement the optional QR adapter, typed unavailable error, temporal grouping and evidence fusion.**
- [ ] **Step 4: Run the QR tests; tests must pass both with a fake detector and with OpenCV absent.**
- [ ] **Step 5: Run the existing semantic tests to ensure no import-time behavior changes.**

### Task 3: Implement the coarse/fine brand scan detector using the existing Qwen worker

**Files:**
- Create: `brand_scan/detector.py`
- Modify: `semantic_cleaner/qwen.py` (shared frame extraction/contact-sheet helpers and prompt response normalization only)
- Test: `tests/test_brand_scan_detector.py`

**Interfaces:**
- `BrandScanConfig(coarse_interval: float = 10.0, fine_interval: float = 2.0, temporal_padding: float = 0.25, min_consecutive_frames: int = 2, recall_first: bool = True)` reads `BRAND_SCAN_*` environment overrides.
- `BrandScanDetector(detector: Any, qr_detector: Callable[[Image.Image], bool], config: BrandScanConfig).scan(source: Path, duration: float) -> BrandScanResult` performs full coarse scan, candidate generation, fine scan, QR confirmation and temporal expansion.
- Qwen prompt accepts only `PERSONAL_BRAND`, `SPONSOR`, `QR`, `ADVERTISEMENT`, `NONE`; response normalization retains absolute source timestamps and evidence metadata.

- [ ] **Step 1: Write failing tests** for coarse candidates creating fine windows, Qwen sponsor/logo evidence becoming detections, QR-only confirmation, full successful scan status, and worker/frame/QR failure producing `BRAND_SCAN_INCOMPLETE`.
- [ ] **Step 2: Run `pytest tests/test_brand_scan_detector.py -q` and verify failure.**
- [ ] **Step 3: Implement the detector with injected Qwen and QR dependencies; reuse existing extraction/contact-sheet code instead of loading another model.**
- [ ] **Step 4: Run detector tests and verify all timestamps are bounded, merged and non-overlapping.**
- [ ] **Step 5: Run `pytest tests/test_semantic_cleaner.py -q`; existing INTRO/AD/OUTRO behavior must remain unchanged.**

### Task 4: Add artifact writer and production timeline integration

**Files:**
- Create: `brand_scan/pipeline.py`
- Modify: `production/pipeline.py`
- Modify: `backend/job_runner.py`
- Test: `tests/test_brand_scan_pipeline.py`
- Test: `tests/test_production_pipeline.py`

**Interfaces:**
- `run_brand_scan(source: Path, report: Path, artifact: Path, detector: BrandScanDetector) -> dict[str, Any]` writes `brand_ad_scan.json` with status, detections, cut intervals, timings, and Qwen generation count.
- Production adds `brand_scan_status`, `brand_scan_artifact`, `brand_cut_intervals`, and `brand_removed_duration` to the report, subtracting brand cuts from current KEEP through the existing interval subtraction utility.
- Backend runs the stage after the normal speech/content timeline and before final rendering; `BRAND_SCAN_INCOMPLETE` propagates as a visible incomplete/failure state and never silently renders as complete.

- [ ] **Step 1: Write failing tests** for successful cuts, no candidates preserving KEEP, overlapping cuts, speech-overlap preservation, artifact fields, and incomplete scan propagation.
- [ ] **Step 2: Run the focused tests and verify failure.**
- [ ] **Step 3: Implement artifact writing and production/backend integration without changing renderer arguments or detector model calls.**
- [ ] **Step 4: Run focused tests and existing `tests/test_production_pipeline.py tests/test_job_runner.py`.**
- [ ] **Step 5: Confirm existing no-speech, intro/outro, tight2 and renderer tests remain green.**

### Task 5: Add operational reporting, configuration and full regression verification

**Files:**
- Modify: `brand_scan/__init__.py`
- Modify: `docs/superpowers/specs/2026-08-24-brand-ad-qr-detector-design.md` only if implementation details require clarification
- Test: `tests/test_brand_scan_reporting.py`

- [ ] **Step 1: Write failing tests** for `APPLIED`, `NO_CANDIDATES`, and `BRAND_SCAN_INCOMPLETE` artifacts, timing fields, `recall_first`, and stable JSON output.
- [ ] **Step 2: Implement stable report schema and CLI-visible status text.**
- [ ] **Step 3: Run brand-scan tests, semantic tests, production tests, timeline tests and renderer tests.**
- [ ] **Step 4: Run the complete suite with `pytest -q`; record exact pass/skip/fail counts.**
- [ ] **Step 5: Run `git diff --check` and inspect `git status`; preserve unrelated `hardware_probe.json` changes.**

## Verification Gates

- No test may require Silero, SenseVoice or a downloaded model; detector tests inject fakes.
- A real integration smoke test may run only when FFmpeg, OpenCV and the Qwen worker are available; otherwise it must skip explicitly.
- The final report must distinguish “no candidate found” from “scan incomplete”.
- No claim of 100% recall may be emitted; only a complete successful scan may be reported as applied.
