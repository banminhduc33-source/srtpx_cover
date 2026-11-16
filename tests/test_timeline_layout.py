from srtpx_cover.core.timeline_layout import assign_lanes, format_tick, tick_step


def test_non_overlapping_share_lane():
    assert assign_lanes([(0, 1), (1, 2), (2.5, 3)]) == [0, 0, 0]


def test_overlap_goes_to_next_lane():
    assert assign_lanes([(0, 3), (1, 2), (2, 4)]) == [0, 1, 1]


def test_order_preserved_for_unsorted_input():
    assert assign_lanes([(5, 6), (0, 10), (1, 2)]) == [1, 0, 1]


def test_empty():
    assert assign_lanes([]) == []


def test_tick_step_grows_when_zoomed_out():
    assert tick_step(200) < tick_step(20) < tick_step(2)
    assert tick_step(60) * 60 >= 80


def test_format_tick():
    assert format_tick(65, 5) == "1:05"
    assert format_tick(5.5, 0.5) == "0:05.5"
