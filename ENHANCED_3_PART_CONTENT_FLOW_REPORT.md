# Joined Channel Enhanced 3-Part Content Flow

## Routing and bridge contract

Content Ops requests may include:

```json
{"enhanced_content_selection": true}
```

The field is an explicit boolean. It is never inferred from channel name.
Omitted or `false` preserves the existing production path and formatter rules
unchanged. `true` runs enhanced selection for any feasible source duration,
including videos below 900 seconds. Legacy long-video threshold routing remains
available to normal jobs.

## Enhanced flow

1. One local Qwen selector subprocess ranks six compact candidates globally.
2. Code chooses the best non-overlapping three using semantic uniqueness,
   score, timeline span, and minimum temporal separation.
3. Each selected source range is processed independently by the same
   `ProductionRuntime` instance. Existing Silero, SenseVoice, tight2, and
   KEEP/CUT logic are reused without modification.
4. One Semantic Cleaner Qwen instance scans the source once. Its result is
   applied independently to all candidate reports. It is not loaded per part.
5. The fixed enhanced format plan maps BEST_1 to PART_1, BEST_2 to PART_2,
   and BEST_3 to PART_3. Existing `plan_parts()` is bypassed only for enhanced
   mode; layout, crop, banners, pitch/EQ, CUDA/NVDEC/NVENC renderer remain the
   existing implementation.

The selector is isolated in a subprocess. It exits before Semantic Cleaner
loads, reducing observed peak active VRAM from about 15.7 GB in the prototype
to about 9.1 GB during the clean acceptance run.

## Range and part policy

- Selected source range: preferred minimum 120 seconds, adaptive target
  180–240 seconds, maximum 300 seconds.
- Videos too short for three non-overlapping ranges under the coverage guard
  return `ENHANCED_SELECTOR_SKIPPED_INSUFFICIENT_DURATION`.
- Every successful final part must be greater than 60 seconds and no longer
  than 300 seconds.
- A short result first expands toward 300 source seconds, then tries the next
  unused ranked candidate.
- Enhanced success requires all three valid parts. Any selector, semantic,
  recovery, validation, timeout, OOM, or render failure records
  `ENHANCED_CONTENT_SELECTION_SKIPPED` and invokes the unchanged normal flow.

Artifacts:

- `enhanced_content_selection.json`
- `long_video_selection.json`
- `pipeline_report_part_1.json` through `pipeline_report_part_3.json`
- `semantic_segments_part_1.json` through `semantic_segments_part_3.json`
- `format_plan.json`

All internal ranges retain absolute source timestamps and explicit
`part_index` values.

## Real acceptance

Source duration: **1,448.154 seconds**.

Qwen returned seven valid ranked candidates in one generation during the
acceptance benchmark. Production prompt is capped at six candidates and one
generation to reduce latency while retaining alternates.

| Logical part | Absolute source range | Final duration | Rendered duration |
|---|---:|---:|---:|
| BEST_1 / PART_1 | 0–240 s | 226.720 s | 226.720 s |
| BEST_2 / PART_2 | 600–840 s | 240.000 s | 240.000 s |
| BEST_3 / PART_3 | 1080–1320 s | 240.000 s | 240.000 s |

All outputs are 1080×1920 H.264/AAC, rendered with AV1 NVDEC, CUDA filters,
and `h264_nvenc`. Maximum measured A/V delta was 0.020 seconds. Final render
took 77.716 seconds at approximately 8.9–9.3× realtime.

Manual thumbnail-strip inspection confirms three separated source sections:
opening product showcase, middle recipe/food section, and late personal/family
product story. They are not mathematical duration splits. The fixture is one
Costco-haul video, so all three remain under the same overarching theme; truly
independent story diversity is limited by source content.

Selector acceptance metrics from the seven-candidate run:

- Qwen generations: 1
- Qwen model loads: 1
- selector time: 157.116 seconds
- selector model load: 44.276 seconds
- selector peak VRAM: 8,014,389,248 bytes
- full first acceptance: 341.509 seconds
- Semantic Cleaner model loads: 1 total, not per part

## Fail-open and ownership

Enhanced failures do not fail the Content Ops job. The bridge falls back to
the original Silence Cutter → Semantic Cleaner → duration-based formatter
path. Output storage remains caller-supplied; no channel-name or NAS routing
logic was added.

## Known limitations

- Visual-only sparse Qwen ranking can miss important audio-only events.
- A single-topic source can provide distinct sections but not three unrelated
  stories; the selector must not invent diversity.
- Recovery may analyze extra candidates, but reuses loaded detector and
  semantic results.
- Selector and Semantic Cleaner intentionally remain separate subprocess/model
  lifetimes for memory safety and fail-open clarity.

## Tests

`336 passed, 1 skipped, 99 subtests passed in 26.36s`.

Coverage includes enhanced flag routing/default compatibility, below-900
selection, six ranked candidates, explicit part identity, fixed formatter
mapping, duration eligibility, expansion, alternate recovery, whole-flow
fail-open, one semantic model load, and existing Silence Cutter, Semantic
Cleaner, Long Video Selector, Formatter, and Content Ops regressions.
