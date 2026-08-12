# Báo cáo tái sử dụng luồng chia part hiện hữu cho Content Ops

Ngày kiểm tra: 2026-08-12

## Luồng hiện hữu được tái sử dụng

Luồng chạy thủ công của Silence Cutter đi qua:

1. `backend.job_runner._pipeline()` chạy `python -m production --debug --analysis-only` để tạo `pipeline_report.json` cùng timeline KEEP.
2. `backend.job_runner._format_done_job()` gọi `formatter.planner.plan_done_job()`.
3. `formatter.planner.plan_done_job()` dùng chính `plan_parts()`, chính quy tắc số part/thời lượng, mốc cắt, tiêu đề, layout và crop hiện hữu.
4. `formatter.renderer.render_format_plan()` dùng `build_render_jobs()`, tạo tên `PART_1.mp4`... và render bằng chiến lược FFmpeg hiện hữu.
5. Renderer tạo file tạm `.PART_n-<uuid>.mp4`, kiểm tra media rồi dùng `os.replace()` để hoàn tất từng file.

Content Ops bridge chỉ tạo bộ hồ sơ công việc tương thích, gọi đúng `plan_done_job()` rồi `render_format_plan()`, và trả nguyên danh sách `formatted_outputs[].path`. `job.output_dir` được chuyển thẳng thành `output_folder`; bridge không tính lại thư mục kênh, không có bộ chia hoặc quy tắc đặt tên/collision riêng.

Không thay đổi Content Boundary Detector, tight2, Silero, SenseVoice, KEEP/CUT, formatter, crop, pitch, FFmpeg, ngưỡng, quy tắc thời lượng, cân bằng part hay thuật toán chọn mốc cắt.

## Kiểm tra thực tế cùng một nguồn

Nguồn: `D:\ContentOps_Work\1\clean master.mp4` (851.141 giây).

Luồng Silence Cutter thủ công, job `2987f30a6eaa4614bf16f21e07a065ff`:

- `D:\Silence_Part_Manual_Output\clean master_2987f30a\PART_1.mp4` — 227.700 giây
- `D:\Silence_Part_Manual_Output\clean master_2987f30a\PART_2.mp4` — 305.100 giây
- `D:\Silence_Part_Manual_Output\clean master_2987f30a\PART_3.mp4` — 298.265 giây

Luồng Content Ops thật tới NAS:

- `\\192.168.1.18\Team 1\ContentOps\TN004UK - Nhật\PART_1.mp4` — 227.700 giây
- `\\192.168.1.18\Team 1\ContentOps\TN004UK - Nhật\PART_2.mp4` — 305.100 giây
- `\\192.168.1.18\Team 1\ContentOps\TN004UK - Nhật\PART_3.mp4` — 298.265 giây

Hai lần chạy có cùng 3 part và cùng mốc kế hoạch `0–227.69`, `227.69–532.77`, `532.77–831.041`. Các plan khớp hoàn toàn ở `part_count`, `parts`, `part_boundaries`, `layout`, `audio_profile`, encoder, decoder và thiết bị filter. Mọi part đều H.264/AAC, 1080x1920, 48 kHz, SAR 1:1, DAR 9:16, render bằng `h264_nvenc` và giải mã `h264_cuvid`.

NAS có đúng một bộ `PART_1.mp4`–`PART_3.mp4`, không còn `.PART_*.mp4` hoặc `.processing`. Submit/restore cùng `handoff_id=1` trả lại `external_id=contentops-process-1`, không tạo công việc hay bộ part mới.

## Regression

- Toàn bộ Silence Cutter: `291 passed, 1 skipped, 93 subtests passed`.
- Luồng thủ công hoàn tất với `status=DONE`, `formatter_status=DONE`.
- Kiểm tra bridge chuyên biệt: `6 passed`.
