# srtpx_cover — che phụ đề cứng bằng hộp đen chữ trắng

Mở video có phụ đề cứng + file `.srtpx`, app vẽ hộp đen đục đè đúng vị trí chữ gốc và đặt chữ mới màu trắng căn giữa. Chỉnh sửa trực tiếp, lưu `.srtpx`, xuất video mới bằng ffmpeg.

## Dùng nhanh (không cần biết code)
1. Giải nén thư mục, **bấm đúp `Chay_App.bat`**. Lần đầu máy tự cài thư viện, tải ffmpeg và font (cần Internet, 1–3 phút); các lần sau app mở ngay.
2. Nếu máy chưa có Python, file sẽ mở trang tải Python: cài và nhớ tích **Add python.exe to PATH**, rồi bấm đúp lại.
3. Trong app: **1. Mở video → 2. Mở .srtpx → kéo hộp cho khớp chữ → Xuất** (nút hồng góc phải).

## Yêu cầu
- Windows 10/11, Python 3.11+
- **ffmpeg và ffprobe** (có libx264, libfreetype): tải bản "essentials" tại https://www.gyan.dev/ffmpeg/builds/ hoặc https://github.com/BtbN/FFmpeg-Builds/releases. Đặt `ffmpeg.exe`, `ffprobe.exe` vào PATH hoặc cạnh app.
- **Font Bevan Regular** (giấy phép OFL): tải tại https://fonts.google.com/specimen/Bevan, chép `Bevan-Regular.ttf` vào thư mục `fonts/`. Font dự phòng (Arial/Segoe UI, Microsoft YaHei) lấy từ `C:\Windows\Fonts`.

## Cài đặt và chạy
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m srtpx_cover.gui
```
Xuất bằng dòng lệnh (không cần giao diện):
```
python -m srtpx_cover.cli video.mp4 phude.srtpx ra.mp4 [--start 5 --end 20] [--codec libx265]
```

## Giao diện (kiểu Alight Motion)
- Nền tối, điểm nhấn hồng; thanh trên cùng có menu, hoàn tác/làm lại, lưu, cài đặt và nút **Xuất** màu hồng.
- Giữa: preview (hộp chọn màu hồng, kéo/đổi cỡ bằng tay nắm). Phải: danh sách **lớp phụ đề** và ô sửa nội dung.
- Dưới: **timeline nhiều lớp** — làn Video và các làn phụ đề (phụ đề chồng giờ tự xuống làn dưới), playhead, thước thời gian.
  - Bấm thanh để chọn; kéo thân để **dời** (keyframe dời theo); kéo hai mép của thanh đang chọn để **cắt** đầu/đuôi. Có hút (snap) vào playhead và mép các thanh khác.
  - Bấm/kéo trên thước hoặc chỗ trống để tua. **Ctrl + lăn chuột** zoom, **Shift + lăn** cuộn ngang, lăn thường cuộn các làn.
  - Keyframe hiện bằng hình thoi vàng trên thanh đang chọn; dải KEYFRAME phía dưới để chọn/kéo/xóa.
- Mọi thao tác kéo trên timeline đều là một bước hoàn tác (Ctrl+Z).
- Code giao diện: `gui/theme.py` (màu + QSS, đổi `ACCENT` để đổi màu chủ đạo), `gui/icons.py` (icon vẽ bằng code), `gui/timeline.py`.

## Lồng tiếng (TikTok TTS)
Mở file `.srtpx` xong, app tự lọc phụ đề và tạo giọng từng câu (có cache, câu không đổi thì không tạo lại). Khi **Xuất**, giọng được trộn vào video:
- Giọng đặt đúng mốc **bắt đầu** của phụ đề.
- Giọng **dài hơn** khoảng [bắt đầu, kết thúc] của phụ đề thì **tua nhanh** cho vừa khít (trần mặc định x3, chỉnh trong Cài đặt); **ngắn hơn** thì giữ nguyên tốc độ, phần còn lại để im.
- **Chú thích** (có dấu `*` `#` `※` `★` ... ở đầu/cuối, kể cả trong ngoặc, hoặc cả câu nằm trong một cặp ngoặc bất kỳ như `(cười)`, `[nhạc]`, `【…】`, `《…》`) **không lồng tiếng**.
- Các block **trùng giờ** nhau chỉ lồng **một** block chính (block bắt đầu sớm nhất), còn lại bỏ qua. Tích "Lồng cả chú thích và block trùng giờ" trong Cài đặt để tắt các luật này.
- Âm thanh gốc hạ còn 30% (chỉnh được; đặt 0% để tắt hẳn).

Thiết lập: **Cài đặt > Lồng tiếng**, dán `sessionid` (đăng nhập tiktok.com > F12 > Application > Cookies > `sessionid`; coi như mật khẩu, nên dùng tài khoản phụ; app lưu trong cài đặt của máy bạn). Có thể dùng biến môi trường `TIKTOK_SESSIONID` thay cho ô nhập. Menu có thêm **Tạo giọng đọc…** để tạo lại thủ công. Giọng mặc định `BV074_streaming` (tiếng Việt, chưa kiểm chứng); có thể gõ mã giọng khác. Đây là endpoint nội bộ của TikTok, có thể đổi hoặc ngừng bất cứ lúc nào.

Dòng lệnh: `python -m srtpx_cover.cli video.mp4 phude.srtpx ra.mp4 --dub --voice BV074_streaming [--dub-comments] [--orig-volume 0.3] [--max-speed 3]`

## Ô che lớn tự xuống dòng
Phụ đề thường (hộp thấp) **không** xuống dòng, chữ dài thì co cỡ. Riêng ô che lớn (hộp cao từ 12% chiều cao video trở lên, vd bảng chữ nhiều dòng) mà chữ chỉ có một dòng thì chữ tự xuống dòng theo từ (chữ Hán: giữa các chữ) để dàn đầy hộp; chỉ xuống dòng khi chữ to hơn ít nhất 15%. Chữ đã có dòng sẵn thì giữ nguyên. Ngưỡng chỉnh ở `WRAP_MIN_HEIGHT_RATIO` / `WRAP_MIN_GAIN` trong `core/layout.py`.

## Chữ dính vào hộp
Chữ luôn co giãn và dời theo hộp đen: hộp phóng to/thu nhỏ/di chuyển theo keyframe thì chữ to/nhỏ/dời theo, mượt (cỡ chữ và vị trí là số thực, không nhảy từng px). Cỡ chữ = min(cao hộp / 1,2 x số dòng, rộng hộp / độ rộng dòng dài nhất).

**Vì sao trước đây chữ bị nhảy nấc khi xuất, và cách sửa.** `drawtext` chỉ nhận cỡ chữ và vị trí **số nguyên px**. Bản trước vẽ chữ một lần rồi `scale` + `overlay` theo từng khung, nhưng `scale` làm tròn kích thước theo bước 2px còn `overlay` làm tròn vị trí (video yuv420p còn ép về bội số của 2px), nên tâm chữ lệch tới ~3px so với hộp đen và nhảy nấc (đo trên video thử: lệch ngang 0,59px, lệch dọc 0,55px). Bản này vẽ chữ **một lần** ở cỡ giữa dải cỡ chữ lên một canvas trong suốt, rồi filter `perspective` (nội suy cubic, toạ độ số thực) co giãn và đặt chữ đúng vị trí ở **từng khung** với độ chính xác dưới 1px; canvas bao trọn mọi vị trí của hộp và được `overlay` đè ở toạ độ chẵn **cố định**, nên overlay không còn làm tròn gì cả (lệch ngang còn ~0,10px, dọc ~0,03px). Dùng `filter_complex` nên vẫn giữ âm thanh.

**Lần sửa thứ hai (chữ vẫn khựng với video điện thoại / 29,97 fps).** Lớp chữ được sinh theo lưới khung đều k/fps, còn video thật thường có PTS không đều (VFR, quay điện thoại), fps 29,97 hoặc `start_time` khác 0; `overlay` ghép lệch khung nên chữ lúc đứng yên lúc nhảy 2–3px (đo: bước 0,3px xen 3px). Nay đầu filter graph có `fps=N/D:start_time=0` ép video về đúng lưới khung, và lớp chữ dời mốc bằng `setpts=PTS+k0` (số nguyên khung, không làm tròn giây 4 chữ số). Đo lại trên video VFR/29,97: bước chữ đều, lệch tối đa ~0,4px (trước đó tới ~1,8px). Lưu ý: video VFR sẽ được xuất thành fps đều (CFR). Chữ có cỡ và vị trí không đổi vẫn dùng `drawtext` như cũ. Đã thử với ffmpeg 6.1.

**Lần sửa thứ ba (chữ + hộp có keyframe vẫn giật).** Có hai nguyên nhân còn lại. (1) Hộp đen vẽ bằng `drawbox` chỉ nhận toạ độ nguyên, mép hộp nhảy 1–2px không đều; chữ thì bị `perspective` co từ một cỡ cố định nên răng cưa/nhoè và độ rộng chữ lệch từng khung. Nay phụ đề **có keyframe** được vẽ **từng khung bằng Pillow** (`core/animlayer.py`): hộp vẽ siêu mẫu 8x với toạ độ số thực (mép hộp mượt dưới 1px), chữ vẽ MỘT lần ở cỡ lớn rồi co giãn bằng `resize(box=...)` toạ độ số thực nên độ rộng/vị trí chữ đổi liên tục (đo: độ rung ngang của chữ giảm ~60%). Kết quả là chuỗi PNG RGBA, ffmpeg chỉ `overlay` ở toạ độ chẵn cố định. (2) Nội suy **tuyến tính** làm tốc độ đổi đột ngột tại keyframe (vd 3,4px/khung xuống 0,86px/khung) nên mắt thấy khựng. Nay mặc định nội suy **PCHIP** (`ExportOptions.smooth=True`): tốc độ liên tục qua keyframe, không vọt quá giá trị keyframe, dừng êm ở keyframe cuối. Đặt `smooth=False` để về tuyến tính. Cần thêm thư viện **Pillow** (đã có trong `requirements.txt`).

## Sử dụng nhanh
- **Mở video / Mở .srtpx**, bấm dòng trong bảng để nhảy tới phụ đề; kéo hộp hoặc tay nắm để chỉnh.
- Phím cách: phát/dừng; ←/→: lùi/tiến một khung; Ctrl+Z / Ctrl+Y: hoàn tác/làm lại.
- **Xuất video (Ctrl+E)**: chọn nơi lưu, cả video hoặc một đoạn (giây A → B, giờ phụ đề vẫn theo video gốc); có thanh tiến trình và nút Hủy (xóa file dở).
- **Cài đặt**: lề an toàn, font (chính / tiếng Việt / CJK), codec-CRF-preset-luồng, proxy 480p.
- **Proxy 480p** (tùy chọn trong Cài đặt): tạo bản nhẹ trong thư mục tạm để xem/chỉnh trên máy yếu; toạ độ vẫn theo video gốc và xuất luôn dùng video gốc.

## Đóng gói .exe
```
pip install -r requirements-dev.txt
build_exe.bat
```
Kết quả: `dist\srtpx_cover\srtpx_cover.exe`. Thư mục `fonts/` được chép cạnh .exe; hãy đảm bảo có `Bevan-Regular.ttf` trong đó và `ffmpeg.exe`/`ffprobe.exe` cạnh .exe hoặc trong PATH.

## Test
```
pip install -r requirements-dev.txt
pytest
```
Test cần ffmpeg/font sẽ tự bỏ qua nếu thiếu. Log lỗi nằm trong thư mục AppData của ứng dụng (`app.log`).
