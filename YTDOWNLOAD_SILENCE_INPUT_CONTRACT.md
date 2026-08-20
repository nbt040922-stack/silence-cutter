# YTDOWNLOAD → Silence Cutter inbox contract

Hai ứng dụng dùng chung một inbox được cấu hình bằng biến môi trường
`SILENCE_INPUT_DIR`. Silence Cutter cũng lưu đường dẫn này trong phần cài đặt
hiện có; khi biến môi trường được đặt, nó là nguồn sự thật canonical.

## Luồng bắt buộc cho YTDOWNLOAD

Áp dụng cho **Download Entire Channel** và **Latest Video — Last 7 Days**:

1. Tải và ghép file trong thư mục staging/work riêng của YTDOWNLOAD.
2. Chờ yt-dlp/FFmpeg hoàn tất, rồi kiểm tra file video cuối cùng.
3. Đặt tên ổn định theo video, không ghi đè file đã có.
4. Dùng move/rename nguyên tử vào `SILENCE_INPUT_DIR` sau khi kiểm tra thành công.
5. Không đưa `.part`, `.ytdl`, `.tmp` hoặc stream chưa ghép vào inbox.

Silence Cutter chỉ nhận các video đã ổn định, có phần mở rộng hỗ trợ và qua
kiểm tra ffprobe. Nó chờ trạng thái file không đổi theo cơ chế ổn định hiện có,
nên nhiều video hoàn tất có thể cùng nằm trong inbox và được xếp thành các job
độc lập theo scheduler hiện tại.

## Dọn inbox sau thành công

Sau khi job đạt `DONE`, formatter đã hoàn tất và mọi output được kiểm tra tồn tại
và khác 0 byte, Silence Cutter xóa đúng source nằm trong `SILENCE_INPUT_DIR`.
Source ngoài inbox, đang được job khác dùng, hoặc job lỗi vẫn được giữ lại.

Luồng Single Video của YTDOWNLOAD không thay đổi nếu không cấu hình rõ việc gửi
file vào inbox này. Hợp đồng này không thay đổi YT_NOTIFI, Content Ops hoặc
production bridge.
