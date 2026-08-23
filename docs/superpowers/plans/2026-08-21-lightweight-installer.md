# Kế hoạch triển khai Installer nhẹ

> Dành cho worker triển khai: thực hiện từng hạng mục, chạy kiểm thử liên quan sau mỗi thay đổi, không mở rộng phạm vi.

## Mục tiêu

Tạo bản Windows installer nhẹ, một lần bấm để cài và chạy. Installer không nhúng các model lớn; lần chạy đầu sẽ quét tài nguyên có sẵn trong thư mục dự án/máy, tự chọn bản Qwen phù hợp, và chỉ tải phần còn thiếu từ nguồn đã khai báo. Giữ nguyên pipeline Silence Cutter hiện tại.

## Kiến trúc và giới hạn khóa

- Nguồn sự thật là `D:\Silence_cutter`, không chỉ các file đã commit.
- Giữ nguyên Silero + SenseVoiceSmall/FSMN-VAD, fusion, renderer, downloader và editorial logic.
- Qwen chạy worker tại `127.0.0.1:8792`; bridge ContentOps chạy tại `127.0.0.1:8791`.
- Tauri khởi động theo thứ tự: Qwen (nếu cần) → bridge → backend → giao diện.
- Không đưa model lớn, cache, workspace, log, benchmark hoặc profile YouTube vào installer.
- Không ghi cookie, secret, đường dẫn profile hoặc dữ liệu nhạy cảm vào report/job.

## Hạng mục triển khai

### 1. Manifest và quét tài nguyên

Tạo `installer/model_manifest.json` và package `installer_setup/`.

- `models.py`: `ModelSpec`, `ModelRecord`, trạng thái `found/verified/missing`.
- `inventory.py`: quét `local_models`, Tauri release resources, ModelScope/Hugging Face cache và `%LOCALAPPDATA%\\SilenceCutter\\models`.
- Ghi nhận model Silence (Silero, SenseVoiceSmall, FSMN-VAD) riêng với các ứng viên Qwen.
- ModelSpec phải có version, size, checksum tùy chọn, nguồn tải tùy chọn; thiếu nguồn thì báo rõ `MODEL_SOURCE_NOT_CONFIGURED`, không bịa URL.
- Test: phát hiện model trong nhiều root, không nhận nhầm thư mục, không phụ thuộc máy phát triển.

### 2. Xác minh và tải model lần đầu

Tạo `installer_setup/downloads.py` và test.

- Tải vào file tạm trong thư mục model người dùng, hỗ trợ tiếp tục tải.
- Xác minh checksum/kích thước trước khi đổi tên nguyên tử.
- Không tải lại nếu bản hợp lệ đã tồn tại.
- Lỗi mạng, thiếu nguồn, thiếu dung lượng phải có mã lỗi và hướng dẫn tiếng Việt; không làm hỏng model đang dùng.

### 3. Probe phần cứng và chọn Qwen

Tạo `installer_setup/hardware.py`, `recommendation.py` và test.

- Tái sử dụng `backend.hardware.probe_hardware`; bổ sung Windows, RAM, VRAM, CUDA, NVENC, dung lượng trống nếu cần.
- Xếp hạng ứng viên Qwen theo VRAM/CUDA/độ phù hợp thực tế; không quyết định chỉ từ tên GPU.
- RTX 2080 Super/3060 dùng GPU nếu CUDA, model load và NVENC hợp lệ; lỗi CUDA chỉ chuyển inference sang CPU, không chuyển toàn bộ pipeline.

### 4. Readiness và first-run setup

Tạo `installer_setup/readiness.py`, `cli.py` và test.

- Kiểm tra runtime đóng gói, FFmpeg/ffprobe, model Silence, model Qwen, cổng 8792/8791.
- Luồng first-run: quét → đề xuất Qwen → tải phần thiếu → kiểm tra model → báo sẵn sàng.
- Hiển thị rõ các trạng thái `READY`, `MODEL_MISSING`, `MODEL_SOURCE_NOT_CONFIGURED`, `CUDA_UNAVAILABLE`, `FFMPEG_MISSING`, `PORT_IN_USE`.
- Có chế độ dry-run để kiểm tra máy đồng nghiệp mà không chạy job.

### 5. Tích hợp Tauri và tiến trình nền

Sửa tối thiểu `desktop/src-tauri/src/lib.rs`.

- Quản lý child process cho Qwen worker, ContentOps bridge và backend.
- Thêm kiểm tra health `/health` cho Qwen và endpoint bridge; chỉ mở UI sau khi dependency sẵn sàng.
- Shutdown có thứ tự, không giết tiến trình ngoài ứng dụng.
- Có log lỗi thân thiện, không in cookie/token.
- Test contract cho thứ tự khởi động, health timeout, và dọn child process.

### 6. Tự phục hồi an toàn

Mở rộng bounded restart/backoff hiện có trong `qwen_worker/supervisor.py` và lớp Rust.

- Chỉ restart worker lỗi; không restart vô hạn.
- Sau giới hạn retry, UI báo `QWEN_UNAVAILABLE` và hướng dẫn chạy lại setup.
- Bridge/backend vẫn giữ lỗi có thể truy nguyên.

### 7. Giao diện tiếng Việt

Sửa `desktop/src/index.html`, `desktop/src/main.js`, `desktop/src/styles.css` và command cần thiết.

- Màn hình setup hiển thị phần cứng, model đã tìm thấy, model còn thiếu, tiến độ tải và nút thử lại.
- Nút `Kiểm tra môi trường`, `Tải model còn thiếu`, `Chạy thử job`.
- Không hiển thị full UUID làm tên chính; giữ display name hiện có.
- Không có tùy chọn Anonymous/Profile cho downloader nếu production flow đã khóa profile.

### 8. Đóng gói bản nhẹ một-click

Sửa `scripts/build_internal_rc.ps1`, `scripts/build_one_click_installer.ps1`, `installer/SilenceCutter.iss`; thêm script staging nếu cần.

- Stage chỉ runtime, Python packages, FFmpeg, yt-dlp/Deno theo cấu hình hiện tại, Tauri app và manifest.
- Loại trừ `local_models`, model cache, workspace, benchmark, test output, YouTube profile và log.
- Installer tạo thư mục dữ liệu người dùng dưới `%LOCALAPPDATA%\\SilenceCutter`.
- Đầu ra dự kiến: `release/SilenceCutter_Setup.exe`.
- Bản nhẹ của máy phát triển dùng cùng artifact nhưng cho phép dùng model đã có trong workspace qua inventory.

### 9. Smoke test và hồi quy

- Cài vào profile sạch trên Windows; mở app bằng một click.
- Kiểm tra startup Qwen → bridge → backend, tải/nhận model, chạy dry-run và một job mẫu.
- Chạy test installer/setup, test bridge/Qwen, sau đó toàn bộ test hiện có.
- Kiểm tra `git diff --check`, manifest không chứa đường dẫn `C:\\Users\\nbt04`/`D:\\Silence_cutter`, và artifact không chứa model lớn.
- Ghi báo cáo `docs/reports/lightweight-installer-build.md` gồm file build, kích thước, hash, kết quả smoke/regression và giới hạn còn lại.

## Tiêu chí nghiệm thu

1. Máy đồng nghiệp không cần cài Python, FFmpeg, pip package hay tự tải model bằng tay.
2. Installer nhẹ không chứa model lớn; first-run báo đúng thiếu/thừa và có đường dẫn xử lý.
3. Mọi model tìm được trong thư mục dự án được ưu tiên dùng, không bị xem là thiếu chỉ vì chưa commit.
4. Qwen phù hợp phần cứng được chọn tự động; lỗi CUDA chỉ ảnh hưởng component inference.
5. Qwen, bridge và backend khởi động đúng thứ tự; lỗi có thông báo tiếng Việt.
6. Pipeline Silence Cutter và các dịch vụ người dùng đang chạy không bị dừng ngoài phạm vi yêu cầu.

