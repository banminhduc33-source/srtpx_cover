"""Tìm ffmpeg/ffprobe, đọc thông số video, chạy ffmpeg theo luồng (không Qt)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable

DOWNLOAD_HINT = (
    "Hãy tải ffmpeg (bản có libx264 và libfreetype, ví dụ bản 'essentials' của gyan.dev "
    "hoặc BtbN trên GitHub), giải nén rồi đặt ffmpeg.exe và ffprobe.exe vào PATH hoặc "
    "vào thư mục cạnh ứng dụng."
)


class FfmpegError(Exception):
    """Lỗi liên quan tới ffmpeg/ffprobe. Thông báo bằng tiếng Việt."""


class FfmpegCancelled(FfmpegError):
    """Người dùng bấm Hủy."""


def _creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def find_tool(name: str, extra_dirs: list[Path] | None = None) -> str:
    """Tìm `name` (ffmpeg/ffprobe) trong thư mục cạnh app rồi tới PATH."""
    exe = name + (".exe" if os.name == "nt" else "")
    dirs = list(extra_dirs or [])
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).parent)
    dirs.append(Path(__file__).resolve().parents[2])  # thư mục gốc dự án, cạnh package
    for d in dirs:
        cand = Path(d) / exe
        if cand.is_file():
            return str(cand)
    found = shutil.which(name)
    if found:
        return found
    raise FfmpegError(f"Không tìm thấy {exe}. {DOWNLOAD_HINT}")


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    fps_num: int
    fps_den: int
    duration: float  # giây

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    @property
    def fps(self) -> float:
        return self.fps_num / self.fps_den


def probe_video(path: str | Path, ffprobe: str) -> VideoInfo:
    """Đọc độ phân giải, fps, thời lượng bằng ffprobe."""
    try:
        p = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,r_frame_rate,duration:format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL,
            creationflags=_creationflags(),
        )
    except OSError as exc:
        raise FfmpegError(f"Không chạy được ffprobe: {exc}") from exc
    if p.returncode != 0:
        raise FfmpegError(f"Không mở được video (ffprobe báo lỗi): {p.stderr.strip()[:300]}")
    try:
        data = json.loads(p.stdout)
        st = data["streams"][0]
        fr = Fraction(st["r_frame_rate"])
        dur = st.get("duration") or data.get("format", {}).get("duration")
        return VideoInfo(int(st["width"]), int(st["height"]), fr.numerator, fr.denominator, float(dur))
    except (KeyError, IndexError, ValueError, ZeroDivisionError, TypeError) as exc:
        raise FfmpegError("Video không có luồng hình hợp lệ hoặc thiếu thông tin fps/thời lượng.") from exc


def ffmpeg_major_version(ffmpeg: str) -> int | None:
    """Số phiên bản chính của ffmpeg (None nếu là bản git không có số)."""
    try:
        p = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, creationflags=_creationflags())
    except OSError:
        return None
    m = re.search(r"ffmpeg version n?(\d+)\.", p.stdout)
    return int(m.group(1)) if m else None


def run_ffmpeg(
    cmd: list[str],
    total_seconds: float,
    on_progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> None:
    """Chạy ffmpeg với `-progress pipe:1 -nostats`; báo tiến trình 0..1; ném FfmpegError nếu lỗi."""
    full = [cmd[0], "-nostdin", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats", *cmd[1:]]
    try:
        proc = subprocess.Popen(
            full, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", creationflags=_creationflags(),
        )
    except OSError as exc:
        raise FfmpegError(f"Không chạy được ffmpeg: {exc}") from exc
    assert proc.stdout is not None and proc.stderr is not None
    for line in proc.stdout:
        if cancel is not None and cancel.is_set():
            proc.kill()
            proc.wait()
            raise FfmpegCancelled("Đã hủy xuất video.")
        if line.startswith("out_time_us=") and on_progress and total_seconds > 0:
            try:
                on_progress(min(1.0, max(0.0, int(line.split("=")[1]) / 1e6 / total_seconds)))
            except ValueError:
                pass  # out_time_us=N/A ở đầu quá trình
    err = proc.stderr.read()
    if cancel is not None and cancel.is_set():
        proc.wait()
        raise FfmpegCancelled("Đã hủy xuất video.")
    if proc.wait() != 0:
        low = err.lower()
        if "no space left" in low:
            raise FfmpegError("Ổ đĩa đã hết dung lượng. Hãy giải phóng ổ đĩa hoặc chọn nơi lưu khác.")
        raise FfmpegError("ffmpeg báo lỗi khi xuất video:\n" + err.strip()[-600:])
    if on_progress:
        on_progress(1.0)


def build_proxy_command(ffmpeg: str, src: str | Path, dst: str | Path, height: int = 480) -> list[str]:
    """Lệnh tạo proxy 480p (chỉ để xem/chỉnh sửa; xuất luôn dùng video gốc). Giữ fps, bỏ âm thanh cho nhẹ."""
    return [
        ffmpeg, "-y", "-i", str(src), "-vf", f"scale=-2:{height}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p",
        "-an", "-threads", "2", str(dst),
    ]
