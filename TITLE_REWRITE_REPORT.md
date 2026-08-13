# Local Title Rewrite Report

## Kết quả

Local Title Rewrite được chèn sau Semantic Cleaner và trước format plan. Cả luồng enhanced và normal dùng cùng một `filename_base` cho mọi PART. Renderer chỉ đọc giá trị này; crop, scale, banner, audio, CUDA, NVDEC, NVENC và chất lượng render không đổi.

## Kiến trúc

```text
original title
  -> persistent localhost Qwen worker (task=title_rewrite, text-only)
  -> strict JSON parser
  -> deterministic Windows/NAS sanitizer
  -> title_rewrite.json
  -> format_plan.json filename_base
  -> <filename_base>_PART_N.mp4
```

Model dùng lại: `Qwen/Qwen2.5-VL-7B-Instruct-AWQ`. Title stage không tạo server, không tải model cục bộ và không gửi frame. Worker xử lý tuần tự bằng queue sẵn có.

## Prompt contract

- Giữ ngôn ngữ gốc, chủ đề, tên riêng và số hữu ích.
- Bỏ clickbait, ALL CAPS thừa, emoji, hashtag, dấu trang trí và filler.
- Không dịch, không bịa, không tạo hook/SEO/description.
- Chỉ trả JSON `{"rewritten_title":"..."}`.
- `do_sample=False`, `max_new_tokens=32`.

## Artifact và lỗi

`title_rewrite.json` lưu title gốc, title rewrite, basename đã sanitize, status, model và timing. Retry/restart đọc artifact trước nên không sinh lại và không đổi đường dẫn.

Worker unavailable, timeout, JSON lỗi hoặc title rỗng đều dùng title gốc đã sanitize với `status=FALLBACK`; video vẫn tiếp tục. Sanitizer giữ Unicode an toàn, thay ký tự Windows cấm, bỏ control/emoji, gộp whitespace, bỏ dấu chấm cuối, cắt tối đa 120 ký tự và dùng `video_<video_id>` khi rỗng. Collision thêm cùng một `video_id` suffix cho toàn bộ PART.

## Acceptance thật

Nguồn: video AV1 1.448,154 giây hiện có trong workspace.

- Worker trước job: `READY`, `model_load_count=1`, `request_count=0`.
- Original: `50 *NEW* Dollar Tree Deals you NEED to buy! (from the pro!)`.
- Rewritten: `50 Dollar Tree Deals`.
- Status: `APPLIED`.
- Title generations/video: `1`.
- Queue wait: `0,000002 s`.
- Generation: `11,635 s`.
- Title stage total: `11,639 s`.
- Model loads do title stage gây ra: `0`; worker vẫn `model_load_count=1`.
- Full enhanced processing: `220,764 s`; lệnh ngoài đo `223,1 s`.
- Formatter render: `68,516 s`, 3 job song song.
- Retry artifact: worker requests `4 -> 4`, model loads `1 -> 1`.

Filename thực:

```text
50 Dollar Tree Deals_PART_1.mp4
50 Dollar Tree Deals_PART_2.mp4
50 Dollar Tree Deals_PART_3.mp4
```

Validation:

- PART mapping 1/2/3 đúng.
- Planned durations: `237,730 s`, `240,000 s`, `240,000 s`.
- Max duration error: `0,003008 s`.
- Max A/V delta: `0,007000 s`.
- Video: H.264 NVENC, 1080x1920; audio: AAC 48 kHz.
- A/V validation: PASS.
- Title latency target mềm `<3 s`: **chưa đạt** trên máy acceptance (`11,635 s`). Thử 16 và 12 token vẫn lần lượt `11,703 s` và `11,334 s`, nên giữ giới hạn an toàn 32 token; không đổi chất lượng để chạy theo benchmark.

## Kiểm thử

- Full suite cuối: `349 passed, 1 skipped, 102 subtests passed in 43.45s`.
- Bao phủ worker task, model reuse, text-only, một generation, JSON/fallback/timeout/empty, artifact reuse, Unicode, truncation, collision, normal 2 PART và enhanced 3 PART.
- `compileall` và `git diff --check`: PASS.
