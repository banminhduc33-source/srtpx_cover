@echo off
setlocal
cd /d "%~dp0"
title srtpx_cover - Dang khoi dong

REM ===== Lan dau: tu cai het. Cac lan sau: mo app ngay. =====

REM 1) Tim Python
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
  echo.
  echo Chua co Python tren may nay.
  echo Dang mo trang tai Python. Hay cai, nho TICH "Add python.exe to PATH", roi bam dup file nay lai.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)

REM 2) Tao moi truong va cai thu vien (chi lan dau)
if not exist ".venv\Scripts\pythonw.exe" (
  echo Dang cai dat lan dau, vui long cho 1-3 phut...
  %PY% -m venv .venv || goto :loi
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :loi
)

REM 3) Tai ffmpeg neu chua co (de xuat video)
if not exist "ffmpeg.exe" (
  where ffmpeg >nul 2>nul || (
    echo Dang tai ffmpeg...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $z=Join-Path $env:TEMP 'ffmpeg_dl.zip'; $d=Join-Path $env:TEMP 'ffmpeg_dl'; Invoke-WebRequest 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $z; if(Test-Path $d){Remove-Item $d -Recurse -Force}; Expand-Archive $z $d -Force; Get-ChildItem $d -Recurse -Include ffmpeg.exe,ffprobe.exe | Copy-Item -Destination '.' -Force; Remove-Item $z -Force" || echo Khong tai duoc ffmpeg. Ban co the tai tay, xem README.md.
  )
)

REM 4) Tai font Bevan neu chua co (chu tren hop den)
if not exist "fonts\Bevan-Regular.ttf" (
  echo Dang tai font Bevan...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "try{Invoke-WebRequest 'https://github.com/google/fonts/raw/main/ofl/bevan/Bevan-Regular.ttf' -OutFile 'fonts\Bevan-Regular.ttf'}catch{Write-Host 'Khong tai duoc font Bevan, app se dung font du phong.'}"
)

REM 5) Mo app (khong hien cua so den)
start "" ".venv\Scripts\pythonw.exe" -m srtpx_cover.gui
exit /b 0

:loi
echo.
echo Cai dat bi loi. Kiem tra ket noi Internet roi bam dup file nay lai.
pause
exit /b 1
