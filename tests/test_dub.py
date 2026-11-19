"""Test lồng tiếng: chọn phụ đề, tốc độ, đặt giọng đúng mốc, xuất video có tiếng. TTS giả, ffmpeg thật."""
from __future__ import annotations

import shutil
import subprocess
import wave
from array import array
from pathlib import Path

import pytest

from srtpx_cover.core.dub import (
    RATE, DubOptions, atempo_filter, drop_overlaps, is_comment, keep_main_subtitles, split_dub_subtitles, build_dub_track, place_clip, prepare_dub_audio, select_dub_subtitles, slot_for, speed_for,
)
from srtpx_cover.core.parser import read_srtpx
from srtpx_cover.core.tts import TtsError, clip_path, ensure_clips, split_text

DATA = Path(__file__).parent / "data" / "testvn.srtpx"


def _sample_doc():
    """File mẫu có chữ ở nửa trên khung hình; bỏ #res để không áp luật "chỉ lồng phụ đề ở dưới" (test này kiểm tra thời gian)."""
    doc = read_srtpx(DATA).document
    doc.res = None
    return doc
FFMPEG, FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")
need_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="cần ffmpeg/ffprobe")


def fake_mp3(seconds: float) -> bytes:
    p = subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                        "-ar", "24000", "-ac", "1", "-f", "mp3", "-"], capture_output=True, check=True)
    return p.stdout


# ------------------------------------------------------------ không cần ffmpeg
def test_split_text_limits_and_content():
    long = "một hai ba bốn năm " * 30
    parts = split_text(long)
    assert all(len(p) <= 200 for p in parts)
    assert " ".join(parts).split() == long.split()
    assert split_text("Xin chào. Bạn khỏe không?") == ["Xin chào. Bạn khỏe không?"]
    assert split_text("   ") == []


def test_comment_detection():
    for c in ["*Ghi chú*", "(*bình luận)", "[*chú thích]", "【*Ghi chú】", "*abc", "abc*", "(cười)", "[nhạc]", "（注）", "#ghi chú", "(#note)", "＃注", "《注》", "〖注〗", "｢注｣", "❨x❩", "※ lưu ý", "[★hay]", "{xyz}", "<abc>"]:
        assert is_comment(c), c
    for t in ["Xin chào", "Valeriy!?", '"Chào bạn"', "Giá 5*3 là 15", "A ha ha...", "«Chào»", "“Chào”", "Số 5 # 6 giữa câu"]:
        assert not is_comment(t), t


def test_overlap_keeps_only_main_block():
    from srtpx_cover.core.model import Box, SrtpxDocument, Subtitle
    box = Box(0, 10, 0, 10)
    mk = lambda i, a, b, t: Subtitle(i, a, b, t, box=box)
    doc = SrtpxDocument(subtitles=[
        mk(1, 0.0, 3.0, "Câu chính"), mk(2, 1.0, 2.0, "Câu phụ chồng giờ"), mk(3, 2.0, 2.5, "*Bình luận*"),
        mk(4, 3.0, 4.0, "Câu kế tiếp (chỉ chạm mép)"), mk(5, 5.0, 6.0, "(cười)")])
    kept, comments, overlaps, floating = split_dub_subtitles(doc)
    assert [s.index for s in kept] == [1, 4]
    assert [s.index for s in comments] == [3, 5] and [s.index for s in overlaps] == [2]
    assert [s.index for s in select_dub_subtitles(doc, include_comments=True)] == [1, 2, 3, 4, 5]


def test_overlap_runs_after_main_filter_so_dropped_label_does_not_swallow_dialogue():
    """Nhãn "Người phỏng vấn" hiện 0-7.7s bắt đầu sớm nhất: nếu xử lý trùng giờ trước khi loại phụ đề phụ thì nó nuốt hết thoại 0.2-7.5s."""
    from srtpx_cover.core.model import Box, SrtpxDocument, Subtitle
    box = Box(0, 10, 0, 10)
    mk = lambda i, a, b, t: Subtitle(i, a, b, t, box=box)
    doc = SrtpxDocument(subtitles=[
        mk(1, 0.0, 7.7, "Người phỏng vấn"), mk(2, 0.2, 1.3, "Cậu bị ép từ chức à?"),
        mk(3, 1.3, 3.6, "Đúng vậy"), mk(4, 7.8, 9.0, "Lời thoại sau")])
    # Cách cũ: nhãn là block chính, thoại 2-3 bị nuốt trước khi kịp loại nhãn.
    assert [s.index for s in split_dub_subtitles(doc)[0]] == [1, 4]
    # Cách mới: lấy hết ứng viên -> loại nhãn (#1, phụ đề phụ) -> mới xử lý trùng giờ.
    cands, _, no_overlaps, _ = split_dub_subtitles(doc, resolve_overlaps=False)
    assert [s.index for s in cands] == [1, 2, 3, 4] and no_overlaps == []
    after_main = [s for s in cands if s.index != 1]
    kept, overlaps = drop_overlaps(after_main)
    assert [s.index for s in kept] == [2, 3, 4] and overlaps == []
    # Vẫn bỏ block trùng giờ thật sự; include_comments=True thì không bỏ gì.
    both = [mk(5, 0.0, 3.0, "Câu chính"), mk(6, 1.0, 2.0, "Câu phụ chồng giờ")]
    kept, overlaps = drop_overlaps(both)
    assert [s.index for s in kept] == [5] and [s.index for s in overlaps] == [6]
    assert drop_overlaps(both, include_comments=True)[1] == []


def _main_doc():
    """3 câu cùng vị trí ở dưới (phụ đề chính) + 1 nhãn nằm lệch chỗ khác (phụ đề phụ)."""
    from srtpx_cover.core.model import Box, SrtpxDocument, Subtitle
    main_box, other = Box(100, 400, 500, 550), Box(500, 700, 420, 460)
    return SrtpxDocument(res=(768, 576), subtitles=[
        Subtitle(1, 0.0, 1.0, "Câu một", box=main_box), Subtitle(2, 2.0, 3.0, "Nhãn chữ", box=other),
        Subtitle(3, 4.0, 5.0, "Câu hai", box=main_box), Subtitle(4, 6.0, 7.0, "Câu ba", box=main_box)])


def test_only_main_captions_are_dubbed_others_skipped():
    doc = _main_doc()
    cands = split_dub_subtitles(doc, resolve_overlaps=False)[0]
    kept, dropped, found = keep_main_subtitles(cands, doc)
    assert found and [s.index for s in kept] == [1, 3, 4] and [s.index for s in dropped] == [2]
    # include_comments=True (lồng tất cả): không lọc
    kept, dropped, found = keep_main_subtitles(cands, doc, include_comments=True)
    assert [s.index for s in kept] == [1, 2, 3, 4] and dropped == []


def test_no_main_group_keeps_everything():
    from srtpx_cover.core.model import Box, SrtpxDocument, Subtitle
    doc = SrtpxDocument(res=(768, 576), subtitles=[Subtitle(1, 0.0, 1.0, "Chỉ một câu", box=Box(100, 400, 500, 550))])
    kept, dropped, found = keep_main_subtitles(doc.subtitles, doc)
    assert not found and [s.index for s in kept] == [1] and dropped == []


def test_only_bottom_subtitles_are_dubbed_floating_text_is_skipped():
    from srtpx_cover.core.model import Box, SrtpxDocument, Subtitle
    bottom, top, mid = Box(100, 400, 500, 550), Box(100, 400, 20, 60), Box(100, 400, 250, 300)
    doc = SrtpxDocument(res=(768, 576), subtitles=[
        Subtitle(1, 0.0, 1.0, "Dưới", box=bottom), Subtitle(2, 2.0, 3.0, "Trên cùng", box=top),
        Subtitle(3, 4.0, 5.0, "Giữa khung", box=mid), Subtitle(4, 6.0, 7.0, "Dưới nữa", box=bottom)])
    kept, comments, overlaps, floating = split_dub_subtitles(doc)
    assert [s.index for s in kept] == [1, 4] and [s.index for s in floating] == [2, 3]
    assert [s.index for s in select_dub_subtitles(doc, include_comments=True)] == [1, 2, 3, 4]   # công tắc tắt luật
    doc.res = None                                        # không có #res: không phân biệt được => lồng hết
    assert [s.index for s in select_dub_subtitles(doc)] == [1, 2, 3, 4]


def test_user_sample_all_main_blocks_dubbed():
    doc = _sample_doc()
    assert [s.index for s in select_dub_subtitles(doc)] == [1, 2, 3, 4, 5]


def test_speed_rules():
    assert speed_for(0.5, 1.0, 3.0) == 1.0           # ngắn hơn: giữ nguyên
    assert speed_for(1.0, 1.0, 3.0) == 1.0
    assert speed_for(2.0, 1.0, 3.0) == pytest.approx(2.0)
    assert speed_for(9.0, 1.0, 3.0) == 3.0           # chặn bởi trần
    assert atempo_filter(1.5) == "atempo=1.50000"
    assert atempo_filter(3.0) == "atempo=2.00000,atempo=1.50000"


def test_slot_borrows_gap_before_next_subtitle():
    by = {s.index: s for s in select_dub_subtitles(_sample_doc())}   # 3: 10.167-11.167 | 4: 11.167-11.833 | 5: 15.3-16.233
    s3, s4, s5 = by[3], by[4], by[5]
    assert slot_for(s3, s4.start, 20.0) == pytest.approx(s3.end - s3.start)           # liền kề: không có chỗ mượn
    assert slot_for(s4, s5.start, 20.0) == pytest.approx(s5.start - s4.start - 0.1)   # có khoảng im: mượn, chừa 0.1s
    assert slot_for(s4, s5.start, 14.0) == pytest.approx(14.0 - s4.start - 0.1)       # không mượn quá hết video
    assert slot_for(s4, None, 14.0) == pytest.approx(14.0 - s4.start - 0.1)           # câu cuối: mượn tới hết video
    assert slot_for(s4, s4.start + 0.2, 20.0) == pytest.approx(s4.end - s4.start)     # không bao giờ ngắn hơn khung gốc


def test_place_clip_overlap_adds_and_clamps():
    buf = array("h", [0] * 20)
    wm = place_clip(buf, array("h", [30000] * 6), 2, 0)
    assert wm == 8 and list(buf[2:8]) == [30000] * 6
    wm = place_clip(buf, array("h", [30000] * 6), 5, wm)  # chồng 3 mẫu -> cộng, chặn 32767
    assert wm == 11
    assert list(buf[5:8]) == [32767] * 3 and list(buf[8:11]) == [30000] * 3
    assert place_clip(buf, array("h", [1] * 50), 18, wm) == 20  # tràn cuối buffer bị cắt


def test_ensure_clips_cache_and_abort(tmp_path):
    doc = _sample_doc()
    subs = select_dub_subtitles(doc)
    calls = []

    def synth(text, voice):
        calls.append(text)
        return b"xx"

    clips, errs = ensure_clips(subs, "v", tmp_path, synth)
    assert len(clips) == 5 and not errs and len(calls) == 5
    ensure_clips(subs, "v", tmp_path, synth)
    assert len(calls) == 5                            # lần hai lấy từ cache
    assert clip_path(tmp_path, "v", subs[0].text) in clips.values()

    def bad(text, voice):
        raise TtsError("sessionid sai")

    with pytest.raises(TtsError, match="liên tiếp"):
        ensure_clips(subs, "other", tmp_path, bad)


# ------------------------------------------------------------ cần ffmpeg
@need_ffmpeg
def test_track_positions_and_speedup(tmp_path):
    """Giọng ngắn: đúng mốc bắt đầu, phần sau im. Giọng dài: tua nhanh cho vừa khung."""
    doc = _sample_doc()
    subs = select_dub_subtitles(doc)              # 2: 9.467-10.167 (0.7s) | 3: 10.167-11.167 (1s) | 4: 11.833-...
    by = {s.index: s for s in subs}
    lengths = {2: 2.1, 3: 0.4, 4: 0.5}            # 2 dài gấp 3 so với khung (0.7s); 3 ngắn; 4 ngắn
    clips = {}
    for i, sec in lengths.items():
        f = tmp_path / f"c{i}.mp3"
        f.write_bytes(fake_mp3(sec))
        clips[i] = f
    wav = tmp_path / "dub.wav"
    rep = build_dub_track(FFMPEG, subs, clips, 14.0, DubOptions(max_speed=4.0), wav)
    assert rep.placed == 3 and rep.sped_up == 1 and not rep.over_cap
    assert rep.max_factor == pytest.approx(3.0, rel=0.1)
    # thời lượng giọng đọc thực tế (sau tua nhanh): câu 2 dài 2.1s tua x3 -> ~0.7s; câu ngắn giữ nguyên
    # (mp3 giả được đệm thêm ~0.05s khi giải mã nên dung sai 0.1s)
    assert rep.spoken[2] == pytest.approx(0.7, abs=0.1) and rep.spoken[3] == pytest.approx(0.4, abs=0.1)
    assert rep.spoken[4] == pytest.approx(0.5, abs=0.1)

    with wave.open(str(wav)) as w:
        assert w.getframerate() == RATE and w.getnframes() == 14 * RATE
        pcm = array("h"); pcm.frombytes(w.readframes(w.getnframes()))

    def active(a, b):
        seg = pcm[int(a * RATE):int(b * RATE)]
        return sum(1 for v in seg if abs(v) > 500)

    assert active(0, by[2].start - 0.01) == 0                       # trước câu 2 im
    assert active(by[2].start + 0.05, by[2].end - 0.05) > 0.6 * (by[2].end - by[2].start - 0.1) * RATE
    assert active(by[2].end + 0.05, by[3].start - 0.01) < 0.02 * RATE   # câu 2 đã tua nhanh vừa khung, không tràn
    assert active(by[3].start + 0.05, by[3].start + 0.35) > 0.8 * 0.3 * RATE    # câu 3 bắt đầu đúng mốc
    assert active(by[3].start + 0.5, by[3].end) == 0                # câu 3 ngắn: phần còn lại im, không kéo dãn
    assert active(by[4].start + 0.05, by[4].start + 0.45) > 0.8 * 0.4 * RATE
    assert active(by[4].start + 0.7, 14.0) == 0


@need_ffmpeg
def test_over_cap_reported(tmp_path):
    doc = _sample_doc()
    subs = [s for s in select_dub_subtitles(doc) if s.index in (2, 3)]  # câu 2: khung 0.7s, liền ngay câu 3 (không mượn được)
    clips = {}
    for i, sec in {2: 3.5, 3: 0.4}.items():                          # câu 2 cần x5
        clips[i] = tmp_path / f"c{i}.mp3"; clips[i].write_bytes(fake_mp3(sec))
    rep = build_dub_track(FFMPEG, subs, clips, 14.0, DubOptions(max_speed=2.0), tmp_path / "d.wav")
    assert rep.over_cap == [2] and rep.max_factor == pytest.approx(2.0, rel=0.1)


@need_ffmpeg
def test_voice_borrows_silence_instead_of_speeding_up(tmp_path):
    doc = _sample_doc()
    subs = [s for s in select_dub_subtitles(doc) if s.index == 4]   # câu 4: khung 0.67s, phía sau im tới hết video (14s)
    f = tmp_path / "c4.mp3"; f.write_bytes(fake_mp3(2.0))            # dài gấp 3 khung gốc, nhưng vừa trong khoảng được mượn
    wav = tmp_path / "d.wav"
    rep = build_dub_track(FFMPEG, subs, {4: f}, 14.0, DubOptions(max_speed=1.4), wav)
    assert rep.placed == 1 and rep.sped_up == 0 and not rep.over_cap
    assert rep.spoken[4] == pytest.approx(2.0, abs=0.1)              # mượn khoảng im: giọng chiếm đủ 2s, dài hơn khung gốc
    with wave.open(str(wav)) as w:
        pcm = array("h"); pcm.frombytes(w.readframes(w.getnframes()))
    seg = pcm[int((subs[0].end + 0.3) * RATE):int((subs[0].end + 0.8) * RATE)]   # sau mốc kết thúc phụ đề: giọng vẫn đang đọc
    assert sum(1 for v in seg if abs(v) > 500) > 0.8 * len(seg)


@need_ffmpeg
def test_end_to_end_cli_export_has_dubbed_audio(tmp_path, monkeypatch):
    """Xuất qua CLI với TTS giả: video ra có luồng âm thanh, dài bằng video, và có tiếng đúng chỗ."""
    from srtpx_cover import cli

    video = tmp_path / "in.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=768x576:r=25:d=14",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "14", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)], check=True)
    monkeypatch.setattr(cli, "tiktok_synthesizer", lambda sid: (lambda text, voice: fake_mp3(0.3 + 0.05 * len(text) / 4)))
    out = tmp_path / "out.mp4"
    sample = tmp_path / "sample.srtpx"            # bỏ dòng #res: không áp luật "chỉ lồng phụ đề ở dưới"
    sample.write_bytes(b"\r\n".join(l for l in DATA.read_bytes().splitlines() if not l.startswith(b"#res")))
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    args = [str(video), str(sample), str(out), "--dub", "--sessionid", "x", "--tts-cache", str(tmp_path / "cache"),
            "--orig-volume", "0", "--no-text", "--no-vertical"]
    if Path(font).exists():
        args += ["--font", font]
    assert cli.main(args) == 0
    info = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "csv=p=0", str(out)],
                          capture_output=True, text=True, check=True).stdout
    assert "audio" in info and "video" in info
    # file mẫu chỉ có #2, #3 cùng vị trí (phụ đề chính): có tiếng quanh 10s, còn phụ đề phụ #1 (0.3s) và #4 (11.2s) thì im
    pcm = subprocess.run([FFMPEG, "-v", "error", "-i", str(out), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    a = array("h"); a.frombytes(pcm)
    def active(x, y): return sum(1 for v in a[int(x * 8000):int(y * 8000)] if abs(v) > 300)
    assert active(0.3, 0.8) == 0 and active(12, 14) == 0   # #1 (0.27s) và #5 là phụ đề phụ nên không đọc
    assert active(9.5, 10.9) > 0


def _make_silent_video(path):
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=768x576:r=25:d=14",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "14", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)


def _audio_pcm(path):
    pcm = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    a = array("h"); a.frombytes(pcm)
    return a


def _probe(path, entries):
    return subprocess.run([FFPROBE, "-v", "error", "-show_entries", entries, "-of", "csv=p=0", str(path)],
                          capture_output=True, text=True, check=True).stdout


@need_ffmpeg
def test_cli_both_writes_tts_and_notts_videos(tmp_path, monkeypatch):
    """--both: ra hai video cùng hình, `_notts` giữ âm thanh gốc (im), `_tts` có giọng đọc đúng chỗ; không có file `ra.mp4`."""
    from srtpx_cover import cli

    video = tmp_path / "in.mp4"
    _make_silent_video(video)
    monkeypatch.setattr(cli, "tiktok_synthesizer", lambda sid: (lambda text, voice: fake_mp3(0.3 + 0.05 * len(text) / 4)))
    out = tmp_path / "ra.mp4"
    sample = tmp_path / "sample.srtpx"
    sample.write_bytes(b"\r\n".join(l for l in DATA.read_bytes().splitlines() if not l.startswith(b"#res")))
    args = [str(video), str(sample), str(out), "--dub", "--both", "--sessionid", "x", "--tts-cache", str(tmp_path / "cache"),
            "--orig-volume", "0", "--no-text", "--no-vertical"]
    assert cli.main(args) == 0
    tts, notts = tmp_path / "ra_tts.mp4", tmp_path / "ra_notts.mp4"
    assert tts.exists() and notts.exists() and not out.exists()
    for f in (tts, notts):
        info = _probe(f, "stream=codec_type")
        assert "video" in info and "audio" in info
    # cùng hình: hình chỉ dựng một lần, bản TTS copy hình nên cùng độ dài
    dv = lambda f: float(_probe(f, "format=duration").strip())
    assert dv(tts) == pytest.approx(dv(notts), abs=0.2)
    act = lambda a, x, y: sum(1 for v in a[int(x * 8000):int(y * 8000)] if abs(v) > 300)
    a_tts, a_no = _audio_pcm(tts), _audio_pcm(notts)
    assert act(a_tts, 9.5, 10.9) > 0          # bản TTS: có giọng đọc quanh 10s (như test lồng tiếng)
    assert act(a_no, 0, 14) == 0              # bản không TTS: vẫn là âm thanh gốc (im lặng), không lẫn giọng


@need_ffmpeg
def test_cli_both_without_dub_falls_back_to_single_output(tmp_path, capsys):
    from srtpx_cover import cli

    video = tmp_path / "in.mp4"
    _make_silent_video(video)
    out = tmp_path / "ra.mp4"
    sample = tmp_path / "sample.srtpx"
    sample.write_bytes(b"\r\n".join(l for l in DATA.read_bytes().splitlines() if not l.startswith(b"#res")))
    assert cli.main([str(video), str(sample), str(out), "--both", "--no-text", "--no-vertical"]) == 0
    assert out.exists() and not (tmp_path / "ra_tts.mp4").exists() and not (tmp_path / "ra_notts.mp4").exists()
    assert "--both" in capsys.readouterr().err


def test_swap_audio_command_copies_video_and_cuts_audio_from_start():
    from srtpx_cover.core.dub import build_swap_audio_command

    cmd = build_swap_audio_command("ffmpeg", "v.mp4", "a.m4a", "o.mp4", start=12.5)
    assert cmd[cmd.index("-c:v") + 1] == "copy"                                  # không dựng lại hình
    assert cmd[cmd.index("-ss") + 1] == "12.500" and cmd.index("-ss") < cmd.index("a.m4a")   # -ss thuộc đầu vào âm thanh
    assert "-ss" not in build_swap_audio_command("ffmpeg", "v.mp4", "a.m4a", "o.mp4")
