"""Vẽ lớp (hộp đen + chữ trắng) cho phụ đề CÓ KEYFRAME, từng khung một bằng Pillow (thuần Python, không Qt).

Vì sao: drawbox chỉ nhận toạ độ nguyên nên mép hộp nhảy từng 1-2px không đều; drawtext + perspective thì chữ
bị răng cưa/nhoè khi co giãn. Ở đây mỗi khung hình được vẽ ở độ phân giải gấp SS lần với toạ độ SỐ THỰC (hộp
và chữ cùng một nguồn hình học, nên không thể lệch nhau) rồi thu nhỏ bằng bộ lọc BOX -> mép hộp và nét chữ
chuyển động liên tục dưới 1px. Kết quả là chuỗi PNG RGBA, ffmpeg chỉ việc overlay ở toạ độ nguyên cố định.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

from .fonts import LINE_HEIGHT_FACTOR

SS = 8  # hệ số siêu mẫu


@dataclass
class AnimLayer:
    directory: Path
    font_path: str
    face_index: int
    lines: tuple[str, ...]
    k0: int
    k1: int
    ox: int                      # góc trên trái của canvas trong video (số nguyên chẵn)
    oy: int
    cw: int
    ch: int
    # hàm theo khung k -> (x1, x2, y1, y2, cỡ chữ) đều là số thực theo điểm ảnh video
    geometry: Callable[[int], tuple[float, float, float, float, float]] = field(repr=False, default=None)  # type: ignore[assignment]

    @property
    def pattern(self) -> str:
        return str(self.directory / "f%05d.png")

    def _line_masks(self, big_size: int, pad: int) -> list[tuple[Image.Image, float, float]]:
        """Mỗi dòng chữ vẽ MỘT LẦN ở cỡ lớn (mặt nạ L, tâm ở giữa ảnh). Trả (mặt nạ, tâm_x, tâm_y).

        Vẽ một lần rồi co giãn bằng resample có toạ độ số thực, nên độ rộng chữ đổi liên tục theo hộp: font
        hinting/làm tròn độ rộng từng glyph ở mỗi cỡ chữ khác nhau (nguyên nhân chữ rung ngang) không còn xảy ra.
        """
        font = ImageFont.truetype(self.font_path, big_size, index=self.face_index,
                                  layout_engine=ImageFont.Layout.BASIC)
        out = []
        for line in self.lines:
            if not line:
                out.append((None, 0.0, 0.0))
                continue
            w = math.ceil(font.getlength(line)) + 2 * pad
            h = math.ceil(big_size * 1.6) + 2 * pad
            m = Image.new("L", (w, h), 0)
            ImageDraw.Draw(m).text((w / 2, h / 2), line, font=font, fill=255, anchor="mm")
            out.append((m, w / 2, h / 2))
        return out

    def render(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        n = len(self.lines)
        geoms = {k: self.geometry(k) for k in range(self.k0, self.k1 + 1)}
        sizes = [g[4] for g in geoms.values()]
        big = int(min(400, max(120, round(max(sizes) * 8))))
        pad = int(min(2000, math.ceil(4 * big / max(min(sizes), 1.0))) + 8)
        masks = self._line_masks(big, pad)
        cache: dict[tuple, bytes] = {}
        for k in range(self.k0, self.k1 + 1):
            x1, x2, y1, y2, size = geoms[k]
            key = tuple(round(v, 3) for v in (x1, x2, y1, y2, size))
            path = self.directory / f"f{k - self.k0:05d}.png"
            if key in cache:
                path.write_bytes(cache[key])
                continue
            # hộp: vẽ ở SS lần rồi thu nhỏ -> mép hộp có độ phủ phân số, di chuyển liên tục
            boxbig = Image.new("RGBA", (self.cw * SS, self.ch * SS), (0, 0, 0, 0))
            ImageDraw.Draw(boxbig).rectangle(
                [round((x1 - self.ox) * SS), round((y1 - self.oy) * SS),
                 round((x2 - self.ox) * SS) - 1, round((y2 - self.oy) * SS) - 1],
                fill=(0, 0, 0, 255),
            )
            layer = boxbig.resize((self.cw, self.ch), Image.Resampling.BOX)
            # chữ: mỗi dòng co về cỡ `size` quanh tâm dòng (số thực), đặt vào lưới điểm ảnh nguyên bằng resize(box=...)
            amask = Image.new("L", (self.cw, self.ch), 0)
            f = size / big
            cx = (x1 + x2) / 2 - self.ox
            for j, (m, mcx, mcy) in enumerate(masks):
                if m is None:
                    continue
                cy = (y1 + y2) / 2 - self.oy + (j - (n - 1) / 2) * LINE_HEIGHT_FACTOR * size
                # vùng đích chỉ bao phần chữ (bỏ phần đệm của mặt nạ) + 2px; phần đệm đảm bảo box nguồn luôn nằm trong ảnh
                hw, hh = (m.width / 2 - pad) * f, (m.height / 2 - pad) * f
                px0, px1 = math.floor(cx - hw) - 2, math.ceil(cx + hw) + 2
                py0, py1 = math.floor(cy - hh) - 2, math.ceil(cy + hh) + 2
                src = ((px0 - cx) / f + mcx, (py0 - cy) / f + mcy, (px1 - cx) / f + mcx, (py1 - cy) / f + mcy)
                src = (max(0.0, src[0]), max(0.0, src[1]), min(float(m.width), src[2]), min(float(m.height), src[3]))
                patch = m.resize((px1 - px0, py1 - py0), Image.Resampling.LANCZOS, box=src)
                from PIL import ImageChops
                cur = Image.new("L", (self.cw, self.ch), 0)
                cur.paste(patch, (px0, py0))
                amask = ImageChops.lighter(amask, cur)
            white = Image.new("RGBA", (self.cw, self.ch), (255, 255, 255, 0))
            white.putalpha(amask)
            small = Image.alpha_composite(layer, white)
            from io import BytesIO
            buf = BytesIO()
            small.save(buf, "PNG", compress_level=1)
            data = buf.getvalue()
            cache[key] = data
            path.write_bytes(data)


def canvas_for(boxes: list[tuple[float, float, float, float]], pad: int = 6) -> tuple[int, int, int, int]:
    """(ox, oy, cw, ch) chẵn, bao trọn mọi hộp."""
    x_lo = math.floor(min(b[0] for b in boxes)) - pad
    x_hi = math.ceil(max(b[1] for b in boxes)) + pad
    y_lo = math.floor(min(b[2] for b in boxes)) - pad
    y_hi = math.ceil(max(b[3] for b in boxes)) + pad
    ox, oy = x_lo - (x_lo % 2), y_lo - (y_lo % 2)
    cw = x_hi - ox + ((x_hi - ox) % 2)
    ch = y_hi - oy + ((y_hi - oy) % 2)
    return ox, oy, cw, ch
