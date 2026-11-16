"""TikTok TTS (chỉ dùng thư viện chuẩn của Python, không Qt).

Chuyển từ tiktok_tts.py của bạn. Cần cookie `sessionid` của tài khoản TikTok (xem README).
Đây là endpoint nội bộ, không chính thức: có thể đổi hoặc ngừng bất cứ lúc nào.

Mỗi câu được cache thành file .mp3 theo (giọng, chữ) nên sửa một câu chỉ tạo lại đúng câu đó.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Sequence

from .model import Subtitle

ENDPOINTS = [
    "https://api16-normal-c-useast1a.tiktokv.com/media/api/text/speech/invoke/",
    "https://api16-normal-v6.tiktokv.com/media/api/text/speech/invoke/",
    "https://api22-normal-c-useast1a.tiktokv.com/media/api/text/speech/invoke/",
    "https://api16-normal-c-alisg.tiktokv.com/media/api/text/speech/invoke/",
]
UA = ("com.zhiliaoapp.musically/2022600030 (Linux; U; Android 7.1.2; es_ES; "
      "SM-G988N; Build/NRD90M;tt-ok/3.12.13.1)")
LIMIT = 200  # TikTok giới hạn ~200 ký tự mỗi request
DEFAULT_VOICE = "BV074_streaming"
MAX_CONSECUTIVE_FAILURES = 3  # lỗi liên tiếp quá số này thì dừng (thường là sessionid sai/hết hạn)

VOICES = {
    "BV074_streaming": "Việt - nữ (chưa kiểm chứng)",
    "en_us_001": "English US - nữ", "en_us_006": "English US - nam 1",
    "en_us_007": "English US - nam 2", "en_us_009": "English US - nam 3",
    "en_us_010": "English US - nam 4", "en_uk_001": "English UK - nam 1",
    "en_au_001": "English AU - nữ", "fr_001": "Pháp - nam", "de_001": "Đức - nữ",
    "es_002": "Tây Ban Nha - nam", "br_003": "Bồ Đào Nha BR - nữ",
    "id_001": "Indonesia - nữ", "jp_001": "Nhật - nữ", "kr_003": "Hàn - nữ",
}

# (chữ, mã giọng) -> bytes mp3. Cho phép thay bằng bản giả khi test.
Synthesizer = Callable[[str, str], bytes]


class TtsError(Exception):
    """Lỗi tạo giọng. Thông báo bằng tiếng Việt."""


class TtsCancelled(TtsError):
    """Người dùng bấm Hủy."""


def split_text(text: str, limit: int = LIMIT) -> list[str]:
    """Cắt chữ thành các đoạn <= limit ký tự, ưu tiên cắt ở dấu câu rồi tới khoảng trắng."""
    parts: list[str] = []
    cur = ""
    for s in re.split(r"(?<=[.!?,;])\s+", text.strip()):
        while len(s) > limit:
            cut = s.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(s[:cut])
            s = s[cut:].strip()
        if not s:
            continue
        if len(cur) + len(s) + 1 <= limit:
            cur = f"{cur} {s}".strip()
        else:
            if cur:
                parts.append(cur)
            cur = s
    if cur:
        parts.append(cur)
    return [p for p in parts if p]


def _request_chunk(chunk: str, voice: str, sid: str) -> bytes:
    q = urllib.parse.urlencode({"text_speaker": voice, "req_text": chunk, "speaker_map_type": 0, "aid": 1233})
    last = "không rõ"
    for ep in ENDPOINTS:
        req = urllib.request.Request(
            f"{ep}?{q}", data=b"", method="POST",
            headers={"User-Agent": UA, "Cookie": f"sessionid={sid}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                j = json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - thử endpoint kế tiếp
            last = f"{ep.split('/')[2]}: {e}"
            continue
        v = (j.get("data") or {}).get("v_str")
        if v:
            return base64.b64decode(v)
        last = f"{ep.split('/')[2]}: {j.get('message') or j.get('status_msg') or j}"
    raise TtsError(last)


def tiktok_synthesizer(sessionid: str) -> Synthesizer:
    """Trả về hàm (chữ, giọng) -> mp3 dùng sessionid đã cho."""
    sid = sessionid.strip()
    if not sid:
        raise TtsError("Chưa có sessionid của TikTok. Vào Cài đặt > Lồng tiếng để nhập.")

    def synth(text: str, voice: str) -> bytes:
        return b"".join(_request_chunk(c, voice, sid) for c in split_text(text))

    return synth


def clean_text(text: str) -> str:
    """Chữ gửi cho TTS: gộp xuống dòng/khoảng trắng thành một dấu cách."""
    return " ".join(text.split())


def clip_path(cache_dir: Path, voice: str, text: str) -> Path:
    key = hashlib.sha1(f"{voice}\n{clean_text(text)}".encode("utf-8")).hexdigest()[:20]
    return cache_dir / f"{key}.mp3"


def ensure_clips(
    subs: Sequence[Subtitle],
    voice: str,
    cache_dir: Path,
    synth: Synthesizer,
    on_progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> tuple[dict[int, Path], list[str]]:
    """Tạo (hoặc lấy từ cache) giọng cho từng phụ đề. Trả về ({subtitle.index: file mp3}, lỗi từng câu).

    Dừng sớm bằng TtsError nếu lỗi liên tiếp (sessionid sai thì mọi câu đều lỗi, không cần gọi hết).
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    out: dict[int, Path] = {}
    errors: list[str] = []
    streak = 0
    for i, sub in enumerate(subs):
        if cancel is not None and cancel.is_set():
            raise TtsCancelled("Đã hủy tạo giọng.")
        text = clean_text(sub.text)
        path = clip_path(cache_dir, voice, text)
        if not (path.is_file() and path.stat().st_size > 0):
            try:
                data = synth(text, voice)
                if not data:
                    raise TtsError("TikTok trả về âm thanh rỗng")
                tmp = path.with_suffix(".part")
                tmp.write_bytes(data)
                tmp.replace(path)
                streak = 0
            except TtsError as exc:
                streak += 1
                errors.append(f"Phụ đề {sub.index}: {exc}")
                if streak >= MAX_CONSECUTIVE_FAILURES:
                    raise TtsError(
                        f"Lỗi {streak} câu liên tiếp, dừng lại. Có thể sessionid sai/hết hạn hoặc "
                        f"TikTok đã đổi endpoint.\nLỗi cuối: {exc}") from exc
                if on_progress:
                    on_progress((i + 1) / len(subs))
                continue
        out[sub.index] = path
        if on_progress:
            on_progress((i + 1) / len(subs))
    return out, errors
