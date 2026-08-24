# Qwen Per-Part Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Qwen inspection after timestamped part planning, limit it to the three part roles, and ensure videos at or below 25 minutes never call Qwen.

**Architecture:** Keep production analysis and the existing formatter as the source of timestamp mappings. Add a small per-part inspection boundary that accepts source ranges and role prompts, returns removable intervals, and updates the plan before rendering. The duration gate is enforced both at the orchestration layer and inside the inspection helper so enhanced, normal, and fallback paths cannot bypass it.

**Tech Stack:** Python, pytest, existing `semantic_cleaner.qwen.QwenWorkerDetector`, existing formatter plan/render pipeline, JSON artifacts.

**Spec:** `docs/superpowers/specs/2026-08-24-qwen-per-part-pipeline-design.md`

## Global Constraints

- `duration <= 1500.0` seconds means no Qwen call.
- Qwen receives bounded part ranges only.
- Part roles are `INTRO`, `AD`, and `OUTTRO` for parts 1, 2, and 3.
- Any part longer than 600 seconds is capped at 480 seconds before Qwen/render.
- Qwen failure preserves and renders the timestamp plan.
- Do not redesign the WPF UI, change API contracts, restart running services, commit, or push.

---

### Task 1: Add pure per-part policy helpers

**Files:**
- Create: `qwen_part_policy.py`
- Test: `tests/test_qwen_part_policy.py`

**Interfaces:**
- `should_inspect_with_qwen(source_duration: float, threshold: float = 1500.0) -> bool`
- `cap_part_range(start: float, end: float, source_duration: float, max_part_seconds: float = 480.0, trigger_seconds: float = 600.0) -> dict[str, float]`
- `part_role(part_index: int) -> str | None`

- [ ] **Step 1: Write failing tests** for the inclusive gate, role mapping, unchanged short parts, and 600+ second cap.
- [ ] **Step 2: Run** `python -m pytest tests/test_qwen_part_policy.py -q`; confirm collection succeeds and assertions fail because the module is absent.
- [ ] **Step 3: Implement** the three pure helpers with finite/range validation and no Qwen imports.
- [ ] **Step 4: Run** the same test; confirm all pass.

### Task 2: Add bounded Qwen part inspection

**Files:**
- Modify: `enhanced_content_flow/flow.py`
- Test: `tests/test_enhanced_content_flow.py`

**Interfaces:**
- Add `inspect_qwen_parts(source: Path, duration: float, parts: list[dict[str, Any]], job_dir: Path, detector_factory: Callable[[], Any]) -> dict[str, Any]`.
- Each detector call receives one bounded range and a role-specific prompt. The returned artifact records `role`, `part_index`, `source_range`, `status`, and `removed_segments`.

- [ ] **Step 1: Add failing tests** proving short sources do not call `detector_factory`, long sources call at most parts 1–3, roles are `INTRO/AD/OUTTRO`, and a long part is capped to 480 seconds.
- [ ] **Step 2: Run** `python -m pytest tests/test_enhanced_content_flow.py -q`; confirm the new tests fail on missing helper/behavior.
- [ ] **Step 3: Implement** the helper using the policy module and existing detector API, with per-part exception handling that preserves the plan.
- [ ] **Step 4: Run** the focused tests and existing enhanced-flow tests; confirm pass.

### Task 3: Route orchestration through the new policy

**Files:**
- Modify: `backend/job_runner.py`
- Modify: `contentops_process_bridge.py`
- Test: `tests/test_job_runner.py`
- Test: `tests/test_contentops_process_bridge.py`

**Interfaces:**
- Normal processing must create a skipped semantic artifact for sources at or below 1500 seconds and must not invoke the old whole-video semantic stage.
- Enhanced processing must inspect bounded parts after the plan exists and before `render_format_plan`.

- [ ] **Step 1: Add failing orchestration tests** that patch Qwen detector creation and assert no call for 1500 seconds, plus assert the new artifact is attached to the job/report.
- [ ] **Step 2: Run** the focused tests and verify the failures are caused by unconditional semantic/enhanced calls.
- [ ] **Step 3: Implement** the smallest routing change: gate old whole-video semantic/brand-Qwen paths, invoke bounded inspection only for long sources, and pass the updated mapping to the existing renderer.
- [ ] **Step 4: Run** focused job-runner, bridge, and enhanced-flow tests; fix only regressions caused by the route change.

### Task 4: Full verification and release safety check

**Files:**
- Modify: none unless a test fixture needs correction.

- [ ] **Step 1: Run** `python -m pytest tests/test_qwen_part_policy.py tests/test_enhanced_content_flow.py tests/test_job_runner.py tests/test_contentops_process_bridge.py -q`.
- [ ] **Step 2: Run** the relevant Qwen, renderer, and formatter tests with the project runtime that has Pillow installed.
- [ ] **Step 3: Run** `git diff --check` and inspect `git status --short`.
- [ ] **Step 4: Confirm** the desktop app and running services were not stopped or restarted.
