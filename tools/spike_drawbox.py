"""Spike mốc 2 (mục 8): drawbox có nhận biểu thức theo thời gian không? So sánh phương án.

Chạy: python tools/spike_drawbox.py
Dùng phụ đề 1 của file mẫu (có KF: 0.6s -> 9.4s) trên video thử 768x576, 30 fps.
(a) chia thành các đoạn: mỗi đoạn một drawbox toạ độ hằng + enable riêng;
(b) nguồn color=black co giãn từng khung (scale eval=frame) + overlay x,y theo thời gian.
Đo: chạy được không, độ lệch so với hàm hình học (pixel_box), thời gian, độ dài filter script.
"""
from __future__ import annotations

import math
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from srtpx_cover.core.geometry import compute_scale, pixel_box  # noqa: E402
from srtpx_cover.core.parser import read_srtpx  # noqa: E402

FPS, W, H, MARGIN = 30, 768, 576, 3
SAMPLE = Path(__file__).resolve().parent.parent / "tests" / "data" / "sample.srtpx"


def ff(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
                          capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)


def luma_frames(video: Path, frames: list[int]) -> np.ndarray:
    sel = "+".join(f"eq(n\\,{k})" for k in frames)
    p = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(video), "-vf",
         f"select='{sel}',extractplanes=y", "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
    return np.frombuffer(p.stdout, np.uint8).reshape(len(frames), H, W)


def lin(points: list[tuple[float, float]]) -> str:
    """Biểu thức nội suy tuyến tính theo biến t (giây) qua các điểm (t, giá trị)."""
    expr = f"{points[-1][1]:.4f}"
    for (t0, v0), (t1, v1) in reversed(list(zip(points, points[1:]))):
        expr = f"if(lt(t,{t1:.4f}),{v0:.4f}+({v1-v0:.4f})*(t-{t0:.4f})/{t1-t0:.4f},{expr})"
    return f"if(lt(t,{points[0][0]:.4f}),{points[0][1]:.4f},{expr})"


def main() -> None:
    doc = read_srtpx(SAMPLE).document
    sub = doc.subtitles[0]
    scale = compute_scale(doc.res, (W, H))
    tmp = Path(tempfile.mkdtemp())
    src = tmp / "src.mp4"
    ff("-f", "lavfi", "-i", f"testsrc=size={W}x{H}:rate={FPS}:duration=10", "-c:v", "libx264",
       "-crf", "0", "-pix_fmt", "yuv420p", str(src))
    frames = [9, 10, 50, 100, 150, 200, 250, 284]
    ref = luma_frames(src, frames)

    print("== 0. drawbox nhận biểu thức theo thời gian? ==")
    for name, expr in (("n", "n*2"), ("t", "if(gte(t,5),100,0)")):
        r = ff("-i", str(src), "-t", "1", "-vf", f"drawbox=x='{expr}':y=10:w=50:h=50:color=black:t=fill",
               "-f", "null", "-")
        print(f"  x='{expr}': " + ("chạy được (nhưng t là ĐỘ DÀY, không phải giờ)" if r.returncode == 0
                                    else "LỖI: " + r.stderr.strip().splitlines()[-1][:90]))

    def score(video: Path) -> int:
        out = luma_frames(video, frames)
        worst = 0
        for i, k in enumerate(frames):
            b = pixel_box(sub, k / FPS, scale, MARGIN, (W, H))
            # đo lệch: số điểm ảnh sai ngoài/ trong hộp kỳ vọng (trong thu 1px, ngoài nở 1px)
            inside = out[i, b.y1 + 1:b.y2 - 1, b.x1 + 1:b.x2 - 1]
            mask = np.ones((H, W), bool)
            mask[max(b.y1 - 1, 0):b.y2 + 1, max(b.x1 - 1, 0):b.x2 + 1] = False
            bad = int((inside > 17).sum()) + int((out[i][mask] != ref[i][mask]).sum())
            worst = max(worst, bad)
        return worst

    # (a) các đoạn hằng: gộp các khung liên tiếp có cùng hộp nguyên.
    k0, k1 = math.ceil(sub.start * FPS), math.ceil(sub.end * FPS) - 1
    runs: list[list] = []
    for k in range(k0, k1 + 1):
        b = pixel_box(sub, k / FPS, scale, MARGIN, (W, H))
        if runs and runs[-1][0] == b:
            runs[-1][2] = k
        else:
            runs.append([b, k, k])
    parts = [f"drawbox=x={b.x1}:y={b.y1}:w={b.w}:h={b.h}:color=black:t=fill:"
             f"enable='gte(t,{(a - .5) / FPS:.5f})*lt(t,{(z + .5) / FPS:.5f})'" for b, a, z in runs]
    script_a = ",\n".join(parts)
    (tmp / "a.txt").write_text(script_a)
    t0 = time.time()
    ff("-i", str(src), "-filter_script:v", str(tmp / "a.txt"), "-c:v", "libx264", "-crf", "0",
       "-preset", "ultrafast", str(tmp / "a.mp4"))
    ta = time.time() - t0
    print(f"== (a) đoạn hằng: {len(runs)} đoạn, script {len(script_a)} ký tự, {ta:.2f}s, "
          f"điểm sai tệ nhất = {score(tmp / 'a.mp4')}")

    # (b) color + scale eval=frame + overlay.
    kt = [(k.time, k.box) for k in sub.keyframes]
    def edge(get, rnd, sgn, s):
        pts = [(t, get(b) * s + sgn * MARGIN) for t, b in kt]
        return f"{rnd}({lin(pts)})"
    x1 = edge(lambda b: b.x1, "floor", -1, scale.sx); x2 = edge(lambda b: b.x2, "ceil", 1, scale.sx)
    y1 = edge(lambda b: b.y1, "floor", -1, scale.sy); y2 = edge(lambda b: b.y2, "ceil", 1, scale.sy)
    tt = "(n/30)"
    sub_t = lambda e: e.replace("(t,", f"({tt},").replace("(t-", f"({tt}-")  # noqa: E731
    fc = (f"color=c=black:s=16x16:r={FPS}[c];[c]scale=w='{sub_t(x2)}-{sub_t(x1)}':h='{sub_t(y2)}-{sub_t(y1)}':"
          f"eval=frame[b];[0:v][b]overlay=x='{sub_t(x1)}':y='{sub_t(y1)}':eval=frame:shortest=1:"
          f"enable='gte(t,{(k0 - .5) / FPS:.5f})*lt(t,{(k1 + .5) / FPS:.5f})'")
    (tmp / "b.txt").write_text(fc)
    t0 = time.time()
    r = ff("-i", str(src), "-filter_complex_script", str(tmp / "b.txt"), "-c:v", "libx264", "-crf", "0",
           "-preset", "ultrafast", str(tmp / "b.mp4"))
    tb = time.time() - t0
    if r.returncode:
        print("== (b) color+overlay: LỖI", r.stderr.strip().splitlines()[-1][:120])
    else:
        print(f"== (b) color+scale+overlay: script {len(fc)} ký tự, {tb:.2f}s, "
              f"điểm sai tệ nhất = {score(tmp / 'b.mp4')}")


if __name__ == "__main__":
    main()
