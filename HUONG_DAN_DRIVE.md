# Gửi video + .srtpx lên Google Drive → GitHub tự xuất → kết quả về Drive

## Cách hoạt động
```
Bạn tải video + .srtpx (cùng tên) vào Drive: srtpx/in
   → Apps Script (quét mỗi phút) thấy đủ cặp, chuyển sang srtpx/processing, gọi GitHub
   → GitHub Actions bật máy ảo Ubuntu, tải cặp file, chạy srtpx_cover.cli
   → video mới được gửi vào Drive: srtpx/out   (file gốc chuyển sang srtpx/done)
```
Lưu ý thật: Google Drive không có "báo ngay khi có file mới" cho Apps Script, nên độ trễ là **tối đa ~1 phút** (quét) + **~30–60 giây** (GitHub bật máy ảo) trước khi bắt đầu xuất. Sau đó thời gian xuất tuỳ độ dài video.

Quy ước tên: `abc.mp4` đi với `abc.srtpx`. Kết quả: `abc_out.mp4`. Lỗi: file chuyển vào `srtpx/failed` kèm `abc.log.txt`.

## Cài đặt (làm một lần, ~15 phút)

### 1. Đưa code lên GitHub
Tạo repo mới (private hoặc public), đẩy toàn bộ thư mục này lên (giữ nguyên `.github/` và `ci/`).
Tuỳ chọn: chép `Bevan-Regular.ttf` vào `fonts/` rồi commit; nếu không, workflow tự tải Bevan từ google/fonts.
Repo private: GitHub Free có 2.000 phút chạy/tháng; repo public thì không giới hạn.

### 2. Cho GitHub quyền vào Drive (rclone)
Trên máy bạn, cài rclone (https://rclone.org/downloads/) rồi chạy:
```
rclone config
```
- `n` (new remote) → tên: **gd** → loại: **drive** → để trống client_id/secret → scope: **1** (full access) → các bước còn lại Enter → đăng nhập Google bằng trình duyệt.
- Mở file cấu hình: `rclone config file` cho biết đường dẫn `rclone.conf`. Chép **toàn bộ nội dung** file.
- Trên GitHub: repo → Settings → Secrets and variables → Actions → **New repository secret** → tên `RCLONE_CONF`, dán nội dung vào.

Dùng **cùng tài khoản Google** cho cả rclone và Apps Script ở bước 4.

### 3. Tạo token để Apps Script gọi GitHub
GitHub → Settings (tài khoản) → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate:
- Repository access: chỉ repo này
- Permissions → Repository → **Contents: Read and write** (cần cho `repository_dispatch`)
Chép token (chỉ hiện một lần).

### 4. Apps Script theo dõi Drive
1. Vào https://script.google.com → **Dự án mới**.
2. Dán nội dung `ci/drive_watcher.gs` vào, lưu.
3. Project Settings (bánh răng) → **Script Properties** → thêm:
   - `GH_REPO` = `tenban/tenrepo`
   - `GH_TOKEN` = token ở bước 3
4. Chọn hàm **setup** → Chạy → cấp quyền Drive/Mạng khi được hỏi. Hàm này tự tạo thư mục `srtpx/{in,processing,out,done,failed}` trong My Drive và bật trigger mỗi phút.

### 5. Dùng
Thả `abc.mp4` + `abc.srtpx` vào `srtpx/in` (Drive trên web/điện thoại/máy tính đều được). Đợi `abc_out.mp4` hiện trong `srtpx/out`.
Theo dõi tiến độ ở tab **Actions** của repo.

## Tuỳ chỉnh
- Chất lượng/tốc độ: Settings → Secrets and variables → Actions → tab **Variables** → thêm `EXPORT_ARGS`, ví dụ `--crf 20 --preset medium`, hoặc `--codec libx265`.
- Đổi tên thư mục gốc trên Drive: thêm variable `DRIVE_REMOTE` (vd `gd:thumuc`) và sửa `ROOT_NAME` trong script Apps Script cho khớp.
- Chạy tay: tab Actions → srtpx-export → Run workflow (xử lý mọi file đang nằm trong `processing/`, hữu ích nếu một lần chạy bị gián đoạn).

## Sự cố thường gặp
- **Actions không chạy:** xem log Apps Script (Executions); mã lỗi 404/403 thường là token sai quyền hoặc `GH_REPO` sai.
- **Bước rclone lỗi:** `RCLONE_CONF` thiếu dòng `[gd]` hoặc dán thiếu token.
- **Font chữ Trung/Việt thiếu:** runner đã cài Liberation Sans (có tiếng Việt) và Noto Sans CJK làm font dự phòng sau Bevan.
- File `.srtpx` phải tạo từ app (có tọa độ hộp); workflow không có giao diện để chỉnh.

## Lồng tiếng trên GitHub Actions (giấu sessionid)
- Repo → Settings → Secrets and variables → Actions → **New repository secret** → tên `TIKTOK_SESSIONID`, dán giá trị cookie `sessionid` vào.
- Có secret này thì job tự lồng tiếng Việt (`BV074_streaming`), âm gốc 30%, giọng đọc 100%. Không có thì xuất như cũ.
- Chỉ các **phụ đề chính** (nhóm cùng vị trí đông nhất) được lồng tiếng; phụ đề phụ bị bỏ qua. Không cần thêm secret nào khác.
- Tuyệt đối không ghi sessionid vào code, README, file .srtpx hay commit. Repo public vẫn an toàn vì secret không hiện trong code hay log.
