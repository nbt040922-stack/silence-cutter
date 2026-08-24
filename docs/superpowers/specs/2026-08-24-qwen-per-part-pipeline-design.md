# Qwen Per-Part Pipeline Design

## Goal

Separate Qwen inspection from the whole-video pipeline. A source video at or below 25 minutes must not call Qwen. For longer videos, Qwen may inspect only the already planned parts: intro on Part 1, advertisements on Part 2, and outro on Part 3. FFmpeg renders only after the timestamp plan is finalized.

## Contract

- The source-duration gate is inclusive: `duration <= 1500.0` seconds means no Qwen call.
- The pipeline creates a timestamped part plan before Qwen inspection and before FFmpeg rendering.
- Qwen receives bounded part ranges, never the complete source timeline.
- Part roles are stable: part 1 = INTRO, part 2 = AD, part 3 = OUTTRO.
- A part longer than 600 seconds is truncated to 480 seconds before inspection/rendering.
- No generic semantic “find good segments” scan is used in this mode.
- Existing output/report contracts remain compatible; new report fields are additive.

## Failure behavior

- Qwen failure does not erase the existing timestamp plan.
- If Qwen is unavailable, the plan is rendered without Qwen removals and records the skip/error reason.
- A short video writes a skipped Qwen artifact with `reason=source_duration_at_or_below_25_minutes`.

## Verification

- Unit tests prove the inclusive 25-minute gate.
- Unit tests prove bounded role/range calls and the 10-minute-to-8-minute cap.
- Existing renderer and bridge tests remain green.
