# Spike mục 8: drawbox theo thời gian

Chạy: `python tools/spike_drawbox.py` (ffmpeg 6.1.1, video thử 768x576 @30fps, phụ đề 1 của file mẫu có KF 0.6s -> 9.4s).

| | Kết quả |
|---|---|
| drawbox với biểu thức theo giờ | **Không dùng được.** Biến `t` trong drawbox là *độ dày viền*, không phải thời gian; `n` báo lỗi "Undefined constant". |
| (a) chia đoạn: mỗi đoạn một drawbox toạ độ hằng + enable riêng | 56 đoạn, script ~4.9 KB, 0.53 s (crf 0, ultrafast), sai lệch so với hàm hình học = 0 điểm ảnh |
| (b) `color=black` + `scale eval=frame` + `overlay` x,y theo giờ | script ~0.8 KB, 0.46 s, sai lệch = 0 điểm ảnh. Phải dùng `shortest=1` nếu không nguồn color chạy vô hạn và ffmpeg treo |

Quyết định: **(a)**. Cùng độ chính xác và tốc độ, nhưng chỉ một chuỗi `-vf` đơn giản (không filter_complex, không thêm luồng
cho mỗi phụ đề, không rủi ro treo). Đoạn = các khung liên tiếp có cùng hộp nguyên theo `pixel_box`, nên hộp xuất ra
khớp hàm hình học tuyệt đối, kể cả phụ đề không keyframe (một đoạn duy nhất). Chi phí: độ dài script tỉ lệ với số lần
hộp đổi điểm ảnh (tối đa bằng số khung hiển thị của phụ đề).

Escape đường dẫn (đã đo): trong filter script không bao nháy, `:` -> `\\:`, `'` -> `\\\'`, và `, ; [ ] =` -> `\,` ...;
dạng `'\''` (đóng nháy rồi mở lại) không chạy.
