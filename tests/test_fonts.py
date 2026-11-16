import os
import tempfile
from pathlib import Path

import pytest

from srtpx_cover.core.fonts import (
    FontChoice,
    FontData,
    FontError,
    FontSpec,
    choose_font,
    default_font_chain,
    describe_font_warnings,
    missing_chars,
    text_width,
)
from tests.helpers import fake_chain, make_font


def _chain():
    return list(fake_chain(Path(tempfile.mkdtemp())))


def test_latin_uses_primary_font():
    c = choose_font("Hello World 123", _chain())
    assert c.chain_index == 0 and not c.used_fallback and c.missing == ()


def test_whitespace_and_newlines_are_ignored_for_coverage():
    # font giả không có glyph cho khoảng trắng / xuống dòng / tab
    c = choose_font("Hello \n\tWorld\r\n", _chain())
    assert c.chain_index == 0 and c.missing == ()


def test_vietnamese_with_marks_falls_back_to_second_font():
    c = choose_font("Việt Nam đẹp quá", _chain())
    assert c.chain_index == 1 and c.used_fallback and c.missing == ()


def test_chinese_falls_back_to_cjk_font():
    c = choose_font("原神大世界做镜子了？", _chain())
    assert c.chain_index == 2 and c.missing == ()


def test_mixed_string_picks_font_covering_most_and_lists_missing():
    # "Việt" cần font Việt, "原神" cần font CJK; không font nào phủ hết.
    chain = _chain()
    c = choose_font("Việt 原神", chain)
    assert c.chain_index == 2          # CJK chỉ thiếu 'ệ'; Việt thiếu 2 chữ Hán; Latin thiếu 3
    assert c.missing == ("ệ",)


def test_tie_prefers_earlier_font():
    chain = _chain()
    c = choose_font("ệ原", chain)      # Việt thiếu 原, CJK thiếu ệ, Latin thiếu cả hai
    assert c.chain_index == 1 and c.missing == ("原",)


def test_char_missing_in_every_font():
    c = choose_font("Hi ☃", _chain())
    assert c.missing == ("☃",)


def test_color_emoji_counts_as_missing_even_if_font_has_glyph():
    tmp = Path(tempfile.mkdtemp())
    f = FontData(make_font(tmp / "emo.ttf", ASCII_ONLY := "abc", extra=(0x1F600,)))
    assert f.has_char("\U0001F600")
    assert missing_chars(f, "a\U0001F600") == ["\U0001F600"]


def test_missing_chars_unique_in_order():
    f = _chain()[0]
    assert missing_chars(f, "a原b神原") == ["原", "神"]


def test_text_width_uses_advance_and_safety_margin():
    f = _chain()[0]  # advance 600/1000
    assert text_width(f, "abc", 20) == pytest.approx(3 * 0.6 * 20 * 1.04)
    # ký tự thiếu dùng .notdef (500/1000)
    assert text_width(f, "原", 20) == pytest.approx(0.5 * 20 * 1.04)


def test_choose_font_with_empty_chain_raises_vietnamese_error():
    with pytest.raises(FontError, match="font"):
        choose_font("abc", [])


def test_font_errors_are_vietnamese():
    with pytest.raises(FontError, match="Không tìm thấy file font"):
        FontData(FontSpec("/khong/co/font.ttf"))
    bad = Path(tempfile.mkdtemp()) / "bad.ttf"
    bad.write_bytes(b"day khong phai font")
    with pytest.raises(FontError, match="Không đọc được font"):
        FontData(FontSpec(str(bad)))


def test_describe_font_warnings():
    chain = _chain()
    ok = {1: choose_font("abc", chain)}
    assert describe_font_warnings(ok) is None
    choices = {1: choose_font("abc", chain), 2: choose_font("Việt", chain), 3: choose_font("Hi ☃", chain)}
    msg = describe_font_warnings(choices)
    assert msg is not None
    assert "dự phòng" in msg and "2" in msg
    assert "Phụ đề 3" in msg and "U+2603" in msg
    assert msg.count("Phụ đề phải đổi") == 1  # MỘT cảnh báo duy nhất


def test_default_font_chain_keeps_only_existing_files():
    root = Path(tempfile.mkdtemp())
    fonts = root / "fonts"
    fonts.mkdir()
    (fonts / "Bevan-Regular.ttf").write_bytes(b"x")
    win = root / "win" / "Fonts"
    win.mkdir(parents=True)
    (win / "msyh.ttc").write_bytes(b"x")
    old = os.environ.get("WINDIR")
    os.environ["WINDIR"] = str(root / "win")
    try:
        chain, warnings = default_font_chain(fonts)
    finally:
        if old is None:
            del os.environ["WINDIR"]
        else:
            os.environ["WINDIR"] = old
    assert [Path(s.path).name for s in chain] == ["Bevan-Regular.ttf", "msyh.ttc"]
    assert len(warnings) == 1 and "Arial" in warnings[0]


def test_ttc_loads_with_face_zero():
    p = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    if not Path(p).is_file():
        pytest.skip("không có font TTC trên máy này")
    f = FontData(FontSpec(p, 0))
    assert f.has_char("原") and not f.has_char("\U0001F600")
