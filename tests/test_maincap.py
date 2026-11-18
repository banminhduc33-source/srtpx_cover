"""Test phụ đề chính: nhận diện nhóm đông nhất, hộp cố định, chia cụm, mốc thời gian theo giọng đọc. Thuần Python."""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest

from srtpx_cover.core.export import ExportOptions, build_filter_script
from srtpx_cover.core.ffmpeg_tools import VideoInfo
from srtpx_cover.core.layout import plan_text
from srtpx_cover.core.maincap import (
    MainCaptionOptions, apply_main_captions, chunk_times, find_main_group, split_chunks, word_weights,
)
from srtpx_cover.core.model import Box, Keyframe, SrtpxDocument, Subtitle
from tests.helpers import fake_chain

CHAIN = list(fake_chain(Path(tempfile.mkdtemp())))
RES = (768, 576)


def mk(i, a, b, text, x1=200, x2=560, y1=500, y2=535, keyframes=None):
    if keyframes:
        return Subtitle(i, a, b, text, keyframes=[Keyframe(t, bx) for t, bx in keyframes])
    return Subtitle(i, a, b, text, box=Box(x1, x2, y1, y2))


def doc_of(*subs):
    return SrtpxDocument(res=RES, subtitles=list(subs))


# ------------------------------------------------------------------ nhóm chính

def test_largest_cluster_wins_even_with_ocr_jitter():
    subs = [
        mk(1, 0, 1, "a", 200, 560, 500, 535),
        mk(2, 1, 2, "b", 203, 557, 498, 537),      # nhiễu vài px
        mk(3, 2, 3, "c", 197, 565, 503, 533),
        mk(4, 3, 4, "d", 100, 300, 100, 130),      # chữ bay: nhóm khác chỉ có 2 dòng
        mk(5, 4, 5, "e", 105, 305, 98, 128),
    ]
    assert find_main_group(subs, 768 * 0.05, 576 * 0.025) == [0, 1, 2]


def test_tie_prefers_longer_total_duration_then_lower_position():
    a = [mk(1, 0, 1, "a", y1=100, y2=130), mk(2, 1, 2, "b", y1=100, y2=130)]
    b = [mk(3, 2, 3, "c", y1=500, y2=535), mk(4, 3, 4, "d", y1=500, y2=535)]
    assert find_main_group(a + b, 38, 14) == [2, 3]             # hòa số lượng và thời lượng: nhóm nằm thấp hơn
    long_top = [mk(1, 0, 5, "a", y1=100, y2=130), mk(2, 5, 10, "b", y1=100, y2=130)]
    assert find_main_group(long_top + b, 38, 14) == [0, 1]      # nhóm trên dài hơn thì thắng


def test_keyframed_subtitle_is_positioned_by_its_resting_box():
    fly = mk(9, 0, 3, "x", keyframes=[(0.0, Box(0, 800, -50, 20)), (1.0, Box(200, 560, 500, 535))])
    plain = [mk(1, 3, 4, "a"), mk(2, 4, 5, "b")]
    assert find_main_group([fly, *plain], 38, 14) == [0, 1, 2]


def test_no_group_when_every_subtitle_is_alone():
    doc = doc_of(mk(1, 0, 1, "a", y1=100, y2=130), mk(2, 1, 2, "b", y1=300, y2=330), mk(3, 2, 3, "c", y1=500, y2=535))
    new, info = apply_main_captions(doc)
    assert new is doc and not info.applied and "đông nhất" in info.message()


# ------------------------------------------------------------------ chia cụm

def test_split_balanced_never_exceeds_limit_and_keeps_every_word():
    for n in range(1, 40):
        text = " ".join(f"w{i}" for i in range(n))
        chunks = split_chunks(text, 5)
        assert all(1 <= len(c) <= 5 for c in chunks)
        assert [w for c in chunks for w in c] == text.split()
        if n > 5:
            assert max(map(len, chunks)) - min(map(len, chunks)) <= 1   # không có cụm lẻ 1 chữ


def test_split_literal_five_per_chunk():
    chunks = split_chunks("1 2 3 4 5 6 7 8 9 10 11", 5, balance=False)
    assert [len(c) for c in chunks] == [5, 5, 1]
    assert [len(c) for c in split_chunks("1 2 3 4 5 6 7 8 9 10 11", 5)] == [4, 4, 3]


def test_split_handles_newlines_and_blank():
    assert split_chunks("một\nhai   ba") == [["một", "hai", "ba"]]
    assert split_chunks("   \n ") == []


def test_weights_add_pause_at_punctuation_but_not_at_the_end():
    w = word_weights(["Xin", "chào,", "bạn", "khỏe", "không?"])
    assert w[0] == 1.0 and w[1] > 1.0 and w[2] == 1.0 and w[4] == 1.0
    assert word_weights(["(ừ)", "thế."])[0] == 1.0          # dấu ngoặc đóng không che mất dấu câu phía trước
    assert word_weights(["ừ.”", "thế"])[0] > 1.0


def test_chunk_times_are_contiguous_and_cover_the_span():
    times = chunk_times([5.6, 5.0, 3.0], t0=10.0, speech_end=12.0, t_end=13.0)
    assert times[0][0] == 10.0 and times[-1][1] == 13.0
    assert all(a[1] == b[0] for a, b in zip(times, times[1:]))
    assert times[0][1] == pytest.approx(10.0 + 2.0 * 5.6 / 13.6)    # chia theo giọng đọc (tới 12.0), không theo 13.0


# ------------------------------------------------------------------ áp dụng

def three_main():
    return doc_of(
        mk(1, 0.0, 3.0, "một hai ba bốn năm sáu bảy tám chín mười", 200, 560, 500, 535),
        mk(2, 3.0, 4.0, "Xin chào", 210, 540, 502, 538),
        mk(3, 4.0, 6.0, "Câu cuối nhé", 190, 570, 498, 533),
        mk(4, 1.0, 2.0, "Chữ bay", 100, 200, 100, 130),     # phụ
    )


def test_main_boxes_are_one_fixed_mean_box_with_fixed_font_and_secondary_untouched():
    doc = three_main()
    new, info = apply_main_captions(doc)
    assert info.applied and info.n_main == 3 and info.n_total == 4
    main = [s for s in new.subtitles if s.index not in {1, 2, 3, 4}]
    assert main and all(s.box == info.box and not s.keyframes and s.font_px == 20.0 for s in main)
    # hộp = trung bình hộp của cả nhóm chính
    assert info.box.x1 == pytest.approx((200 + 210 + 190) / 3)
    assert info.box.x2 == pytest.approx((560 + 540 + 570) / 3)
    assert info.box.y1 == pytest.approx((500 + 502 + 498) / 3)
    assert info.box.y2 == pytest.approx((535 + 538 + 533) / 3)
    fly = [s for s in new.subtitles if s.text == "Chữ bay"]
    assert len(fly) == 1 and fly[0] == doc.subtitles[3] and fly[0].font_px is None
    assert doc.subtitles[0].text.startswith("một hai") and doc.subtitles[0].font_px is None   # tài liệu gốc không đổi


def test_every_chunk_has_at_most_five_words_and_unique_index_and_order_kept():
    new, _ = apply_main_captions(three_main())
    assert all(len(s.text.split()) <= 5 for s in new.subtitles)
    idx = [s.index for s in new.subtitles]
    assert len(idx) == len(set(idx))
    # thứ tự file được giữ: các cụm thay đúng chỗ phụ đề chính, phụ đề phụ vẫn đứng cuối như ban đầu
    assert [s.text for s in new.subtitles] == [
        "một hai ba bốn năm", "sáu bảy tám chín mười", "Xin chào", "Câu cuối nhé", "Chữ bay"]


def test_no_voice_splits_evenly_over_subtitle_span_and_boxes_cover_it_without_gaps():
    new, _ = apply_main_captions(three_main())
    first = [s for s in new.subtitles if s.text in ("một hai ba bốn năm", "sáu bảy tám chín mười")]
    assert first[0].start == 0.0 and first[0].end == pytest.approx(first[1].start) and first[1].end == 3.0
    assert first[0].end == pytest.approx(1.5)


def test_spoken_longer_than_slot_borrows_silence_but_stops_at_next_main_subtitle():
    doc = doc_of(mk(1, 0.0, 1.0, "a b c d e f g h i j"), mk(2, 5.0, 6.0, "k l"))
    new, _ = apply_main_captions(doc, spoken={1: 2.0})
    ch = [s for s in new.subtitles if s.text in ("a b c d e", "f g h i j")]
    assert ch[0].start == 0.0 and ch[1].end == pytest.approx(2.0)        # giọng đọc 2s: chữ hiện tới 2s, dài hơn phụ đề gốc 1s
    assert ch[0].end == pytest.approx(1.0)
    capped, _ = apply_main_captions(doc, spoken={1: 9.0})
    last = [s for s in capped.subtitles if s.text == "f g h i j"][0]
    assert last.end == pytest.approx(5.0)                                # không lấn sang phụ đề chính kế tiếp


def test_spoken_shorter_than_slot_keeps_cover_until_original_end():
    doc = doc_of(mk(1, 0.0, 4.0, "a b c d e f g h i j"), mk(2, 5.0, 6.0, "k l"))
    new, _ = apply_main_captions(doc, spoken={1: 2.0})
    a, b = new.subtitles[:2]
    assert a.start == 0.0 and a.end == pytest.approx(1.0)                # chuyển cụm khi giọng đọc tới giữa câu (2s / 2)
    assert b.end == 4.0                                                   # cụm cuối giữ tới `end` để hộp che chữ gốc


def test_overlapping_main_blocks_keep_box_but_lose_text():
    doc = doc_of(mk(1, 0.0, 3.0, "câu chính"), mk(2, 1.0, 2.0, "câu chồng giờ"), mk(3, 3.0, 4.0, "câu kế"))
    new, info = apply_main_captions(doc)
    assert info.n_dropped_overlap == 1
    dropped = [s for s in new.subtitles if s.index == 2][0]
    assert dropped.text == "" and dropped.box == info.box and dropped.font_px is None
    assert [s.text for s in new.subtitles if s.text] == ["câu chính", "câu kế"]


def test_empty_text_subtitle_in_main_position_is_still_covered_by_fixed_box():
    doc = doc_of(mk(1, 0.0, 1.0, "a b"), mk(2, 1.0, 2.0, "", 190, 570, 498, 533), mk(3, 2.0, 3.0, "c d"))
    new, info = apply_main_captions(doc)
    empty = [s for s in new.subtitles if s.index == 2][0]
    assert empty.text == "" and empty.box == info.box and (empty.start, empty.end) == (1.0, 2.0)


def test_box_stays_the_srtpx_box_and_is_not_grown_or_split_by_chunks():
    small = doc_of(*[mk(i, i, i + 1, "abcde fghij klmno pqrst uvwxy", 300, 360, 500, 520) for i in range(1, 4)])
    new, info = apply_main_captions(small, chain=CHAIN)
    assert info.box == Box(300, 360, 500, 520)            # đúng hộp trong file, không nới theo chữ
    assert all(s.box == info.box for s in new.subtitles)   # mọi cụm dùng chung một hộp


# ------------------------------------------------------------------ vẽ ra

def test_fixed_size_overrides_fit_and_scales_with_video_height():
    sub = Subtitle(1, 0, 1, "abc", box=Box(0, 400, 0, 100), font_px=18.0)
    _, plan = plan_text(sub, CHAIN, 2.0)
    assert plan.size_for(400, 100) == 36.0 == plan.size_for(10, 10)        # không co theo hộp
    _, plain = plan_text(Subtitle(1, 0, 1, "abc", box=Box(0, 400, 0, 100)), CHAIN, 2.0)
    assert plain.size_for(400, 100) != 36.0
    assert plan.fits_at(400, 100) and not plan.fits_at(10, 10)


def test_filter_script_draws_main_captions_at_exactly_20px_in_one_black_box():
    video = VideoInfo(768, 576, 30, 1, 10.0)
    new, info = apply_main_captions(three_main(), chain=CHAIN)
    script = build_filter_script(new, video, CHAIN, ExportOptions(), Path("/tmp/work"))
    sizes = set(re.findall(r"fontsize='([\d.]+)'", script.text))
    assert "20" in sizes
    boxes = set(re.findall(r"drawbox=x=(\d+):y=(\d+):w=(\d+):h=(\d+)", script.text))
    assert len(boxes) == 2                               # hộp cố định của phụ đề chính + hộp của chữ bay (phụ)
    assert not script.layers                             # không còn keyframe nên không cần lớp vẽ từng khung
