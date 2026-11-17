import tempfile
from pathlib import Path

import pytest

from srtpx_cover.core.layout import fit_text
from srtpx_cover.core.fonts import FontData
from tests.helpers import ASCII_NO_SPACE, VIET_CHARS, fake_chain, make_font

LATIN, VIET, CJK = fake_chain(Path(tempfile.mkdtemp()))
# LATIN: mỗi ký tự 0.6em => chiều rộng px = n * 0.6 * size * 1.04 ; CJK: 1.0em


def test_largest_size_that_fits_width():
    lay = fit_text("abcdefghij", LATIN, 100, 30)       # 10 ký tự: 6.24*size <= 100 => size 16
    assert (lay.size, lay.lines, lay.fits) == (16, ("abcdefghij",), True)


def test_height_limits_size():
    lay = fit_text("ab", LATIN, 500, 24)                # 1.2*size <= 24 => size 20
    assert lay.size == 20 and lay.fits


def test_keeps_original_lines_and_shrinks_before_wrapping():
    lay = fit_text("abcd\nefgh", LATIN, 200, 60)        # 2 dòng: 2.4*size <= 60 => 25
    assert lay.lines == ("abcd", "efgh") and lay.size == 25


def test_never_wraps_long_text_shrinks_below_old_minimum():
    # 14 ký tự (kể cả 2 khoảng trắng 0.5em) = 8.2em; hộp rộng 40px => 8.528*s <= 40 => s=4 (dưới mức 10 cũ)
    lay = fit_text("aaaa bbbb cccc", LATIN, 40, 100)
    assert lay.lines == ("aaaa bbbb cccc",) and lay.size == 4 and lay.fits


def test_shrinks_instead_of_wrapping():
    lay = fit_text("aaaa bbbb cccc", LATIN, 120, 100)
    assert lay.lines == ("aaaa bbbb cccc",) and lay.size == 14


def test_cjk_not_broken():
    lay = fit_text("原神大世界做", CJK, 30, 40)               # 6 chữ x 1.04em: 6.24*s <= 30 => s=4
    assert lay.lines == ("原神大世界做",) and lay.size == 4 and lay.fits


def test_result_always_inside_box_width():
    from srtpx_cover.core.fonts import text_width
    for w in (30, 57, 120, 400):
        lay = fit_text("Mít-ti-a nói rằng hôm nay phải về sớm", VIET, w, 200)
        assert len(lay.lines) == 1 and lay.fits
        assert text_width(VIET, lay.lines[0], lay.size) <= w
        assert text_width(VIET, lay.lines[0], lay.size + 1) > w or lay.size + 1 > 200 / 1.2


def test_original_lines_are_kept_each_fitting_the_width():
    lay = fit_text("abcdefghij\nab", LATIN, 50, 100)           # dòng dài quyết định cỡ: 6.24*s <= 50 => s=8
    assert lay.lines == ("abcdefghij", "ab") and lay.size == 8


def test_blank_lines_kept_and_lines_stripped():
    lay = fit_text("  a  \n\nb", LATIN, 200, 100)
    assert lay.lines == ("a", "", "b")


def test_box_too_small_goes_down_to_1px():
    lay = fit_text("abc", LATIN, 100, 5)                      # 1.2*s <= 5 => s=4
    assert lay.size == 4 and lay.fits
    lay = fit_text("abc", LATIN, 100, 0.5)
    assert lay.size == 1 and not lay.fits


def test_layout_dimensions():
    lay = fit_text("ab\ncd", LATIN, 500, 60)
    assert lay.line_height == pytest.approx(1.2 * lay.size)
    assert lay.block_height == pytest.approx(2 * 1.2 * lay.size)


# ---- dấu câu font chính thiếu: đổi sang ký tự tương đương để cả câu giữ MỘT font

def test_missing_bracket_does_not_switch_whole_subtitle_to_fallback_font():
    from srtpx_cover.core.fonts import choose_font, fold_for_font
    from srtpx_cover.core.layout import plan_text
    from srtpx_cover.core.model import Box, Subtitle
    primary = FontData(make_font(Path(tempfile.mkdtemp()) / "p.ttf", ASCII_NO_SPACE + VIET_CHARS))   # có " nhưng không có 《》
    fallback = FontData(make_font(Path(tempfile.mkdtemp()) / "f.ttf", ASCII_NO_SPACE + VIET_CHARS + "《》"))
    chain = [primary, fallback]
    text = "《Thôi học nhưng được tuyển thẳng》"
    assert choose_font(text, chain).chain_index == 1                       # trước: nhảy sang font dự phòng
    assert fold_for_font(text, chain) == '"Thôi học nhưng được tuyển thẳng"'
    choice, plan = plan_text(Subtitle(1, 0.0, 1.0, text, Box(0, 100, 0, 30), []), chain)
    assert choice.chain_index == 0 and plan.lines == ('"Thôi học nhưng được tuyển thẳng"',)


def test_fold_keeps_real_cjk_text_untouched():
    from srtpx_cover.core.fonts import fold_for_font
    assert fold_for_font("《原神》", [LATIN, CJK]) == "《原神》"             # font chính không phủ nổi chữ Hán => giữ nguyên
    assert fold_for_font("Xin chào", [VIET]) == "Xin chào"
