"""Lọc phụ đề cần lồng tiếng bằng Gemini (chỉ dùng thư viện chuẩn của Python, không Qt).

Chạy SAU các luật cứng của dub.py (chú thích, vị trí, trùng giờ): đưa các dòng còn lại (đã dịch) cho
Gemini Flash-Lite phân loại "đọc thành tiếng" hay "bỏ qua" (tiếng động, cảm thán, chữ trên màn hình, quảng cáo...).

Nguyên tắc an toàn: LỖI hay không chắc thì LỒNG TIẾNG (thà đọc thừa còn hơn mất thoại). Kết quả cache theo
từng dòng (chữ + model + phiên bản prompt) nên chạy lại / sửa một câu không tốn thêm lượt gọi.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from .model import Subtitle
from .tts import TtsCancelled

DEFAULT_MODEL = "gemini-3.5-flash-lite"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
BATCH_SIZE = 40                 # số dòng cần phân loại mỗi lượt gọi
CONTEXT_LINES = 3               # số dòng trước/sau lô đưa kèm để model hiểu tình huống
MAX_CONSECUTIVE_FAILURES = 2    # lỗi liên tiếp quá số này thì thôi gọi nữa (key sai/hết hạn mức): phần còn lại mặc định lồng
RETRIES = 3                     # thử lại khi 429 / 5xx / lỗi mạng
PROMPT_VERSION = "1"            # đổi khi sửa SYSTEM_PROMPT để cache cũ không còn hiệu lực
CACHE_NAME = "ai_filter.json"

# Sửa đoạn này để chỉnh tiêu chí lọc cho hợp thể loại video của bạn (nhớ tăng PROMPT_VERSION).
SYSTEM_PROMPT = """Bạn lọc phụ đề đã dịch để quyết định dòng nào được ĐỌC THÀNH TIẾNG (lồng tiếng bằng TTS).
Với mỗi dòng trong "lines", trả về dub=true nếu đó là lời thoại / lời thuyết minh mà người trong video NÓI RA
(kể cả câu rất ngắn nhưng có nghĩa như "Vâng.", "Không!", "Đi thôi.").
Trả về dub=false nếu dòng đó KHÔNG phải lời nói, ví dụ:
- tiếng động, tiếng cười/khóc/thở/ho, cảm thán đơn thuần ("ha ha", "ừm", "ồ", "hừ", "á"), từ tượng thanh ("rầm", "ting");
- lời bài hát / nhạc nền;
- chữ hiển thị trên màn hình: tiêu đề, tên kênh/watermark/ID, kêu gọi theo dõi - thích - chia sẻ, quảng cáo,
  số điện thoại/đường link, bình luận của người xem, nhãn tên người nói, số tập, credit;
- chú thích bối cảnh dạng chữ (địa điểm, mốc thời gian, "vài năm sau");
- ký hiệu / số / emoji đơn thuần, chuỗi ký tự vô nghĩa hoặc lỗi nhận dạng chữ.
"context_before" và "context_after" chỉ để hiểu ngữ cảnh, KHÔNG phân loại chúng. "seconds" là thời lượng dòng.
Nếu phân vân thì chọn dub=true. "reason" nêu lý do thật ngắn (tối đa 8 từ).
Trả về đúng một phần tử cho mỗi id trong "lines"."""

RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "INTEGER"},
            "dub": {"type": "BOOLEAN"},
            "reason": {"type": "STRING"},
        },
        "required": ["id", "dub"],
    },
}

# Nhận danh sách {"id", "text", "seconds"} cùng ngữ cảnh, trả về {id: (dub, lý do)}. Cho phép thay bản giả khi test.
Classifier = Callable[[list[dict], list[dict], list[dict]], dict[int, tuple[bool, str]]]


class DubFilterError(Exception):
    """Lỗi gọi Gemini. Thông báo bằng tiếng Việt."""


@dataclass
class FilterResult:
    kept: list[Subtitle] = field(default_factory=list)
    dropped: list[Subtitle] = field(default_factory=list)
    reasons: dict[int, str] = field(default_factory=dict)   # {subtitle.index: lý do bỏ} — chỉ cho dòng bị bỏ
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- gọi Gemini

def _post_json(url: str, body: dict, api_key: str, timeout: float = 60) -> dict:
    """POST JSON tới Gemini. Key đi trong header (không nằm trên URL nên không lộ trong thông báo lỗi)."""
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _http_message(exc: urllib.error.HTTPError) -> str:
    try:
        j = json.loads(exc.read().decode("utf-8", "replace"))
        return str((j.get("error") or {}).get("message") or j)[:300]
    except Exception:  # noqa: BLE001
        return exc.reason if isinstance(exc.reason, str) else ""


def _response_text(j: dict) -> str:
    cands = j.get("candidates") or []
    if not cands:
        fb = (j.get("promptFeedback") or {}).get("blockReason")
        raise DubFilterError(f"Gemini không trả về kết quả{f' (bị chặn: {fb})' if fb else ''}")
    parts = (cands[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    if not text.strip():
        raise DubFilterError(f"Gemini trả về rỗng (finishReason={cands[0].get('finishReason')})")
    return text


def _parse_verdicts(text: str) -> dict[int, tuple[bool, str]]:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise DubFilterError(f"Gemini trả về JSON hỏng: {exc}") from exc
    if isinstance(data, dict):  # phòng khi model bọc mảng trong một khóa
        data = next((v for v in data.values() if isinstance(v, list)), [])
    out: dict[int, tuple[bool, str]] = {}
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and isinstance(item.get("id"), int) and isinstance(item.get("dub"), bool):
            out[item["id"]] = (item["dub"], str(item.get("reason") or "").strip())
    return out


def gemini_classifier(api_key: str, model: str = DEFAULT_MODEL) -> Classifier:
    """Trả về hàm phân loại dùng Gemini với key và model đã cho."""
    key = api_key.strip()
    if not key:
        raise DubFilterError("Chưa có API key Gemini. Vào Cài đặt > Lồng tiếng để nhập (hoặc biến môi trường GEMINI_API_KEY).")
    url = ENDPOINT.format(model=(model or DEFAULT_MODEL).strip())

    def classify(lines: list[dict], before: list[dict], after: list[dict]) -> dict[int, tuple[bool, str]]:
        body = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": json.dumps(
                {"context_before": before, "lines": lines, "context_after": after}, ensure_ascii=False)}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": RESPONSE_SCHEMA},
        }
        last = "không rõ"
        for attempt in range(RETRIES):
            try:
                return _parse_verdicts(_response_text(_post_json(url, body, key)))
            except urllib.error.HTTPError as exc:
                last = f"HTTP {exc.code}: {_http_message(exc)}"
                if exc.code not in (429, 500, 502, 503, 504):
                    raise DubFilterError(last) from exc  # key sai / model sai / yêu cầu sai: thử lại cũng vô ích
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = f"lỗi mạng: {exc}"
            except DubFilterError as exc:
                last = str(exc)
            if attempt + 1 < RETRIES:
                time.sleep(2.0 * (attempt + 1))
        raise DubFilterError(last)

    return classify


# ---------------------------------------------------------------- cache theo dòng

def _clean(text: str) -> str:
    return " ".join(text.split())


def _cache_key(model: str, text: str) -> str:
    return hashlib.sha1(f"{PROMPT_VERSION}\n{model}\n{_clean(text)}".encode("utf-8")).hexdigest()[:20]


def _load_cache(path: Path) -> dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else None
        return items if isinstance(items, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(path: Path, items: dict[str, dict]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_text(json.dumps({"version": 1, "items": items}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass  # cache hỏng/không ghi được thì chỉ tốn thêm lượt gọi, không làm hỏng kết quả


# ---------------------------------------------------------------- lọc

def filter_subtitles(
    subs: Sequence[Subtitle],
    context: Sequence[Subtitle],
    classifier: Classifier,
    cache_file: Path | None = None,
    model: str = DEFAULT_MODEL,
    on_progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> FilterResult:
    """Chia `subs` thành (giữ lại để lồng, bỏ qua). `context` là toàn bộ phụ đề của file, chỉ để làm ngữ cảnh.

    Lỗi API không ném ra: lô đó (và phần còn lại nếu lỗi liên tiếp) mặc định được LỒNG, kèm cảnh báo.
    Chỉ ném TtsCancelled khi người dùng bấm Hủy.
    """
    res = FilterResult()
    cache = _load_cache(cache_file) if cache_file else {}
    verdict: dict[int, tuple[bool, str]] = {}
    todo: list[Subtitle] = []
    for s in subs:
        hit = cache.get(_cache_key(model, s.text))
        if isinstance(hit, dict) and isinstance(hit.get("dub"), bool):
            verdict[s.index] = (hit["dub"], str(hit.get("reason") or ""))
        else:
            todo.append(s)

    ordered = sorted((s for s in context if s.text.strip()), key=lambda s: (s.start, s.index))
    pos = {s.index: i for i, s in enumerate(ordered)}

    def ctx(s: Subtitle) -> dict:
        return {"id": s.index, "text": _clean(s.text)}

    streak = 0
    gave_up = False
    for b in range(0, len(todo), BATCH_SIZE):
        if cancel is not None and cancel.is_set():
            raise TtsCancelled("Đã hủy lọc phụ đề.")
        batch = todo[b:b + BATCH_SIZE]
        if gave_up:
            break
        ids = {s.index for s in batch}
        first = min((pos[s.index] for s in batch if s.index in pos), default=0)
        last = max((pos[s.index] for s in batch if s.index in pos), default=-1)
        before = [ctx(s) for s in ordered[max(0, first - CONTEXT_LINES):first] if s.index not in ids]
        after = [ctx(s) for s in ordered[last + 1:last + 1 + CONTEXT_LINES] if s.index not in ids]
        lines = [{"id": s.index, "text": _clean(s.text), "seconds": round(max(0.0, s.end - s.start), 2)} for s in batch]
        try:
            got = classifier(lines, before, after)
            streak = 0
        except DubFilterError as exc:
            streak += 1
            res.warnings.append(f"Lọc AI lỗi ở {len(batch)} phụ đề (từ #{batch[0].index}), mặc định vẫn lồng tiếng: {exc}")
            if streak >= MAX_CONSECUTIVE_FAILURES:
                res.warnings.append("Lọc AI lỗi liên tiếp nên dừng; các phụ đề còn lại mặc định vẫn lồng tiếng "
                                    "(kiểm tra API key / hạn mức Gemini).")
                gave_up = True
            got = {}
        for s in batch:
            if s.index in got:
                verdict[s.index] = got[s.index]
                cache[_cache_key(model, s.text)] = {"dub": got[s.index][0], "reason": got[s.index][1]}
        if cache_file:
            _save_cache(cache_file, cache)
        if on_progress:
            on_progress(min(1.0, (b + len(batch)) / len(todo)))

    for s in subs:
        dub, reason = verdict.get(s.index, (True, ""))   # không có kết luận -> lồng
        if dub:
            res.kept.append(s)
        else:
            res.dropped.append(s)
            res.reasons[s.index] = reason
    if on_progress:
        on_progress(1.0)
    return res
