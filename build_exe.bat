@echo off
REM Đóng gói .exe (onedir). Chạy trong venv đã cài requirements-dev.txt.
pip install -r requirements-dev.txt || exit /b 1
pyinstaller --noconfirm --clean srtpx_cover.spec || exit /b 1
xcopy /E /I /Y fonts dist\srtpx_cover\fonts >nul
if exist ffmpeg.exe copy /Y ffmpeg.exe dist\srtpx_cover\ >nul
if exist ffprobe.exe copy /Y ffprobe.exe dist\srtpx_cover\ >nul
echo Xong: dist\srtpx_cover\srtpx_cover.exe
echo Nhớ đặt Bevan-Regular.ttf vào dist\srtpx_cover\fonts\ và ffmpeg.exe, ffprobe.exe cạnh file .exe (hoặc vào PATH).
