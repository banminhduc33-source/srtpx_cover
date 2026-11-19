"""Phụ đề chính: chuẩn hóa hiển thị khi xuất (thuần Python, không Qt).

Phim thường có MỘT chỗ đặt phụ đề (cùng vị trí, chỉ lệch vài px do nhiễu OCR) và vài chữ khác bay lơ lửng. Bước này:

1. Gom các phụ đề theo vị trí (tâm hộp, dung sai theo tỷ lệ khung hình) rồi chọn nhóm ĐÔNG NHẤT làm **phụ đề chính**
   (hòa thì nhóm tổng thời lượng dài hơn, rồi nhóm nằm thấp hơn). Phần còn lại là **phụ đề phụ**, giữ nguyên.
2. Hộp đen của mỗi phụ đề chính: chiều NGANG (x1, x2) lấy của chính nó trong file .srtpx (chữ gốc tiếng Trung) rồi nhân 1,025 quanh tâm,
   chiều DỌC (y1, y2) lấy trung bình của cả nhóm phụ đề chính để mọi câu nằm cùng một hàng, cùng cao. Chữ chia thành
   nhiều cụm nhưng các cụm của một câu dùng chung hộp đó; hộp không bị chia và không bị nới theo chữ.
3. Cỡ chữ tính như phụ đề phụ (co vừa hộp, không đặt cỡ riêng). Số chữ mỗi cụm KHÔNG cố định mà theo SỨC CHỨA của hộp đen
   của chính câu đó: hộp rộng chứa được nhiều chữ hơn hộp hẹp. Với mỗi câu, app cho chữ co nhỏ dần từ cỡ lớn nhất theo chiều cao hộp
   xuống tới `min_size_ratio` (mặc định 75%) rồi đo độ rộng thật của từng chữ để biết một cụm chứa được tối đa bao nhiêu chữ,
   lấy đó chia câu thành ít cụm nhất, rồi chia đều theo độ rộng. Không có font thì dùng số chữ cố định (`DEFAULT_WORDS`).
4. Mốc thời gian từng cụm chia theo THỜI LƯỢNG GIỌNG ĐỌC thực tế (`spoken` lấy từ `DubReport.spoken`) chứ không theo
   [start, end] của phụ đề, vì TTS đọc nguyên câu một mạch và có thể bị tua nhanh hoặc mượn khoảng im phía sau. TikTok TTS
   không trả mốc từng từ nên mốc tính theo tỷ lệ số âm tiết (mỗi chữ tiếng Việt ~ một âm tiết), cộng trọng số nghỉ ở dấu câu.
   Không lồng tiếng thì chia đều theo [start, end] của phụ đề.

Phụ đề PHỤ (ngoài nhóm chính) giữ nguyên hộp và keyframe; riêng phụ đề phụ có hộp CAO HƠN RỘNG và chỉ một dòng thì được tự xuống dòng
(`Subtitle.wrap`, `layout.wrap_lines`) để chữ to hơn thay vì co nhỏ cho vừa chiều ngang hẹp.

Hộp luôn liền mạch từ `start` tới hết phụ đề (cụm cuối giữ tới `end`) để che kín chữ gốc dưới video.
Hàm trả về bản sao tài liệu; tài liệu gốc (đang chỉnh trong giao diện) không bị đổi.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Sequence

from .dub import drop_overlaps
from .fonts import LINE_HEIGHT_FACTOR, FontData, choose_font, fold_for_font, text_width
from .model import Box, SrtpxDocument, Subtitle

DEFAULT_WORDS = 5         # số chữ mỗi cụm khi KHÔNG đo được hộp (thiếu font) hoặc làm mức trần khi người dùng đặt
DEFAULT_MIN_SIZE = 0.75   # cỡ chữ nhỏ nhất của một cụm, tính theo tỷ lệ so với cỡ lớn nhất vừa chiều cao hộp
BOX_WIDTH_SCALE = 1.025   # chiều ngang hộp đen nhân thêm, giữ nguyên tâm ngang

# Trọng số nghỉ (tính theo "số âm tiết") TTS thường chèn sau dấu câu.
PAUSE_WEIGHT = {",": 0.6, ";": 0.8, ":": 0.6, "…": 1.2, ".": 1.2, "?": 1.2, "!": 1.2}
_CLOSERS = "\"'”’)]}»›」』】》"


@dataclass(frozen=True)
class MainCaptionOptions:
    words_per_chunk: int | None = None    # None: tự tính theo sức chứa hộp đen từng câu; số: mức trần số chữ mỗi cụm (và là số chữ cố định khi thiếu font)
    min_size_ratio: float = DEFAULT_MIN_SIZE  # cụm được co chữ xuống tối thiểu tỷ lệ này so với cỡ lớn nhất theo chiều cao hộp (0 < r <= 1)
    balance: bool = True                  # True: chia đều theo độ rộng, tránh cụm lẻ 1 chữ; False: nhồi đầy từng cụm rồi mới sang cụm kế
    tol_x_ratio: float = 0.05             # dung sai tâm theo chiều ngang, tỷ lệ chiều rộng khung hình
    tol_y_ratio: float = 0.025            # dung sai tâm theo chiều dọc, tỷ lệ chiều cao khung hình
    min_group: int = 2                    # nhóm đông nhất ít hơn số này thì coi như không có phụ đề chính
    wrap_secondary: bool = True           # phụ đề PHỤ có hộp cao hơn rộng được tự xuống dòng (layout.wrap_lines); False = giữ một dòng


@dataclass(frozen=True)
class MainCaptionInfo:
    applied: bool
    reason: str = ""                      # lý do không áp dụng
    n_main: int = 0                       # số phụ đề thuộc nhóm chính (gồm cả nhiễu cùng vị trí)
    n_total: int = 0
    n_chunks: int = 0
    n_dropped_overlap: int = 0            # phụ đề chính trùng giờ: chỉ giữ hộp, bỏ chữ (cùng luật với lồng tiếng)
    box: Box | None = None
    max_chunk_words: int = 0              # số chữ của cụm dài nhất thực tế
    by_box: bool = False                  # True: số chữ mỗi cụm tính theo sức chứa hộp; False: số chữ cố định

    def message(self) -> str:
        if not self.applied or self.box is None:
            return f"Phụ đề chính: không áp dụng ({self.reason})."
        b = self.box
        s = (f"Phụ đề chính: {self.n_main}/{self.n_total} dòng cùng vị trí -> hộp đen: ngang theo từng phụ đề trong file, dọc trung bình ( {b.width:.0f}x{b.height:.0f} "
             f"tại ({(b.x1 + b.x2) / 2:.0f}, {(b.y1 + b.y2) / 2:.0f})), "
             f"{self.n_chunks} cụm " + (f"theo sức chứa hộp, dài nhất {self.max_chunk_words} chữ" if self.by_box
                                         else f"tối đa {self.max_chunk_words} chữ"))
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


def _can_wrap(sub: Subtitle) -> bool:
    """Phụ đề phụ nào có thể được cho xuống dòng: chữ một dòng, từ 2 chữ trở lên, hộp lúc nằm yên CAO HƠN RỘNG."""
    box = _rest_box(sub)
    return len(sub.text.split()) >= 2 and "\n" not in sub.text.strip() and box.height > box.width > 0


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
    không phụ thuộc vào việc phụ đề nào tình cờ được chọn làm tâm đầu tiên. Cuối cùng, mọi hộp nằm trọn trong vùng bao
    của nhóm (dù tâm lệch quá dung sai) cũng được kết nạp.
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
    # Mở rộng: hộp nằm TRỌN trong vùng bao của nhóm (hộp nhỏ nhất chứa mọi hộp của nhóm) cũng là phụ đề chính,
    # dù tâm lệch quá dung sai. Vùng bao không đổi khi thêm các hộp nằm trong nó nên chỉ cần xét một lượt.
    rects = [_rest_box(s) for s in subs]
    gx1, gx2 = min(rects[j].x1 for j in best), max(rects[j].x2 for j in best)
    gy1, gy2 = min(rects[j].y1 for j in best), max(rects[j].y2 for j in best)
    inside = {j for j, r in enumerate(rects) if r.x1 >= gx1 and r.x2 <= gx2 and r.y1 >= gy1 and r.y2 <= gy2}
    return sorted(set(best) | inside)


def _main_positions(doc: SrtpxDocument, options: MainCaptionOptions) -> list[int]:
    """Vị trí (trong `doc.subtitles`) của các phụ đề thuộc nhóm chính. Có thể ít hơn `options.min_group` (khi đó coi như không có)."""
    subs = doc.subtitles
    fw, fh = _frame_size(doc)
    # Phụ đề chữ nghiêng (có đa giác) giữ nguyên như phụ đề phụ: chuẩn hóa hộp ngang/dọc sẽ làm đa giác mất khớp với chữ gốc.
    flat = [j for j, s in enumerate(subs) if s.polygon is None]
    return [flat[j] for j in find_main_group([subs[j] for j in flat], fw * options.tol_x_ratio, fh * options.tol_y_ratio)]


def main_group_indices(doc: SrtpxDocument, options: MainCaptionOptions = MainCaptionOptions()) -> set[int] | None:
    """Tập `Subtitle.index` của các phụ đề chính (cùng nhóm với khi xuất video); None nếu không tìm được nhóm chính."""
    group = _main_positions(doc, options)
    if len(group) < options.min_group:
        return None
    return {doc.subtitles[j].index for j in group}


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


def _balanced_partition(widths: Sequence[float], space: float, k: int, max_words: int) -> list[tuple[int, int]]:
    """Chia dãy chữ (độ rộng từng chữ ở cỡ 1px) thành ĐÚNG k đoạn liền nhau, mỗi đoạn tối đa `max_words` chữ, sao cho đoạn rộng
    nhất hẹp nhất (hòa: các đoạn đều nhau hơn nên không có cụm lẻ 1 chữ). Trả về [(đầu, cuối)) theo chỉ số chữ.
    Độ rộng một đoạn = tổng độ rộng chữ + khoảng trắng giữa các chữ (tuyến tính theo cỡ chữ nên cộng được)."""
    n = len(widths)
    pre = [0.0]
    for w in widths:
        pre.append(pre[-1] + w)

    def width(i: int, j: int) -> float:
        return pre[j] - pre[i] + (j - i - 1) * space

    inf = (float("inf"), float("inf"))
    dp = [[inf] * (n + 1) for _ in range(k + 1)]      # dp[kk][j] = (độ rộng lớn nhất, tổng bình phương) tốt nhất khi chia j chữ đầu thành kk đoạn
    cut = [[0] * (n + 1) for _ in range(k + 1)]
    dp[0][0] = (0.0, 0.0)
    for kk in range(1, k + 1):
        for j in range(kk, n + 1):
            for i in range(max(kk - 1, j - max_words), j):
                prev = dp[kk - 1][i]
                if prev == inf:
                    continue
                w = width(i, j)
                cand = (max(prev[0], w), prev[1] + w * w)
                if cand < dp[kk][j]:
                    dp[kk][j], cut[kk][j] = cand, i
    bounds: list[tuple[int, int]] = []
    j = n
    for kk in range(k, 0, -1):
        bounds.append((cut[kk][j], j))
        j = cut[kk][j]
    return bounds[::-1]


def split_chunks_to_fit(
    text: str,
    font: FontData,
    box_w: float,
    box_h: float,
    min_size_ratio: float = DEFAULT_MIN_SIZE,
    max_words: int | None = None,
    balance: bool = True,
    measure_text: str | None = None,
) -> list[list[str]]:
    """Chia chữ thành các cụm theo SỨC CHỨA của hộp đen box_w x box_h (cùng đơn vị với cỡ chữ, vd toạ độ #res).

    Mỗi cụm là một dòng. Cỡ chữ lớn nhất của dòng là box_h / 1,2 (vừa chiều cao hộp); cho chữ co xuống tối thiểu
    `min_size_ratio` lần cỡ đó thì độ rộng dòng tối đa là box_w / (cỡ nhỏ nhất). Đo độ rộng thật từng chữ bằng `font` rồi nhồi chữ
    vào cụm cho tới khi hết chỗ: số cụm ít nhất `k`. `balance` thì chia lại đều theo độ rộng thành đúng k cụm (cụm nào cũng không
    rộng hơn mức nhồi đầy), nên cỡ chữ các cụm gần nhau. `max_words` là mức trần số chữ mỗi cụm nếu người dùng muốn.
    Không cắt giữa một chữ: chữ rộng hơn hộp nằm riêng một cụm (cỡ chữ lúc vẽ sẽ co theo hộp).

    `measure_text`: bản chữ đã đổi dấu câu cho khớp font (`fold_for_font`) để đo; phải cùng số chữ với `text`, không thì đo `text`."""
    words = text.split()
    if not words:
        return []
    n = len(words)
    if box_w <= 0 or box_h <= 0:
        return split_chunks(text, max_words or DEFAULT_WORDS, balance)
    measured = (measure_text if measure_text is not None else text).split()
    if len(measured) != n:
        measured = words
    ratio = min(1.0, max(0.05, min_size_ratio))
    max_em = box_w / (box_h / LINE_HEIGHT_FACTOR * ratio)     # độ rộng dòng tối đa (em, ở cỡ chữ 1px) khi chữ ở cỡ nhỏ nhất cho phép
    limit = max_words if max_words and max_words > 0 else n
    ww = [text_width(font, w, 1.0) for w in measured]
    space = text_width(font, " ", 1.0)

    bounds: list[tuple[int, int]] = []
    i = 0
    while i < n:                                              # nhồi đầy từng cụm (tham lam cho số cụm ít nhất)
        j, w = i + 1, ww[i]
        while j < n and j - i < limit and w + space + ww[j] <= max_em + 1e-9:
            w += space + ww[j]
            j += 1
        bounds.append((i, j))
        i = j
    if balance and len(bounds) > 1:
        bounds = _balanced_partition(ww, space, len(bounds), limit)
    return [words[a:b] for a, b in bounds]


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
    `chain`: chuỗi font để đo độ rộng chữ. Có thì số chữ mỗi cụm tính theo sức chứa hộp đen từng câu (`split_chunks_to_fit`);
    không có thì chia theo số chữ cố định (`options.words_per_chunk` hoặc `DEFAULT_WORDS`). Hộp luôn lấy đúng từ file, không nới theo chữ.
    """
    subs = doc.subtitles
    total = len(subs)
    if not subs:
        return doc, MainCaptionInfo(False, "không có phụ đề")
    group = _main_positions(doc, options)
    if len(group) < options.min_group:
        return doc, MainCaptionInfo(False, f"nhóm cùng vị trí đông nhất chỉ có {len(group)} dòng", n_total=total)

    main_pos = set(group)
    members = [subs[j] for j in group]

    # Trùng giờ: cùng luật lồng tiếng (chỉ giữ block bắt đầu sớm nhất), các block còn lại chỉ giữ hộp để khỏi chồng chữ.
    kept, dropped = drop_overlaps([s for s in members if s.text.strip()])
    kept_ids = {id(s) for s in kept}
    kept.sort(key=lambda s: (s.start, s.index))

    # Chiều dọc = trung bình cả nhóm; chiều ngang giữ riêng từng phụ đề theo file .srtpx (không nới theo chữ).
    fixed = _mean_box([_rest_box(s) for s in members])

    def own_box(sub: Subtitle) -> Box:
        r = _rest_box(sub)
        cx, half = (r.x1 + r.x2) / 2, (r.x2 - r.x1) * BOX_WIDTH_SCALE / 2
        return Box(cx - half, cx + half, fixed.y1, fixed.y2)

    by_box = bool(chain)

    def chunks_for(sub: Subtitle) -> list[list[str]]:
        """Chia chữ một phụ đề chính: theo sức chứa hộp đen của chính nó (có font), không thì số chữ cố định."""
        if not by_box:
            return split_chunks(sub.text, options.words_per_chunk or DEFAULT_WORDS, options.balance)
        folded = fold_for_font(sub.text, chain)
        font = choose_font(folded, chain).font          # cùng font mà lúc vẽ sẽ chọn cho câu này
        b = own_box(sub)
        return split_chunks_to_fit(sub.text, font, b.width, b.height, options.min_size_ratio,
                                   options.words_per_chunk, options.balance, measure_text=folded)

    plan: dict[int, list[tuple[float, float, str]]] = {}   # id(phụ đề) -> [(bắt đầu, kết thúc, chữ)]
    for i, s in enumerate(kept):
        nxt = kept[i + 1].start if i + 1 < len(kept) else None
        speech_end, t_end = _span(s, nxt, (spoken or {}).get(s.index))
        chunks = chunks_for(s)
        weights = [sum(ws) for ws in _per_chunk_weights(chunks)]
        times = chunk_times(weights, s.start, speech_end, t_end)
        plan[id(s)] = [(a, b, " ".join(c)) for (a, b), c in zip(times, chunks)]

    next_index = max(s.index for s in subs) + 1
    out: list[Subtitle] = []
    n_chunks = 0
    for j, s in enumerate(subs):
        if j not in main_pos:
            # phụ đề phụ giữ nguyên (cùng đối tượng); chỉ khi có thể xuống dòng (một dòng nhiều chữ, hộp cao hơn rộng) mới đánh dấu wrap
            out.append(replace(s, wrap=True) if options.wrap_secondary and _can_wrap(s) else s)
            continue
        if id(s) not in kept_ids:   # không có chữ, hoặc trùng giờ: chỉ che bằng hộp cố định
            out.append(replace(s, text="", box=own_box(s), keyframes=[], font_px=None))
            continue
        for a, b, text in plan[id(s)]:
            sub = Subtitle(next_index, a, b, text, box=own_box(s))
            sub.validate()
            out.append(sub)
            next_index += 1
            n_chunks += 1

    info = MainCaptionInfo(
        True, n_main=len(group), n_total=total, n_chunks=n_chunks,
        n_dropped_overlap=len(dropped), box=fixed, by_box=by_box,
        max_chunk_words=max((len(t.split()) for chunks in plan.values() for _, _, t in chunks), default=0),
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
