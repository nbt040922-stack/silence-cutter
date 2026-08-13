# Maximum Throughput + Always-Warm Qwen Report

## Result

Production Qwen now runs as one loopback-only persistent worker. Desktop startup
loads the AWQ model, performs a warm-up generation, waits for `READY`, then starts
the normal backend worker. Selector and Semantic Cleaner use the same resident
model. Enhanced jobs never load Qwen locally.

Acceptance: **PASS**. Best measured warm job: **177.636 s** (`<240 s`, also
**STRONG PASS** `<180 s`). Model loads per warm video: **0**.

## Before

Same 1,448.154-second acceptance source:

| Stage | Seconds |
|---|---:|
| Selector | 157.116 |
| Selected-range Silence | 42.817 |
| Semantic Cleaner | 51.601 |
| Render | 84.659 |
| Whole enhanced flow | 341.509 |

Selector and Semantic Cleaner each owned a Qwen model load. Rendering was
sequential.

## Cold startup

Measured on NVIDIA GeForce RTX 5060 Ti 16 GB:

| Metric | Result |
|---|---:|
| Model load | 14.091 s |
| Warm-up inference | 2.423 s |
| Process start to READY | 16.521 s |
| Resident GPU memory after READY | about 8,387 MiB |
| Model load count | 1 |

`READY` is impossible before model load and warm-up both succeed.

## Warm jobs

Three consecutive full jobs used the same live Qwen worker. Jobs 2 and 3 also
ran inside one pipeline process, proving detector-runtime reuse.

| Metric | Job 1 | Job 2 | Job 3 |
|---|---:|---:|---:|
| Selector | 85.851 | 84.805 | 83.990 |
| Frame extraction | 30.950 | 30.951 | 30.793 |
| Selector generation | 54.700 | 53.658 | 53.047 |
| Silence | 43.664 | 39.348 | 20.473 |
| Semantic | 11.017 | 10.822 | 10.878 |
| Parallel render | 65.819 | 59.525 | 61.825 |
| Whole job | 206.839 | 194.929 | **177.636** |
| Qwen model loads/video | 0 | 0 | 0 |
| Qwen generations | 1 | 1 | 1 |

Three-job total: **579.404 s**. Average: **193.135 s/job**.

After enabling exact frame reuse, a separate cold-detector/warm-Qwen validation
completed in **187.322 s**: selector 85.433 s, Silence 39.372 s, semantic 0.070 s,
render 62.015 s. Semantic reused 73 selector frames and launched no additional
visual decode. This fixture had no visual semantic candidate, so no Semantic
Cleaner generation was necessary; selector still used one generation.

## Runtime architecture

- Bind: `127.0.0.1:8792` only.
- States: `STARTING`, `LOADING_MODEL`, `WARMING_UP`, `READY`, `BUSY`, `ERROR`.
- One sequential inference lock; BUSY requests wait in queue.
- One resident detector serves Content Selector and Semantic Cleaner prompts.
- Worker crash/generation failure makes supervisor restart and warm one owned
  child; client retries one request after READY.
- Desktop owns both worker process trees and stops only those children.
- `production-runtime.json` records owner PID, Qwen PID/health and backend PID.
- Dedicated stdout/stderr: `qwen-worker.stdout.log`,
  `qwen-worker.stderr.log`.

## Decode and cache behavior

- Selector remains one sparse Qwen generation and returns 6 ranked candidates.
- One per-job visual cache is shared by selector and Semantic Cleaner, then
  deleted.
- Visual extraction: one sparse global FFmpeg decode plus three selected-range
  decodes. Semantic reuses those range frames; no second visual scan.
- Silence analysis uses three part-local audio seeks. Unselected source audio is
  not decoded. One `ProductionRuntime`/SenseVoice detector is shared.
- Recovery scans an alternate/expanded range only when that range is attempted;
  global selection is never rerun.

## Render concurrency and Qwen contention

Exact existing AV1 NVDEC/CUDA/NVENC render while Qwen remained resident:

| Concurrency | Wall time | Valid duration/A-V |
|---:|---:|---|
| 1 | 83.065 s | PASS |
| 2 | 70.258 s | PASS |
| 3 | **57.418 s** | PASS |

Enhanced production therefore uses concurrency 3. Normal formatter remains
sequential. Qwen remains resident; no per-video unload/reload strategy is used.

## Correctness

- BEST_1/PART_1, BEST_2/PART_2, BEST_3/PART_3 identity preserved.
- All parts remained `>60 s` and `<=300 s`.
- Latest full outputs passed duration and A/V validation; maximum measured A/V
  delta was 0.007 s.
- Editorial scoring, detector models, tight2, semantic confidence, crop/layout,
  pitch/EQ and NVENC quality were unchanged.
- Full regression: **342 passed, 1 skipped in 28.49 s**.
- Desktop Rust launcher: `cargo check` PASS.

## Remaining measured limit

Warm selector generation is about 53–55 seconds, above the requested 30–40
second preference. It is now pure warm inference, not model load. Reducing it
further would require changing prompt/output calibration or model/runtime, both
outside the locked-business-logic constraint.
