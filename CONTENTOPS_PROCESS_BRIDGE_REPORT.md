# Báo cáo Content Ops Process Bridge

## Kiến trúc và core được tái sử dụng

`contentops_process_bridge.py` là HTTP adapter Python chuẩn, bind cứng `127.0.0.1`. Adapter gọi trực tiếp public production entry point hiện có:

```python
from production import process_video
process_video(source_file, temporary_output, report_path=report)
```

Luồng Content Boundary Detector, tight2, Silero VAD, SenseVoice safety, union/KEEP/CUT và production renderer không bị sao chép hay sửa. Formatter, crop 9:16, pitch và desktop/manual flow cũng không bị thay đổi.

## API

Port lấy từ `CONTENTOPS_PROCESS_BRIDGE_PORT`, mặc định `8791`. Concurrency lấy từ `SILENCE_PROCESS_MAX_CONCURRENCY`, mặc định `1`.

Khởi chạy service bằng môi trường production hiện có:

```powershell
.\.venv_asr_test\Scripts\python.exe contentops_process_bridge.py
```

```http
POST /api/process-jobs
GET /api/process-jobs/{external_id}
GET /health
```

Bridge chỉ nhận handoff metadata; không nhận token, cookie, password hoặc browser session. Source phải là regular file tuyệt đối. Output phải là thư mục tuyệt đối đang khả dụng; bridge không fallback local.

## Idempotency và restart

Mapping `handoff_id → contentops-process-<handoff_id> → processing record` được persist nguyên tử trong `workspace/contentops-process-jobs.json` dưới `SILENCE_CUTTER_DATA_DIR`.

- Duplicate handoff trả record hiện có; không render lần hai.
- Output path được giữ ngay khi enqueue để hai active jobs không collision.
- Sau restart, active record có final file sẽ chuyển `DONE`.
- Active record chưa có final sẽ xóa partial cũ và chạy lại đúng một lần với external ID cũ.
- Source luôn được giữ.

## Filename và atomic finalization

Tên dùng `video_title`, fallback `video_id`, loại ký tự Windows cấm và bảo vệ reserved device name. Collision ngoài mapping thêm `_video_id`; collision tiếp theo thêm `_handoff_id`.

Core ghi vào `<name>.processing.mp4`. Chỉ sau khi core hoàn tất và file tồn tại, bridge dùng `os.replace()` sang `<name>.mp4`. Thất bại xóa partial, không tạo final mới và trả mã ngắn như `SOURCE_FILE_MISSING`, `NAS_UNAVAILABLE` hoặc `PROCESSING_FAILED`.

## Tests

- Full Silence Cutter regression: **292 passed, 1 skipped, 93 subtests passed**
- Bridge tests: **7 passed**
- Bao phủ POST/idempotency, validation, missing source, NAS unavailable, filename/collision, partial/atomic rename, failure cleanup, stale recovery, GET và localhost lifecycle.
- Existing files thuộc `production/`, `formatter/`, `backend/job_runner.py` không bị adapter sửa.

## Manual validation

HTTP bridge thật xử lý `D:\ContentOps_Work_Test\9001\source.mp4` bằng production core thật:

- State: `QUEUED → PROCESSING → FINALIZING → DONE`
- Exact final path: `D:\Silence_Output_Test\Me at the zoo.mp4`
- Input 19,014 giây → output 5,947 giây; loại 13,1 giây
- Một final MP4; không còn `.processing.mp4`; source còn nguyên

CLI production bình thường ngoài Content Ops cho cùng duration/output behavior. NAS thật chưa chạy vì YT_NOTIFI chưa cấu hình `NAS_OUTPUT_ROOT`; không có local fallback được dùng thay cho bài test NAS.
