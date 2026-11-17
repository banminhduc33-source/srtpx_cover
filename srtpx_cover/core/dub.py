"""Lồng tiếng theo mốc phụ đề (không Qt).

Quy tắc:
- Giọng đặt đúng mốc BẮT ĐẦU của phụ đề.
- Khoảng cho phép của một câu = từ mốc bắt đầu tới (mốc bắt đầu câu kế - GAP), tối đa mượn thêm MAX_BORROW giây
  sau mốc kết thúc phụ đề (tận dụng chỗ trống giữa hai câu), nên đầu/đuôi hai câu không dính vào nhau.
- Giọng DÀI hơn khoảng cho phép thì tua nhanh (atempo) cho vừa; NGẮN hơn thì giữ nguyên tốc độ, phần còn lại để im.
- Câu trước vẫn tràn (đã chạm trần tốc độ) thì câu sau được lùi mốc bắt đầu tối đa MAX_PUSH giây thay vì đè lên.
- Phụ đề chú thích ("lơ lửng": có dấu * hoặc cả câu nằm trong ngoặc) mặc định KHÔNG lồng tiếng.
- Các block trùng giờ nhau thì chỉ MỘT block chính được lồng (block bắt đầu sớm nhất), còn lại bỏ qua.

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

from .dubfilter import CACHE_NAME as AI_FILTER_CACHE, DEFAULT_MODEL, Classifier, FilterResult, filter_subtitles
from .ffmpeg_tools import FfmpegError, FfmpegCancelled, _creationflags
from .model import SrtpxDocument, Subtitle

RATE = 24000  # TikTok trả mp3 24 kHz mono
DEFAULT_MAX_SPEED = 3.0
DEFAULT_GAP = 0.0          # giây chừa thêm giữa hết lời câu trước và đầu câu sau (TTS đã tự có khoảng lặng cuối câu)
DEFAULT_TAIL_SILENCE = 0.3  # giây lặng sẵn ở cuối mỗi câu TTS: không tính vào thời lượng lời nói khi tính tốc độ
DEFAULT_FIRST_START = 0.3  # phụ đề đầu tiên bắt đầu ở 0s thì dời sang mốc này
DEFAULT_MAX_BORROW = 1.0   # giây tối đa được mượn thêm sau mốc kết thúc phụ đề (nếu chỗ trống còn dư)
DEFAULT_MAX_PUSH = 0.5     # giây tối đa được lùi mốc bắt đầu để không đè lên giọng câu trước
FADE_SECONDS = 0.008       # làm mềm đầu/đuôi mỗi câu để không bị "tạch" khi cắt


@dataclass(frozen=True)
class DubOptions:
    include_comments: bool = False        # True: lồng cả chú thích và cả block trùng giờ
    max_speed: float = DEFAULT_MAX_SPEED  # trần tốc độ tua nhanh; vượt thì giọng tràn qua mốc kết thúc
    orig_volume: float = 0.3              # âm lượng âm thanh gốc khi trộn (1.0 = giữ nguyên)
    voice_volume: float = 1.0
    tail_silence: float = DEFAULT_TAIL_SILENCE  # khoảng lặng cuối câu của TTS (0 = tính cả khoảng lặng như bản cũ)
    first_start: float = DEFAULT_FIRST_START    # câu đầu tiên bắt đầu ở 0s thì dời sang mốc này (0 = không dời)
    gap: float = DEFAULT_GAP              # chừa im lặng giữa hai câu liền nhau (0 = như bản cũ)
    max_borrow: float = DEFAULT_MAX_BORROW  # được dùng thêm chỗ trống sau mốc kết thúc (0 = chỉ trong [start, end])
    max_push: float = DEFAULT_MAX_PUSH    # được lùi mốc bắt đầu nếu câu trước còn tràn (0 = cho đè như bản cũ)
    ai_model: str = DEFAULT_MODEL         # model Gemini lọc dòng không cần lồng (chỉ để làm khóa cache; có classifier mới lọc)


@dataclass
class DubReport:
    placed: int = 0
    sped_up: int = 0
    max_factor: float = 1.0
    over_cap: list[int] = field(default_factory=list)   # phụ đề cần tua nhanh hơn trần cho phép
    pushed: list[int] = field(default_factory=list)     # phụ đề bị lùi mốc bắt đầu để khỏi đè lên câu trước
    still_overlap: list[int] = field(default_factory=list)  # phụ đề vẫn đè lên câu trước dù đã lùi hết mức
    skipped_comments: list[int] = field(default_factory=list)
    skipped_overlap: list[int] = field(default_factory=list)
    skipped_floating: list[int] = field(default_factory=list)   # chữ bay lơ lửng (không nằm ở phần dưới khung hình)
    skipped_ai: list[int] = field(default_factory=list)         # Gemini cho là không cần lồng (tiếng động, chữ trên màn hình...)
    ai_reasons: dict[int, str] = field(default_factory=dict)    # {chỉ số phụ đề: lý do Gemini bỏ}
    skipped_other: list[str] = field(default_factory=list)

    def summary(self) -> str:
        s = f"Đã lồng {self.placed} câu"
        if self.sped_up:
            s += f", {self.sped_up} câu tua nhanh (tối đa x{self.max_factor:.2f})"
        if self.pushed:
            s += f", {len(self.pushed)} câu lùi mốc nhẹ để khỏi đè"
        if self.still_overlap:
            s += f", {len(self.still_overlap)} câu vẫn đè câu trước"
        if self.skipped_comments:
            s += f", bỏ qua {len(self.skipped_comments)} chú thích"
        if self.skipped_overlap:
            s += f", bỏ qua {len(self.skipped_overlap)} block trùng giờ"
        if self.skipped_floating:
            s += f", bỏ qua {len(self.skipped_floating)} chữ bay lơ lửng (không nằm ở dưới)"
        if self.skipped_ai:
            s += f", AI lọc bỏ {len(self.skipped_ai)} dòng không cần lồng"
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

    `resolve_overlaps=False`: chưa xử lý trùng giờ, trả về mọi ứng viên (đã sắp theo giờ) để cho AI lọc trước, rồi gọi
    `drop_overlaps` trên kết quả. Làm vậy thì nhãn/chữ trên màn hình bị AI loại sẽ không nuốt mất câu thoại trùng giờ với nó."""
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


def apply_ai_filter(
    subs: Sequence[Subtitle],
    doc: SrtpxDocument,
    options: DubOptions,
    classifier: Classifier | None,
    cache_dir: Path,
    on_progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> FilterResult:
    """Bước lọc thứ hai (sau luật cứng): Gemini bỏ các dòng không phải lời nói. Không có classifier hoặc
    `include_comments=True` (lồng tất cả) thì giữ nguyên danh sách."""
    if classifier is None or options.include_comments or not subs:
        return FilterResult(kept=list(subs))
    return filter_subtitles(subs, doc.subtitles, classifier, cache_dir / AI_FILTER_CACHE, options.ai_model,
                            on_progress, cancel)


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


def slot_end_for(sub: Subtitle, nxt: Subtitle | None, total_seconds: float, options: DubOptions) -> float:
    """Mốc giây muộn nhất mà giọng của `sub` được phép kéo dài tới: chừa `gap` trước câu kế, và chỉ mượn tối đa
    `max_borrow` giây sau mốc kết thúc phụ đề. Câu cuối thì tới hết video."""
    limit = (nxt.start - options.gap) if nxt is not None else total_seconds
    return min(sub.end + max(0.0, options.max_borrow), limit, total_seconds)


def fade_edges(samples: array, fade: int) -> array:
    """Giảm dần biên độ ở đầu và đuôi `fade` mẫu (tránh tiếng "tạch" khi hai câu sát nhau)."""
    n = len(samples)
    f = min(fade, n // 2)
    for i in range(f):
        k = i / f
        samples[i] = int(samples[i] * k)
        samples[n - 1 - i] = int(samples[n - 1 - i] * k)
    return samples


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
    speech_end = 0.0   # giây: hết LỜI của câu trước (không tính khoảng lặng cuối câu, cho phép đuôi lặng chồng lên câu sau)
    todo = sorted((s for s in subs if s.index in clips), key=lambda s: (s.start, s.index))
    for i, sub in enumerate(todo):
        if cancel is not None and cancel.is_set():
            raise FfmpegCancelled("Đã hủy.")
        if sub.start >= total_seconds:
            report.skipped_other.append(f"Phụ đề {sub.index}: bắt đầu sau khi video kết thúc")
            continue
        # Mốc bắt đầu thực: câu đầu ở 0s thì dời sang first_start; câu trước còn tràn thì lùi nhẹ (tối đa max_push).
        start_s = sub.start
        if i == 0 and sub.start <= 0 and options.first_start > 0:
            start_s = min(options.first_start, total_seconds)
        busy_until = speech_end + options.gap
        if options.max_push > 0 and busy_until > start_s:
            pushed_to = min(busy_until, sub.start + options.max_push)
            if pushed_to > start_s + 1e-6:
                start_s = pushed_to
                report.pushed.append(sub.index)
        nxt = todo[i + 1] if i + 1 < len(todo) else None
        own = max(0.0, sub.end - sub.start)
        # Khoảng cho phép; không để quá nhỏ (câu bị lùi/kẹt sát câu sau) để khỏi tua tới mức vô lý.
        slot = max(slot_end_for(sub, nxt, total_seconds, options) - start_s, 0.4 * own, 0.05)
        pcm = decode_pcm(ffmpeg, clips[sub.index])
        full = len(pcm) / 2 / RATE
        tail = min(max(0.0, options.tail_silence), full * 0.5)
        dur = full - tail                  # thời lượng lời nói thật, khoảng lặng cuối câu không cần tua cho vừa
        sp = speed_for(dur, slot, options.max_speed)
        if dur > slot > 0 and dur / slot > options.max_speed + 1e-9:
            report.over_cap.append(sub.index)
        if sp > 1.0001:
            pcm = decode_pcm(ffmpeg, clips[sub.index], sp)
            report.sped_up += 1
            report.max_factor = max(report.max_factor, sp)
        samples = fade_edges(_as_samples(pcm), int(FADE_SECONDS * RATE))
        pos = int(round(start_s * RATE))
        if pos < int(round(speech_end * RATE)) - 1:
            report.still_overlap.append(sub.index)
        watermark = place_clip(buf, samples, pos, watermark)
        speech_end = (pos + len(samples)) / RATE - tail / max(sp, 1.0)
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
    classifier: Classifier | None = None,
) -> tuple[Path | None, DubReport, list[str]]:
    """Tạo giọng từng câu (cache), dựng track theo mốc, trộn với âm thanh gốc.

    `classifier` (Gemini, xem dubfilter.py) nếu có sẽ lọc thêm các dòng không phải lời nói trước khi tạo giọng.
    Trả về (file .m4a để thêm vào video — None nếu không có câu nào để lồng, báo cáo, cảnh báo).
    """
    from .ffmpeg_tools import run_ffmpeg
    from .tts import ensure_clips

    def stage(lo: float, hi: float) -> Callable[[float], None]:
        return lambda p: on_progress(lo + (hi - lo) * p) if on_progress else None

    # Thứ tự: luật cứng (chú thích, vị trí) -> AI lọc -> trùng giờ. Để trùng giờ chạy SAU AI thì chữ/nhãn bị AI loại
    # không còn nuốt mất câu thoại bắt đầu trong khoảng thời gian của nó.
    subs, comments, _, floating = split_dub_subtitles(doc, options.include_comments, resolve_overlaps=False)
    ai = apply_ai_filter(subs, doc, options, classifier, cache_dir, stage(0.0, 0.1), cancel)
    subs, overlaps = drop_overlaps(ai.kept, options.include_comments)
    skipped = dict(skipped_comments=[s.index for s in comments], skipped_overlap=[s.index for s in overlaps],
                   skipped_floating=[s.index for s in floating], skipped_ai=[s.index for s in ai.dropped],
                   ai_reasons=dict(ai.reasons))
    if not subs:
        rep = DubReport(**skipped)
        return None, rep, [*ai.warnings, "Không có phụ đề nào để lồng tiếng."]
    clips, errors = ensure_clips(subs, voice, cache_dir, synth, stage(0.1, 0.55), cancel)
    wav = workdir / "dub.wav"
    rep = build_dub_track(ffmpeg, subs, clips, total_seconds, options, wav, stage(0.55, 0.8), cancel)
    rep.skipped_comments, rep.skipped_overlap = skipped["skipped_comments"], skipped["skipped_overlap"]
    rep.skipped_floating = skipped["skipped_floating"]
    rep.skipped_ai, rep.ai_reasons = skipped["skipped_ai"], skipped["ai_reasons"]
    warnings = [*ai.warnings, *errors]
    if rep.over_cap:
        warnings.append("Cần tua nhanh hơn trần x%.1f nên giọng tràn qua mốc kết thúc ở phụ đề: %s."
                        % (options.max_speed, ", ".join(map(str, rep.over_cap))))
    if rep.still_overlap:
        warnings.append("Giọng vẫn đè lên câu trước ở phụ đề: %s (hãy nâng trần tua nhanh hoặc rút gọn lời dịch)."
                        % ", ".join(map(str, rep.still_overlap)))
    if rep.placed == 0:
        return None, rep, warnings
    mixed = workdir / "dub_mix.m4a"
    cmd = build_mix_command(ffmpeg, video, wav, mixed, options, has_audio_stream(video, ffprobe))
    run_ffmpeg(cmd, total_seconds, stage(0.8, 1.0), cancel)
    return mixed, rep, warnings
