"""Lồng tiếng theo mốc phụ đề (không Qt).

Quy tắc:
- Giọng đặt đúng mốc BẮT ĐẦU của phụ đề.
- Giọng DÀI hơn khoảng [start, end] của phụ đề thì trước hết mượn khoảng im lặng phía sau (đến lúc câu kế tiếp bắt đầu),
  vẫn không đủ thì mới tua nhanh (atempo) cho vừa khít, tối đa max_speed.
- Giọng NGẮN hơn thì giữ nguyên tốc độ, phần còn lại để im.
- Phụ đề chú thích ("lơ lửng": có dấu * hoặc cả câu nằm trong ngoặc) mặc định KHÔNG lồng tiếng.
- Chỉ lồng các phụ đề thuộc nhóm PHỤ ĐỀ CHÍNH (cùng vị trí đông nhất, xem maincap.py); phụ đề phụ bỏ qua.
- Các block trùng giờ nhau thì chỉ MỘT block được lồng (block bắt đầu sớm nhất), còn lại bỏ qua.

Track giọng dựng bằng Python (mảng int16 mono 24 kHz) rồi ffmpeg trộn với âm thanh gốc;
nhờ vậy dòng lệnh ffmpeg không phụ thuộc số câu (tránh giới hạn độ dài lệnh của Windows).
"""
from __future__ import annotations

import subprocess
import sys
import threading
import wave
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from .ffmpeg_tools import FfmpegError, FfmpegCancelled, _creationflags
from .model import SrtpxDocument, Subtitle

RATE = 24000  # TikTok trả mp3 24 kHz mono
DEFAULT_MAX_SPEED = 1.4   # tua quá x1.5 thì giọng TTS tiếng Việt méo, khó nghe
NO_MAIN_WARNING = "Không xác định được phụ đề chính (quá ít phụ đề cùng vị trí) nên lồng tiếng mọi dòng còn lại."


@dataclass(frozen=True)
class DubOptions:
    include_comments: bool = False        # True: lồng cả chú thích và cả block trùng giờ
    max_speed: float = DEFAULT_MAX_SPEED  # trần tốc độ tua nhanh; vượt thì giọng tràn qua mốc kết thúc
    orig_volume: float = 0.3              # âm lượng âm thanh gốc khi trộn (1.0 = giữ nguyên)
    voice_volume: float = 1.0


@dataclass
class DubReport:
    placed: int = 0
    sped_up: int = 0
    max_factor: float = 1.0
    over_cap: list[int] = field(default_factory=list)   # phụ đề cần tua nhanh hơn trần cho phép
    skipped_comments: list[int] = field(default_factory=list)
    skipped_overlap: list[int] = field(default_factory=list)
    skipped_floating: list[int] = field(default_factory=list)   # chữ bay lơ lửng (không nằm ở phần dưới khung hình)
    skipped_not_main: list[int] = field(default_factory=list)   # phụ đề phụ: không thuộc nhóm phụ đề chính
    spoken: dict[int, float] = field(default_factory=dict)      # {chỉ số phụ đề: số giây giọng đọc thực sự chiếm (sau tua nhanh)}
    skipped_other: list[str] = field(default_factory=list)

    def summary(self) -> str:
        s = f"Đã lồng {self.placed} câu"
        if self.sped_up:
            s += f", {self.sped_up} câu tua nhanh (tối đa x{self.max_factor:.2f})"
        if self.skipped_comments:
            s += f", bỏ qua {len(self.skipped_comments)} chú thích"
        if self.skipped_overlap:
            s += f", bỏ qua {len(self.skipped_overlap)} block trùng giờ"
        if self.skipped_floating:
            s += f", bỏ qua {len(self.skipped_floating)} chữ bay lơ lửng (không nằm ở dưới)"
        if self.skipped_not_main:
            s += f", bỏ qua {len(self.skipped_not_main)} phụ đề phụ (không thuộc phụ đề chính)"
        return s + "."


# ---------------------------------------------------------------- chọn phụ đề

# Các cặp ngoặc coi là "chú thích" khi bao trọn cả câu.
BRACKET_PAIRS = {
    "(": ")", "[": "]", "{": "}", "<": ">",
    "（": "）", "［": "］", "｛": "｝", "＜": "＞", "｟": "｠", "⦅": "⦆",
    "【": "】", "〖": "〗", "〔": "〕", "〘": "〙", "〚": "〛", "〈": "〉", "《": "》", "⟨": "⟩", "⟪": "⟫",
    "⟦": "⟧", "「": "」", "『": "』", "｢": "｣", "⌈": "⌉", "⌊": "⌋", "⁅": "⁆",
    "❨": "❩", "❪": "❫", "❬": "❭", "❮": "❯", "❰": "❱", "❲": "❳", "❴": "❵", "﹙": "﹚", "﹛": "﹜", "﹝": "﹞",
}
# Ngoặc kép/nháy là lời thoại chứ không phải chú thích: không tính khi bao trọn cả câu,
# nhưng vẫn được bóc ra khi tìm dấu đánh dấu ở đầu/cuối (vd "*abc*").
QUOTE_PAIRS = {'"': '"', "'": "'", "“": "”", "‘": "’", "«": "»", "‹": "›", "„": "“", "‚": "‘"}
# Dấu đánh dấu chú thích ở đầu/cuối nội dung.
MARKERS = "*＊#＃※★☆†‡✱✲⁎"
_STRIP = "".join(BRACKET_PAIRS) + "".join(BRACKET_PAIRS.values()) + "".join(QUOTE_PAIRS) + "".join(QUOTE_PAIRS.values()) + " \t\r\n"
BOTTOM_MIN_CENTER_RATIO = 0.6   # tâm hộp phải ở từ 60% chiều cao khung hình trở xuống mới là phụ đề "ở dưới"
OVERLAP_TOL = 0.05  # giây: chồng nhỏ hơn mức này coi như chỉ chạm nhau (sai số làm tròn mốc)


def is_comment(text: str) -> bool:
    """Chú thích/bình luận: có dấu đánh dấu (* # ※ ★ ...) ở đầu hoặc cuối nội dung (kể cả bên trong ngoặc),
    hoặc cả câu nằm trong một cặp ngoặc bất kỳ như "(cười)", "[nhạc]", "【chú thích】"."""
    t = text.strip()
    if not t:
        return False
    core = t.strip(_STRIP)
    if core and (core[0] in MARKERS or core[-1] in MARKERS):
        return True
    return len(t) >= 2 and BRACKET_PAIRS.get(t[0]) == t[-1]


def is_bottom(sub: Subtitle, doc: SrtpxDocument) -> bool:
    """Phụ đề nằm ở phần dưới khung hình (tâm hộp >= BOTTOM_MIN_CENTER_RATIO chiều cao #res).
    Có keyframe thì lấy trung bình tâm các keyframe. File không có dòng #res thì không phân biệt được: coi là ở dưới."""
    if not doc.res:
        return True
    boxes = [k.box for k in sub.keyframes] or ([sub.box] if sub.box is not None else [])
    if not boxes:
        return True
    cy = sum((b.y1 + b.y2) / 2 for b in boxes) / len(boxes)
    return cy >= BOTTOM_MIN_CENTER_RATIO * doc.res[1]


def select_dub_subtitles(doc: SrtpxDocument, include_comments: bool = False) -> list[Subtitle]:
    """Các phụ đề sẽ được lồng tiếng: có chữ, nằm ở dưới, không phải chú thích, và là block chính trong nhóm trùng giờ."""
    return split_dub_subtitles(doc, include_comments)[0]


def split_dub_subtitles(doc: SrtpxDocument, include_comments: bool = False, resolve_overlaps: bool = True):
    """(sẽ lồng, chú thích bị bỏ, block trùng giờ bị bỏ, chữ bay lơ lửng bị bỏ).

    Chỉ lồng phụ đề nằm ở dưới; chữ ở chỗ khác (giữa/trên khung hình) bỏ qua. Mỗi nhóm trùng giờ giữ block
    bắt đầu sớm nhất. `include_comments=True` tắt hết các luật bỏ qua này.

    `resolve_overlaps=False`: chưa xử lý trùng giờ, trả về mọi ứng viên (đã sắp theo giờ) để lọc phụ đề chính trước
    (`keep_main_subtitles`), rồi gọi `drop_overlaps` trên kết quả. Làm vậy thì phụ đề phụ bị loại sẽ không nuốt mất
    câu thoại chính trùng giờ với nó."""
    comments: list[Subtitle] = []
    floating: list[Subtitle] = []
    cands = []
    for sub in doc.subtitles:
        if not sub.text.strip():
            continue
        if is_comment(sub.text) and not include_comments:
            comments.append(sub)
        elif not include_comments and not is_bottom(sub, doc):
            floating.append(sub)
        else:
            cands.append(sub)
    if not resolve_overlaps:
        return sorted(cands, key=lambda s: (s.start, -len(s.text), s.index)), comments, [], floating
    kept, overlaps = drop_overlaps(cands, include_comments)
    return kept, comments, overlaps, floating


def drop_overlaps(subs: Sequence[Subtitle], include_comments: bool = False) -> tuple[list[Subtitle], list[Subtitle]]:
    """(giữ lại, block trùng giờ bị bỏ): mỗi nhóm trùng giờ chỉ giữ block bắt đầu sớm nhất.
    `include_comments=True` thì không bỏ block nào."""
    kept: list[Subtitle] = []
    overlaps: list[Subtitle] = []
    last_end = float("-inf")
    for sub in sorted(subs, key=lambda s: (s.start, -len(s.text), s.index)):
        if sub.start < last_end - OVERLAP_TOL and not include_comments:
            overlaps.append(sub)
        else:
            kept.append(sub)
            last_end = max(last_end, sub.end)
    return kept, overlaps


def keep_main_subtitles(
    subs: Sequence[Subtitle], doc: SrtpxDocument, include_comments: bool = False,
) -> tuple[list[Subtitle], list[Subtitle], bool]:
    """Bước lọc thứ hai (sau luật cứng): chỉ giữ phụ đề thuộc nhóm phụ đề chính, phụ đề phụ bỏ qua.

    Trả về (giữ lại để lồng, phụ đề phụ bị bỏ, tìm được nhóm chính hay không). Nhóm chính tính trên CẢ tài liệu, đúng như
    khi xuất video. Không tìm được nhóm chính (quá ít phụ đề cùng vị trí) thì giữ nguyên danh sách để khỏi mất hết thoại.
    `include_comments=True` (lồng tất cả) cũng giữ nguyên."""
    if include_comments or not subs:
        return list(subs), [], True
    from .maincap import main_group_indices   # nhập trễ: maincap cũng nhập dub (drop_overlaps)
    main = main_group_indices(doc)
    if main is None:
        return list(subs), [], False
    return [s for s in subs if s.index in main], [s for s in subs if s.index not in main], True


# ---------------------------------------------------------------- tốc độ

def atempo_filter(speed: float) -> str:
    """Chuỗi atempo cho hệ số `speed` (mỗi atempo gói trong 0.5..2 cho an toàn trên mọi bản ffmpeg)."""
    if speed <= 0:
        raise ValueError("speed phải > 0")
    parts: list[float] = []
    while speed > 2.0:
        parts.append(2.0)
        speed /= 2.0
    parts.append(speed)
    return ",".join(f"atempo={p:.5f}" for p in parts)


def speed_for(clip_seconds: float, slot_seconds: float, max_speed: float) -> float:
    """Hệ số tua: 1.0 nếu giọng vừa/ngắn hơn khoảng; ngược lại clip/slot, chặn bởi max_speed."""
    if slot_seconds <= 0 or clip_seconds <= slot_seconds:
        return 1.0
    return min(clip_seconds / slot_seconds, max(1.0, max_speed))


GAP_BEFORE_NEXT = 0.1  # giây: chừa lại trước câu kế tiếp khi giọng mượn khoảng im lặng phía sau


def slot_for(sub: Subtitle, next_start: float | None, total_seconds: float) -> float:
    """Thời lượng giọng được phép chiếm: từ lúc bắt đầu phụ đề đến lúc câu kế tiếp bắt đầu (trừ GAP_BEFORE_NEXT),
    nhưng không bao giờ ngắn hơn khoảng [start, end] của chính phụ đề."""
    own = sub.end - sub.start
    limit = total_seconds if next_start is None else min(next_start, total_seconds)
    return max(own, limit - sub.start - GAP_BEFORE_NEXT)


def decode_pcm(ffmpeg: str, path: Path, speed: float = 1.0) -> bytes:
    """Giải mã mp3 thành PCM s16le mono 24 kHz, có tua nhanh nếu speed > 1."""
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path), "-vn"]
    if speed > 1.0001:
        cmd += ["-af", atempo_filter(speed)]
    cmd += ["-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, creationflags=_creationflags())
    except OSError as exc:
        raise FfmpegError(f"Không chạy được ffmpeg: {exc}") from exc
    if p.returncode != 0 or not p.stdout:
        raise FfmpegError(f"Không giải mã được giọng {path.name}: {p.stderr.decode('utf-8', 'replace').strip()[-300:]}")
    return p.stdout


# ---------------------------------------------------------------- dựng track

def _as_samples(pcm: bytes) -> array:
    a = array("h")
    a.frombytes(pcm[: len(pcm) // 2 * 2])
    if sys.byteorder == "big":
        a.byteswap()
    return a


def place_clip(buf: array, samples: array, start: int, watermark: int) -> int:
    """Đặt `samples` vào `buf` tại mẫu `start`; trả về watermark mới (mẫu cuối cùng đã bị chiếm).

    Phần không chồng thì gán nguyên khối (nhanh); phần chồng lên giọng trước thì cộng và chặn biên độ.
    """
    n = min(len(samples), len(buf) - start)
    if n <= 0:
        return watermark
    if n < len(samples):
        samples = samples[:n]
    ov = max(0, min(n, watermark - start))
    for i in range(ov):
        v = buf[start + i] + samples[i]
        buf[start + i] = 32767 if v > 32767 else -32768 if v < -32768 else v
    if ov < n:
        buf[start + ov:start + n] = samples[ov:]
    return max(watermark, start + n)


def build_dub_track(
    ffmpeg: str,
    subs: Sequence[Subtitle],
    clips: dict[int, Path],
    total_seconds: float,
    options: DubOptions,
    out_wav: Path,
    on_progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> DubReport:
    """Ghi track giọng (WAV mono 24 kHz, dài đúng `total_seconds`) vào `out_wav` và trả về báo cáo."""
    report = DubReport()
    buf = array("h", bytes(int(total_seconds * RATE) * 2))
    watermark = 0
    todo = sorted((s for s in subs if s.index in clips), key=lambda s: (s.start, s.index))
    for i, sub in enumerate(todo):
        if cancel is not None and cancel.is_set():
            raise FfmpegCancelled("Đã hủy.")
        if sub.start >= total_seconds:
            report.skipped_other.append(f"Phụ đề {sub.index}: bắt đầu sau khi video kết thúc")
            continue
        nxt = todo[i + 1].start if i + 1 < len(todo) else None
        slot = slot_for(sub, nxt, total_seconds)
        pcm = decode_pcm(ffmpeg, clips[sub.index])
        dur = len(pcm) / 2 / RATE
        sp = speed_for(dur, slot, options.max_speed)
        if dur > slot > 0 and dur / slot > options.max_speed + 1e-9:
            report.over_cap.append(sub.index)
        if sp > 1.0001:
            pcm = decode_pcm(ffmpeg, clips[sub.index], sp)
            report.sped_up += 1
            report.max_factor = max(report.max_factor, sp)
        watermark = place_clip(buf, _as_samples(pcm), int(round(sub.start * RATE)), watermark)
        report.spoken[sub.index] = len(pcm) / 2 / RATE
        report.placed += 1
        if on_progress:
            on_progress((i + 1) / len(todo))
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(buf.tobytes() if sys.byteorder == "little" else _swapped(buf))
    return report


def _swapped(buf: array) -> bytes:
    b = array("h", buf)
    b.byteswap()
    return b.tobytes()


# ---------------------------------------------------------------- trộn với âm thanh gốc

def has_audio_stream(video: str | Path, ffprobe: str) -> bool:
    try:
        p = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=index", "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, creationflags=_creationflags(),
        )
    except OSError:
        return False
    return p.returncode == 0 and bool(p.stdout.strip())


def build_mix_command(
    ffmpeg: str, video: str | Path, dub_wav: str | Path, out_audio: str | Path,
    options: DubOptions, video_has_audio: bool,
) -> list[str]:
    """Lệnh trộn âm thanh gốc (đã hạ âm lượng) với track giọng thành một file AAC (.m4a)."""
    v = f"volume={options.voice_volume:.3f}"
    if video_has_audio:
        graph = (f"[0:a]volume={options.orig_volume:.3f}[o];[1:a]{v}[d];"
                 f"[o][d]amix=inputs=2:duration=longest:normalize=0[a]")
        maps = ["-filter_complex", graph, "-map", "[a]"]
    else:
        maps = ["-filter_complex", f"[1:a]{v}[a]", "-map", "[a]"]
    return [ffmpeg, "-y", "-i", str(video), "-i", str(dub_wav), *maps, "-vn",
            "-c:a", "aac", "-b:a", "192k", str(out_audio)]


# ---------------------------------------------------------------- một bước từ đầu tới cuối

def prepare_dub_audio(
    doc: SrtpxDocument,
    video: str | Path,
    total_seconds: float,
    voice: str,
    synth,
    cache_dir: Path,
    workdir: Path,
    options: DubOptions,
    ffmpeg: str,
    ffprobe: str,
    on_progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> tuple[Path | None, DubReport, list[str]]:
    """Tạo giọng từng câu (cache), dựng track theo mốc, trộn với âm thanh gốc.

    Chỉ lồng tiếng các phụ đề thuộc nhóm phụ đề chính (xem `keep_main_subtitles`).
    Trả về (file .m4a để thêm vào video — None nếu không có câu nào để lồng, báo cáo, cảnh báo).
    """
    from .ffmpeg_tools import run_ffmpeg
    from .tts import ensure_clips

    def stage(lo: float, hi: float) -> Callable[[float], None]:
        return lambda p: on_progress(lo + (hi - lo) * p) if on_progress else None

    # Thứ tự: luật cứng (chú thích, vị trí) -> chỉ giữ phụ đề chính -> trùng giờ. Để trùng giờ chạy SAU bước lọc chính/phụ
    # thì phụ đề phụ không còn nuốt mất câu thoại chính bắt đầu trong khoảng thời gian của nó.
    subs, comments, _, floating = split_dub_subtitles(doc, options.include_comments, resolve_overlaps=False)
    subs, not_main, found_main = keep_main_subtitles(subs, doc, options.include_comments)
    subs, overlaps = drop_overlaps(subs, options.include_comments)
    skipped = dict(skipped_comments=[s.index for s in comments], skipped_overlap=[s.index for s in overlaps],
                   skipped_floating=[s.index for s in floating], skipped_not_main=[s.index for s in not_main])
    warnings: list[str] = [] if found_main else [NO_MAIN_WARNING]
    if not subs:
        rep = DubReport(**skipped)
        return None, rep, [*warnings, "Không có phụ đề nào để lồng tiếng."]
    clips, errors = ensure_clips(subs, voice, cache_dir, synth, stage(0.0, 0.55), cancel)
    wav = workdir / "dub.wav"
    rep = build_dub_track(ffmpeg, subs, clips, total_seconds, options, wav, stage(0.55, 0.8), cancel)
    rep.skipped_comments, rep.skipped_overlap = skipped["skipped_comments"], skipped["skipped_overlap"]
    rep.skipped_floating = skipped["skipped_floating"]
    rep.skipped_not_main = skipped["skipped_not_main"]
    warnings += errors
    if rep.over_cap:
        warnings.append("Cần tua nhanh hơn trần x%.1f nên giọng tràn qua mốc kết thúc ở phụ đề: %s."
                        % (options.max_speed, ", ".join(map(str, rep.over_cap))))
    if rep.placed == 0:
        return None, rep, warnings
    mixed = workdir / "dub_mix.m4a"
    cmd = build_mix_command(ffmpeg, video, wav, mixed, options, has_audio_stream(video, ffprobe))
    run_ffmpeg(cmd, total_seconds, stage(0.8, 1.0), cancel)
    return mixed, rep, warnings
