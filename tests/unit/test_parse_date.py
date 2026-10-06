import datetime

import pytest

from analyze import parse_date


@pytest.mark.parametrize("raw", ["2024-03-05T10:20:30Z", "2024-03-05T10:20:30+00:00", "2024-03-05T10:20:30+05:30"])
def test_parses_iso_variants_as_naive_wall_clock(raw):
    # Offsets are stripped, not converted: the wall-clock fields are kept.
    assert parse_date(raw) == datetime.datetime(2024, 3, 5, 10, 20, 30)


@pytest.mark.parametrize("raw", [None, "", "not-a-date", "2024-03-05", "05/03/2024"])
def test_unparseable_returns_none(raw):
    assert parse_date(raw) is None
