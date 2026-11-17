"""Test lọc phụ đề cần lồng bằng Gemini. Gemini giả (không gọi mạng), TTS giả, ffmpeg thật."""
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
from pathlib import Path

import pytest

from srtpx_cover.core import dubfilter
from srtpx_cover.core.dub import DubOptions, prepare_dub_audio
from srtpx_cover.core.dubfilter import DubFilterError, filter_subtitles, gemini_classifier
from srtpx_cover.core.model import Box, SrtpxDocument, Subtitle
from srtpx_cover.core.tts import TtsCancelled

FFMPEG, FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")
need_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="cần ffmpeg/ffprobe")
BOX = Box(0, 10, 0, 10)


def mk(i: int, text: str, start: float | None = None, dur: float = 1.0) -> Subtitle:
    start = float(i) * 2 if start is None else start
    return Subtitle(i, start, start + dur, text, box=BOX)


def sample_subs() -> list[Subtitle]:
    return [mk(1, "Xin chào các bạn."), mk(2, "Ha ha ha"), mk(3, "Đăng ký kênh nhé"), mk(4, "Đi thôi!"), mk(5, "Ừm")]


NOT_SPOKEN = {"Ha ha ha": "tiếng cười", "Đăng ký kênh nhé": "kêu gọi theo dõi", "Ừm": "cảm thán"}


class FakeClassifier:
    """Gemini giả: ghi lại các lượt gọi, trả kết luận theo bảng NOT_SPOKEN."""

    def __init__(self):
        self.calls: list[tuple[list[dict], list[dict], list[dict]]] = []

    def __call__(self, lines, before, after):
        self.calls.append((lines, before, after))
        return {l["id"]: (l["text"] not in NOT_SPOKEN, NOT_SPOKEN.get(l["text"], "")) for l in lines}


# ------------------------------------------------------------------ filter_subtitles
def test_filter_splits_and_reports_reasons():
    subs = sample_subs()
    res = filter_subtitles(subs, subs, FakeClassifier())
    assert [s.index for s in res.kept] == [1, 4]
    assert [s.index for s in res.dropped] == [2, 3, 5]
    assert res.reasons == {2: "tiếng cười", 3: "kêu gọi theo dõi", 5: "cảm thán"}
    assert res.warnings == []


def test_filter_cache_avoids_second_call(tmp_path):
    subs, cache = sample_subs(), tmp_path / "ai.json"
    first = FakeClassifier()
    filter_subtitles(subs, subs, first, cache)
    assert len(first.calls) == 1 and cache.is_file()
    second = FakeClassifier()
    res = filter_subtitles(subs, subs, second, cache)
    assert second.calls == []                                   # toàn bộ từ cache
    assert [s.index for s in res.dropped] == [2, 3, 5] and res.reasons[2] == "tiếng cười"
    # đổi model thì cache cũ không áp dụng
    third = FakeClassifier()
    filter_subtitles(subs, subs, third, cache, model="gemini-khac")
    assert len(third.calls) == 1


def test_filter_only_classifies_new_lines(tmp_path):
    subs, cache = sample_subs(), tmp_path / "ai.json"
    filter_subtitles(subs, subs, FakeClassifier(), cache)
    subs2 = [*subs, mk(6, "Cảm ơn mọi người.")]
    fake = FakeClassifier()
    res = filter_subtitles(subs2, subs2, fake, cache)
    assert [[l["id"] for l in lines] for lines, _, _ in fake.calls] == [[6]]
    assert [s.index for s in res.kept] == [1, 4, 6]


def test_filter_error_defaults_to_dub(tmp_path):
    subs, cache = sample_subs(), tmp_path / "ai.json"

    def boom(lines, before, after):
        raise DubFilterError("HTTP 500")

    res = filter_subtitles(subs, subs, boom, cache)
    assert [s.index for s in res.kept] == [1, 2, 3, 4, 5] and res.dropped == []   # lỗi -> lồng hết
    assert res.warnings and "HTTP 500" in res.warnings[0]
    assert not cache.exists() or json.loads(cache.read_text(encoding="utf-8"))["items"] == {}  # lỗi không được cache


def test_filter_missing_verdict_defaults_to_dub():
    subs = sample_subs()
    res = filter_subtitles(subs, subs, lambda lines, b, a: {2: (False, "x")})   # chỉ trả lời id 2
    assert [s.index for s in res.dropped] == [2]
    assert [s.index for s in res.kept] == [1, 3, 4, 5]


def test_filter_stops_after_consecutive_failures(monkeypatch):
    monkeypatch.setattr(dubfilter, "BATCH_SIZE", 1)
    subs, calls = sample_subs(), []

    def boom(lines, before, after):
        calls.append(lines)
        raise DubFilterError("key sai")

    res = filter_subtitles(subs, subs, boom)
    assert len(calls) == dubfilter.MAX_CONSECUTIVE_FAILURES      # không gọi hết 5 lượt
    assert len(res.kept) == 5 and any("dừng" in w for w in res.warnings)


def test_filter_batches_with_context(monkeypatch):
    monkeypatch.setattr(dubfilter, "BATCH_SIZE", 2)
    monkeypatch.setattr(dubfilter, "CONTEXT_LINES", 1)
    subs, fake = sample_subs(), FakeClassifier()
    filter_subtitles(subs, subs, fake)
    ids = lambda xs: [x["id"] for x in xs]
    assert [ids(l) for l, _, _ in fake.calls] == [[1, 2], [3, 4], [5]]
    assert [ids(b) for _, b, _ in fake.calls] == [[], [2], [4]]       # ngữ cảnh trước
    assert [ids(a) for _, _, a in fake.calls] == [[3], [5], []]       # ngữ cảnh sau
    assert fake.calls[0][0][0]["seconds"] == 1.0


def test_filter_cancel():
    import threading
    ev = threading.Event(); ev.set()
    subs = sample_subs()
    with pytest.raises(TtsCancelled):
        filter_subtitles(subs, subs, FakeClassifier(), cancel=ev)


# ------------------------------------------------------------------ gemini_classifier
def _reply(items) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(items)}]}}]}


def test_gemini_classifier_request_and_parse(monkeypatch):
    seen = {}

    def fake_post(url, body, key, timeout=60):
        seen.update(url=url, body=body, key=key)
        return _reply([{"id": 2, "dub": False, "reason": "tiếng cười"}, {"id": 1, "dub": True}])

    monkeypatch.setattr(dubfilter, "_post_json", fake_post)
    out = gemini_classifier("KEY123", "gemini-3.5-flash-lite")(
        [{"id": 1, "text": "Chào", "seconds": 1.0}, {"id": 2, "text": "Ha ha", "seconds": 0.5}], [], [])
    assert out == {2: (False, "tiếng cười"), 1: (True, "")}
    assert "gemini-3.5-flash-lite:generateContent" in seen["url"] and "KEY123" not in seen["url"]   # key không nằm trên URL
    cfg = seen["body"]["generationConfig"]
    assert cfg["responseMimeType"] == "application/json" and cfg["responseSchema"]["type"] == "ARRAY"
    sent = json.loads(seen["body"]["contents"][0]["parts"][0]["text"])
    assert [l["id"] for l in sent["lines"]] == [1, 2]


def test_gemini_classifier_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(dubfilter.time, "sleep", lambda s: None)
    n = {"c": 0}

    def flaky(url, body, key, timeout=60):
        n["c"] += 1
        if n["c"] < 3:
            raise urllib.error.HTTPError(url, 503, "busy", {}, None)
        return _reply([{"id": 1, "dub": True}])

    monkeypatch.setattr(dubfilter, "_post_json", flaky)
    assert gemini_classifier("k")([{"id": 1, "text": "a", "seconds": 1}], [], []) == {1: (True, "")}
    assert n["c"] == 3


def test_gemini_classifier_bad_key_fails_fast(monkeypatch):
    monkeypatch.setattr(dubfilter.time, "sleep", lambda s: pytest.fail("không được thử lại khi 400/403"))
    n = {"c": 0}

    def denied(url, body, key, timeout=60):
        n["c"] += 1
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(dubfilter, "_post_json", denied)
    with pytest.raises(DubFilterError):
        gemini_classifier("k")([{"id": 1, "text": "a", "seconds": 1}], [], [])
    assert n["c"] == 1


def test_gemini_classifier_requires_key():
    with pytest.raises(DubFilterError):
        gemini_classifier("   ")


def test_parse_verdicts_tolerates_junk():
    assert dubfilter._parse_verdicts('[{"id": 1, "dub": true}, {"id": "x", "dub": true}, {"id": 2}, 5]') == {1: (True, "")}
    assert dubfilter._parse_verdicts('{"items": [{"id": 3, "dub": false, "reason": "r"}]}') == {3: (False, "r")}
    with pytest.raises(DubFilterError):
        dubfilter._parse_verdicts("không phải json")


# ------------------------------------------------------------------ prepare_dub_audio
def fake_mp3(seconds: float) -> bytes:
    p = subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                        "-ar", "24000", "-ac", "1", "-f", "mp3", "-"], capture_output=True, check=True)
    return p.stdout


@need_ffmpeg
def test_prepare_dub_audio_skips_ai_dropped_lines(tmp_path):
    doc = SrtpxDocument(subtitles=sample_subs())
    spoken: list[str] = []

    def synth(text, voice):
        spoken.append(text)
        return fake_mp3(0.4)

    video = tmp_path / "v.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=12", "-an", str(video)], check=True)
    mixed, rep, warnings = prepare_dub_audio(
        doc, video, 12.0, "v", synth, tmp_path / "cache", tmp_path, DubOptions(), FFMPEG, FFPROBE,
        classifier=FakeClassifier())
    assert spoken == ["Xin chào các bạn.", "Đi thôi!"]            # chỉ đọc dòng AI giữ lại
    assert rep.placed == 2 and rep.skipped_ai == [2, 3, 5]
    assert rep.ai_reasons[3] == "kêu gọi theo dõi" and "AI lọc bỏ 3" in rep.summary()
    assert mixed is not None and mixed.is_file() and warnings == []


@need_ffmpeg
def test_prepare_dub_audio_include_comments_disables_ai(tmp_path):
    doc = SrtpxDocument(subtitles=sample_subs())
    fake = FakeClassifier()
    video = tmp_path / "v.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=12", "-an", str(video)], check=True)
    _, rep, _ = prepare_dub_audio(
        doc, video, 12.0, "v", lambda t, v: fake_mp3(0.3), tmp_path / "cache", tmp_path,
        DubOptions(include_comments=True), FFMPEG, FFPROBE, classifier=fake)
    assert fake.calls == [] and rep.placed == 5 and rep.skipped_ai == []     # "lồng tất cả" thì không lọc AI


@need_ffmpeg
def test_prepare_dub_audio_without_classifier_unchanged(tmp_path):
    doc = SrtpxDocument(subtitles=sample_subs())
    video = tmp_path / "v.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=12", "-an", str(video)], check=True)
    _, rep, _ = prepare_dub_audio(
        doc, video, 12.0, "v", lambda t, v: fake_mp3(0.3), tmp_path / "cache", tmp_path, DubOptions(), FFMPEG, FFPROBE)
    assert rep.placed == 5 and rep.skipped_ai == []
