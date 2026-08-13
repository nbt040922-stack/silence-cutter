# Semantic Cleaner Performance Optimization

## Kết quả

Đã tối ưu từ nhiều Qwen generation theo từng cửa sổ thành:

`một semantic subprocess/video -> load model một lần -> coarse visual scan rẻ -> candidate windows -> một batched Qwen fine scan -> threshold/safe snapping cũ`

Video thật `1004.134s`:

- Cold semantic total: `64.869s`
- Warm-model equivalent (không tính load): `48.791s`
- Performance classification: trên mức acceptable 60s khoảng `4.869s`, dưới failure 90s.
- Whole local pipeline: `203.34s` (`3m23s`), đạt mục tiêu dưới 4 phút.

Không thêm daemon/persistent worker. Model vẫn nằm trong một process con duy nhất cho mỗi video và thoát sau khi hoàn tất.

## Trước và sau

### Trước — commit 195b39d

- INTRO 90s, AD window 60s/stride 45s, OUTRO 120s.
- Mỗi cửa sổ: extract 8 frame rồi gọi Qwen riêng.
- `max_new_tokens=512`.
- Baseline 50s/8 frame: load `14.8s`, inference `41.0s`, total `55.8s` cho một cửa sổ.
- Video 1004s cần khoảng 25 generation; không thể mở rộng thực tế.

### Sau

- Coarse: 1 frame/10s, tối đa 6 transition mạnh nhất; coarse chỉ tạo candidate, không được xóa.
- AV1/H.264/HEVC NVIDIA: CUVID decoder resize + GPU `framestep`; CPU FFmpeg là fallback.
- Candidate: thêm context 15s, merge overlap.
- Fine: 1 frame/4s chỉ trong candidate, 16 cell/contact sheet, timestamp tuyệt đối in trên từng cell.
- Qwen: một prompt cho mọi candidate sheet; output nội bộ CSV ngắn, chuẩn hóa về artifact JSON cũ.
- Generation budget: 32 token.
- OOM batch: giải phóng cache rồi hạ xuống từng contact sheet.
- Qwen output được căn theo visual transition đo được, sau đó vẫn phải qua confidence `0.85` và safe speech/KEEP snapping cũ.
- Với video >240s, AD nằm hoàn toàn trong 120s cuối bị giữ lại để tránh nhầm closing CTA thành quảng cáo. OUTRO hợp lệ vẫn được phép.

## Benchmark matrix

| Phương án | Model load | Extraction | Coarse | Fine/Qwen | Generation | Total | Accuracy fixture |
|---|---:|---:|---:|---:|---:|---:|---|
| A — cũ, từng window | 14.8s | chưa tách | — | 41.0s/window | ~25/1004s | >1000s dự phóng | AD PASS/window |
| B — Qwen coarse + Qwen fine contact sheets | 14.06s | 2.26s | 34.82s | 33.60s | 2 | 84.83s/fixture | FAIL: lặp schema |
| C — cheap coarse + batched fine, CPU extraction | 17.07s | 40.31s | 0.14s | 32.08s | 1 | 89.72s/1004s | FAIL: closing CTA bị gọi là AD |
| C final — CUVID + compact CSV/contact sheet | 13.61s | 1.52s | 0.01s | 28.11s | 1 | 43.28s/fixture | PASS |
| C final — video thật 1004s | 16.08s | 14.65s | 0.14s | 33.95s | 1 | 64.87s | zero false removal |

Phương án B bị loại vì hai generation AWQ_TORCH đã vượt ngân sách ngay cả trên fixture ngắn. Phương án C giữ Qwen là bên duy nhất quyết định semantic removal; coarse transition/QR-style signal chỉ giới hạn nơi cần xem.

## Accuracy guard

### Controlled visible-ad fixture

- Duration: `50.000s`
- Known AD ground truth: `20.000–30.000s`
- Coarse frames: `5`
- Candidate windows: `1`
- Fine frames: `13`
- Contact sheets: `1`
- Qwen generations: `1`
- Qwen raw: `AD 16–32`, confidence `0.90`
- Measured transition alignment: `AD 20–30`
- Final safe removal: `20–30`
- Final KEEP: `0–20`, `30–50` = `40.000s`
- False INTRO/OUTRO removal: `0`

### Real 16m44s video

- Source: existing local job `5a347a09e47640baac395925a45511e4`
- Duration: `1004.134s`
- Coarse frames: `101`
- Candidate count: `1`
- Fine frames: `8`
- Contact sheets: `1`
- Generations: `1`
- Visual inspection around `970–1000s`: substantive conclusion, subscribe CTA and end screen.
- Raw Qwen called it AD; conservative last-120s guard retained it.
- Final semantic removal: none; real content preserved.

## CUDA và bộ nhớ

Long-video final run:

- Peak allocated: `8,014,389,248 bytes` (`7.46 GiB`)
- End allocated: `6,933,963,264 bytes` (`6.46 GiB`)
- Reserved: `8,183,087,104 bytes` (`7.62 GiB`; allocator varies per run)
- RTX 5060 Ti 16GB: không OOM, còn headroom lớn.

Artifact ghi riêng `peak_vram_bytes`, `allocated_vram_bytes`, `reserved_vram_bytes`, frame counts, contact sheets, candidate count và generation count.

## Whole-pipeline benchmark

Video thật `1004.134s`, download bị loại khỏi phép đo:

- Silence analysis: `37.579s` (report hiện có, không chạy lại detector)
- Semantic cold stage: `64.869s`
- Formatter render + validation: `100.892s`
- Tổng: `203.340s` (`3m23.34s`)
- Target: `<=240s` — PASS, dư `36.66s`.

Formatter giữ nguyên NVDEC/CUDA/NVENC:

- Part 1 render `25.017s`, duration error `0.020s`, A/V delta `0.031s`
- Part 2 render `44.747s`, duration error `0.030s`, A/V delta `0.030s`
- Part 3 render `29.602s`, duration error `0.001s`, A/V delta `0.001s`

## Fail-safe và compatibility

- Qwen load/OOM/timeout/invalid response vẫn tạo `SEMANTIC_CLEANER_SKIPPED`, giữ original KEEP và tiếp tục formatter.
- `semantic_segments.json` giữ contract cũ và chỉ thêm diagnostics.
- `pipeline_report.original.json` vẫn được bảo toàn.
- Stale `output_duration` của clean-master cũ bị bỏ khi FINAL KEEP được tạo; formatter dùng deterministic `expected_output_duration`/mapping.
- Không sửa Silence Cutter, formatter, splitter, renderer, crop, pitch hoặc NAS.

## Remaining bottleneck

- AWQ_TORCH fine generation: `~28–34s`.
- Cold model initialization: thường `~14–17s`, đã có outlier `49.63s` trên Windows.
- Full NVDEC coarse extraction: `~14–15s` cho 1004s AV1.
- Warm persistent worker sẽ bỏ phần model load và đưa long-video stage xuống `48.79s`, nhưng chưa triển khai để giữ kiến trúc một subprocess/video và tránh daemon mới.

## Regression

- Full suite: `315 passed, 1 skipped, 96 subtests passed in 27.81s`.
- Controlled AD removal: PASS.
- No-candidate skips fine generation: PASS.
- Candidate-only fine scan, absolute timestamp mapping, OOM batch fallback: PASS.
- Fail-safe original KEEP fallback: PASS.
- Three formatter outputs and A/V validation: PASS.
