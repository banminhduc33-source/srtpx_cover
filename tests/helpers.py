"""Hàm dùng chung cho test mốc 2: font giả (fontTools) và tìm font/ffmpeg thật trên máy."""
from __future__ import annotations

import shutil
from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from srtpx_cover.core.fonts import FontData, FontSpec

VIET_CHARS = (
    "ăâêôơưđĂÂÊÔƠƯĐáàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ"
)
ASCII_NO_SPACE = "".join(chr(c) for c in range(33, 127))  # cố ý bỏ khoảng trắng: nó không cần glyph


def make_font(path: Path, text_chars: str, advance: int = 600, upem: int = 1000, extra: tuple[int, ...] = ()) -> FontSpec:
    """Tạo font TTF giả phủ đúng các ký tự đã cho; mỗi glyph rộng `advance`, .notdef rộng 500."""
    cps = sorted({ord(c) for c in text_chars} | set(extra))
    names = [".notdef"] + [f"g{cp:X}" for cp in cps]
    fb = FontBuilder(upem, isTTF=True)
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap({cp: f"g{cp:X}" for cp in cps})
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0)); pen.lineTo((0, 500)); pen.lineTo((500, 500)); pen.lineTo((500, 0)); pen.closePath()
    glyph = pen.glyph()
    fb.setupGlyf({n: glyph for n in names})
    fb.setupHorizontalMetrics({n: (500 if n == ".notdef" else advance, 0) for n in names})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Test", "styleName": "Regular"})
    fb.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    fb.setupPost()
    fb.save(str(path))
    return FontSpec(str(path))


def fake_chain(tmp: Path) -> tuple[FontData, FontData, FontData]:
    """(latin chỉ ASCII, viet = ASCII + tiếng Việt, cjk = ASCII + vài chữ Hán, rộng 1em)."""
    latin = FontData(make_font(tmp / "latin.ttf", ASCII_NO_SPACE))
    viet = FontData(make_font(tmp / "viet.ttf", ASCII_NO_SPACE + VIET_CHARS))
    cjk = FontData(make_font(tmp / "cjk.ttf", ASCII_NO_SPACE + "原神大世界做镜子了？米提亚先生该回去上班瓦列里啊哈", advance=1000))
    return latin, viet, cjk


# ---- font và công cụ thật (cho test pixel; thiếu thì test tự bỏ qua)

_FONT_CANDIDATES = {
    "latin": [
        "fonts/Bevan-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ],
    "viet": [
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ],
    "cjk": [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/msyh.ttc",
    ],
}


def real_font_chain() -> list[FontSpec] | None:
    """Chuỗi font thật (Latin -> Việt -> CJK) hoặc None nếu máy thiếu font CJK."""
    chain: list[FontSpec] = []
    for slot in ("latin", "viet", "cjk"):
        found = next((p for p in _FONT_CANDIDATES[slot] if Path(p).is_file()), None)
        if found is None:
            if slot == "cjk":
                return None
            continue
        chain.append(FontSpec(found, 0))
    return chain


def have_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


# ---- đọc ngược filter script: kích thước/tâm chữ co giãn (lớp perspective) tại giờ t

def eval_ff(expr: str, **env) -> float:
    """Tính biểu thức ffmpeg đơn giản (if/lt/max/min + biến) bằng Python."""
    py = (expr.replace("if(", "_if(").replace("lt(", "_lt(")
          .replace("max(", "_max(").replace("min(", "_min("))
    return eval(py, {"_if": lambda c, a, b: a if c else b, "_lt": lambda a, b: a < b,
                     "_max": max, "_min": min, **env})


def layer_at(script_text: str, index: int, t: float, fps: float = 30.0) -> tuple[float, float, float, tuple[int, int]]:
    """(cỡ chữ, tâm x, tâm y dòng đầu, (ox, oy) của overlay) của lớp chữ co giãn phụ đề `index` tại giờ t."""
    import re
    segs = script_text.split(";\n")
    seg = next(s for s in segs if "perspective=" in s and f"sub{index:04d}_" in s)
    lay = re.search(r"\[(tx\d+)\]$", seg.strip()).group(1)
    ov = next(s for s in segs if f"[{lay}]overlay=" in s)
    ox, oy = map(int, re.search(r"overlay=x=(-?\d+):y=(-?\d+)", ov).groups())
    cw = int(re.search(r"s=(\d+)x\d+", seg).group(1))
    s0 = int(re.search(r"fontsize=(\d+)", seg).group(1))
    lay_t = int(re.search(r"setpts=PTS\+(\d+),", seg).group(1)) / fps  # PTS đếm theo số khung: cộng k0 nguyên
    on = round((t - lay_t) * fps)
    m = re.search(r"perspective=x0='(.*?)':y0='(.*?)':x1='(.*?)':y1='(.*?)':x2='(.*?)':y2='(.*?)':x3='(.*?)':y3='(.*?)':", seg)
    x0, y0, x1, _, _, y2, _, _ = (eval_ff(g, on=on) for g in m.groups())
    return (x1 - x0) / cw * s0, (x0 + x1) / 2 + ox, (y0 + y2) / 2 + oy, (ox, oy)
