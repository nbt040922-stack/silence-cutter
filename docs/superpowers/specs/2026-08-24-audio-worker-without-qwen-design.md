# Audio Worker Without Qwen Design

## Goal

Remove Qwen from the job-processing critical path and keep the audio analysis models warm inside the long-lived Silence Scheduler service on port 8791.

## Scope

- The processing bridge must not require or call Qwen for normal job analysis.
- SenseVoiceSmall and FSMN VAD must be loaded once by a long-lived audio worker and reused by jobs.
- Port 8791 health must report audio readiness separately and only report service readiness after the audio worker is warm.
- Audio processing concurrency remains 1.
- Existing job, output, and report contracts remain compatible.
- Qwen files remain on disk but are not launched or used by this pipeline.

## Architecture

Silence Scheduler owns an `AudioAnalysisWorker` singleton. The worker loads the existing SenseVoiceSmall and FSMN VAD models during service startup, stores the loaded analyzer in memory, and serializes analysis requests through one queue. The processing bridge calls this worker instead of spawning a `production` subprocess that lazily loads models.

The `/health` response exposes `audio_model_status` (`LOADING`, `READY`, or `ERROR`), `audio_model_error`, and `processing_concurrency`. Qwen fields remain informational only if the endpoint is still present, but Qwen readiness cannot block audio or job readiness.

## Failure behavior

- If audio warmup fails, 8791 remains reachable but reports `status=DEGRADED`, `audio_model_status=ERROR`, and a diagnostic error.
- Jobs are rejected/queued with an explicit `AUDIO_MODEL_NOT_READY` error until the worker becomes ready.
- Qwen being stopped or unavailable does not fail an audio job.
- A job produces its normal report only after the audio worker returns analysis successfully.

## Verification

- Unit tests prove the worker loads models once, serializes requests, and reuses the loaded analyzer.
- Bridge tests prove Qwen health is not required for audio readiness or job submission.
- Integration smoke test starts 8791, waits for `audio_model_status=READY`, processes a short local media sample, and verifies `pipeline_report.json` exists.
- Existing service-control and job-runner tests must remain green.
