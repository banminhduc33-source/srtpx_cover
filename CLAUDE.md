# Quy trình làm việc (srtpx_cover)

Áp dụng cho mọi lần sửa lỗi / thêm tính năng. Ngắn gọn: **một việc = một nhánh = một PR**, có test, cập nhật README, kiểm tra đã đẩy lên GitHub rồi mới báo xong.

## 1. Nhánh
- Luôn tạo từ `main` mới nhất: `git fetch origin main && git checkout -B fix/<ten-ngan> origin/main`.
- Tiền tố: `fix/` (sửa lỗi), `feat/` (tính năng), `docs/` (tài liệu).
- Mỗi lỗi/tính năng một nhánh riêng. Chỉ **xếp chồng** (nhánh con tạo từ nhánh chưa merge) khi hai việc cùng sửa một file và không tách được. Khi xếp chồng, ghi rõ trong mô tả PR "merge #N trước", và đặt base của PR là nhánh bên dưới.
- Kiểm tra PR trước đó đã merge chưa (`gh api repos/<owner>/<repo>/pulls/<n> --jq '{state, merged}'`) trước khi tạo nhánh từ `main`.

## 2. Trước khi sửa
- Đọc mục liên quan trong `README.md` (mỗi tính năng có mục riêng, nêu cả quy tắc và lý do).
- Tìm đúng chỗ gây lỗi rồi mới sửa; nói rõ nguyên nhân trong commit/PR, không chỉ nói đã sửa.

## 3. Quy tắc code
- `srtpx_cover/core/` là **Python thuần, không Qt** (có `tests/test_core_purity.py` canh). Giao diện nằm ở `gui/`.
- **Preview và video xuất dùng chung một nguồn hình học và bố cục chữ** (`core/geometry.py`, `core/layout.py`). Sửa cách vẽ ở xuất (`core/export.py`, `core/animlayer.py`) thì xem preview (`core/preview.py`, `gui/video_view.py`) có cần sửa theo không, và ngược lại.
- Giữ nguyên hành vi cũ khi không liên quan (ví dụ chữ thẳng vẫn đi đường vẽ cũ), để không làm lệch pixel ở test và video đã xuất.
- Chú thích và README viết tiếng Việt, giống phần còn lại của repo.

## 4. Test
- Chạy toàn bộ: `python3 -m pytest -q`.
- **Mỗi lỗi sửa kèm test mới** bắt đúng lỗi đó. Xác nhận test fail trên code cũ (`git stash push srtpx_cover` → chạy test → `git stash pop`) rồi pass trên code mới.
- Trên sandbox có **9 test fail sẵn trên `main`** (thiếu font/ffmpeg phù hợp, không liên quan thay đổi): `test_export_pixels::test_text_is_white_inside_boxes_and_nothing_leaks_outside`, 6 test trong `test_export_script.py` (keyframed/resizing/utf8 textfiles), và 2 test trong `test_preview.py`. Chỉ cần số fail **không tăng** và các test mới pass. Muốn chắc thì chạy `main` sạch (`git worktree add`) để so sánh.
- Sandbox không có PySide6: phần `gui/` chỉ kiểm tra cú pháp (`python3 -m py_compile`). Ghi rõ trong PR "chưa chạy GUI, cần xem trên máy".
- Kiểm tra bằng mắt với thay đổi về hình: render ảnh bằng font thật (`/usr/share/fonts/truetype/dejavu/`) và xem lại.

## 5. Commit
- Tiêu đề ngắn nói **làm gì**; thân giải thích **vì sao** và các quyết định chính.
- Mỗi commit gọn một việc; không đưa file tạm vào repo (ảnh thử, `tmp`, cache).

## 6. Đẩy lên và mở PR
- Đẩy: `git -c push.negotiate=true push -u origin <nhanh>`.
- **GitHub GraphQL bị chặn**, nên không dùng `gh pr view/create`. Dùng REST:
  - Xem PR: `gh api repos/<owner>/<repo>/pulls/<n>`
  - Tạo PR: `gh api repos/<owner>/<repo>/pulls -f title=... -f head=<nhanh> -f base=main -f body=...`
- Mô tả PR gồm: lỗi/nguyên nhân, cách sửa, đã kiểm tra gì (số test pass/fail so với `main`), phần chưa kiểm tra được.
- Sau khi đẩy, **xác nhận bằng `git ls-remote origin refs/heads/<nhanh>`** trùng `git rev-parse HEAD`. Hook "unpushed commits / no remote branch" có thể báo sai vì clone nông qua proxy không cập nhật ref theo dõi; nếu `ls-remote` đã đúng thì cập nhật ref cho hook hết báo: `git update-ref refs/remotes/origin/<nhanh> HEAD`. Không đẩy lại, không tạo commit rỗng để "chữa" cảnh báo.
- Sau khi PR đã merge thì nhánh đó xong; việc mới tạo nhánh mới từ `main`, không đẩy thêm vào nhánh cũ.

## 7. Merge
- Merge theo thứ tự nếu PR xếp chồng (PR dưới trước); GitHub tự đổi base về `main`.
- Xung đột khi merge: giữ cả hai bên nếu hai tính năng độc lập (đã gặp ở README và `cli.py`), chạy lại test sau khi gỡ marker, đẩy lại và chờ GitHub báo `mergeable: true`.

## 8. Báo kết quả
Nói ngắn: đã sửa gì, kiểm tra thế nào, link PR, và việc người dùng cần tự làm (ví dụ xem GUI trên máy, thứ tự merge).
