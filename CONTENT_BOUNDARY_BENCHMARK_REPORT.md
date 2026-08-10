# Content Boundary Benchmark

## Result

- Input: `input.mp4` (300.002s)
- Content window: 0.000s–300.002s
- Intro removed: 0.000s
- Outro removed: 0.000s
- Silence removed: 7.550s
- Total removed: 7.550s
- Output duration: 292.467s

The strongest intro candidate was 33.250s at 0.641 confidence. This is below
the conservative 0.700 threshold, so the source edge was preserved. No outro
candidate had enough structural evidence.

## Performance

- Boundary analysis: 3.333s
- Cold speech analysis: 58.454s
- Render: 21.900s
- Total: 84.090s
- SLA (<=120s): PASS

## Safety

- Known speech gaps: 18
- Fully protected: 16
- Partially protected: 2
- Still unprotected: 0
- Whisper loaded: NO
- Fun-ASR-Nano loaded: NO
- SRT generated: NO

## Tests

129 passed, 1 skipped, 44 subtests passed in 5.35s.

## Manual review

Not production-ready from one sample. Review the first and final 30 seconds of
`output.maincontent.mp4` before changing the confidence threshold or scoring.
