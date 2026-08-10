# Downloader Manager V1 Report

## Architecture

One persistent Python coordinator owns two independent, bounded lanes:

```text
Downloader lane (max 1): QUEUED -> DOWNLOADING -> READY
Processor lane  (max 1): READY -> ANALYZING -> RENDERING -> DONE
```

Both lanes use the existing atomic per-job JSON files as the authoritative state. The downloader can cool down or wait for retry without stopping an active production process. `prefetch_depth = 1` prevents more than one downloaded job waiting ahead.

Production defaults:

```text
download_concurrency = 1
process_concurrency = 1
prefetch_depth = 1
download_cooldown_min_seconds = 55
download_cooldown_max_seconds = 70
max_download_retries = 3
```

## Files Changed

- `backend/job_runner.py`
- `desktop/src/main.js`
- `desktop/src/styles.css`
- `tests/test_downloader_manager.py`
- `README.md`
- `UI_IMPLEMENTATION_REPORT.md`
- `DOWNLOADER_MANAGER_V1_REPORT.md`

No production detector, fusion, timeline, renderer, or NVENC code was changed.

## Scheduler States

```text
QUEUED
DOWNLOADING
READY
ANALYZING
RENDERING
DONE
FAILED
CANCELLED
INTERRUPTED
```

`retry_wait` is a persisted stage within `DOWNLOADING`; it is not a separate public status. READY is visible in the desktop queue. READY jobs may be cancelled and must be cancelled before removal.

## Cooldown Logic

Every successful new download records a randomized wall-clock `download_cooldown_until` in its `job.json`. The coordinator does not start another queued download before that time. It continues ticking normally, so READY processing, analysis, and rendering are unaffected.

Cooldown is applied only after a successful download. It is not added to retry backoff and is not applied after a final download failure. Cancelling a queued job during cooldown updates it immediately.

## Prefetch Logic

The downloader starts only when fewer than one READY job exists. While job N is processing, job N+1 may download after cooldown and wait as READY. Job N+2 remains QUEUED until the READY slot is consumed.

## Retry Policy

```text
NETWORK_TRANSIENT: 30s -> 60s -> 120s -> FAILED
HTTP_429:          60s -> 120s -> 300s -> FAILED
```

There are at most three automatic retries after the initial attempt. Authentication, bot/token challenges, HTTP 403, unavailable media, invalid URLs, and unknown failures do not retry automatically. Final failure releases the downloader lane for the next queued job.

## Error Classification

- `NETWORK_TRANSIENT`
- `HTTP_429`
- `HTTP_403`
- `AUTH_REQUIRED`
- `BOT_CHALLENGE_OR_TOKEN`
- `UNAVAILABLE`
- `INVALID_URL`
- `UNKNOWN`

Classification uses only output from the current yt-dlp attempt so an earlier error cannot contaminate a later retry. User/global yt-dlp configuration is ignored; V1 runs public downloads anonymously and never loads browser/account cookies.

## Restart Behavior

Existing `DOWNLOADING`, `ANALYZING`, and `RENDERING` jobs become `INTERRUPTED` on restart. READY remains READY and can be processed without downloading again. Retrying an interrupted job probes an existing `source.*` with ffprobe; a valid source becomes READY, otherwise the job returns to QUEUED.

## Test Results

```text
185 passed, 1 skipped, 57 subtests passed in 11.98s
Frontend production build: PASS
Rust cargo check: PASS
Tauri optimized Windows/NSIS build: PASS
```

Deterministic coverage includes five-job single download concurrency, virtual cooldown, independent processing, prefetch depth, transient and HTTP 429 schedules, final-failure continuation, READY restart reuse, cooldown cancellation, all error classes, and a 40-URL no-burst batch.

## Real Smoke Test

Three public YouTube URLs were downloaded anonymously and processed by the real production CLI. No account or browser cookies were used.

```text
Total elapsed: 175.375s
Job 1: DONE (25s source)
Job 2: DONE (10s source)
Job 3: DONE (21s source)

max simultaneous yt-dlp processes: 1
max simultaneous production processes: 1
max READY jobs observed: 1
```

Measured event timeline:

| Job | Download | Processing |
|---|---:|---:|
| 1 | 0.047–8.344s | 8.391–36.719s |
| 2 | 75.985–85.047s | 85.125–108.703s |
| 3 | 145.141–150.641s | 150.875–175.157s |

Job 1 processing continued during downloader cooldown. No second yt-dlp or production process overlapped.

Raw evidence: `latency_benchmark/downloader_manager_v1_smoke/smoke_result.json`.

## Known Limitations

- V1 supports anonymous public downloads only; authenticated/private/age-gated media fails clearly without cookie fallback.
- Error classification is based on stable yt-dlp error text patterns and retains `UNKNOWN` for unrecognized failures.
- Scheduler configuration is internal and persisted but intentionally has no additional UI controls.
- The real smoke test uses three short videos; the 40-URL behavior is deterministic/tested without downloading 40 real videos.
