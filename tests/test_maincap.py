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
    MainCaptionOptions, apply_main_captions, chunk_times, find_main_group, split_chunks, split_chunks_to_fit, word_weights,
)
from srtpx_cover.core.fonts import text_width
from srtpx_cover.core.model import Box, Keyframe, SrtpxDocument, Subtitle
from tests.helpers import fake_chain

CHAIN = list(fake_chain(Path(tempfile.mkdtemp())))
RES = (768, 576)


def mk(i, a, b, text, x1=200, x2=560, y1=500, y2=535, keyframes=None):
    if keyframes:
        return Subtitle(i, a, b, text, keyframes=[Keyframe(t, bx) for t, bx in keyframes])
    return Subtitle(i, a, b, text, box=Box(x1, x2, y1, y2))


def widen(x1, x2):
    """Ngang theo file nhân 1,025 quanh tâm -> (x1, x2)."""
    c, h = (x1 + x2) / 2, (x2 - x1) * 1.025 / 2
    return c - h, c + h


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


def test_box_enclosed_by_the_main_group_region_joins_the_group():
    subs = [
        mk(1, 0, 1, "a", 200, 560, 500, 535),
        mk(2, 1, 2, "b", 210, 550, 498, 537),
        mk(3, 2, 3, "c", 190, 570, 490, 545),      # hộp lớn hơn: định hình vùng bao của nhóm
        mk(4, 3, 4, "d", 400, 540, 492, 512),      # tâm dọc 502, lệch tâm nhóm ~15px; nằm trọn trong vùng bao
        mk(5, 4, 5, "e", 100, 300, 100, 130),      # ngoài vùng bao: vẫn là phụ đề phụ
    ]
    # dung sai dọc chỉ 2px nên hộp 4 không vào bằng tâm, chỉ vào vì nằm trọn trong vùng bao của nhóm
    assert find_main_group(subs, 768 * 0.05, 2) == [0, 1, 2, 3]


def test_box_poking_out_of_the_group_region_is_not_enclosed():
    subs = [mk(1, 0, 1, "a", 200, 560, 500, 535), mk(2, 1, 2, "b", 205, 555, 502, 533),
            mk(3, 2, 3, "c", 300, 600, 500, 535)]      # thò ra ngoài bên phải
    assert find_main_group(subs, 768 * 0.05, 576 * 0.025) == [0, 1]


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


# ------------------------------------------------------------------ chia cụm theo sức chứa hộp

FONT = CHAIN[0]
H = 36.0                                   # cao hộp -> cỡ chữ lớn nhất 30, co tối đa tới 75% = 22,5
WORDS12 = " ".join(["aaaa"] * 12)          # mỗi chữ 2,496em, khoảng trắng 0,52em (font giả 0,6em/ký tự, nhân 1,04)


def max_em(box_w, box_h=H, ratio=0.75):
    return box_w / (box_h / 1.2 * ratio)


def test_wider_box_holds_more_words_per_chunk():
    narrow = split_chunks_to_fit(WORDS12, FONT, 225, H)
    wide = split_chunks_to_fit(WORDS12, FONT, 450, H)
    assert max(map(len, narrow)) == 3 and max(map(len, wide)) == 6
    assert [len(c) for c in wide] == [6, 6] and len(narrow) == 4
    for chunks in (narrow, wide):
        assert [w for c in chunks for w in c] == WORDS12.split()


def test_every_chunk_fits_the_box_at_the_smallest_allowed_size():
    for w in (80, 140, 225, 337, 450, 900):
        for ratio in (0.5, 0.75, 1.0):
            limit = max_em(w, ratio=ratio)
            for c in split_chunks_to_fit(WORDS12, FONT, w, H, ratio):
                assert len(c) == 1 or text_width(FONT, " ".join(c), 1.0) <= limit + 1e-9


def test_lower_min_size_means_fewer_chunks():
    counts = [len(split_chunks_to_fit(WORDS12, FONT, 225, H, r)) for r in (1.0, 0.75, 0.5, 0.25)]
    assert counts == sorted(counts, reverse=True) and counts[0] > counts[-1]


def test_fit_chunks_are_balanced_unless_balance_is_off():
    box_w = 12 * (H / 1.2 * 0.75)                                   # vừa đủ 4 chữ một dòng, 5 chữ thì tràn
    nine = " ".join(["aaaa"] * 9)
    assert [len(c) for c in split_chunks_to_fit(nine, FONT, box_w, H)] == [3, 3, 3]
    assert [len(c) for c in split_chunks_to_fit(nine, FONT, box_w, H, balance=False)] == [4, 4, 1]


def test_max_words_caps_chunks_even_when_the_box_could_hold_more():
    chunks = split_chunks_to_fit(WORDS12, FONT, 900, H, max_words=4)
    assert max(map(len, chunks)) <= 4 and [len(c) for c in chunks] == [4, 4, 4]


def test_word_wider_than_the_box_is_never_cut_and_stands_alone():
    chunks = split_chunks_to_fit("aa bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb cc", FONT, 60, H)
    assert [w for c in chunks for w in c] == ["aa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "cc"]
    assert ["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"] in chunks


def test_fit_handles_blank_degenerate_box_and_short_text():
    assert split_chunks_to_fit("  \n ", FONT, 300, H) == []
    assert split_chunks_to_fit("một hai", FONT, 600, H) == [["một", "hai"]]
    assert [len(c) for c in split_chunks_to_fit("1 2 3 4 5 6 7", FONT, 0, H)] == [4, 3]   # hộp hỏng: lùi về số chữ cố định


def test_apply_with_fonts_sizes_chunks_by_each_subtitles_own_box_width():
    wide = mk(1, 0.0, 3.0, WORDS12, 100, 700, 500, 535)             # cùng tâm ngang 400, khác độ rộng
    narrow = mk(2, 3.0, 6.0, WORDS12, 370, 430, 500, 535)
    third = mk(3, 6.0, 7.0, "aaaa aaaa", 100, 700, 500, 535)
    new, info = apply_main_captions(doc_of(wide, narrow, third), chain=CHAIN)
    assert info.applied and info.by_box and "sức chứa hộp" in info.message()
    n_wide = sum(1 for s in new.subtitles if s.box.width > 300 and s.text.startswith("aaaa") and s.start < 3.0)
    n_narrow = sum(1 for s in new.subtitles if s.box.width < 100)
    assert n_wide < n_narrow                                         # hộp rộng chia ít cụm hơn hộp hẹp
    assert all(len(s.text.split()) == 1 for s in new.subtitles if s.box.width < 100)
    assert info.max_chunk_words == max(len(s.text.split()) for s in new.subtitles)
    assert [w for s in new.subtitles if s.start < 3.0 for w in s.text.split()] == WORDS12.split()


def test_apply_without_fonts_falls_back_to_fixed_word_count():
    new, info = apply_main_captions(three_main())
    assert info.applied and not info.by_box and info.max_chunk_words == 5 and "tối đa 5 chữ" in info.message()
    capped, info2 = apply_main_captions(three_main(), MainCaptionOptions(words_per_chunk=3))
    assert info2.max_chunk_words <= 3 and all(len(s.text.split()) <= 3 for s in capped.subtitles)


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


def test_each_main_subtitle_keeps_its_own_width_but_mean_vertical_with_fixed_font_and_secondary_untouched():
    doc = three_main()
    new, info = apply_main_captions(doc)
    assert info.applied and info.n_main == 3 and info.n_total == 4
    main = [s for s in new.subtitles if s.index not in {1, 2, 3, 4}]
    assert main and all(not s.keyframes and s.font_px is None for s in main)   # cỡ chữ co theo hộp như phụ đề phụ
    y1, y2 = (500 + 502 + 498) / 3, (535 + 538 + 533) / 3
    def wide(x1, x2):   # ngang theo file nhân 1,025 quanh tâm
        c, h = (x1 + x2) / 2, (x2 - x1) * 1.025 / 2
        return Box(c - h, c + h, y1, y2)
    own = {1: wide(200, 560), 2: wide(210, 540), 3: wide(190, 570)}
    for idx, text_part in ((1, "một hai"), (1, "sáu bảy"), (2, "Xin chào"), (3, "Câu cuối nhé")):
        s = next(x for x in main if x.text.startswith(text_part))
        assert s.box == own[idx]                            # ngang theo file x1,025, dọc trung bình
    assert len({s.box for s in main}) == 3
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
    assert dropped.text == "" and (dropped.box.x1, dropped.box.x2) == pytest.approx(widen(200, 560)) and dropped.box.y1 == info.box.y1 and dropped.font_px is None
    assert [s.text for s in new.subtitles if s.text] == ["câu chính", "câu kế"]


def test_empty_text_subtitle_in_main_position_is_still_covered_by_its_own_box():
    doc = doc_of(mk(1, 0.0, 1.0, "a b"), mk(2, 1.0, 2.0, "", 190, 570, 498, 533), mk(3, 2.0, 3.0, "c d"))
    new, info = apply_main_captions(doc)
    empty = [s for s in new.subtitles if s.index == 2][0]
    assert empty.text == "" and (empty.box.x1, empty.box.x2) == pytest.approx(widen(190, 570)) and empty.box.y1 == info.box.y1 and (empty.start, empty.end) == (1.0, 2.0)


def test_box_is_never_grown_or_split_by_chunks():
    small = doc_of(*[mk(i, i, i + 1, "abcde fghij klmno pqrst uvwxy", 300, 360, 500, 520) for i in range(1, 4)])
    new, _ = apply_main_captions(small, chain=CHAIN)
    assert all((s.box.x1, s.box.x2) == pytest.approx(widen(300, 360)) and (s.box.y1, s.box.y2) == (500, 520) for s in new.subtitles)   # không nới theo chữ


# ------------------------------------------------------------------ vẽ ra

def test_font_px_is_a_cap_that_scales_with_video_and_still_shrinks_to_small_box():
    sub = Subtitle(1, 0, 1, "abc", box=Box(0, 400, 0, 100), font_px=18.0)
    _, plan = plan_text(sub, CHAIN, 2.0)
    assert plan.size_for(400, 100) == 36.0                                  # hộp rộng: đúng cỡ tối đa, nhân theo độ phân giải
    assert plan.size_for(10, 10) < 36.0                                     # hộp nhỏ: chữ co theo hộp, không tràn
    _, plain = plan_text(Subtitle(1, 0, 1, "abc", box=Box(0, 400, 0, 100)), CHAIN, 2.0)
    assert plain.size_for(400, 100) != 36.0
    assert plan.fits_at(400, 100) and not plan.fits_at(1, 1)


def test_filter_script_draws_main_captions_with_each_subtitles_own_black_box_and_fit_to_box_size():
    video = VideoInfo(768, 576, 30, 1, 10.0)
    new, info = apply_main_captions(three_main(), chain=CHAIN)
    script = build_filter_script(new, video, CHAIN, ExportOptions(), Path("/tmp/work"))
    sizes = set(re.findall(r"fontsize='([\d.]+)'", script.text))
    assert sizes and all(float(x) > 1 for x in sizes)
    boxes = set(re.findall(r"drawbox=x=(\d+):y=(\d+):w=(\d+):h=(\d+)", script.text))
    assert len(boxes) == 4                               # 3 phụ đề chính (ngang riêng, dọc chung) + hộp của chữ bay (phụ)
    assert not script.layers                             # không còn keyframe nên không cần lớp vẽ từng khung
