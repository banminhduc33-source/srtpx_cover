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
- Giọng **dài hơn** khoảng [bắt đầu, kết thúc] của phụ đề thì được **mượn khoảng im lặng phía sau** (đến lúc câu kế tiếp bắt đầu, trừ 0,1 giây); vẫn không đủ thì mới **tua nhanh** cho vừa (trần mặc định x1.4, chỉnh trong Cài đặt); **ngắn hơn** thì giữ nguyên tốc độ, phần còn lại để im.
- **Chỉ lồng phụ đề nằm ở dưới** (tâm hộp từ 60% chiều cao khung hình trở xuống); chữ bay lơ lửng ở giữa/trên khung hình thì bỏ qua.
- **Chú thích** (có dấu `*` `#` `※` `★` ... ở đầu/cuối, kể cả trong ngoặc, hoặc cả câu nằm trong một cặp ngoặc bất kỳ như `(cười)`, `[nhạc]`, `【…】`, `《…》`) **không lồng tiếng**.
- Các block **trùng giờ** nhau chỉ lồng **một** block chính (block bắt đầu sớm nhất), còn lại bỏ qua. Luật này chạy **sau** bước lọc AI (nếu bật), nên nhãn/chữ trên màn hình bị AI loại không còn nuốt mất câu thoại trùng giờ với nó. Tích "Lồng cả chú thích và block trùng giờ" trong Cài đặt để tắt các luật này (kể cả luật chỉ lồng phụ đề ở dưới).
- Âm thanh gốc hạ còn 30% (chỉnh được; đặt 0% để tắt hẳn).
- **Lọc bằng AI (tùy chọn, mặc định tắt)**: sau luật chú thích và vị trí, các dòng còn lại (đã dịch) được gửi theo lô 40 dòng cho Gemini (`gemini-3.5-flash-lite`) để bỏ những dòng không phải lời nói: tiếng động / cười / cảm thán ("ha ha", "ừm"), lời bài hát, chữ trên màn hình (kêu gọi theo dõi, quảng cáo, tên kênh, nhãn người nói, số tập), chú thích bối cảnh, ký tự rác. **Gemini lỗi tạm thời (429, 5xx, mất mạng, trả lời hỏng) thì tự thử lại đến khi được** (chờ 2s, 4s, 8s... tối đa 30s mỗi lần, bấm Hủy để dừng); key/model sai (HTTP 400/401/403/404) thì dừng và báo lỗi vì thử lại cũng vô ích. Dòng nào model bỏ sót sau vài lần hỏi lại thì vẫn lồng (thà đọc thừa còn hơn mất thoại). Kết quả cache theo từng dòng trong thư mục cache giọng nên mở lại / xuất lại không tốn thêm lượt gọi. Bật "Lồng cả chú thích và block trùng giờ" thì tắt luôn bước lọc AI. Tiêu chí lọc nằm ở `SYSTEM_PROMPT` trong `srtpx_cover/core/dubfilter.py` (sửa xong nhớ tăng `PROMPT_VERSION` để bỏ cache cũ).

Thiết lập lọc AI: **Cài đặt > Lồng tiếng**, tích "Lọc bằng AI", dán API key Gemini (lấy ở https://aistudio.google.com/apikey; hoặc dùng biến môi trường `GEMINI_API_KEY`). Dòng bị AI bỏ được đếm ở thanh trạng thái; dòng lệnh in từng dòng bị bỏ kèm lý do để bạn kiểm tra.

Thiết lập: **Cài đặt > Lồng tiếng**, dán `sessionid` (đăng nhập tiktok.com > F12 > Application > Cookies > `sessionid`; coi như mật khẩu, nên dùng tài khoản phụ; app lưu trong cài đặt của máy bạn). Có thể dùng biến môi trường `TIKTOK_SESSIONID` thay cho ô nhập. Menu có thêm **Tạo giọng đọc…** để tạo lại thủ công. Giọng mặc định `BV074_streaming` (tiếng Việt, chưa kiểm chứng); có thể gõ mã giọng khác. Đây là endpoint nội bộ của TikTok, có thể đổi hoặc ngừng bất cứ lúc nào.

Dòng lệnh: `python -m srtpx_cover.cli video.mp4 phude.srtpx ra.mp4 --dub --voice BV074_streaming [--dub-comments] [--orig-volume 0.3] [--max-speed 1.4]`
Thêm lọc AI: `... --dub --ai-filter [--gemini-key KEY] [--gemini-model gemini-3.5-flash-lite]` (key mặc định lấy từ `GEMINI_API_KEY`).

## Phụ đề chính (chuẩn hóa chữ và hộp khi xuất)
Câu thoại dài bị co chữ cho vừa hộp gốc nên các phụ đề có cỡ chữ lớn nhỏ khác nhau (câu dài có thể chỉ còn vài px). Mặc định khi xuất, app (`core/maincap.py`):
1. **Tìm phụ đề chính:** gom các phụ đề theo vị trí tâm hộp (dung sai 5% chiều rộng, 2,5% chiều cao khung hình, đủ để nuốt nhiễu OCR vài px) và lấy **nhóm đông nhất**; hòa thì nhóm có tổng thời lượng dài hơn, rồi nhóm nằm thấp hơn. Phần còn lại là **phụ đề phụ**, **giữ nguyên như cũ** (hộp, keyframe, co chữ theo hộp). Nhóm đông nhất dưới 2 dòng thì không áp dụng gì.
2. **Hộp đen theo file `.srtpx`:** mỗi phụ đề chính dùng đúng hộp của chính nó trong file (vị trí, rộng, cao của chữ gốc tiếng Trung); chữ chia thành nhiều cụm nhưng các cụm cùng một câu dùng chung hộp gốc đó, hộp **không bị chia và không nới**. Các phụ đề chính khác nhau có hộp riêng, không gộp thành một hộp chung cho cả video. Phụ đề chính có keyframe lấy hộp keyframe cuối (lúc nằm yên).
3. **Chữ cỡ cố định 20px** (đơn vị toạ độ `#res`, tự nhân theo độ phân giải video thật), mỗi lần hiện **tối đa 5 chữ**; câu dài chia thành nhiều cụm nối tiếp trong cùng hộp. Mặc định chia **đều** (11 chữ thành 4+4+3) để khỏi có cụm lẻ 1 chữ; muốn đúng 5+5+1 thì đặt `balance=False` trong `MainCaptionOptions`.
4. **Mốc đổi cụm theo giọng đọc:** TikTok TTS nhận cả câu một mạch và không trả mốc từng từ, nên app lấy **thời lượng giọng đọc thực tế của câu** (sau khi tua nhanh/mượn khoảng im, xem `DubReport.spoken`) rồi chia cho các cụm theo tỷ lệ số âm tiết (mỗi chữ tiếng Việt ~ một âm tiết, cộng thêm nghỉ ở dấu `, ; : . ? !`; chữ cuối cả câu không cộng nghỉ). Giọng dài hơn phụ đề gốc thì chữ hiện kéo dài theo giọng nhưng không lấn sang phụ đề chính kế tiếp; giọng ngắn hơn thì cụm cuối vẫn giữ tới hết phụ đề để hộp che chữ gốc. Không lồng tiếng thì chia đều theo [bắt đầu, kết thúc] của phụ đề. Đây là **ước lượng**: câu có nhiều số/tên riêng đọc không đều có thể lệch nửa nhịp; muốn chính xác từng từ cần căn chỉnh âm thanh (forced alignment).
5. **Phụ đề chính trùng giờ** nhau (nhiễu OCR) theo cùng luật lồng tiếng: chỉ block bắt đầu sớm nhất có chữ, các block còn lại giữ hộp nhưng bỏ chữ để khỏi chồng chữ lên nhau.

Vì mốc cụm cần thời lượng giọng đọc nên khi xuất, **lồng tiếng chạy trước rồi mới dựng phụ đề** (hộp cảnh báo font/hộp quá nhỏ nay hiện sau bước tạo giọng, không phải trước). Bật/tắt bằng ô tích trong hộp thoại **Xuất** (nhớ lựa chọn lần sau). Preview trong app vẫn hiện phụ đề gốc, chuẩn hóa chỉ áp dụng cho video xuất.

Dòng lệnh: mặc định bật; `--no-main-captions` để tắt, `--main-font-px 20`, `--main-words 5`. Dùng `--no-text` thì không chuẩn hóa (chỉ vẽ hộp đen như cũ).

## Khung 9:16 + logo (tự động sau khi xuất)
Xuất xong, app dựng tiếp video dọc **1080x1920** từ video vừa xuất (bỏ tích ở hộp thoại Xuất, hoặc `--no-vertical` ở dòng lệnh để tắt):
- **Nền:** bản sao video phóng to phủ kín khung 9:16, làm mờ, hạ độ sáng còn **35%**.
- **Giữa:** video giữ nguyên tỷ lệ, vừa chiều ngang khung (vị trí theo ảnh mẫu).
- **Logo GenshinViet.net** (`assets/logo_genshinviet.png`) nằm **dưới video đúng 20px**, căn giữa. Video quá cao không còn chỗ thì bỏ logo.
- Âm thanh giữ nguyên. Thông số (độ mờ, độ sáng, khoảng cách, cỡ logo...) ở `VerticalOptions` trong `core/vertical.py`.

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
