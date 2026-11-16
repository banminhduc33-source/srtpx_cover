import tempfile
from pathlib import Path

import pytest

from srtpx_cover.core.layout import fit_text
from tests.helpers import fake_chain

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


# ---- ô che lớn tự xuống dòng, phụ đề thường thì không

from srtpx_cover.core.geometry import compute_scale
from srtpx_cover.core.layout import plan_text, wrap_to_box
from srtpx_cover.core.model import Box, Subtitle

VIDEO = (768, 576)


def _sub(text, box):
    return Subtitle(1, 0.0, 2.0, text, box, [])


def _plan(text, box):
    sub = _sub(text, box)
    return plan_text(sub, [LATIN], compute_scale(VIDEO, VIDEO), 0.0, VIDEO)[1]


def test_normal_subtitle_box_never_wraps():
    # hộp 1 dòng (cao 34px = 5,9% video): chữ dài vẫn 1 dòng, chỉ co cỡ chữ
    plan = _plan("aaaa bbbb cccc dddd eeee ffff", Box(100, 400, 480, 514))
    assert plan.lines == ("aaaa bbbb cccc dddd eeee ffff",)


def test_big_cover_box_wraps_words_to_fill_it():
    text = "aaaa bbbb cccc dddd eeee ffff gggg hhhh iiii jjjj kkkk llll"
    plan = _plan(text, Box(100, 300, 100, 400))          # hộp 200x300 = 52% chiều cao video
    assert len(plan.lines) > 1
    assert " ".join(plan.lines) == text                  # không mất/đổi chữ
    one = _plan(text, Box(100, 300, 480, 514))           # cùng chiều rộng nhưng thấp: không wrap
    assert len(one.lines) == 1
    assert plan.size_for(200, 300) > one.size_for(200, 34) * 2


def test_wrap_picks_the_size_maximising_line_count():
    lines = wrap_to_box("aa bb cc dd ee ff gg hh", LATIN, 100, 300)
    from srtpx_cover.core.fonts import text_width
    assert len(lines) > 1 and all(text_width(LATIN, ln, 1.0) > 0 for ln in lines)
    assert max(text_width(LATIN, ln, 1.0) for ln in lines) <= 100            # vừa ở cỡ >= 1px


def test_big_box_short_text_stays_single_line():
    # chữ ngắn đã vừa chiều rộng hộp lớn: xuống dòng không làm to hơn => giữ 1 dòng
    assert _plan("abc", Box(100, 600, 100, 400)).lines == ("abc",)


def test_existing_manual_line_breaks_are_kept_in_big_box():
    plan = _plan("aaaa bbbb\ncccc dddd", Box(100, 300, 100, 400))
    assert plan.lines == ("aaaa bbbb", "cccc dddd")


def test_cjk_wraps_between_characters_in_big_box():
    sub = _sub("原神大世界做镜子了米提亚先生该回去上班", Box(100, 200, 100, 400))
    plan = plan_text(sub, [CJK], compute_scale(VIDEO, VIDEO), 0.0, VIDEO)[1]
    assert len(plan.lines) > 1 and "".join(plan.lines) == "原神大世界做镜子了米提亚先生该回去上班"
