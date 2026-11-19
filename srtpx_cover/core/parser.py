"""Đọc/ghi file .srtpx (thuần Python, không Qt).

Quy ước đã chọn (ghi trong tóm tắt Mốc 1):
- Dòng "#" chỉ là chú thích khi nằm NGOÀI nội dung phụ đề (trước block đầu hoặc sau
  dòng Độ rộng/Vị trí/KF), vì nội dung phụ đề có thể bắt đầu bằng "#".
- Có KF thì bỏ qua dòng Độ rộng/Vị trí (mô hình chỉ giữ keyframe).
- Mép x1>x2 hoặc y1>y2 được tự đổi chỗ kèm cảnh báo; giờ kết thúc <= giờ bắt đầu
  chỉ cảnh báo (vẫn giữ block để người dùng sửa).
- Đa giác theo keyframe: dòng "KP <giây>s: x0,y0 x1,y1 x2,y2 x3,y3" đi kèm dòng KF cùng giờ (chữ nghiêng/xoay + chuyển động); hộp đen vẽ
  theo đa giác nội suy giữa các keyframe, KF vẫn là hộp thẳng đặt chữ. Keyframe không có KP dùng 4 góc của hộp thẳng.
- Dòng tuỳ chọn "Đa giác: x0,y0 x1,y1 x2,y2 x3,y3" (4 góc thật của chữ nghiêng, dấu thập phân là '.'): hộp đen vẽ theo
  đa giác; Độ rộng/Vị trí vẫn là hộp thẳng để đặt chữ trắng. Chỉ có tác dụng với phụ đề hộp cố định (có KF thì bỏ qua).
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from pathlib import Path

from .model import (
    Box,
    Keyframe,
    ParseResult,
    ParseWarning,
    SrtpxDocument,
    Subtitle,
)

_NUM = r"-?\d+(?:[.,]\d+)?"
_RES_RE = re.compile(r"^\s*#\s*res\s*[:=]?\s*(\d+)\s*[x×*]\s*(\d+)", re.IGNORECASE)
_INT_LINE_RE = re.compile(r"^\s*\d+\s*$")
_TIME_RE = re.compile(
    r"^\s*(\d+)\s*:\s*(\d{1,2})\s*:\s*(\d{1,2})(?:\s*[,.]\s*(\d{1,3}))?"
    r"\s*-+>\s*"
    r"(\d+)\s*:\s*(\d{1,2})\s*:\s*(\d{1,2})(?:\s*[,.]\s*(\d{1,3}))?\s*$"
)
_ARROW_RE = re.compile(r"-+>")
_WIDTH_RE = re.compile(r"^\s*Độ\s*rộng\s*[:：]", re.IGNORECASE)
_POS_RE = re.compile(r"^\s*Vị\s*trí\s*[:：]", re.IGNORECASE)
_POLY_RE = re.compile(r"^\s*Đa\s*giác\s*[:：]", re.IGNORECASE)
_POLY_PAIR_RE = re.compile(rf"(-?\d+(?:\.\d+)?)\s*[,;]\s*(-?\d+(?:\.\d+)?)")
_KP_RE = re.compile(r"^\s*KP\s*(\d+(?:[.,]\d+)?)\s*s?\s*[:：]?\s*(.*)$", re.IGNORECASE)
_KF_RE = re.compile(r"^\s*KF\s*(\d+(?:[.,]\d+)?)\s*s?\s*[:：]?\s*(.*)$", re.IGNORECASE)
_PAREN_RE = re.compile(r"\([^)]*\)|（[^）]*）")
_PAIR_RE = re.compile(rf"^\s*({_NUM})\s*(?:px)?\s*[-–—~]\s*({_NUM})\s*(?:px)?\s*$", re.IGNORECASE)
_SPLIT_LINES_RE = re.compile(r"\r\n|\r|\n")


class SrtpxError(Exception):
    """Lỗi không đọc được cả file (ví dụ sai mã hoá). Thông báo bằng tiếng Việt."""


def _norm(line: str) -> str:
    """Chuẩn hoá NFC chỉ để so khớp nhãn (file có thể dùng dấu tiếng Việt dạng tách)."""
    return unicodedata.normalize("NFC", line)


def _to_float(s: str) -> float:
    return float(s.replace(",", "."))


def _ms_to_seconds(h: str, m: str, s: str, frac: str | None) -> float:
    ms = 0
    if frac:
        ms = int(frac.ljust(3, "0"))  # "5" -> 500 ms, "30" -> 300 ms
    total_ms = (int(h) * 3600 + int(m) * 60 + int(s)) * 1000 + ms
    return total_ms / 1000  # chia số nguyên cho 1000 => trùng đúng float của chuỗi thập phân


def _parse_pair(value: str) -> tuple[float, float] | None:
    """Đọc 'a px - b px' (bỏ chú thích trong ngoặc). Trả None nếu sai định dạng."""
    cleaned = _PAREN_RE.sub("", value)
    m = _PAIR_RE.match(cleaned)
    if not m:
        return None
    return _to_float(m.group(1)), _to_float(m.group(2))


def _parse_four(value: str) -> tuple[float, float, float, float] | None:
    """Đọc 4 số 'x1 x2 y1 y2' (cách nhau bằng khoảng trắng; nếu không được thì thử dấu , ;)."""
    cleaned = re.sub(r"px", "", _PAREN_RE.sub("", value), flags=re.IGNORECASE)
    tokens = re.findall(_NUM, cleaned)
    if len(tokens) != 4:
        tokens = re.findall(r"-?\d+(?:\.\d+)?", re.sub(r"[;,]", " ", cleaned))
    if len(tokens) != 4:
        return None
    a, b, c, d = (_to_float(t) for t in tokens)
    return a, b, c, d


def _parse_polygon(value: str) -> tuple[tuple[float, float], ...] | None:
    """Đọc 'x0,y0 x1,y1 x2,y2 x3,y3' (bỏ chú thích trong ngoặc và 'px'). None nếu không đúng 4 góc."""
    cleaned = re.sub(r"px", "", _PAREN_RE.sub("", value), flags=re.IGNORECASE)
    pairs = _POLY_PAIR_RE.findall(cleaned)
    if len(pairs) != 4:
        return None
    return tuple((float(a), float(b)) for a, b in pairs)


def _polygon_area(pts: tuple[tuple[float, float], ...]) -> float:
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]))) / 2


def _ordered_box(x1: float, x2: float, y1: float, y2: float) -> tuple[Box, bool]:
    swapped = x1 > x2 or y1 > y2
    return Box(min(x1, x2), max(x1, x2), min(y1, y2), max(y1, y2)), swapped


def _find_block_starts(lines: list[str]) -> list[tuple[int, int | None, bool]]:
    """Trả về [(chỉ số dòng giờ, chỉ số dòng số thứ tự hoặc None, giờ_hợp_lệ)]."""
    starts: list[tuple[int, int | None, bool]] = []
    prev_time_idx = -1
    for i, raw in enumerate(lines):
        line = _norm(raw)
        full = bool(_TIME_RE.match(line))
        loose = False
        number_idx: int | None = None
        if full or _ARROW_RE.search(line):
            j = i - 1
            while j > prev_time_idx and not lines[j].strip():
                j -= 1
            if j > prev_time_idx and _INT_LINE_RE.match(lines[j]):
                number_idx = j
            # Dòng chỉ chứa '-->' trong nội dung thì không phải đầu block, trừ khi có số thứ tự đứng trước.
            loose = number_idx is not None and not full
        if full or loose:
            starts.append((i, number_idx, full))
            prev_time_idx = i
    return starts


def parse_srtpx(text: str) -> ParseResult:
    """Đọc nội dung .srtpx. Không bao giờ ném lỗi vì dữ liệu xấu: gom cảnh báo, bỏ block lỗi."""
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = _SPLIT_LINES_RE.split(text)
    warnings: list[ParseWarning] = []
    doc = SrtpxDocument()

    starts = _find_block_starts(lines)
    first_block_line = (starts[0][1] if starts[0][1] is not None else starts[0][0]) if starts else len(lines)

    # Phần đầu file: tìm "#res".
    for line_no, raw in enumerate(lines[:first_block_line], start=1):
        m = _RES_RE.match(_norm(raw))
        if m:
            w, h = int(m.group(1)), int(m.group(2))
            if w > 0 and h > 0:
                doc.res = (w, h)
            else:
                warnings.append(ParseWarning(line_no, "Giá trị #res phải lớn hơn 0, bỏ qua."))
            break

    if not starts:
        warnings.append(ParseWarning(1, "Không tìm thấy phụ đề nào trong file."))
        return ParseResult(doc, warnings)

    last_index = 0
    for k, (time_idx, number_idx, time_ok) in enumerate(starts):
        block_begin = number_idx if number_idx is not None else time_idx
        if k + 1 < len(starts):
            nxt_time, nxt_num, _ = starts[k + 1]
            region_end = nxt_num if nxt_num is not None else nxt_time
        else:
            region_end = len(lines)
        sub = _parse_block(lines, block_begin, number_idx, time_idx, time_ok, region_end, last_index, warnings)
        if sub is not None:
            doc.subtitles.append(sub)
            last_index = sub.index
        elif number_idx is not None:
            last_index = int(lines[number_idx].strip())
    return ParseResult(doc, warnings)


def _parse_block(
    lines: list[str],
    block_begin: int,
    number_idx: int | None,
    time_idx: int,
    time_ok: bool,
    region_end: int,
    last_index: int,
    warnings: list[ParseWarning],
) -> Subtitle | None:
    lineno = block_begin + 1

    def fatal(line_no: int, msg: str) -> None:
        warnings.append(ParseWarning(line_no, msg, block_skipped=True))

    if not time_ok:
        fatal(time_idx + 1, "Dòng thời gian sai định dạng (cần dạng 00:00:00,000 --> 00:00:00,000).")
        return None

    if number_idx is not None:
        index = int(lines[number_idx].strip())
    else:
        index = last_index + 1
        warnings.append(ParseWarning(time_idx + 1, f"Thiếu số thứ tự, tự đặt là {index}."))

    m = _TIME_RE.match(_norm(lines[time_idx]))
    assert m is not None
    start = _ms_to_seconds(*m.group(1, 2, 3, 4))
    end = _ms_to_seconds(*m.group(5, 6, 7, 8))
    if end <= start:
        warnings.append(ParseWarning(time_idx + 1, f"Phụ đề {index}: giờ kết thúc không sau giờ bắt đầu (vẫn giữ để bạn sửa)."))

    body = list(range(time_idx + 1, region_end))
    norm = {i: _norm(lines[i]) for i in body}
    width_idx = next((i for i in body if _WIDTH_RE.match(norm[i])), None)
    pos_idx = next((i for i in body if _POS_RE.match(norm[i])), None)
    poly_idx = next((i for i in body if _POLY_RE.match(norm[i])), None)
    kf_idxs = [i for i in body if _KF_RE.match(norm[i])]

    # Điểm neo của nội dung: dòng "Độ rộng:"; nếu thiếu thì dòng Vị trí/KF đầu tiên.
    if width_idx is not None:
        content_end = width_idx
    else:
        fallbacks = [i for i in ([pos_idx] if pos_idx is not None else []) + ([poly_idx] if poly_idx is not None else []) + kf_idxs]
        content_end = min(fallbacks) if fallbacks else region_end
    content_lines = [lines[i] for i in range(time_idx + 1, content_end)]
    while content_lines and not content_lines[0].strip():
        content_lines.pop(0)
    while content_lines and not content_lines[-1].strip():
        content_lines.pop()
    text = "\n".join(content_lines)

    # Phần metadata sau nội dung.
    keyframes: dict[float, Keyframe] = {}
    kp_lines: dict[float, tuple[tuple[float, float], ...]] = {}
    for i in range(content_end, region_end):
        line = norm[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if i == width_idx or i == pos_idx or i == poly_idx:
            continue
        pm = _KP_RE.match(line)
        if pm:
            poly_kp = _parse_polygon(pm.group(2))
            if poly_kp is None or _polygon_area(poly_kp) < 1.0:
                warnings.append(ParseWarning(i + 1, f"Phụ đề {index}: dòng KP cần đúng 4 góc dạng x,y có diện tích; bỏ qua dòng này."))
            else:
                kp_lines[_to_float(pm.group(1))] = poly_kp
            continue
        km = _KF_RE.match(line)
        if km:
            nums = _parse_four(km.group(2))
            if nums is None:
                warnings.append(ParseWarning(i + 1, f"Phụ đề {index}: dòng KF sai định dạng, bỏ qua dòng này."))
                continue
            box, swapped = _ordered_box(*nums)
            if swapped:
                warnings.append(ParseWarning(i + 1, f"Phụ đề {index}: keyframe có mép trái/phải hoặc trên/dưới bị ngược, đã đổi chỗ."))
            t = _to_float(km.group(1))
            if t in keyframes:
                warnings.append(ParseWarning(i + 1, f"Phụ đề {index}: hai keyframe trùng giờ {t}s, giữ dòng sau."))
            keyframes[t] = Keyframe(t, box)
            continue
        warnings.append(ParseWarning(i + 1, f"Phụ đề {index}: dòng không nhận dạng được, bỏ qua."))

    if keyframes:
        if poly_idx is not None:
            warnings.append(ParseWarning(poly_idx + 1, f"Phụ đề {index}: có KF nên bỏ qua dòng Đa giác."))
        for t, poly_kp in kp_lines.items():
            if t in keyframes:
                keyframes[t] = Keyframe(t, keyframes[t].box, poly_kp)
            else:
                warnings.append(ParseWarning(content_end + 1, f"Phụ đề {index}: dòng KP {t}s không có keyframe KF cùng giờ, bỏ qua."))
        kfs = [keyframes[t] for t in sorted(keyframes)]
        return Subtitle(index, start, end, text, None, kfs)

    # Không có KF: cần cả Độ rộng và Vị trí.
    if width_idx is None or pos_idx is None:
        missing = " và ".join(n for n, v in (("Độ rộng", width_idx), ("Vị trí", pos_idx)) if v is None)
        fatal(lineno, f"Phụ đề {index}: thiếu {missing} và không có keyframe.")
        return None
    xs = _parse_pair(norm[width_idx].split(":", 1)[1] if ":" in norm[width_idx] else norm[width_idx].split("：", 1)[1])
    ys = _parse_pair(norm[pos_idx].split(":", 1)[1] if ":" in norm[pos_idx] else norm[pos_idx].split("：", 1)[1])
    if xs is None:
        fatal(width_idx + 1, f"Phụ đề {index}: dòng Độ rộng sai định dạng.")
        return None
    if ys is None:
        fatal(pos_idx + 1, f"Phụ đề {index}: dòng Vị trí sai định dạng.")
        return None
    box, swapped = _ordered_box(xs[0], xs[1], ys[0], ys[1])
    if swapped:
        warnings.append(ParseWarning(width_idx + 1, f"Phụ đề {index}: mép bị ngược, đã đổi chỗ."))
    polygon = None
    if poly_idx is not None:
        raw_poly = norm[poly_idx].split(":", 1)[1] if ":" in norm[poly_idx] else norm[poly_idx].split("：", 1)[1]
        polygon = _parse_polygon(raw_poly)
        if polygon is None:
            warnings.append(ParseWarning(poly_idx + 1, f"Phụ đề {index}: dòng Đa giác cần đúng 4 góc dạng x,y; bỏ qua dòng này."))
        elif _polygon_area(polygon) < 1.0:
            warnings.append(ParseWarning(poly_idx + 1, f"Phụ đề {index}: đa giác không có diện tích; bỏ qua dòng này."))
            polygon = None
    return Subtitle(index, start, end, text, box, [], polygon=polygon)


# ---------------------------------------------------------------- Ghi file

def _fmt_num(v: float) -> str:
    """Số thập phân ngắn nhất mà đọc lại ra đúng giá trị (không dùng ký hiệu 1e-05)."""
    s = format(Decimal(repr(float(v))), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _fmt_seconds(v: float) -> str:
    s = format(Decimal(repr(float(v))), "f")
    if "." not in s:
        return s + ".00"
    whole, frac = s.split(".")
    frac = frac.rstrip("0").ljust(2, "0")
    return f"{whole}.{frac}"


def format_time(t: float) -> str:
    """Giây -> 'HH:MM:SS,mmm' (làm tròn tới mili giây, không âm)."""
    ms_total = max(0, round(t * 1000))
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srtpx(doc: SrtpxDocument) -> str:
    """Sinh nội dung .srtpx (xuống dòng '\\n', không BOM). Chú thích trong ngoặc không được giữ."""
    out: list[str] = []
    if doc.res is not None:
        out.append(f"#res {doc.res[0]}x{doc.res[1]}")
        out.append("")
    for sub in doc.subtitles:
        sub.validate()
        out.append(str(sub.index))
        out.append(f"{format_time(sub.start)} --> {format_time(sub.end)}")
        text = sub.text.replace("\r\n", "\n").replace("\r", "\n")
        out.extend(text.split("\n"))
        b = sub.first_box
        out.append(f"Độ rộng: {_fmt_num(b.x1)}px - {_fmt_num(b.x2)}px")
        out.append(f"Vị trí: {_fmt_num(b.y1)}px - {_fmt_num(b.y2)}px")
        if sub.polygon is not None:
            out.append("Đa giác: " + " ".join(f"{_fmt_num(x)},{_fmt_num(y)}" for x, y in sub.polygon))
        for kf in sub.keyframes:
            kb = kf.box
            out.append(
                f"KF {_fmt_seconds(kf.time)}s: "
                f"{_fmt_num(kb.x1)} {_fmt_num(kb.x2)} {_fmt_num(kb.y1)} {_fmt_num(kb.y2)}"
            )
            if kf.polygon is not None:
                out.append(f"KP {_fmt_seconds(kf.time)}s: " + " ".join(f"{_fmt_num(x)},{_fmt_num(y)}" for x, y in kf.polygon))
        out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------- Đọc/ghi file trên đĩa

def read_srtpx(path: str | Path) -> ParseResult:
    """Đọc file .srtpx (UTF-8, chịu BOM)."""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise SrtpxError(f"Không mở được file: {exc}") from exc
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SrtpxError("File không phải mã hoá UTF-8. Hãy lưu lại file bằng UTF-8.") from exc
    return parse_srtpx(text)


def save_srtpx(doc: SrtpxDocument, path: str | Path) -> None:
    """Ghi file .srtpx bằng UTF-8 không BOM."""
    try:
        Path(path).write_bytes(write_srtpx(doc).encode("utf-8"))
    except OSError as exc:
        raise SrtpxError(f"Không ghi được file: {exc}") from exc
