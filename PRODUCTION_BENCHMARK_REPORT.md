# Production Benchmark Report

## Pipeline

Silero + SenseVoiceSmall -> UNION -> padding once -> merge -> KEEP/CUT -> NVENC (libx264 fallback)

## Latency

| Run | Audio extraction | Model load | Silero | SenseVoice | Fusion | Timeline | Render | Total |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Cold analysis | 0.330s | 11.971s | 12.884s | 4.381s | 0.0001s | 0.0001s | - | 28.354s |
| Warm analysis | 0.340s | 0.000s | 5.845s | 4.647s | 0.0001s | 0.0001s | - | 6.253s |
| Full run | 0.344s | 0.000s | 5.913s | 4.744s | 0.0001s | 0.0001s | 22.100s | 28.490s |

Parallel detector median: 5.971s. Sequential detector median: 8.544s. Selected default: **parallel**.

## Speech and cuts

- Silero speech: 287.800s
- SenseVoice speech: 292.820s
- Union speech: 293.490s
- Final KEEP: 294.972s
- Final CUT: 5.030s
- Removed: 1.677%

## Safety and regression

- Known Whisper gaps: 18
- Protected by union: 18
- Still unprotected: 0
- Whisper model loaded: NO
- Fun-ASR-Nano model loaded: NO
- SRT generated: NO
- Tests: 115 passed, 1 skipped, 44 subtests passed in 4.89s
- Overall: **PASS**
