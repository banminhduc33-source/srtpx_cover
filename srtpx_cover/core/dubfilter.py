"""Lọc phụ đề cần lồng tiếng bằng Gemini (chỉ dùng thư viện chuẩn của Python, không Qt).

Chạy SAU các luật cứng của dub.py (chú thích, vị trí, trùng giờ): đưa các dòng còn lại (đã dịch) cho
Gemini Flash-Lite phân loại "đọc thành tiếng" hay "bỏ qua" (tiếng động, cảm thán, chữ trên màn hình, quảng cáo...).

Gemini lỗi tạm thời (429, 5xx, mất mạng, trả lời hỏng) thì THỬ LẠI đến khi được (chờ tăng dần, bấm Hủy để dừng);
lỗi vĩnh viễn (key/model sai) thì dừng và báo lỗi vì thử lại cũng vô ích. Chỉ khi model trả lời sót một dòng sau vài
lần hỏi lại thì dòng đó mới mặc định LỒNG TIẾNG (thà đọc thừa còn hơn mất thoại). Kết quả cache theo từng dòng
(chữ + model + phiên bản prompt) nên chạy lại / sửa một câu không tốn thêm lượt gọi.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from .model import Subtitle
from .tts import TtsCancelled, TtsError

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.6-flash"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
BATCH_SIZE = 40                 # số dòng cần phân loại mỗi lượt gọi
CONTEXT_LINES = 3               # số dòng trước/sau lô đưa kèm để model hiểu tình huống
BACKOFF_START = 2.0             # giây chờ trước lần thử lại đầu tiên, nhân đôi mỗi lần
BACKOFF_MAX = 30.0              # trần thời gian chờ giữa hai lần thử
BAD_RESPONSE_TRIES = 5          # trả lời rỗng/JSON hỏng: thử tối đa chừng này lần (có thể lặp mãi nếu bị chặn nội dung)
MISSING_RETRIES = 2             # model bỏ sót dòng nào thì hỏi lại riêng dòng đó tối đa chừng này lần
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


class DubFilterError(TtsError):
    """Lỗi gọi Gemini. Thông báo bằng tiếng Việt.

    `max_tries`: None = lỗi tạm thời, thử lại mãi (chờ tăng dần) đến khi được; một số = chỉ thử tối đa chừng đó lần
    rồi ném lỗi ra (1 = lỗi vĩnh viễn như key/model sai).
    """

    def __init__(self, message: str, max_tries: int | None = None) -> None:
        super().__init__(message)
        self.max_tries = max_tries


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
        raise DubFilterError(f"Gemini không trả về kết quả{f' (bị chặn: {fb})' if fb else ''}", BAD_RESPONSE_TRIES)
    parts = (cands[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    if not text.strip():
        raise DubFilterError(f"Gemini trả về rỗng (finishReason={cands[0].get('finishReason')})", BAD_RESPONSE_TRIES)
    return text


def _parse_verdicts(text: str) -> dict[int, tuple[bool, str]]:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise DubFilterError(f"Gemini trả về JSON hỏng: {exc}", BAD_RESPONSE_TRIES) from exc
    if isinstance(data, dict):  # phòng khi model bọc mảng trong một khóa
        data = next((v for v in data.values() if isinstance(v, list)), [])
    out: dict[int, tuple[bool, str]] = {}
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and isinstance(item.get("id"), int) and isinstance(item.get("dub"), bool):
            out[item["id"]] = (item["dub"], str(item.get("reason") or "").strip())
    return out


def _clean_key(raw: str) -> str:
    """Làm sạch một key: bỏ khoảng trắng, dấu nháy bao quanh và tiền tố 'GEMINI_API_KEY='."""
    key = "".join((raw or "").split())
    if "=" in key:
        key = key.rsplit("=", 1)[1]
    return key.strip("\"'`")


def _valid_key(key: str) -> bool:
    """Key Gemini chỉ gồm chữ, số, '-' và '_' (header HTTP không nhận ký tự lạ)."""
    return bool(key) and key.isascii() and all(c.isalnum() or c in "-_" for c in key)


def parse_keys(raw: str) -> list[str]:
    """Tách danh sách key từ chuỗi nhập: mỗi key một dòng (cũng nhận dấu phẩy/chấm phẩy). Bỏ dòng trống, key trùng
    và key sai định dạng (chỉ ghi log số thứ tự, không in key)."""
    keys: list[str] = []
    for n, part in enumerate(re.split(r"[\r\n,;]+", raw or ""), 1):
        key = _clean_key(part)
        if not key:
            continue
        if not _valid_key(key):
            log.warning("Bỏ qua API key Gemini thứ %d: có ký tự không hợp lệ", n)
            continue
        if key not in keys:
            keys.append(key)
    return keys


def gemini_classifier(api_key: str, model: str = DEFAULT_MODEL) -> Classifier:
    """Trả về hàm phân loại dùng Gemini. `api_key` có thể là nhiều key, mỗi key một dòng: các lượt gọi được chia đều
    (xoay vòng) giữa các key, và key nào hết quota/lỗi thì chuyển ngay sang key kế tiếp."""
    if not (api_key or "").strip():
        raise DubFilterError("Chưa có API key Gemini. Vào Cài đặt > Lồng tiếng để nhập (hoặc biến môi trường GEMINI_API_KEY).", 1)
    keys = parse_keys(api_key)
    if not keys:
        # Không in key ra thông báo. Thử lại cũng vô ích.
        raise DubFilterError(
            "Không có API key Gemini hợp lệ (key chỉ gồm chữ, số, '-' và '_'; mỗi key một dòng). "
            "Hãy kiểm tra lại Secrets GEMINI_API_KEY (hoặc Cài đặt > Lồng tiếng).", 1)
    url = ENDPOINT.format(model=(model or DEFAULT_MODEL).strip())
    lock = threading.Lock()
    turn = [0]   # lượt gọi kế tiếp bắt đầu từ key nào

    def classify(lines: list[dict], before: list[dict], after: list[dict]) -> dict[int, tuple[bool, str]]:
        body = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": json.dumps(
                {"context_before": before, "lines": lines, "context_after": after}, ensure_ascii=False)}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": RESPONSE_SCHEMA},
        }
        with lock:
            first = turn[0] % len(keys)
            turn[0] += 1
        last: DubFilterError | None = None
        temporary = False
        for k in range(len(keys)):
            key = keys[(first + k) % len(keys)]
            try:
                return _parse_verdicts(_response_text(_post_json(url, body, key)))
            except urllib.error.HTTPError as exc:
                # 400/401/403/404: key sai, model sai... 429/5xx: hết quota hoặc server bận (tạm thời).
                # Lỗi theo key thì thử key kế tiếp; hết key mà có lỗi tạm thời thì thử lại mãi, toàn lỗi vĩnh viễn thì dừng.
                permanent = exc.code in (400, 401, 403, 404)
                temporary = temporary or not permanent
                last = DubFilterError(f"HTTP {exc.code}: {_http_message(exc)}", 1 if permanent else None)
                last.__cause__ = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise DubFilterError(f"lỗi mạng: {exc}") from exc
            except ValueError as exc:   # HTTP 200 nhưng thân không phải JSON
                raise DubFilterError(f"Gemini trả về dữ liệu hỏng: {exc}", BAD_RESPONSE_TRIES) from exc
        assert last is not None
        last.max_tries = None if temporary else 1
        raise last

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


# ---------------------------------------------------------------- thử lại

def _sleep(seconds: float, cancel: threading.Event | None) -> None:
    """Chờ `seconds` giây; bấm Hủy thì dừng ngay."""
    if cancel is None:
        time.sleep(seconds)
    elif cancel.wait(seconds):
        raise TtsCancelled("Đã hủy lọc phụ đề.")


def _classify_with_retry(classifier: Classifier, lines: list[dict], before: list[dict], after: list[dict],
                         cancel: threading.Event | None) -> dict[int, tuple[bool, str]]:
    """Gọi classifier, lỗi tạm thời thì chờ (2s, 4s, 8s... tối đa 30s) rồi gọi lại đến khi được."""
    tries = 0
    while True:
        try:
            return classifier(lines, before, after)
        except DubFilterError as exc:
            tries += 1
            if exc.max_tries is not None and tries >= exc.max_tries:
                raise
            delay = min(BACKOFF_START * 2 ** min(tries - 1, 10), BACKOFF_MAX)
            log.warning("Gemini lỗi (%s), thử lại sau %.0fs (lần %d)", exc, delay, tries)
            _sleep(delay, cancel)


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

    Lỗi tạm thời của Gemini được thử lại đến khi được. Ném DubFilterError nếu lỗi vĩnh viễn (key/model sai) hoặc
    Gemini cứ trả lời hỏng; ném TtsCancelled khi người dùng bấm Hủy. Dòng nào model bỏ sót mãi thì mặc định LỒNG.
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

    for b in range(0, len(todo), BATCH_SIZE):
        if cancel is not None and cancel.is_set():
            raise TtsCancelled("Đã hủy lọc phụ đề.")
        batch = todo[b:b + BATCH_SIZE]
        ids = {s.index for s in batch}
        first = min((pos[s.index] for s in batch if s.index in pos), default=0)
        last = max((pos[s.index] for s in batch if s.index in pos), default=-1)
        before = [ctx(s) for s in ordered[max(0, first - CONTEXT_LINES):first] if s.index not in ids]
        after = [ctx(s) for s in ordered[last + 1:last + 1 + CONTEXT_LINES] if s.index not in ids]
        lines = [{"id": s.index, "text": _clean(s.text), "seconds": round(max(0.0, s.end - s.start), 2)} for s in batch]
        got = _classify_with_retry(classifier, lines, before, after, cancel)
        for _ in range(MISSING_RETRIES):   # model bỏ sót dòng nào thì hỏi lại riêng dòng đó
            missing = [l for l in lines if l["id"] not in got]
            if not missing:
                break
            got = {**got, **_classify_with_retry(classifier, missing, before, after, cancel)}
        lost = [l["id"] for l in lines if l["id"] not in got]
        if lost:
            res.warnings.append(f"Gemini không trả lời {len(lost)} phụ đề (từ #{lost[0]}), mặc định vẫn lồng tiếng.")
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
