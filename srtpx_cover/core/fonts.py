"""Chọn font theo độ phủ glyph và đo chiều rộng chữ bằng fontTools (thuần Python, không Qt)."""
from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from fontTools.ttLib import TTFont

SAFETY_FACTOR = 1.04   # chừa 4% an toàn khi đo chiều rộng (không tính kerning)
LINE_HEIGHT_FACTOR = 1.2


class FontError(Exception):
    """Lỗi font (thiếu file, file hỏng). Thông báo bằng tiếng Việt."""


@dataclass(frozen=True)
class FontSpec:
    """Đường dẫn font; `face_index` dùng cho file TTC (msyh.ttc -> 0)."""

    path: str
    face_index: int = 0

    @property
    def label(self) -> str:
        return Path(self.path).name


class FontData:
    """Dữ liệu đã đọc của một font: bảng cmap và chiều rộng (advance) từng glyph."""

    def __init__(self, spec: FontSpec):
        self.spec = spec
        try:
            with TTFont(spec.path, fontNumber=spec.face_index, lazy=False) as f:
                self.units_per_em: int = int(f["head"].unitsPerEm)
                self._cmap: dict[int, str] = dict(f.getBestCmap() or {})
                self._advance: dict[str, int] = {n: m[0] for n, m in f["hmtx"].metrics.items()}
        except FileNotFoundError as exc:
            raise FontError(f"Không tìm thấy file font: {spec.path}") from exc
        except Exception as exc:  # fontTools ném nhiều loại lỗi khác nhau với file hỏng
            raise FontError(f"Không đọc được font {spec.path}: {exc}") from exc
        self._notdef = self._advance.get(".notdef", self.units_per_em // 2)

    def has_char(self, ch: str) -> bool:
        return ord(ch) in self._cmap

    def advance(self, ch: str) -> float:
        """Chiều rộng ký tự tính theo em (1.0 = bằng cỡ chữ). Ký tự thiếu dùng .notdef."""
        name = self._cmap.get(ord(ch))
        units = self._advance.get(name, self._notdef) if name is not None else self._notdef
        return units / self.units_per_em


def is_emoji(ch: str) -> bool:
    """Emoji màu: drawtext không hỗ trợ nên luôn tính là thiếu (dải U+1F000–U+1FAFF)."""
    return 0x1F000 <= ord(ch) <= 0x1FAFF


def _ignored(ch: str) -> bool:
    """Ký tự không cần glyph: khoảng trắng, xuống dòng, điều khiển/định dạng (ZWJ), chọn biến thể."""
    return ch.isspace() or unicodedata.category(ch) in ("Cc", "Cf") or 0xFE00 <= ord(ch) <= 0xFE0F


def missing_chars(font: FontData, text: str) -> list[str]:
    """Các ký tự (không trùng, theo thứ tự xuất hiện) mà font không vẽ được."""
    seen: dict[str, None] = {}
    for ch in text:
        if _ignored(ch) or ch in seen:
            continue
        if is_emoji(ch) or not font.has_char(ch):
            seen[ch] = None
    return list(seen)


# Dấu câu hay gặp trong bản dịch mà font đẹp (Bevan...) thường thiếu -> ký tự tương đương dễ có.
PUNCT_FOLD = {
    "《": '"', "》": '"', "〈": '"', "〉": '"', "「": '"', "」": '"', "『": '"', "』": '"', "｢": '"', "｣": '"',
    "“": '"', "”": '"', "„": '"', "‟": '"', "«": '"', "»": '"', "‹": '"', "›": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "【": "[", "】": "]", "〔": "(", "〕": ")", "〖": "[", "〗": "]", "（": "(", "）": ")", "［": "[", "］": "]",
    "｛": "{", "｝": "}", "…": "...", "—": "-", "–": "-", "―": "-", "～": "~", "、": ",", "，": ",", "。": ".",
    "：": ":", "；": ";", "？": "?", "！": "!", "\u3000": " ", "\u00a0": " ",
}


def fold_for_font(text: str, chain: Sequence[FontData]) -> str:
    """Đổi các dấu câu mà font chính (chain[0]) thiếu sang ký tự tương đương để CẢ câu vẽ bằng font chính,
    khỏi bị nhảy sang font dự phòng (khác kiểu chữ) chỉ vì 1 dấu ngoặc kép/ngoặc.

    Chỉ áp dụng khi sau khi đổi font chính phủ trọn câu; nếu không thì giữ nguyên chữ gốc (vd chữ Hán thật)."""
    if not chain or not missing_chars(chain[0], text):
        return text
    base = chain[0]
    out = []
    for ch in text:
        if _ignored(ch) or (not is_emoji(ch) and base.has_char(ch)):
            out.append(ch)
            continue
        sub = PUNCT_FOLD.get(ch) or unicodedata.normalize("NFKC", ch)
        out.append(sub if sub != ch and not missing_chars(base, sub) else ch)
    folded = "".join(out)
    return folded if not missing_chars(base, folded) else text


@dataclass(frozen=True)
class FontChoice:
    font: FontData
    chain_index: int           # 0 = font chính
    missing: tuple[str, ...]   # ký tự thiếu ngay cả ở font đã chọn

    @property
    def used_fallback(self) -> bool:
        return self.chain_index > 0


def choose_font(text: str, chain: Sequence[FontData]) -> FontChoice:
    """Font ĐẦU TIÊN trong chuỗi phủ mọi ký tự; nếu không có thì font phủ nhiều nhất (hoà: font đứng trước)."""
    if not chain:
        raise FontError("Chưa có font nào để vẽ chữ. Hãy kiểm tra thư mục fonts/ hoặc phần Cài đặt.")
    best: tuple[int, int] | None = None
    best_missing: list[str] = []
    for i, font in enumerate(chain):
        miss = missing_chars(font, text)
        if not miss:
            return FontChoice(font, i, ())
        if best is None or len(miss) < len(best_missing):
            best, best_missing = (i, len(miss)), miss
    assert best is not None
    return FontChoice(chain[best[0]], best[0], tuple(best_missing))


def text_width(font: FontData, text: str, size: float) -> float:
    """Chiều rộng px của một dòng ở cỡ `size` (tổng advance, bỏ kerning, nhân hệ số an toàn 4%)."""
    return sum(font.advance(ch) for ch in text) * size * SAFETY_FACTOR


def describe_font_warnings(choices: dict[int, FontChoice]) -> str | None:
    """MỘT cảnh báo tiếng Việt cho cả lần xuất; None nếu không cần cảnh báo.

    `choices`: số thứ tự phụ đề -> font đã chọn.
    """
    swapped = sorted(i for i, c in choices.items() if c.used_fallback)
    with_missing = {i: c.missing for i, c in sorted(choices.items()) if c.missing}
    if not swapped and not with_missing:
        return None
    lines: list[str] = []
    if swapped:
        lines.append("Phụ đề phải đổi sang font dự phòng: " + ", ".join(map(str, swapped)) + ".")
    if with_missing:
        lines.append("Có ký tự mà không font nào vẽ được (sẽ hiện ô trống hoặc bị mất):")
        for i, chars in with_missing.items():
            lines.append(f"  - Phụ đề {i}: " + " ".join(f"{c} (U+{ord(c):04X})" for c in chars))
    return "\n".join(lines)


# ---------------------------------------------------------------- Chuỗi font mặc định

def default_font_chain(fonts_dir: Path) -> tuple[list[FontSpec], list[str]]:
    """Chuỗi mặc định Bevan -> Arial/Segoe UI -> Microsoft YaHei; chỉ giữ file có thật.

    Trả về (chuỗi, danh sách cảnh báo tiếng Việt về font không tìm thấy).
    """
    win = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    slots: list[tuple[str, list[FontSpec]]] = [
        ("Bevan Regular", [FontSpec(str(fonts_dir / "Bevan-Regular.ttf"))]),
        ("Arial / Segoe UI", [FontSpec(str(win / "arial.ttf")), FontSpec(str(win / "segoeui.ttf"))]),
        ("Microsoft YaHei", [FontSpec(str(win / "msyh.ttc"), 0)]),
    ]
    chain: list[FontSpec] = []
    warnings: list[str] = []
    for name, candidates in slots:
        found = next((c for c in candidates if Path(c.path).is_file()), None)
        if found:
            chain.append(found)
        else:
            warnings.append(f"Không tìm thấy font {name} ({candidates[0].path}); bỏ qua font này.")
    return chain, warnings


def load_chain(specs: Sequence[FontSpec]) -> list[FontData]:
    return [FontData(s) for s in specs]


def parse_font_path(s: str) -> FontSpec:
    """'đường/dẫn.ttc' hoặc 'đường/dẫn.ttc#1' (số sau # là chỉ số face trong file TTC)."""
    path, sep, idx = s.rpartition("#")
    if sep and idx.isdigit():
        return FontSpec(path, int(idx))
    return FontSpec(s, 0)


def font_chain_from_settings(fonts_dir: Path, overrides: Sequence[str]) -> tuple[list[FontSpec], list[str]]:
    """Chuỗi font theo Cài đặt: `overrides` = [chính, tiếng Việt, CJK]; ô trống = dùng mặc định của ô đó.

    Trả về (chuỗi chỉ gồm file có thật, cảnh báo tiếng Việt).
    """
    defaults, warnings = default_font_chain(fonts_dir)
    win = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    default_slots = [
        FontSpec(str(fonts_dir / "Bevan-Regular.ttf")),
        next((c for c in (FontSpec(str(win / "arial.ttf")), FontSpec(str(win / "segoeui.ttf")))
              if Path(c.path).is_file()), FontSpec(str(win / "arial.ttf"))),
        FontSpec(str(win / "msyh.ttc"), 0),
    ]
    chain: list[FontSpec] = []
    warnings = []
    names = ("font chính", "font tiếng Việt", "font CJK")
    for i, name in enumerate(names):
        ov = (overrides[i] if i < len(overrides) else "").strip()
        spec = parse_font_path(ov) if ov else default_slots[i]
        if Path(spec.path).is_file():
            chain.append(spec)
        else:
            warnings.append(f"Không tìm thấy {name} ({spec.path}); bỏ qua font này.")
    return chain, warnings
