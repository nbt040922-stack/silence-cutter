# Production Benchmark Report

## Pipeline

Silero + SenseVoiceSmall -> UNION -> merge <=0.15s -> zero padding -> KEEP/CUT -> NVENC (libx264 fallback)

## Latency

| Run | Audio extraction | Model load | Silero | SenseVoice | Fusion | Timeline | Render | Total |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Cold analysis | 0.300s | 15.960s | 12.033s | 6.850s | 0.0004s | 0.0001s | - | 34.147s |
| Warm analysis | 0.298s | 0.000s | 5.843s | 7.160s | 0.0004s | 0.0002s | - | 7.521s |
| Full run | 0.318s | 0.000s | 6.534s | 7.545s | 0.0004s | 0.0002s | 21.945s | 29.948s |

Parallel detector median: 7.723s. Sequential detector median: 11.102s. Selected default: **parallel**.

## Speech and cuts

- Silero speech: 286.800s
- SenseVoice speech: 290.850s
- Union speech: 291.800s
- Final KEEP: 292.452s
- Final CUT: 7.550s
- Removed: 2.517%

## Safety and regression

- Known Whisper gaps: 18
- Protected by union: 18
- Still unprotected: 0
- Whisper model loaded: NO
- Fun-ASR-Nano model loaded: NO
- SRT generated: NO
- Tests: 123 passed, 1 skipped, 44 subtests passed in 3.60s
- Overall: **PASS**
