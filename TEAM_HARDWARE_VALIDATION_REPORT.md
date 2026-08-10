# Team Hardware Validation Report

Release status: **RC — FINAL INSTALLER NOT APPROVED**

Benchmark asset SHA256: `fd0527bdc1df8c0438b37e709a28bee89e1e44c49f0d3ced927f8f084a582018`

## RTX 2080 SUPER

- Machine: PENDING
- CUDA: PENDING
- SenseVoice device/fallback: PENDING
- NVENC: PENDING
- VRAM peak: PENDING
- Total time: PENDING
- Render time: PENDING
- Timeline hash: PENDING

## RTX 3060 12GB

- Machine: PENDING
- CUDA: PENDING
- SenseVoice device/fallback: PENDING
- NVENC: PENDING
- VRAM peak: PENDING
- Total time: PENDING
- Render time: PENDING
- Timeline hash: PENDING

## Timeline comparison

- Same benchmark asset: PENDING
- Intro boundary: PENDING
- Outro boundary: PENDING
- Silero intervals: PENDING
- SenseVoice intervals: PENDING
- Final KEEP/CUT intervals: PENDING
- Timeline hash: PENDING
- Result: **PENDING**

Compare after copying both `hardware_benchmark.json` files:

```powershell
resources\runtime\python\python.exe -m backend.hardware compare 2080\hardware_benchmark.json 3060\hardware_benchmark.json
```

## Portability

- Python install not required: PENDING ON TARGET
- FFmpeg install not required: PENDING ON TARGET
- Model download not required: PENDING ON TARGET
- Developer paths absent: PASS BY LOCAL AUDIT
- Result: **PENDING**

Final installer approval requires `TIMELINE COMPARISON: PASS` and `PORTABILITY: PASS` on both target classes.
