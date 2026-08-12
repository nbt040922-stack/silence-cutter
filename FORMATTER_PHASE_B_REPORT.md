# FORMATTER PHASE B REPORT

## Luồng render trực tiếp

- Job tự động dưới hoặc bằng 20 phút chỉ chạy phân tích để lấy timeline KEEP/CUT.
- Planner giữ thời gian video sạch logic và ánh xạ từng part về đúng các đoạn KEEP của source.
- Mỗi PART được concat, bố cục, xử lý audio và encode trực tiếp từ source trong một lần encode cuối.
- Không chạy lại Silero, SenseVoice, intro/outro hay phân tích silence.
- `clean_master.mp4` chỉ được tạo cho `NEEDS_REVIEW`, tùy chọn `Keep Clean Master`, hoặc chế độ Silence Cutter độc lập.
- Source, báo cáo và trạng thái job được giữ nguyên nếu formatter lỗi.
- Job/report ghi `intermediate_render_skipped`, `estimated_time_saved` và `estimated_disk_saved`.
- Full regression: `253 passed, 1 skipped, 90 subtests passed in 19.13s`.
- Frontend Vite production build: `PASS`.

## Chính sách số part hiện tại

- Clean video `< 600s`: `part_count = 2`
- Clean video `600–1200s`: `part_count = 3`
- Clean video `> 1200s`: `NEEDS_REVIEW`; chỉ lập/render kế hoạch khi người dùng chọn Format Anyway
- Planner, renderer, nhãn bản địa hóa, tiến độ và ETA đều đọc `part_count`; không giả định luôn có ba part.

## Kết quả

- Job: `00eec09aeb5244f0a27a1028287cb084`
- Trạng thái Silence Cutter: `DONE`
- Trạng thái formatter: `DONE`
- Nguồn render: `D:\Vlog\jobs\00eec09aeb5244f0a27a1028287cb084\rendered.mp4`
- Kế hoạch: `D:\Vlog\jobs\00eec09aeb5244f0a27a1028287cb084\format_plan.json`
- Tổng thời gian render: `182.574s`
- Video: `h264_nvenc`, preset `p4`, CQ `23`
- Audio: AAC `192k`
- Kích thước: `1080x1920`

## Đầu ra

| Part | Nhãn | Mốc sạch | Dự kiến | Thực tế | Sai số | A/V delta | Render | Dung lượng |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | パート1 | 0.00–205.66s | 205.660s | 205.667s | 0.0067s | 0.0067s | 35.387s | 80,233,215 bytes |
| 2 | パート2 | 205.66–558.13s | 352.470s | 352.470s | 0.0000s | 0.0033s | 69.381s | 145,898,413 bytes |
| 3 | パート3 | 558.13–851.10s | 292.970s | 292.967s | 0.0033s | 0.0037s | 77.421s | 101,754,442 bytes |

- `D:\Vlog\jobs\00eec09aeb5244f0a27a1028287cb084\formatted\PART_1.mp4`
- `D:\Vlog\jobs\00eec09aeb5244f0a27a1028287cb084\formatted\PART_2.mp4`
- `D:\Vlog\jobs\00eec09aeb5244f0a27a1028287cb084\formatted\PART_3.mp4`

Tất cả đầu ra có video H.264, audio AAC, giữ đúng ba khoảng liên tục từ kế hoạch, không chạy lại Silero/SenseVoice/ASR. Video sạch gốc vẫn được giữ nguyên.

## Kiểm thử

- Python regression: `253 passed, 1 skipped, 90 subtests passed in 19.13s`
- Formatter targeted: `31 passed, 17 subtests passed in 3.41s`
- Frontend Vite production build: `PASS`
- Smoke test ba MP4 thật: `PASS`

## Audio profile — áp dụng từ lần formatter render tiếp theo

- `audio_effect_enabled`: `true`
- `pitch_ratio`: `1.03`
- EQ: `250 Hz -1.2 dB (Q 0.8)`, `3 kHz +1.0 dB (Q 0.8)`, `8 kHz +0.5 dB (Q 0.8)`
- Limiter: `0.95`, không auto-level
- Final sample rate: `48000 Hz`
- Audio codec: `AAC 192k`
- Duration validation: `PASS` (sai số trong ngưỡng `0.08s` ở integration test)
- A/V sync validation: `PASS` (delta trong ngưỡng `0.08s` ở integration test)
- PCM validation: `PASS` (không NaN/giá trị vô hạn, peak không vượt `1.0`)
- `clean_master.mp4`: không bị sửa; hiệu ứng chỉ tồn tại trong `PART_1/2/3.mp4`

Ba MP4 smoke-test dài ở phần trên được tạo trước bản vá audio này và không bị ghi đè. Hồ sơ audio mới được ghi vào `format_plan.json` và `job.json` khi lần render tiếp theo bắt đầu.
