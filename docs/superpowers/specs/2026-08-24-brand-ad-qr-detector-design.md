# Thiết kế: Nhận diện thương hiệu, sponsor, QR và quảng cáo

## Mục tiêu

Mở rộng lớp semantic scan hiện có để phát hiện với recall cao:

- thương hiệu cá nhân/logo kênh;
- logo hoặc nhận diện nhà tài trợ;
- mã QR;
- banner, hình chèn hoặc cảnh quảng cáo cho thương hiệu.

Các vùng có bằng chứng đủ mạnh sẽ được đưa vào timeline CUT. Hệ thống ưu tiên
không bỏ sót và chấp nhận một tỷ lệ cắt nhầm có kiểm soát.

## Phạm vi khóa

- Không thêm Whisper, ASR mới hoặc model detector mới.
- Tái sử dụng Qwen worker hiện có cho nhận diện ngữ cảnh hình ảnh.
- Không thay đổi Silero, SenseVoice, fusion speech hoặc renderer.
- Không coi job là thành công nếu quá trình brand scan bị lỗi hoặc chưa hoàn tất.

## Kiến trúc

1. **Coarse visual scan**: lấy frame thưa trên toàn video bằng pipeline semantic hiện có.
2. **Candidate generation**: Qwen đánh dấu các frame/vùng nghi ngờ theo loại
   `PERSONAL_BRAND`, `SPONSOR`, `QR`, `ADVERTISEMENT`.
3. **QR confirmation**: bộ dò QR hình học xác nhận độc lập khi có thể; kết quả
   QR được giữ ngay cả khi Qwen chỉ cho confidence trung bình.
4. **Fine scan**: lấy frame dày quanh candidate, hợp nhất bằng chứng liên tiếp.
5. **Temporal expansion**: nới biên nhỏ trước/sau candidate để không cắt mất
   frame đầu/cuối của logo hoặc quảng cáo.
6. **Timeline integration**: chuyển các vùng đã xác nhận thành CUT, hợp nhất
   overlap và giữ nguyên tính đơn điệu/không chồng lấn của timeline.

## Chính sách an toàn

- `recall_first=true` là mặc định của brand scan.
- Candidate có confidence thấp nhưng được QR detector hoặc nhiều frame liên tiếp
  hỗ trợ sẽ được giữ để cắt.
- Nếu Qwen worker, frame extraction hoặc QR detector lỗi: trạng thái là
  `BRAND_SCAN_INCOMPLETE`; không báo thành công “đã cắt hết”.
- Speech detector không được dùng để loại bỏ candidate quảng cáo; quảng cáo có
  thể bị cắt dù có speech bên trong.
- Không cắt ngoài khoảng source hợp lệ.

## Artifact/report

Artifact riêng `brand_ad_scan.json` gồm:

- `status`: `APPLIED`, `NO_CANDIDATES`, hoặc `BRAND_SCAN_INCOMPLETE`;
- `scan_version`, `recall_first`, `coarse_interval`, `fine_interval`;
- `detections`: `type`, `start`, `end`, `confidence`, `detectors`, `reason`;
- `cut_intervals`, `removed_duration`, `qwen_generation_count`;
- thời gian extraction/coarse/fine/QR/tổng.

Pipeline report tham chiếu artifact và nêu rõ các vùng CUT do brand scan.

## Kiểm thử

- QR xuất hiện ngắn giữa hai frame coarse vẫn tạo candidate fine.
- Logo xuất hiện liên tiếp được hợp nhất thành một vùng CUT.
- Sponsor/banner có bằng chứng Qwen được cắt.
- Candidate overlap được hợp nhất, không có CUT overlap.
- Speech nằm trong vùng quảng cáo không loại bỏ candidate.
- Qwen/QR/frame extraction lỗi tạo `BRAND_SCAN_INCOMPLETE`.
- Không có candidate giữ nguyên timeline.
- Timestamps nằm trong source duration, không overlap, cover đầy đủ.
- Các test Silence Cutter, formatter và renderer hiện có không đổi hành vi.

## Giới hạn

Không thể chứng minh recall 100% với video tùy ý. Hệ thống chỉ được phép tuyên
bố hoàn tất khi toàn bộ scan đã chạy thành công; các trường hợp lỗi phải hiện
trạng thái incomplete để người dùng biết cần xem lại.
