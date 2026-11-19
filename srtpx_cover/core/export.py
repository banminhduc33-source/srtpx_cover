"""Sinh filter script và lệnh ffmpeg từ model (thuần Python, không Qt).

Quyết định kỹ thuật (xem docs/spike_drawbox.md):
- drawbox KHÔNG nhận biểu thức theo thời gian (biến `t` ở đó là độ dày viền, `n` không tồn tại),
  nên hộp có keyframe được chia thành các đoạn: mỗi đoạn là một drawbox toạ độ hằng + enable riêng.
  Đoạn = các khung hình liên tiếp có cùng hộp nguyên (kết quả của pixel_box tại giờ từng khung),
  nên hộp xuất ra khớp hàm hình học 0px, kể cả phụ đề không có keyframe (chỉ một đoạn).
- Cửa sổ enable đặt lệch nửa khung ở hai đầu: gte(t,(k0-0.5)/fps)*lt(t,(k1+0.5)/fps), để so sánh
  không rơi đúng ranh giới số thực (vd 10.2s). Vẫn là gte/lt nửa mở, không dùng between().
- drawtext nhận biểu thức theo t nhưng làm tròn cỡ chữ/vị trí về số nguyên px, nên chỉ dùng cho chữ cố định. Chữ co giãn hoặc
  dời theo keyframe đi qua lớp `perspective` (xem build_filter_script) để cỡ chữ và vị trí đổi mượt dưới 1px.
  Mỗi dòng chữ là một drawtext riêng, căn giữa bằng text_w (ngang) và text_h (dọc, chiều cao phần chữ
  được vẽ; KHÔNG dùng line_h vì với font CJK nó cao ~1.45em làm chữ lệch lên trên) để không phụ thuộc text_align.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .ffmpeg_tools import VideoInfo
from .fonts import FontChoice, FontData, describe_font_warnings
from .geometry import (
    PixelBox,
    compute_scale,
    frame_time,
    has_keyframe_polygon,
    pixel_box,
    polygon_bounds,
    polygon_frame,
    video_box,
    video_polygon,
    visible_frame_range,
)
from .animlayer import AnimLayer, canvas_for
from .layout import LINE_HEIGHT_FACTOR, plan_text
from .model import SrtpxDocument, Subtitle


@dataclass(frozen=True)
class ExportOptions:
    margin: float = 1.0
    smooth: bool = True             # nội suy mượt (PCHIP) giữa các keyframe; False = tuyến tính như trước
    draw_text: bool = True
    crf: int = 18
    preset: str = "veryfast"
    threads: int = 2
    codec: str = "libx264"          # libx264 hoặc libx265
    start: float | None = None      # xuất theo đoạn: giây bắt đầu (giờ video gốc)
    end: float | None = None        # giây kết thúc; None = tới hết video
    # {Subtitle.index: (R, G, B)}: màu hộp che riêng cho từng phụ đề (xem bgcolor.py); phụ đề không có trong đây dùng hộp đen
    box_colors: dict[int, tuple[int, int, int]] | None = field(default=None, compare=False, repr=False)

    def segment_seconds(self, total: float) -> float:
        """Thời lượng thực sẽ xuất (để tính thanh tiến trình)."""
        a = max(0.0, self.start or 0.0)
        b = min(total, self.end) if self.end is not None else total
        return max(0.0, b - a)


@dataclass
class FilterScript:
    text: str                                  # nội dung file filter script
    textfiles: dict[Path, str] = field(default_factory=dict)  # file chữ tạm cần ghi (UTF-8 không BOM)
    warnings: list[str] = field(default_factory=list)
    complex: bool = False                      # True: là filter_complex (nhãn đầu ra [vout]); False: chuỗi filter đơn
    layers: list = field(default_factory=list)  # AnimLayer: chuỗi PNG vẽ sẵn cho phụ đề có keyframe (đầu vào ffmpeg thứ 1, 2, ...)

    def extra_inputs(self, fps_num: int, fps_den: int) -> list[str]:
        """Đối số `-framerate .. -i ..` cho từng chuỗi PNG (đầu vào số i+1 trong filter_complex)."""
        args: list[str] = []
        for lay in self.layers:
            args += ["-framerate", f"{fps_num}/{fps_den}", "-i", lay.pattern]
        return args


# ---------------------------------------------------------------- Escape và biểu thức

def _escape(s: str, specials: str) -> str:
    return "".join("\\" + c if c in specials else c for c in s)


def escape_path(path: str | Path) -> str:
    """Đường dẫn dùng trong fontfile=/textfile= (không bao nháy), escape hai tầng của ffmpeg.

    '\\' đổi thành '/' (Windows chấp nhận). Tầng 1 (giá trị tuỳ chọn) escape  \\ ' :  ; tầng 2
    (filtergraph) escape  \\ ' , ; [ ] = . Kết quả: ổ đĩa 'C:' thành C\\\\:/..., dấu ' thành \\\\\\'.
    Đã đo trên ffmpeg thật, kể cả đường dẫn chứa : ' , ; [ ] = và khoảng trắng.
    """
    s = str(path).replace("\\", "/")
    return _escape(_escape(s, "\\':"), "\\',;[]=")


def _num(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def lin_expr(points: Sequence[tuple[float, float]], var: str = "t") -> str:
    """Biểu thức ffmpeg nội suy tuyến tính theo `var` (mặc định t; filter perspective dùng biểu thức theo `on`)
    qua các điểm (giờ, giá trị); ngoài khoảng giữ giá trị biên."""
    if len(points) == 1:
        return _num(points[0][1])
    expr = _num(points[-1][1])
    for (t0, v0), (t1, v1) in reversed(list(zip(points, points[1:]))):
        expr = f"if(lt({var},{_num(t1)}),{_num(v0)}+({_num(v1 - v0)})*({var}-{_num(t0)})/{_num(t1 - t0)},{expr})"
    return f"if(lt({var},{_num(points[0][0])}),{_num(points[0][1])},{expr})"


def _enable(a: float, b: float) -> str:
    return f"enable='gte(t,{a:.6f})*lt(t,{b:.6f})'"


# ---------------------------------------------------------------- Sinh filter

def build_filter_script(
    doc: SrtpxDocument,
    video: VideoInfo,
    chain: Sequence[FontData],
    options: ExportOptions,
    workdir: Path,
) -> FilterScript:
    """Sinh filter script cho toàn bộ phụ đề; chữ ghi vào các file chữ trong `workdir` (chưa ghi ra đĩa)."""
    scale = compute_scale(doc.res, video.size)
    out = FilterScript(text="")
    if (w := scale.warning()):
        out.warnings.append(w)

    parts: list[str] = []                      # chuỗi filter đơn đang gom (drawbox, drawtext cỡ cố định)
    segments: list[str] = []                   # các đoạn filter_complex đã chốt (chỉ dùng khi có chữ co giãn)
    cur = "vcfr"                               # đầu vào đã ép về lưới khung đều (xem cuối hàm)
    n_label = 0
    choices: dict[int, FontChoice] = {}
    overflow: list[int] = []
    too_short: list[int] = []
    num, den = video.fps_num, video.fps_den

    def flush_chain(final: bool = False) -> None:
        """Chốt các filter đơn đang gom thành một đoạn của filter_complex."""
        nonlocal cur, n_label
        if not parts and not final:
            return
        label = "vout" if final else f"v{n_label}"
        n_label += 1
        segments.append(f"[{cur}]" + (",\n".join(parts) if parts else "null") + f"[{label}]")
        parts.clear()
        cur = label

    def emit_layer(lay: AnimLayer, k0: int, k1: int, ox: int, oy: int) -> None:
        """Thêm chuỗi PNG của `lay` làm đầu vào ffmpeg tiếp theo và overlay nó lên video trong [k0, k1]."""
        nonlocal cur, n_label
        out.layers.append(lay)
        flush_chain()
        label_in, nxt = f"[{len(out.layers)}:v]", f"v{n_label}"
        n_label += 1
        lab = f"an{n_label}"
        segments.append(
            # settb ép time base = 1 khung để cộng k0 (số nguyên khung) vào PTS là chính xác tuyệt đối.
            f"{label_in}settb={den}/{num},setpts=PTS+{k0},format=rgba[{lab}]"
        )
        segments.append(
            f"[{cur}][{lab}]overlay=x={ox}:y={oy}:eof_action=pass:format=auto:"
            + _enable((k0 - 0.5) * den / num, (k1 + 0.5) * den / num) + f"[{nxt}]"
        )
        cur = nxt

    for sub in doc.subtitles:
        rng = visible_frame_range(sub, num, den)
        if rng is None:
            too_short.append(sub.index)
            continue
        k0, k1 = rng

        has_text = options.draw_text and bool(sub.text.strip())
        box_rgb = (options.box_colors or {}).get(sub.index)
        box_color = ("0x%02x%02x%02x" % box_rgb) if box_rgb else "black"
        boxes_vary = len({(kf.box.x1, kf.box.x2, kf.box.y1, kf.box.y2) for kf in sub.keyframes}) > 1

        # --- phụ đề CÓ ĐA GIÁC (chữ gốc nghiêng): hộp đen vẽ theo đa giác, chữ trắng xoay theo góc đa giác và co vừa đa giác (animlayer.py)
        if sub.polygon is not None and not sub.keyframes:
            poly = video_polygon(sub, scale, options.margin, video.size)
            vb = video_box(sub, sub.start, scale, options.margin, video.size)
            if has_text:
                choice, plan = plan_text(sub, chain, scale.sy)
                choices[sub.index] = choice
                # chữ xoay theo góc đa giác và co vừa CHÍNH đa giác (hộp bao thẳng rộng hơn nhiều khi nghiêng nên chữ sẽ tràn ra ngoài)
                fr = polygon_frame(poly)
                if not plan.fits_at(fr.w, fr.h):
                    overflow.append(sub.index)
                size = plan.size_for(fr.w, fr.h)
                lines, font_path, face = plan.lines, str(choice.font.spec.path), choice.font.spec.face_index
            else:
                size, lines, font_path, face = 1.0, (), "", 0
            px1, px2, py1, py2 = polygon_bounds(poly)
            ox, oy, cw, ch = canvas_for([(min(px1, vb.x1), max(px2, vb.x2), min(py1, vb.y1), max(py2, vb.y2))])
            lay = AnimLayer(
                directory=workdir / f"anim{sub.index:04d}", font_path=font_path, face_index=face,
                lines=lines, k0=k0, k1=k1, ox=ox, oy=oy, cw=cw, ch=ch,
                geometry=lambda k, vb=vb, size=size: (vb.x1, vb.x2, vb.y1, vb.y2, size),
                polygon=tuple(poly), color=box_rgb or (0, 0, 0),
            )
            emit_layer(lay, k0, k1, ox, oy)
            continue

        # --- phụ đề có keyframe VÀ đa giác theo keyframe (chữ nghiêng/xoay + chuyển động): hộp đen vẽ theo đa giác nội suy từng khung
        if sub.keyframes and has_keyframe_polygon(sub):
            if has_text:
                choice, plan = plan_text(sub, chain, scale.sy)
                choices[sub.index] = choice
                for kf in sub.keyframes:
                    kfr = polygon_frame(video_polygon(sub, scale, options.margin, video.size, kf.time))
                    if not plan.fits_at(kfr.w, kfr.h) and sub.index not in overflow:
                        overflow.append(sub.index)
                lines, font_path, face = plan.lines, str(choice.font.spec.path), choice.font.spec.face_index
            else:
                plan, lines, font_path, face = None, (), "", 0

            def poly_p(k: int, sub=sub):
                p = video_polygon(sub, scale, options.margin, video.size, frame_time(k, num, den), options.smooth)
                return tuple(p) if p is not None else None

            def geom_p(k: int, sub=sub, plan=plan):
                vb = video_box(sub, frame_time(k, num, den), scale, options.margin, video.size, options.smooth)
                pl = poly_p(k)
                fr = polygon_frame(pl) if pl is not None else None   # chữ co vừa đa giác (xoay theo nó), không phải hộp bao thẳng
                bw, bh = (fr.w, fr.h) if fr is not None else (vb.x2 - vb.x1, vb.y2 - vb.y1)
                return (vb.x1, vb.x2, vb.y1, vb.y2, plan.size_for(bw, bh) if plan else 1.0)

            boxes_p = []
            for k in range(k0, k1 + 1):
                g, pl = geom_p(k), poly_p(k)
                bx = polygon_bounds(pl) if pl else (g[0], g[1], g[2], g[3])
                boxes_p.append((min(bx[0], g[0]), max(bx[1], g[1]), min(bx[2], g[2]), max(bx[3], g[3])))
            ox, oy, cw, ch = canvas_for(boxes_p)
            lay = AnimLayer(
                directory=workdir / f"anim{sub.index:04d}", font_path=font_path, face_index=face,
                lines=lines, k0=k0, k1=k1, ox=ox, oy=oy, cw=cw, ch=ch, geometry=geom_p, polygon_fn=poly_p,
                color=box_rgb or (0, 0, 0),
            )
            emit_layer(lay, k0, k1, ox, oy)
            continue

        # --- phụ đề CÓ keyframe và có chữ: hộp + chữ vẽ chung từng khung ở toạ độ số thực (xem animlayer.py)
        if has_text and boxes_vary:
            choice, plan = plan_text(sub, chain, scale.sy)
            choices[sub.index] = choice
            for kf in sub.keyframes:
                vb = video_box(sub, kf.time, scale, options.margin, video.size)
                if not plan.fits_at(vb.x2 - vb.x1, vb.y2 - vb.y1) and sub.index not in overflow:
                    overflow.append(sub.index)

            def geom(k: int, sub=sub, plan=plan):
                vb = video_box(sub, frame_time(k, num, den), scale, options.margin, video.size, options.smooth)
                return (vb.x1, vb.x2, vb.y1, vb.y2, plan.size_for(vb.x2 - vb.x1, vb.y2 - vb.y1))

            all_boxes = [geom(k)[:4] for k in range(k0, k1 + 1)]
            ox, oy, cw, ch = canvas_for(all_boxes)
            lay = AnimLayer(
                directory=workdir / f"anim{sub.index:04d}",
                font_path=str(choice.font.spec.path), face_index=choice.font.spec.face_index,
                lines=plan.lines, k0=k0, k1=k1, ox=ox, oy=oy, cw=cw, ch=ch, geometry=geom,
                color=box_rgb or (0, 0, 0),
            )
            emit_layer(lay, k0, k1, ox, oy)
            continue

        # --- hộp: các đoạn hằng
        runs: list[list] = []
        frames = range(k0, k1 + 1) if sub.keyframes else (k0,)
        for k in frames:
            b = pixel_box(sub, frame_time(k, num, den), scale, options.margin, video.size)
            if runs and runs[-1][0] == b:
                runs[-1][2] = k
            else:
                runs.append([b, k, k])
        if not sub.keyframes:
            runs[0][2] = k1
        for b, a_, z in runs:
            if b.w <= 0 or b.h <= 0:
                continue  # hộp bị kẹp hết ra ngoài khung hình
            parts.append(
                f"drawbox=x={b.x1}:y={b.y1}:w={b.w}:h={b.h}:color={box_color}:t=fill:"
                + _enable((a_ - 0.5) * den / num, (z + 0.5) * den / num)
            )

        # --- chữ cố định (hộp không đổi): drawtext nguyên px là đủ chính xác
        if not has_text:
            continue
        choice, plan = plan_text(sub, chain, scale.sy)
        choices[sub.index] = choice
        vb = video_box(sub, sub.start, scale, options.margin, video.size)
        if not plan.fits_at(vb.x2 - vb.x1, vb.y2 - vb.y1):
            overflow.append(sub.index)
        size = _num(plan.size_for(vb.x2 - vb.x1, vb.y2 - vb.y1))
        cx, cy = _num((vb.x1 + vb.x2) / 2), _num((vb.y1 + vb.y2) / 2)
        window = _enable((k0 - 0.5) * den / num, (k1 + 0.5) * den / num)
        for j, line in enumerate(plan.lines):
            if not line:
                continue
            tf = workdir / f"sub{sub.index:04d}_{j}.txt"
            out.textfiles[tf] = line
            dy = _num((j - (plan.n - 1) / 2) * LINE_HEIGHT_FACTOR)
            parts.append(
                f"drawtext=fontfile={escape_path(choice.font.spec.path)}:textfile={escape_path(tf)}:"
                f"expansion=none:fontsize='{size}':fontcolor=white:"
                f"x='({cx})-text_w/2':y='({cy})+({dy})*({size})-text_h/2':{window}"
            )

    if too_short:
        out.warnings.append(
            "Các phụ đề ngắn hơn một khung hình nên không hiện trong video: "
            + ", ".join(map(str, too_short)) + "."
        )
    if overflow:
        out.warnings.append(
            "Hộp quá nhỏ, chữ tràn hộp ngay cả ở 1px ở phụ đề: "
            + ", ".join(map(str, overflow)) + ". Hãy rút gọn chữ hoặc nới hộp."
        )
    if (fw := describe_font_warnings(choices)):
        out.warnings.append(fw)
    if segments:
        flush_chain(final=True)
        # Lớp chữ co giãn (perspective) sinh khung theo lưới đều k/fps, còn video thật có thể có PTS không đều (quay điện
        # thoại/VFR), fps 29,97 hoặc start_time khác 0. Nếu không ép video về cùng lưới thì overlay ghép lệch khung
        # (chữ lúc đứng yên lúc nhảy 2-3px). fps=...:start_time=0 đưa khung thứ k về đúng giờ k/fps.
        segments.insert(0, f"[0:v]fps=fps={num}/{den}:start_time=0:round=near[vcfr]")
        out.text = ";\n".join(segments)
        out.complex = True
    else:
        out.text = ",\n".join(parts) if parts else "null"
    return out


def write_textfiles(script: FilterScript) -> None:
    """Ghi các file chữ UTF-8 không BOM."""
    for path, content in script.textfiles.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
    for lay in script.layers:
        lay.render()


def filter_script_args(ffmpeg_major: int | None, script_path: Path, complex_graph: bool = False,
                       audio_map: str | None = None) -> list[str]:
    """`-filter_script:v` (theo prompt); ffmpeg >= 7 đã đổi sang `-/filter:v`.

    Với filter_complex (chữ co giãn mượt): `-filter_complex_script` / `-/filter_complex`, lấy đầu ra [vout]
    và giữ âm thanh gốc bằng `-map 0:a?`.

    `audio_map` (vd "2:a" khi lồng tiếng): lấy âm thanh từ đầu vào khác thay cho âm thanh gốc.
    """
    new = ffmpeg_major is not None and ffmpeg_major >= 7
    if complex_graph:
        return ["-/filter_complex" if new else "-filter_complex_script", str(script_path),
                "-map", "[vout]", "-map", audio_map or "0:a?"]
    args = ["-/filter:v" if new else "-filter_script:v", str(script_path)]
    if audio_map:
        args += ["-map", "0:v:0", "-map", audio_map]
    return args


def build_command(
    ffmpeg: str,
    video_path: str | Path,
    script_path: Path,
    output: str | Path,
    options: ExportOptions,
    ffmpeg_major: int | None = None,
    complex_graph: bool = False,
    extra_inputs: Sequence[str] = (),
    audio_map: str | None = None,
) -> list[str]:
    """Lệnh ffmpeg xuất cả video: giữ âm thanh (-c:a copy), fps và độ phân giải gốc."""
    # Cắt đoạn bằng -ss/-to ĐẶT SAU -i (tùy chọn đầu ra): ffmpeg vẫn giải mã từ đầu nên biến t trong
    # filter giữ nguyên giờ của video gốc -> phụ đề đúng giờ gốc, không bị lệch về 0 như khi -ss đặt trước -i.
    seg: list[str] = []
    if options.start:
        seg += ["-ss", f"{options.start:.3f}"]
    if options.end is not None:
        seg += ["-to", f"{options.end:.3f}"]
    return [
        ffmpeg, "-y", "-i", str(video_path), *extra_inputs,
        *filter_script_args(ffmpeg_major, script_path, complex_graph, audio_map),
        *seg,
        "-c:v", options.codec, "-crf", str(options.crf), "-preset", options.preset,
        "-threads", str(options.threads), "-pix_fmt", "yuv420p",
        "-c:a", "copy", str(output),
    ]
