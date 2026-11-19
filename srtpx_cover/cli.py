"""Xuất video bằng dòng lệnh (mốc 2). Không dùng PySide6.

Ví dụ:
    python -m srtpx_cover.cli video.mp4 phude.srtpx ra.mp4
    python -m srtpx_cover.cli video.mp4 phude.srtpx ra.mp4 --font fonts/Bevan-Regular.ttf --font C:/Windows/Fonts/arial.ttf
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

from .core.export import ExportOptions, build_command, build_filter_script, write_textfiles
from .core.ffmpeg_tools import FfmpegError, ffmpeg_major_version, find_tool, probe_video, run_ffmpeg
from .core.vertical import (VerticalOptions, build_vertical_command, build_vertical_filter, find_logo, logo_size,
                            plan_vertical)
from .core.dub import DEFAULT_MAX_SPEED, DubOptions, prepare_dub_audio
from .core.tts import DEFAULT_VOICE, TtsError, tiktok_synthesizer
from .core.fonts import FontError, FontSpec, default_font_chain, load_chain, parse_font_path
from .core.geometry import DEFAULT_MARGIN_PX
from .core.maincap import DEFAULT_WORDS, MainCaptionOptions, apply_main_captions
from .core.parser import SrtpxError, read_srtpx

APP_DIR = Path(__file__).resolve().parent.parent


def _parse_font_arg(s: str) -> FontSpec:
    """'đường/dẫn.ttc' hoặc 'đường/dẫn.ttc#1' (số sau # là chỉ số face trong file TTC)."""
    path, sep, idx = s.rpartition("#")
    if sep and idx.isdigit():
        return FontSpec(path, int(idx))
    return FontSpec(s, 0)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Che phụ đề cứng bằng hộp đen chữ trắng theo file .srtpx.")
    p.add_argument("video", help="Video gốc")
    p.add_argument("srtpx", help="File .srtpx")
    p.add_argument("output", help="Video đầu ra")
    p.add_argument("--margin", type=float, default=DEFAULT_MARGIN_PX, help="Lề an toàn (px, tối đa 1), mặc định 1")
    p.add_argument("--font", action="append", default=[], metavar="FILE[#n]",
                   help="Font trong chuỗi ưu tiên (lặp lại nhiều lần). Mặc định: Bevan -> Arial -> YaHei")
    p.add_argument("--fonts-dir", default=str(APP_DIR / "fonts"), help="Thư mục fonts/ chứa Bevan-Regular.ttf")
    p.add_argument("--crf", type=int, default=18)
    p.add_argument("--preset", default="veryfast")
    p.add_argument("--threads", type=int, default=2)
    p.add_argument("--codec", default="libx264", choices=["libx264", "libx265"])
    p.add_argument("--start", type=float, default=None, help="Xuất từ giây này (giờ video gốc)")
    p.add_argument("--end", type=float, default=None, help="Xuất tới giây này")
    p.add_argument("--no-text", action="store_true", help="Chỉ vẽ hộp đen, không vẽ chữ")
    p.add_argument("--dub", action="store_true", help="Lồng tiếng TikTok TTS theo mốc phụ đề (cần sessionid)")
    p.add_argument("--sessionid", default=None, help="sessionid TikTok (mặc định lấy từ biến môi trường TIKTOK_SESSIONID)")
    p.add_argument("--voice", default=DEFAULT_VOICE, help=f"Mã giọng, mặc định {DEFAULT_VOICE}")
    p.add_argument("--dub-comments", action="store_true", help="Lồng cả chú thích (có *, trong ngoặc), block trùng giờ và phụ đề phụ; mặc định chỉ lồng phụ đề chính")
    p.add_argument("--orig-volume", type=float, default=0.3, help="Âm lượng âm thanh gốc khi lồng tiếng (0..1), mặc định 0.3")
    p.add_argument("--voice-volume", type=float, default=1.0, help="Âm lượng giọng đọc, mặc định 1.0")
    p.add_argument("--max-speed", type=float, default=DEFAULT_MAX_SPEED, help=f"Trần tua nhanh khi giọng dài hơn mốc, mặc định {DEFAULT_MAX_SPEED}")
    p.add_argument("--tts-cache", default=str(APP_DIR / ".tts_cache"), help="Thư mục cache giọng từng câu")
    p.add_argument("--no-main-captions", action="store_true",
                   help="Tắt chuẩn hóa phụ đề chính (mặc định: nhóm phụ đề cùng vị trí đông nhất dùng chung một hộp cố định, "
                        "chữ cỡ cố định, chia cụm vài chữ; phụ đề phụ giữ nguyên)")
    p.add_argument("--main-words", type=int, default=DEFAULT_WORDS,
                   help=f"Tối đa số chữ mỗi cụm phụ đề chính, mặc định {DEFAULT_WORDS}")
    p.add_argument("--no-wrap-secondary", action="store_true",
                   help="Phụ đề phụ có hộp cao hơn rộng KHÔNG tự xuống dòng (mặc định: tự xuống dòng để chữ to hơn)")
    p.add_argument("--no-vertical", action="store_true",
                   help="Không dựng khung 9:16 (mặc định: sau khi xuất, dựng thêm nền mờ + logo thành video dọc 1080x1920)")
    p.add_argument("--dump-filter", metavar="FILE", help="Ghi filter script ra file này để kiểm tra rồi thoát")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except (FfmpegError, FontError, SrtpxError, TtsError) as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 1


def vertical_command(ffmpeg: str, src: str, output: str, workdir: Path, opts: ExportOptions, info) -> list[str]:
    """Lệnh dựng khung 9:16 từ video đã xuất `src` (ghi filter script vào workdir)."""
    logo = find_logo()
    vopts = VerticalOptions(crf=opts.crf, preset=opts.preset, threads=opts.threads, codec=opts.codec)
    geom = plan_vertical(info.size, logo_size(logo) if logo else None, vopts)
    if logo is None:
        print("[cảnh báo] Không thấy assets/logo_genshinviet.png nên không gắn logo.", file=sys.stderr)
    script = workdir / "vertical.txt"
    script.write_bytes(build_vertical_filter(geom, vopts).encode("utf-8"))
    return build_vertical_command(ffmpeg, src, logo, script, output, geom, vopts, ffmpeg_major_version(ffmpeg))


def _run(args: argparse.Namespace) -> int:
    ffmpeg, ffprobe = find_tool("ffmpeg"), find_tool("ffprobe")
    video = probe_video(args.video, ffprobe)
    parsed = read_srtpx(args.srtpx)
    for w in parsed.warnings:
        print(f"[srtpx] {w}", file=sys.stderr)

    if args.font:
        specs, font_warnings = [parse_font_path(f) for f in args.font], []
    else:
        specs, font_warnings = default_font_chain(Path(args.fonts_dir))
    for w in font_warnings:
        print(f"[font] {w}", file=sys.stderr)
    chain = load_chain(specs)

    opts = ExportOptions(margin=args.margin, draw_text=not args.no_text,
                         crf=args.crf, preset=args.preset, threads=args.threads,
                         codec=args.codec, start=args.start, end=args.end)
    workdir = Path(tempfile.mkdtemp(prefix="srtpx_"))
    try:
        # Lồng tiếng chạy TRƯỚC trên phụ đề gốc (đọc nguyên câu): cần biết giọng đọc dài bao lâu thì mới chia được mốc
        # chuyển cụm cho phụ đề chính. --dump-filter không tạo giọng nên bỏ qua bước này.
        mixed, spoken = None, None
        if args.dub and not args.dump_filter:
            sid = args.sessionid or os.environ.get("TIKTOK_SESSIONID", "")
            dub_opts = DubOptions(include_comments=args.dub_comments, max_speed=args.max_speed,
                                  orig_volume=args.orig_volume, voice_volume=args.voice_volume)
            print("Đang tạo giọng đọc…")
            mixed, rep, dub_warnings = prepare_dub_audio(
                parsed.document, args.video, video.duration, args.voice, tiktok_synthesizer(sid),
                Path(args.tts_cache), workdir, dub_opts, ffmpeg, ffprobe)
            spoken = rep.spoken
            for w in dub_warnings:
                print(f"[lồng tiếng] {w}", file=sys.stderr)
            texts = {s.index: " ".join(s.text.split()) for s in parsed.document.subtitles}
            spans = {s.index: f"{s.start:.1f}s-{s.end:.1f}s" for s in parsed.document.subtitles}
            for label, ids in (("chú thích", rep.skipped_comments), ("trùng giờ", rep.skipped_overlap),
                               ("chữ bay lơ lửng", rep.skipped_floating), ("phụ đề phụ", rep.skipped_not_main)):
                for i in ids:   # luật cứng bỏ dòng nào thì in ra để kiểm tra
                    print(f"[bỏ qua: {label}] #{i} ({spans.get(i, '?')}) \"{texts.get(i, '')}\"")
            print(rep.summary())

        doc = parsed.document
        if not args.no_main_captions and opts.draw_text:
            mc_opts = MainCaptionOptions(words_per_chunk=args.main_words, wrap_secondary=not args.no_wrap_secondary)
            doc, mc = apply_main_captions(doc, mc_opts, spoken, chain)
            print(f"[phụ đề chính] {mc.message()}")

        script = build_filter_script(doc, video, chain, opts, workdir)
        for w in script.warnings:
            print(f"[cảnh báo] {w}", file=sys.stderr)
        write_textfiles(script)
        script_path = workdir / "filter.txt"
        script_path.write_bytes(script.text.encode("utf-8"))
        if args.dump_filter:
            Path(args.dump_filter).write_bytes(script.text.encode("utf-8"))
            print(f"Đã ghi filter script: {args.dump_filter}")
            return 0
        extra = script.extra_inputs(video.fps_num, video.fps_den)
        audio_map = None
        if mixed is not None:
            audio_map = f"{1 + len(script.layers)}:a"
            extra = [*extra, "-i", str(mixed)]
        vertical = not args.no_vertical
        stage1 = str(workdir / "che.mp4") if vertical else args.output
        cmd = build_command(ffmpeg, args.video, script_path, stage1, opts, ffmpeg_major_version(ffmpeg), script.complex, extra,
                            audio_map)
        last = [-1]

        def show(p: float) -> None:
            pct = int(p * 100)
            if pct != last[0]:
                last[0] = pct
                print(f"\rĐang xuất: {pct:3d}%", end="", flush=True)

        run_ffmpeg(cmd, opts.segment_seconds(video.duration), show)
        if vertical:
            print("\nĐang dựng khung 9:16…")
            last[0] = -1
            run_ffmpeg(vertical_command(ffmpeg, stage1, args.output, workdir, opts, probe_video(stage1, ffprobe)),
                       opts.segment_seconds(video.duration), show)
        print(f"\nXong: {args.output}")
        return 0
    except FfmpegError:
        Path(args.output).unlink(missing_ok=True)  # xoá file dở
        raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
