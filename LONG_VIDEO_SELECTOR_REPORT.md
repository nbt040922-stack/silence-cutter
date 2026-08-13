# Qwen Long Video Best-3 Selector

## Architecture and routing

- `LONG_VIDEO_THRESHOLD_SECONDS=900` is configurable and defaults to 900 seconds.
- Videos at or below the threshold use the existing Production → Semantic Cleaner → Formatter path unchanged.
- Longer videos run the selector first. A valid selection is passed to Production as absolute source ranges; the existing Silero + SenseVoice result is intersected with those ranges without concatenating or rendering an intermediate video.
- Semantic Cleaner and Formatter consume the resulting absolute KEEP timeline exactly as before.
- The selector runs in a local subprocess and writes `long_video_selection.json` beside the job artifacts.

The selector is fail-open. Model/CUDA/OOM/timeout/parser/validation errors, fewer than three valid ranges, overlap, duplicate topics, invalid scores, or excessive coverage produce `LONG_VIDEO_SELECTOR_SKIPPED`; the job then follows the existing full-video path.

## Selection method

1. Decode one sparse frame about every 60 seconds with the shared CUDA-capable frame extractor.
2. Put the whole sparse timeline into compact timestamped contact sheets.
3. Ask local `Qwen/Qwen2.5-VL-7B-Instruct-AWQ` for exactly three globally ranked, semantically distinct centers. The prompt rewards importance, retention, novelty, payoff, tension, useful information and independent comprehension; it rejects intro, ads, outro, CTA, filler and repetition.
4. Decode sparse local neighborhoods around the three centers and inspect visual transitions without another model generation.
5. Form adaptive 180–300 second ranges, reject overlap/duplicate topics, sort chronologically, then apply strict coverage validation.

The compact production prompt is:

```text
Choose ONLY the best 3 diverse moments from this entire long-video timeline.
Do NOT describe cells or chunks. Do NOT print a header.
Output exactly 3 lines only: CENTER,SCORE,TOPIC
```

This uses one Qwen generation per long video. An earlier two-generation coarse/rank/refine prototype exceeded 100 seconds because autoregressive output dominated latency; the one-generation hierarchy preserved global semantic ranking while bringing the real benchmark below 90 seconds.

## Real benchmark

Source: real local video, 1,448.154 seconds (24:08), RTX 5060 Ti 16 GB.

| Metric | Result |
|---|---:|
| Status | APPLIED |
| Coarse chunks | 25 |
| Sampled frames | 43 |
| Candidate count | 3 |
| Qwen generations | 1 |
| Model load | 15.119 s |
| Frame extraction | 28.300 s |
| Coarse/global semantic ranking | 33.202 s |
| Local refinement | 0.013 s |
| Total selector | 76.707 s |
| Peak VRAM | 8,014,389,248 bytes |
| Selected duration | 540.000 s (37.29%) |

Selected ranges, requiring manual review of subjective quality:

1. **510–690 s** — score 0.85 — “Shopping haul review”.
2. **810–990 s** — score 0.75 — “Health update”.
3. **1170–1350 s** — score 0.65 — “Personal milestone”.

All ranges are absolute source timestamps, 180 seconds long, chronological, non-overlapping, and under the 70%/900-second coverage guard.

A second real 1,004.134-second continuous cooking fixture returned centers at 60, 120 and 480 seconds. The first two cannot form distinct non-overlapping 180-second ranges, so the selector correctly returned `LONG_VIDEO_SELECTOR_SKIPPED` in 58.36 seconds instead of forcing filler.

## Whole-pipeline impact estimate

For the 1,448-second benchmark, only 540 seconds reach the expensive speech detector and formatter timeline. The current implementation keeps the selector and Semantic Cleaner in separate subprocesses, so Qwen model load is paid twice on applied long jobs. Sharing one per-video model process was not introduced because it would make the existing Semantic Cleaner architecture more invasive and fragile.

Expected savings therefore come from reducing Silence Cutter analysis and final formatter work from 1,448 seconds to at most 540 seconds. Actual end-to-end time remains content- and segment-count-dependent and should be measured on the release runtime before setting a new SLA.

## Validation and limitations

- Exactly three ranges are mandatory; one or two are never applied silently.
- Range duration is adaptive: 180 seconds through 25 minutes, 180–240 seconds through 40 minutes, then 240–300 seconds for longer sources.
- Scores must be finite and inside 0–1; timestamps must be valid absolute source times.
- Long, visually repetitive content can produce adjacent candidates and intentionally fail open.
- Sparse visual sampling may miss important audio-only events; no ASR or cloud model was added.
- Qwen boundary precision is deliberately approximate. Silence Cutter remains authoritative for exact speech timing.
- The packaged runtime must include the same Torch/Transformers/Torchvision/AWQ dependencies already required by the local Qwen Semantic Cleaner.

## Regression result

`326 passed, 1 skipped, 99 subtests passed in 27.57s`.

This includes the controlled Semantic Cleaner AD tests, Content Ops bridge tests,
formatter/planner/renderer tests, existing Production tests, and the new selector
routing, validation, fail-open, timeout, duplicate-topic, absolute-timestamp, and
scoped Silence Cutter tests.
