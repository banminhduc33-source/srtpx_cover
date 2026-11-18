"""Phụ đề chính: chuẩn hóa hiển thị khi xuất (thuần Python, không Qt).

Phim thường có MỘT chỗ đặt phụ đề (cùng vị trí, chỉ lệch vài px do nhiễu OCR) và vài chữ khác bay lơ lửng. Bước này:

1. Gom các phụ đề theo vị trí (tâm hộp, dung sai theo tỷ lệ khung hình) rồi chọn nhóm ĐÔNG NHẤT làm **phụ đề chính**
   (hòa thì nhóm tổng thời lượng dài hơn, rồi nhóm nằm thấp hơn). Phần còn lại là **phụ đề phụ**, giữ nguyên.
2. Mọi phụ đề chính dùng CHUNG một hộp đen cố định = trung bình hộp của cả nhóm, lấy đúng từ file .srtpx (KHÔNG nới
   theo chữ đã chia cụm): chữ được chia nhỏ nhưng hộp đen vẫn là hộp của cả câu gốc, không bị chia.
3. Chữ vẽ cỡ cố định (mặc định 20px, đơn vị #res), mỗi lần hiện tối đa 5 chữ: câu dài được chia thành nhiều cụm
   nối tiếp nhau trong hộp đó.
4. Mốc thời gian từng cụm chia theo THỜI LƯỢNG GIỌNG ĐỌC thực tế (`spoken` lấy từ `DubReport.spoken`) chứ không theo
   [start, end] của phụ đề, vì TTS đọc nguyên câu một mạch và có thể bị tua nhanh hoặc mượn khoảng im phía sau. TikTok TTS
   không trả mốc từng từ nên mốc tính theo tỷ lệ số âm tiết (mỗi chữ tiếng Việt ~ một âm tiết), cộng trọng số nghỉ ở dấu câu.
   Không lồng tiếng thì chia đều theo [start, end] của phụ đề.

Hộp luôn liền mạch từ `start` tới hết phụ đề (cụm cuối giữ tới `end`) để che kín chữ gốc dưới video.
Hàm trả về bản sao tài liệu; tài liệu gốc (đang chỉnh trong giao diện) không bị đổi.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Sequence

from .dub import drop_overlaps
from .fonts import FontData
from .model import Box, SrtpxDocument, Subtitle

DEFAULT_FONT_PX = 20.0
DEFAULT_WORDS = 5

# Trọng số nghỉ (tính theo "số âm tiết") TTS thường chèn sau dấu câu.
PAUSE_WEIGHT = {",": 0.6, ";": 0.8, ":": 0.6, "…": 1.2, ".": 1.2, "?": 1.2, "!": 1.2}
_CLOSERS = "\"'”’)]}»›」』】》"


@dataclass(frozen=True)
class MainCaptionOptions:
    font_px: float = DEFAULT_FONT_PX      # cỡ chữ (đơn vị toạ độ #res)
    words_per_chunk: int = DEFAULT_WORDS  # tối đa số chữ mỗi cụm
    balance: bool = True                  # True: chia đều (11 chữ -> 4+4+3), tránh cụm lẻ 1 chữ; False: 5+5+1 đúng từng chữ
    tol_x_ratio: float = 0.05             # dung sai tâm theo chiều ngang, tỷ lệ chiều rộng khung hình
    tol_y_ratio: float = 0.025            # dung sai tâm theo chiều dọc, tỷ lệ chiều cao khung hình
    min_group: int = 2                    # nhóm đông nhất ít hơn số này thì coi như không có phụ đề chính


@dataclass(frozen=True)
class MainCaptionInfo:
    applied: bool
    reason: str = ""                      # lý do không áp dụng
    n_main: int = 0                       # số phụ đề thuộc nhóm chính (gồm cả nhiễu cùng vị trí)
    n_total: int = 0
    n_chunks: int = 0
    n_dropped_overlap: int = 0            # phụ đề chính trùng giờ: chỉ giữ hộp, bỏ chữ (cùng luật với lồng tiếng)
    box: Box | None = None
    font_px: float = DEFAULT_FONT_PX
    words_per_chunk: int = DEFAULT_WORDS

    def message(self) -> str:
        if not self.applied or self.box is None:
            return f"Phụ đề chính: không áp dụng ({self.reason})."
        b = self.box
        s = (f"Phụ đề chính: {self.n_main}/{self.n_total} dòng cùng vị trí -> hộp cố định {b.width:.0f}x{b.height:.0f} "
             f"tại ({(b.x1 + b.x2) / 2:.0f}, {(b.y1 + b.y2) / 2:.0f}), chữ {self.font_px:g}px, "
             f"{self.n_chunks} cụm tối đa {self.words_per_chunk} chữ")
        if self.n_dropped_overlap:
            s += f", {self.n_dropped_overlap} dòng trùng giờ chỉ giữ hộp"
        return s + "."


# ---------------------------------------------------------------- nhận diện nhóm chính

def _rest_box(sub: Subtitle) -> Box:
    """Hộp lúc nằm yên: keyframe cuối nếu có (các keyframe đầu thường là hiệu ứng bay vào), không thì hộp cố định."""
    if sub.keyframes:
        return sub.keyframes[-1].box
    assert sub.box is not None, "Phụ đề thiếu cả hộp cố định lẫn keyframe"
    return sub.box


def _frame_size(doc: SrtpxDocument) -> tuple[float, float]:
    """Kích thước khung hình theo toạ độ #res; không có dòng #res thì lấy mép xa nhất của các hộp."""
    if doc.res:
        return float(doc.res[0]), float(doc.res[1])
    boxes = [_rest_box(s) for s in doc.subtitles]
    if not boxes:
        return 1.0, 1.0
    return max(max(b.x2 for b in boxes), 1.0), max(max(b.y2 for b in boxes), 1.0)


def find_main_group(subs: Sequence[Subtitle], tol_x: float, tol_y: float) -> list[int]:
    """Vị trí (chỉ số trong `subs`) của nhóm phụ đề cùng vị trí ĐÔNG NHẤT.

    Mỗi phụ đề lần lượt làm tâm: nhóm = mọi phụ đề có tâm hộp lệch không quá (tol_x, tol_y). Chọn tâm cho nhóm đông nhất
    (hòa: tổng thời lượng dài hơn, rồi nằm thấp hơn trên khung hình), sau đó dời tâm về trung bình của nhóm vài vòng để nhóm
    không phụ thuộc vào việc phụ đề nào tình cờ được chọn làm tâm đầu tiên.
    """
    if not subs:
        return []
    pts: list[tuple[float, float]] = []
    for s in subs:
        b = _rest_box(s)
        pts.append(((b.x1 + b.x2) / 2, (b.y1 + b.y2) / 2))
    dur = [max(0.0, s.end - s.start) for s in subs]

    def around(cx: float, cy: float) -> list[int]:
        return [j for j, (x, y) in enumerate(pts) if abs(x - cx) <= tol_x and abs(y - cy) <= tol_y]

    best_key: tuple[int, float, float] | None = None
    best: list[int] = []
    for cx, cy in pts:
        members = around(cx, cy)
        key = (len(members), sum(dur[j] for j in members), cy)
        if best_key is None or key > best_key:
            best_key, best = key, members
    for _ in range(5):
        cx = sum(pts[j][0] for j in best) / len(best)
        cy = sum(pts[j][1] for j in best) / len(best)
        nxt = around(cx, cy)
        if nxt == best or len(nxt) < len(best):
            break
        best = nxt
    return best


def _mean_box(boxes: Sequence[Box]) -> Box:
    n = len(boxes)
    return Box(sum(b.x1 for b in boxes) / n, sum(b.x2 for b in boxes) / n,
               sum(b.y1 for b in boxes) / n, sum(b.y2 for b in boxes) / n)


# ---------------------------------------------------------------- chia cụm và mốc thời gian

def split_chunks(text: str, per_chunk: int = DEFAULT_WORDS, balance: bool = True) -> list[list[str]]:
    """Chia chữ (tách theo khoảng trắng, y như chữ gửi cho TTS) thành các cụm tối đa `per_chunk` chữ."""
    words = text.split()
    if not words:
        return []
    per_chunk = max(1, per_chunk)
    if len(words) <= per_chunk:
        return [words]
    k = math.ceil(len(words) / per_chunk)
    if balance:
        base, extra = divmod(len(words), k)
        sizes = [base + 1 if i < extra else base for i in range(k)]   # cụm đầu nhận phần dư; luôn <= per_chunk
    else:
        sizes = [per_chunk] * (k - 1) + [len(words) - per_chunk * (k - 1)]
    out: list[list[str]] = []
    pos = 0
    for sz in sizes:
        out.append(words[pos:pos + sz])
        pos += sz
    return out


def word_weights(words: Sequence[str]) -> list[float]:
    """Trọng số thời gian đọc từng chữ: 1 âm tiết + phần nghỉ nếu chữ kết thúc bằng dấu câu.
    Chữ cuối cả câu không cộng nghỉ (khoảng lặng cuối file TTS không đáng kể, cộng vào sẽ làm mốc chuyển cụm bị lệch sớm)."""
    out: list[float] = []
    for i, w in enumerate(words):
        wt = 1.0
        if i < len(words) - 1:
            tail = w.rstrip(_CLOSERS)
            if tail:
                wt += PAUSE_WEIGHT.get(tail[-1], 0.0)
        out.append(wt)
    return out


def chunk_times(weights: Sequence[float], t0: float, speech_end: float, t_end: float) -> list[tuple[float, float]]:
    """Mốc (bắt đầu, kết thúc) từng cụm. Giọng đọc chạy từ t0 tới speech_end, chia theo tổng trọng số từng cụm.
    Cụm đầu bắt đầu đúng t0, cụm cuối kéo dài tới t_end (>= speech_end) để hộp che liền mạch."""
    total = sum(weights)
    out: list[tuple[float, float]] = []
    acc = 0.0
    prev = t0
    for i, w in enumerate(weights):
        acc += w
        end = t_end if i == len(weights) - 1 else t0 + (speech_end - t0) * acc / total
        out.append((prev, end))
        prev = end
    return out


def _span(sub: Subtitle, next_start: float | None, spoken: float | None) -> tuple[float, float]:
    """(speech_end, t_end): giọng đọc chiếm tới đâu và cụm cuối hiện tới đâu.

    Giọng NGẮN hơn [start, end]: cụm cuối vẫn giữ tới `end` (hộp phải che chữ gốc cho tới khi nó biến mất).
    Giọng DÀI hơn (mượn khoảng im phía sau): chữ hiện hết theo giọng, nhưng không chạm sang phụ đề chính kế tiếp."""
    if spoken is None or spoken <= 0:
        return sub.end, sub.end
    cap = float("inf") if next_start is None else max(sub.end, next_start)
    t_end = max(sub.end, min(sub.start + spoken, cap))
    return min(sub.start + spoken, t_end), t_end


# ---------------------------------------------------------------- áp dụng

def apply_main_captions(
    doc: SrtpxDocument,
    options: MainCaptionOptions = MainCaptionOptions(),
    spoken: dict[int, float] | None = None,
    chain: Sequence[FontData] | None = None,
) -> tuple[SrtpxDocument, MainCaptionInfo]:
    """Trả về (tài liệu mới, thông tin). Không tìm được nhóm chính thì trả lại chính `doc` và `applied=False`.

    `spoken`: {chỉ số phụ đề: số giây giọng đọc} (DubReport.spoken); thiếu thì chia theo [start, end] của phụ đề.
    `chain`: giữ lại cho tương thích, không còn dùng (hộp không nới theo chữ).
    """
    subs = doc.subtitles
    total = len(subs)
    if not subs:
        return doc, MainCaptionInfo(False, "không có phụ đề")
    fw, fh = _frame_size(doc)
    group = find_main_group(subs, fw * options.tol_x_ratio, fh * options.tol_y_ratio)
    if len(group) < options.min_group:
        return doc, MainCaptionInfo(False, f"nhóm cùng vị trí đông nhất chỉ có {len(group)} dòng", n_total=total)

    main_pos = set(group)
    members = [subs[j] for j in group]

    # Trùng giờ: cùng luật lồng tiếng (chỉ giữ block bắt đầu sớm nhất), các block còn lại chỉ giữ hộp để khỏi chồng chữ.
    kept, dropped = drop_overlaps([s for s in members if s.text.strip()])
    kept_ids = {id(s) for s in kept}
    kept.sort(key=lambda s: (s.start, s.index))

    plan: dict[int, list[tuple[float, float, str]]] = {}   # id(phụ đề) -> [(bắt đầu, kết thúc, chữ)]
    for i, s in enumerate(kept):
        nxt = kept[i + 1].start if i + 1 < len(kept) else None
        speech_end, t_end = _span(s, nxt, (spoken or {}).get(s.index))
        chunks = split_chunks(s.text, options.words_per_chunk, options.balance)
        weights = [sum(ws) for ws in _per_chunk_weights(chunks)]
        times = chunk_times(weights, s.start, speech_end, t_end)
        plan[id(s)] = [(a, b, " ".join(c)) for (a, b), c in zip(times, chunks)]

    # Hộp cố định: trung bình hộp của cả nhóm trong file .srtpx (không nới, không chia theo cụm chữ).
    fixed = _mean_box([_rest_box(s) for s in members])

    next_index = max(s.index for s in subs) + 1
    out: list[Subtitle] = []
    n_chunks = 0
    for j, s in enumerate(subs):
        if j not in main_pos:
            out.append(s)
            continue
        if id(s) not in kept_ids:   # không có chữ, hoặc trùng giờ: chỉ che bằng hộp cố định
            out.append(replace(s, text="", box=fixed, keyframes=[], font_px=None))
            continue
        for a, b, text in plan[id(s)]:
            sub = Subtitle(next_index, a, b, text, box=fixed, font_px=options.font_px)
            sub.validate()
            out.append(sub)
            next_index += 1
            n_chunks += 1

    info = MainCaptionInfo(
        True, n_main=len(group), n_total=total, n_chunks=n_chunks,
        n_dropped_overlap=len(dropped), box=fixed, font_px=options.font_px, words_per_chunk=options.words_per_chunk,
    )
    return SrtpxDocument(res=doc.res, subtitles=out), info


def _per_chunk_weights(chunks: Sequence[Sequence[str]]) -> list[list[float]]:
    """Trọng số từng chữ, tính trên CẢ câu (để chữ cuối cả câu không cộng nghỉ) rồi cắt lại theo cụm."""
    flat = word_weights([w for c in chunks for w in c])
    out: list[list[float]] = []
    pos = 0
    for c in chunks:
        out.append(flat[pos:pos + len(c)])
        pos += len(c)
    return out
