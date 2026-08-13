# Semantic Intro / Ad / Outro Cleaner

## Kết quả

Đã thêm tầng semantic local theo đúng thứ tự:

`Silence Cutter analysis -> KEEP/CUT -> Semantic Cleaner -> FINAL KEEP -> formatter hiện có -> renderer hiện có`

Tầng mới chỉ trừ các khoảng `INTRO`, `AD`, `OUTRO` khỏi KEEP. Không tạo clean-video trung gian, không sửa nguồn, không đổi Silero, SenseVoice, tight2, Content Boundary Detector, splitter, layout, crop, pitch hoặc FFmpeg formatter.

## Model và runtime

- Checkpoint: `Qwen/Qwen2.5-VL-7B-Instruct-AWQ`
- Nguồn model: <https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct-AWQ>
- Runtime đã xác minh: Transformers `5.14.1`, GPTQModel `7.3.2`, PyTorch `2.11.0+cu130`
- Backend lượng tử: `AWQ_TORCH`
- Thiết bị: CUDA, local-only; không API/cloud
- Model và runtime được chạy trong process con với timeout mặc định 900 giây để lỗi tải model/OOM/timeout không làm hỏng job chính.

## Chiến lược quét

- INTRO: 90 giây đầu.
- AD: cửa sổ 60 giây, bước 45 giây trên toàn video.
- OUTRO: 120 giây cuối.
- Mỗi cửa sổ lấy tối đa 8 frame JPEG rộng 640 px, phân bố đều trên toàn cửa sổ.
- Mỗi frame được ghi timestamp nguồn lên ảnh trong bộ nhớ để Qwen không phải đoán ánh xạ frame-thời gian.
- Không chạy ASR/VAD mới và không đưa full video ở full FPS vào model.

## Prompt

```text
You are a conservative video segment classifier. Analyze only source time {start} to {end} seconds ({role} scan).
Return JSON only with segments containing type, start, end, confidence, and brief reason.
Allowed labels: INTRO, AD, OUTRO, CONTENT. Timestamps are absolute source-video seconds.
The images are chronological samples distributed evenly across the window and carry printed SOURCE TIME labels.
Inspect every image; surrounding CONTENT must not hide a brief frame with clear promotional evidence.
INTRO: branded opening/bumper/montage/title sequence, not substantive early content.
AD: clear sponsor/product/service/offer/URL/QR/coupon/free-trial/affiliate intent; ordinary product discussion is CONTENT.
OUTRO: thanks/subscribe/follow/end-screen/credits/social/next-video/closing branding, not substantive conclusions.
When evidence is weak, return CONTENT or no segment. False positives are worse than misses.
```

## Chính sách confidence và biên cắt

- Ngưỡng mặc định: `SEMANTIC_REMOVE_THRESHOLD=0.85`.
- Dưới ngưỡng hoặc nhãn CONTENT: KEEP.
- Timestamp không hợp lệ: bỏ qua riêng segment đó.
- Biên Qwen được đẩy tới safe speech/KEEP boundary kế tiếp trong tối đa `SEMANTIC_SNAP_TOLERANCE=10.0` giây. Cách này tránh bắt đầu/kết thúc giữa lời nói và không mở rộng về phía trước của điểm bắt đầu chưa an toàn.
- Nếu không tìm được cả hai safe boundary hoặc khoảng sau snap rỗng: KEEP.
- Nếu semantic removal xóa toàn bộ KEEP: hủy semantic result và giữ nguyên timeline.
- Các khoảng semantic chồng nhau được hợp nhất trước phép trừ; phần đã là CUT không bị tính/xóa lần hai.

## Artifact và fail-safe

- `semantic_segments.json`: raw segments, removed segments, uncertain/invalid segments, threshold và số đo runtime.
- `pipeline_report.original.json`: bằng chứng timeline trước semantic.
- `pipeline_report.json`: FINAL KEEP dùng trực tiếp bởi formatter.
- Lỗi model, CUDA OOM, timeout, JSON không thể phục hồi hoặc exception bất kỳ: ghi `SEMANTIC_CLEANER_SKIPPED`, giữ nguyên KEEP và tiếp tục formatter.
- JSON có trường `reason` chứa dấu quote lỗi có thể được phục hồi tối thiểu chỉ từ bốn trường bắt buộc `type/start/end/confidence`; tất cả validation timestamp/confidence vẫn áp dụng.

## Kiểm thử video thực tế

Fixture tích hợp dùng 40 giây footage thật từ `input.mp4`, với một insert quảng cáo hiển thị rõ sponsor, mã ưu đãi, URL và “link in description” được đặt tại biên dựng 20–30 giây. Mục đích là có ground truth chính xác để kiểm tra biên; đây là insert kiểm thử có chủ đích, không phải quảng cáo tự nhiên lấy từ video gốc.

- Original duration: `50.000s`
- Silence KEEP trước semantic: `50.000s`
- Qwen raw: `AD 18.750–25.000`, confidence `0.90`, reason `clear promotional intent`
- Safe aligned removal: `AD 20.000–30.000`
- FINAL KEEP: `0–20`, `30–50`
- Final KEEP duration: `40.000s`
- Removed: `10.000s`
- INTRO/OUTRO: không có trong fixture này, không bị gán nhãn sai.

Kiểm tra hình tại 19.5s / 25s / 30.5s xác nhận quảng cáo nằm hoàn toàn trong phần bị bỏ, hai phía là footage thật. Formatter hiện có chạy trực tiếp từ source timeline và tạo:

- PART 1: `20.000s`, `1080x1920`, A/V delta `0.017s`
- PART 2: `20.000s`, `1080x1920`, A/V delta `0.009s`
- Video: NVDEC/CUDA/NVENC; audio/crop/banner/profile hiện có được giữ nguyên.

## Hiệu năng đo được

Trên RTX 5060 Ti 16 GB, một cửa sổ production 50 giây / 8 frame:

- Model load: `14.775s`
- Semantic scan: `41.023s`
- Tổng bổ sung: `55.798s`
- Peak VRAM: `9,276,655,104 bytes` (`8.64 GiB`)

Đây là số đo baseline, chưa tối ưu theo yêu cầu phase.

## Regression

- Full suite: `305 passed, 1 skipped, 96 subtests passed in 25.86s`.
- Formatter integration: 2 part hợp lệ, duration error `0.000s`, A/V delta trong tolerance.
- Source hash không đổi; không cần clean master trung gian trong production path.

## Giới hạn còn lại

- Qwen2.5-VL là vision-language, không phải audio-language model. Quảng cáo chỉ nói bằng âm thanh mà không có dấu hiệu hình ảnh có thể bị bỏ sót; thêm ASR bị cấm trong phase này.
- Insert hình ngắn hơn khoảng cách frame mẫu có thể bị bỏ sót. Chính sách hiện tại ưu tiên false negative hơn false positive.
- AWQ_TORCH trên Windows chạy được và vừa VRAM, nhưng inference CPU/GPU-kernel hiện còn chậm; chưa tối ưu trong phase này.
