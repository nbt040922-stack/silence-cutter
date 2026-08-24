# Bulk Channel Job Discovery

## Goal

Trong **Jobs → Tạo Job mới**, người dùng có thể dán nhiều link kênh YouTube và bấm nút thủ công để chọn tối đa một video chưa từng dùng từ mỗi kênh, sau đó xếp các video đã chọn vào hàng đợi MANUAL hiện có.

## Scope

- Chỉ thay đổi luồng **Tạo Job mới** và Manual LAN API.
- Không thay đổi trang Kênh theo dõi, poller AUTO, hoặc hợp đồng xử lý scheduler.
- Không tải video trong bước quét; chỉ lấy metadata và tạo job theo URL.
- Phạm vi thời gian mặc định: 2 năm trước đến hiện tại.
- Mỗi kênh tối đa một video trong mỗi lần quét.
- Video đã từng được chọn hoặc đã tạo MANUAL job sẽ bị loại.

## Data flow

Monitor gửi danh sách link kênh đến Manual LAN API. API dùng cùng executable `yt-dlp` của Silence Cutter để liệt kê video, lấy metadata cần thiết, sắp xếp theo `view_count` giảm dần, kiểm tra kho lịch sử cục bộ, rồi gọi logic tạo MANUAL job hiện có cho từng URL theo thứ tự.

Kho lịch sử được lưu trong thư mục dữ liệu của Manual LAN API dưới dạng JSON nguyên tử. Khóa chính là YouTube video ID; record giữ channel URL/ID, video URL, title, view count, published date, selected time và job ID. Việc ghi lịch sử chỉ xảy ra sau khi job được tạo thành công hoặc được xác nhận deduplicate.

## Safety

- Chỉ chấp nhận URL YouTube hợp lệ.
- Giới hạn số link kênh và số entry mỗi kênh để tránh quét vô hạn.
- Một kênh lỗi không làm hỏng toàn bộ batch; response trả kết quả từng kênh.
- Không tự chạy định kỳ.
- Không gọi 8780 từ AUTO flow.

## UI behavior

Tạo Job mới có hai chế độ: **Thủ công** hiện tại và **Săn video theo kênh**. Chế độ mới có một ô multiline, mỗi dòng một link kênh, nút **Quét và tạo Jobs**, trạng thái đang quét, số job đã xếp hàng, và danh sách kết quả theo kênh. Kết quả không làm mất chế độ thủ công hoặc metadata preview hiện tại.
