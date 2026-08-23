# Thiết kế Installer nhẹ và thiết lập lần đầu

## Mục tiêu

Đóng gói ứng dụng hiện tại thành:

```text
SilenceCutter_Setup.exe
```

Installer phải nhẹ, không bắt đồng nghiệp cài Python, pip, Git, FFmpeg hoặc
công cụ lập trình.

Không thay đổi pipeline sản xuất:

```text
Silero + SenseVoiceSmall -> OR/UNION -> KEEP/CUT -> NVENC
```

Nguồn sự thật là toàn bộ thư mục local `D:\Silence_cutter`, gồm cả file chưa
push, `local_models`, resources đã build, cache model và script đóng gói.

## Không làm

- Không viết lại thuật toán Silence Cutter.
- Không đổi Silero, SenseVoice, timeline, Semantic Cleaner, formatter hoặc renderer.
- Không tự thêm model Qwen không có trong thư mục dự án hoặc manifest.
- Không tạo kiến trúc Qwen thứ hai.
- Không xóa model, cấu hình hoặc dữ liệu người dùng khi cập nhật.
- Không đóng gói cache, log, benchmark, file test hoặc model trùng lặp.

## Thành phần thực tế đã phát hiện

| Thành phần | Vị trí/triển khai hiện tại |
|---|---|
| Giao diện | `desktop/src-tauri` |
| Backend | `backend`, `production` |
| Cắt khoảng lặng | `silence_cutter`, `speech_detector` |
| Semantic Cleaner | `semantic_cleaner` |
| Enhanced selector | `long_video_selector`, `enhanced_content_flow` |
| Qwen worker | `qwen_worker`, cổng `8792` |
| Content Ops bridge | `contentops_process_bridge.py`, cổng `8791` |
| Python runtime | `scripts/build_internal_rc.ps1` |
| FFmpeg/ffprobe | `silence_cutter.runtime_paths` |
| Hardware probe | `backend/hardware.py` |
| SenseVoiceSmall | `desktop/src-tauri/target/release/resources/models/SenseVoiceSmall` |
| FSMN-VAD | `desktop/src-tauri/target/release/resources/models/fsmn-vad` |
| Qwen hiện có | `local_models/Qwen2.5-VL-7B-Instruct-AWQ` |

Hiện workspace chỉ có **một Qwen model thật**. Nếu sau này thêm model khác vào
project và khai báo trong manifest, wizard sẽ tự phát hiện.

## Chính sách model

Tạo manifest tại:

```text
installer/model_manifest.json
```

Mỗi model cần có:

```json
{
  "id": "qwen2.5-vl-7b-awq",
  "display_name": "Qwen2.5-VL-7B-Instruct-AWQ",
  "kind": "qwen",
  "source": null,
  "sha256": null,
  "size_bytes": 0,
  "min_vram_mib": 0,
  "min_ram_mib": 0,
  "relative_install_dir": "models/Qwen2.5-VL-7B-Instruct-AWQ",
  "required_for": ["enhanced_content_selection"]
}
```

Model tải từ Internet bắt buộc có nguồn tải, SHA256, kích thước và yêu cầu
phần cứng. Thiếu metadata thì build phải dừng, không được báo thành công giả.

Installer tìm model ở các nơi:

- `D:\Silence_cutter\local_models`
- `desktop\src-tauri\target\release\resources\models`
- cache ModelScope/Hugging Face
- `%LOCALAPPDATA%\SilenceCutter\models`

Model lớn mặc định không nhúng vào installer. Model chỉ được tải khi có trong
manifest và máy đủ điều kiện.

## Nội dung installer

Installer chứa:

- File Tauri và giao diện.
- Python runtime nhúng.
- Package production cần thiết.
- FFmpeg/ffprobe và binary native.
- Asset nhỏ bắt buộc.
- Manifest model.
- Video benchmark phần cứng nhỏ.

Không chứa:

- Qwen weights lớn mặc định.
- Cache phát triển.
- Log.
- Benchmark cũ.
- Model trùng.
- Source/test không cần cho runtime.

## Wizard thiết lập lần đầu

Wizard hiển thị:

1. GPU, model GPU, VRAM, driver NVIDIA, CUDA.
2. CPU, số lõi/luồng, RAM, Windows, dung lượng ổ đĩa.
3. Model đã có và model còn thiếu.
4. Model Qwen được khuyến nghị.
5. Lựa chọn cho phép người dùng đổi model.
6. Dung lượng tải xuống và dung lượng ổ đĩa cần dùng.
7. Tiến trình tải và kiểm tra SHA256.
8. Trạng thái Qwen `127.0.0.1:8792`.
9. Trạng thái Silence Cutter `127.0.0.1:8791`.
10. Trạng thái toàn bộ pipeline.

Wizard dùng lại `backend.hardware.probe_hardware` và
`silence_cutter.runtime_paths`, không tạo hệ thống dò phần cứng thứ hai.

Các thao tác cần có API kiểm thử được:

```python
probe_installation(data_dir, resource_dir)
recommend_qwen(inventory, hardware)
download_model(entry, data_dir, progress_callback)
verify_model(entry, path)
validate_pipeline(readiness_options)
```

## Cách chọn Qwen

Thứ tự kiểm tra:

1. Model tồn tại và checksum đúng.
2. VRAM, RAM và dung lượng đĩa đủ.
3. Model khởi tạo thật thành công.
4. Trong các model đủ điều kiện, chọn model phù hợp nhất.

Không quyết định chỉ dựa vào tên GPU. Nếu không model nào chạy được, wizard báo
`NOT_READY`, nêu rõ GPU, VRAM, RAM, model và lý do lỗi.

## Khởi động dịch vụ

Chỉ tạo **một mục khởi động Windows** cho ứng dụng Tauri. Tauri quản lý thứ tự:

```text
Windows
  ↓
Qwen worker :8792
  ↓ chờ Qwen READY
Content Ops bridge :8791
  ↓
Backend worker
  ↓
Pipeline READY
```

Không tạo hai shortcut độc lập gây chạy đua.

Tauri sở hữu các process con và dừng chúng khi thoát. Cấu hình khởi động lưu tại:

```text
%LOCALAPPDATA%\SilenceCutter\config.json
```

Người dùng có thể bật/tắt tự khởi động. Tắt tự khởi động chỉ gỡ đăng ký Windows,
không xóa model hay cấu hình.

## Khôi phục lỗi

- Qwen chết: restart có giới hạn, load model lại, warm-up, kiểm tra `READY`.
- Bridge/backend chết: restart có giới hạn, kiểm tra health.
- Không restart vô hạn.
- Không tự dừng các dịch vụ khác của người dùng.

## Điều kiện báo cài đặt thành công

Chỉ báo thành công khi tất cả đạt:

- Ứng dụng đã cài.
- Runtime đã cài.
- Model bắt buộc có đủ.
- Checksum đúng.
- Qwen khởi động và `:8792` báo `READY`.
- Silence Cutter khởi động và `:8791` hoạt động.
- Health check pipeline đạt.
- Đăng ký tự khởi động đạt nếu người dùng bật lựa chọn.

## Kiểm thử

- Dò model local, bundled và `%LOCALAPPDATA%`.
- Kiểm tra manifest thiếu metadata.
- Kiểm tra VRAM/RAM/disk và chọn model.
- Kiểm tra tải model, checksum sai, tải dở dang.
- Kiểm tra Qwen READY trước bridge READY.
- Kiểm tra restart có giới hạn.
- Kiểm tra payload không chứa cache/log/benchmark/model trùng.
- Smoke test trên Windows profile sạch.
- Chạy toàn bộ test production hiện có.

## Tiêu chí cuối

`SilenceCutter_Setup.exe` chỉ được báo thành công sau khi runtime, model,
checksum, Qwen `:8792`, bridge `:8791`, pipeline và tùy chọn tự khởi động đều
đạt.
