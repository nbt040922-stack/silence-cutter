# Báo cáo TikTok 3-Part Formatter — Phase A

## Phạm vi hoàn thành

Phase A đã hoàn thành phần lập kế hoạch và ảnh xem trước. Chưa render ba video đầu ra cuối cùng. Các detector, luật dựng timeline và renderer hiện hữu không bị chạy lại hoặc thay đổi hành vi biên tập.

## Tích hợp pipeline

- Đầu vào là một job Silence Cutter có trạng thái `DONE`.
- Video sạch lấy từ `rendered.mp4` của job.
- Các đoạn KEEP và ánh xạ nguồn lấy từ `pipeline_report.debug.render.segments`.
- Các khoảng lời nói hợp nhất lấy từ `pipeline_report.debug.union_intervals`.
- Silero, SenseVoice và ASR không được gọi lại.
- Có thể chạy bằng `python -m formatter <job>` hoặc RPC `plan_tiktok_formatter` của backend.

## Kết quả chia phần trên job thật

Video sạch dài `1419.566667s`, vượt cổng tự động `1200s`. Kết quả replan hiện tại là `formatter_status = NEEDS_REVIEW`; không tự tạo part hoặc preview. Người dùng có thể chọn **Format Anyway** trong ứng dụng để lập kế hoạch ba phần.

Kế hoạch Phase A trước khi áp dụng cổng 20 phút là:

| Phần | Bắt đầu | Kết thúc | Thời lượng |
|---|---:|---:|---:|
| パート1 | 0.000000 | 554.150000 | 554.150000 |
| パート2 | 554.150000 | 1035.910000 | 481.760000 |
| パート3 | 1035.910000 | 1419.566667 | 383.656667 |

Biên thứ nhất là một edit junction có `1.0s` im lặng trong nguồn. Biên thứ hai là quãng nghỉ `0.12s` lấy từ timeline speech hợp nhất có sẵn. Không có khoảng hở, chồng lấn hoặc mất phần cuối video.

## Chính sách thời lượng

- `clean_video_duration <= 1200s`: tự lập kế hoạch đúng ba phần.
- `clean_video_duration > 1200s`: `NEEDS_REVIEW`, gửi thông báo Windows và không lập kế hoạch cho đến khi chọn **Format Anyway**.
- Mục tiêu lý tưởng mỗi phần: `180–360s`.
- `360–420s`: cho phép với penalty nhỏ khi có biên hội thoại/edit sạch hơn.
- Ngoài `180–420s`: penalty tăng dần; cân bằng cực đoan bị tránh khi có phương án tự nhiên hợp lý.
- Edit junction, độ dài silence nguồn và cấu trúc speech sạch được ưu tiên trước độ gần một phần ba.

## Layout và tiêu đề

- Canvas: 1080×1920, nền đen.
- Video sạch 16:9 được center-crop thành 4:3 rồi đặt ở giữa; không kéo méo và không tracking.
- Banner tiêu đề màu trắng ở phía trên, ôm sát nội dung và có safe margin.
- Dùng đúng tiêu đề gốc cho cả ba phần; không dùng LLM.
- Đo chiều rộng chữ thật bằng Pillow; tự giảm cỡ và xuống dòng, tối đa ba dòng.
- Nhận diện ngôn ngữ bằng script/từ khóa xác định, không dùng metadata hoặc LLM.
- Đóng gói font OFL Poppins, Noto Sans JP/KR/SC/TC và Noto Emoji.
- Banner nhãn phần được bản địa hóa và ôm sát nội dung.

## Kết quả bản vá content-fit

| Hạng mục | Trước | Sau |
|---|---|---|
| Title banner | `x=81, y=110, 918×330` | `x=82, y=100, 916×216` |
| Part banner | `x=260, y=1475, 560×140` | `x=369, y=1254, 342×105` |
| Video Y | `555` | `380` |
| Ngôn ngữ | chưa phát hiện | `ja`, confidence `0.99` |
| Nhãn phần | `PART 1` | `パート1` |

Title banner hiện có đúng ba dòng, chiều rộng bằng dòng dài nhất cộng padding và vẫn giữ lề ngang an toàn. Video giữ nguyên `1080×810` (4:3). Khoảng cách title–video và video–part banner đều là `64px`; không chồng lấn.

## Tệp thay đổi trong Phase A

- `formatter/__init__.py`
- `formatter/__main__.py`
- `formatter/planner.py`
- `formatter/preview.py`
- `formatter/assets/fonts/Poppins-SemiBold.ttf`
- `formatter/assets/fonts/Poppins-OFL.txt`
- `formatter/assets/fonts/NotoSansJP-Variable.ttf`
- `formatter/assets/fonts/NotoSansJP-OFL.txt`
- `formatter/assets/fonts/NotoSansKR-Variable.ttf`
- `formatter/assets/fonts/NotoSansSC-Variable.ttf`
- `formatter/assets/fonts/NotoSansTC-Variable.ttf`
- `formatter/assets/fonts/NotoEmoji-Variable.ttf`
- `formatter/assets/fonts/NotoEmoji-OFL.txt`
- `backend/job_runner.py`
- `scripts/build_internal_rc.ps1`
- `pyproject.toml`
- `requirements-production.txt`
- `tests/test_formatter.py`
- `tests/test_job_runner.py`
- `format_plan.json`
- `part1_preview.png`
- `FORMATTER_PHASE_A_REPORT.md`

## Kiểm thử

Lệnh: `.venv\\Scripts\\python.exe -m pytest -q`

Kết quả chính xác sau bản vá thời lượng: `208 passed, 1 skipped, 74 subtests passed in 17.25s`.

Desktop web build: PASS. Tauri/Rust `cargo check`: PASS.

Các kiểm thử mới bao phủ nhãn Nhật/Anh/Hàn/Việt/Tây Ban Nha và fallback, cùng các ngôn ngữ còn lại được hỗ trợ; banner theo kích thước chữ, một/ba dòng, căn giữa, lề an toàn, không chồng lấn và video 1080×810. Các hồi quy planner/job DONE/detector reuse vẫn đạt.

## Artifact Phase A

- Kế hoạch: `D:\Silence_cutter\format_plan.json`
- Ảnh xem trước: không tạo lại vì job hiện tại có trạng thái `NEEDS_REVIEW`; preview cũ đã được xóa để tránh dùng nhầm.
