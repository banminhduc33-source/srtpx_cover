"""Phụ đề phụ có hộp cao hơn rộng được tự xuống dòng (layout.wrap_lines, Subtitle.wrap, maincap). Thuần Python."""
from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path

from srtpx_cover.core.fonts import text_width
from srtpx_cover.core.layout import LINE_HEIGHT_FACTOR, plan_text, wrap_lines
from srtpx_cover.core.maincap import MainCaptionOptions, apply_main_captions
from srtpx_cover.core.model import Box, Keyframe, SrtpxDocument, Subtitle
from tests.helpers import fake_chain

LATIN, VIET, CJK = fake_chain(Path(tempfile.mkdtemp()))
CHAIN = [LATIN, VIET, CJK]
# LATIN: mỗi ký tự 0.6em (kể cả khoảng trắng dùng .notdef 0.5em), nhân 1.04 an toàn


def size_of(lines, w, h):
    em = max(text_width(LATIN, ln, 1.0) for ln in lines)
    return min(h / (LINE_HEIGHT_FACTOR * len(lines)), w / em)


# ---------------------------------------------------------------- wrap_lines

def test_tall_narrow_box_gets_more_lines_and_bigger_text():
    text = "aaaa bbbb cccc dddd"
    lines = wrap_lines(text, LATIN, 60, 200)
    assert len(lines) > 1 and " ".join(lines) == text
    assert size_of(lines, 60, 200) > size_of((text,), 60, 200)          # to hơn một dòng


def test_chosen_line_count_maximizes_size():
    text = "aaaa bbbb cccc dddd eeee ffff"
    lines = wrap_lines(text, LATIN, 80, 220)
    best = max(size_of(wrap_lines(text, LATIN, 80, 220, max_lines=k), 80, 220) for k in range(1, 7))
    assert size_of(lines, 80, 220) >= best - 1e-9


def test_lines_are_balanced_not_one_long_plus_tiny_leftovers():
    lines = wrap_lines("aa bb cc dd ee ff", LATIN, 40, 400)
    assert len(lines) >= 2
    widths = [text_width(LATIN, ln, 1.0) for ln in lines]
    assert max(widths) - min(widths) <= text_width(LATIN, "aa", 1.0) + 1e-9


def test_wide_box_keeps_a_single_line():
    assert wrap_lines("aaaa bbbb cccc", LATIN, 500, 30) == ("aaaa bbbb cccc",)


def test_single_word_is_never_cut():
    assert wrap_lines("abcdefghijkl", LATIN, 20, 300) == ("abcdefghijkl",)


def test_degenerate_box_returns_text_unchanged():
    assert wrap_lines("aa bb", LATIN, 0, 100) == ("aa bb",)
    assert wrap_lines("aa bb", LATIN, 100, 0) == ("aa bb",)


def test_never_exceeds_max_lines():
    assert len(wrap_lines(" ".join(["ab"] * 40), LATIN, 10, 5000, max_lines=5)) <= 5


# ---------------------------------------------------------------- plan_text

def sub_of(text, box, wrap=True, **kw):
    return Subtitle(1, 0.0, 2.0, text, box=box, wrap=wrap, **kw)


def test_plan_wraps_only_when_wrap_flag_and_box_taller_than_wide():
    tall = Box(100, 160, 100, 300)                                      # 60 x 200
    _, on = plan_text(sub_of("aaaa bbbb cccc dddd", tall), CHAIN)
    _, off = plan_text(sub_of("aaaa bbbb cccc dddd", tall, wrap=False), CHAIN)
    _, wide = plan_text(sub_of("aaaa bbbb cccc dddd", Box(100, 400, 100, 140)), CHAIN)
    assert on.n > 1
    assert off.lines == wide.lines == ("aaaa bbbb cccc dddd",)
    assert on.size_for(60, 200) > off.size_for(60, 200)


def test_plan_keeps_explicit_lines_untouched():
    _, plan = plan_text(sub_of("aaaa bbbb\ncccc dddd", Box(100, 160, 100, 300)), CHAIN)
    assert plan.lines == ("aaaa bbbb", "cccc dddd")


def test_square_box_does_not_wrap():
    _, plan = plan_text(sub_of("aaaa bbbb cccc", Box(100, 200, 100, 200)), CHAIN)
    assert plan.n == 1


def test_keyframed_subtitle_uses_the_resting_box():
    sub = Subtitle(1, 0.0, 2.0, "aaaa bbbb cccc dddd", keyframes=[
        Keyframe(0.0, Box(0, 500, 0, 40)),                             # bay vào: ngang
        Keyframe(1.0, Box(100, 160, 100, 300)),                        # nằm yên: cao hơn rộng
    ], wrap=True)
    assert plan_text(sub, CHAIN)[1].n > 1


def test_plan_scales_box_with_scale_y():
    sub = sub_of("aaaa bbbb cccc dddd", Box(100, 130, 100, 200))       # 30 x 100 trong toạ độ #res
    lines_1x = plan_text(sub, CHAIN, 1.0)[1].lines
    lines_4x = plan_text(sub, CHAIN, 4.0)[1].lines                     # video thực gấp 4: hộp 120 x 400 px
    assert size_of(lines_4x, 120, 400) >= size_of(lines_1x, 120, 400) - 1e-9


# ---------------------------------------------------------------- maincap bật wrap cho phụ đề phụ

RES = (768, 576)


def mk(i, a, b, text, x1=200, x2=560, y1=500, y2=535):
    return Subtitle(i, a, b, text, box=Box(x1, x2, y1, y2))


def test_only_secondary_subtitles_get_wrap_and_main_ones_do_not():
    floating = mk(4, 0, 3, "aaaa bbbb cccc dddd", 100, 160, 100, 300)  # chữ bay lơ lửng, cao hơn rộng
    doc = SrtpxDocument(RES, [mk(1, 3, 4, "a b"), mk(2, 4, 5, "c d"), mk(3, 5, 6, "e f"), floating])
    new, info = apply_main_captions(doc)
    assert info.applied
    sec = [s for s in new.subtitles if s.text == "aaaa bbbb cccc dddd"]
    assert len(sec) == 1 and sec[0].wrap and sec[0].box == floating.box
    assert not any(s.wrap for s in new.subtitles if s.text != "aaaa bbbb cccc dddd")
    assert not floating.wrap                                           # tài liệu gốc không bị đổi


def test_secondary_that_cannot_wrap_is_passed_through_untouched():
    wide = mk(4, 0, 3, "aaaa bbbb", 100, 400, 100, 130)                  # hộp ngang
    one_word = mk(5, 0, 3, "abcdef", 100, 160, 200, 400)                 # cao hơn rộng nhưng chỉ một chữ
    multi = mk(6, 0, 3, "aaaa\nbbbb", 100, 160, 200, 400)                # đã có sẵn nhiều dòng
    doc = SrtpxDocument(RES, [mk(1, 3, 4, "a b"), mk(2, 4, 5, "c d"), mk(3, 5, 6, "e f"), wide, one_word, multi])
    new, _ = apply_main_captions(doc)
    for orig in (wide, one_word, multi):
        assert any(s is orig for s in new.subtitles)


def test_wrap_secondary_can_be_turned_off():
    floating = mk(4, 0, 3, "aaaa bbbb cccc dddd", 100, 160, 100, 300)
    doc = SrtpxDocument(RES, [mk(1, 3, 4, "a b"), mk(2, 4, 5, "c d"), mk(3, 5, 6, "e f"), floating])
    new, _ = apply_main_captions(doc, MainCaptionOptions(wrap_secondary=False))
    assert not any(s.wrap for s in new.subtitles)


def test_no_main_group_means_nothing_is_wrapped():
    doc = SrtpxDocument(RES, [mk(1, 0, 1, "aaaa bbbb", 100, 160, 100, 300)])
    new, info = apply_main_captions(doc)
    assert not info.applied and new is doc and not new.subtitles[0].wrap


def test_wrap_is_not_part_of_the_file_format():
    from srtpx_cover.core.parser import parse_srtpx, write_srtpx
    sub = replace(mk(1, 0, 1, "aaaa bbbb cccc dddd", 100, 160, 100, 300), wrap=True)
    text = write_srtpx(SrtpxDocument(RES, [sub]))
    assert "wrap" not in text.lower()
    assert parse_srtpx(text).document.subtitles[0].wrap is False


def test_export_draws_wrapped_lines_for_tall_secondary_but_one_line_when_disabled():
    import re
    from srtpx_cover.core.export import ExportOptions, build_filter_script
    from srtpx_cover.core.ffmpeg_tools import VideoInfo
    video = VideoInfo(768, 576, 30, 1, 10.0)
    floating = mk(4, 0, 3, "aaaa bbbb cccc dddd", 100, 160, 100, 300)
    doc = SrtpxDocument(RES, [mk(1, 3, 4, "a b"), mk(2, 4, 5, "c d"), mk(3, 5, 6, "e f"), floating])

    def lines_drawn(opts):
        new, _ = apply_main_captions(doc, opts, chain=CHAIN)
        script = build_filter_script(new, video, CHAIN, ExportOptions(), Path("/tmp/work"))
        sec = next(s for s in new.subtitles if s.box == floating.box)
        return [t for p, t in script.textfiles.items() if p.name.startswith(f"sub{sec.index:04d}_")], script

    wrapped, script = lines_drawn(MainCaptionOptions())
    single, _ = lines_drawn(MainCaptionOptions(wrap_secondary=False))
    assert len(wrapped) > 1 and " ".join(wrapped) == "aaaa bbbb cccc dddd"
    assert single == ["aaaa bbbb cccc dddd"]
    sizes = [float(x) for x in re.findall(r"fontsize='([\d.]+)'", script.text)]
    assert sizes and max(sizes) > 1
