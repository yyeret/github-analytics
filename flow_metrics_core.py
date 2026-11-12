# Vendored from yyeret/flow-metrics at 87fcc7295ab089fce3c0f00786b69bfc6cd1f4a4. Refresh using scripts/vendor.py upstream.
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Yuval Yeret
"""Flow calculation primitives shared with the JavaScript runtime.

Times in an occupancy or event series must share a comparable type and unit.
Python callers can supply datetime values directly to preserve their ordering.
"""

from bisect import bisect_left, bisect_right
from datetime import timedelta
from itertools import accumulate
from math import ceil, floor, isfinite


def elapsed(start, end, units=1):
    """Return a signed difference. Datetime differences are measured in seconds."""
    difference = end - start
    if isinstance(difference, timedelta):
        difference = difference.total_seconds()
    return difference / units


def _check_order(times):
    if any(current < previous for previous, current in zip(times, times[1:])):
        raise ValueError("sample times must be chronological")


def count_intervals(intervals, samples):
    """Count [start, end) intervals at sorted sample times. None is an open end."""
    _check_order(samples)
    changes = [0] * (len(samples) + 1)
    for start, end in intervals:
        if end is not None and end <= start:
            continue
        changes[bisect_left(samples, start)] += 1
        changes[len(samples) if end is None else bisect_left(samples, end)] -= 1
    return list(accumulate(changes[:-1]))


def count_events_in_windows(events, boundaries):
    """Count events in successive (start, end] windows between sorted boundaries."""
    _check_order(boundaries)
    ordered = sorted(events)
    positions = [bisect_right(ordered, boundary) for boundary in boundaries]
    return [end - start for start, end in zip(positions, positions[1:])]


def percentile(values, probability, *, method="nearest-rank"):
    """Read an empirical quantile with an explicit sample indexing rule."""
    return percentiles(values, [probability], method=method)[0]


def percentiles(values, probabilities, *, method="nearest-rank"):
    """Read several empirical quantiles from the same sorted sample."""
    probabilities = list(probabilities)
    if any(not 0 < probability <= 1 for probability in probabilities):
        raise ValueError("probabilities must be in (0, 1]")
    if method not in ("nearest-rank", "upper-index"):
        raise ValueError("unknown percentile method")
    ordered = sorted(values)
    if any(not isfinite(value) for value in ordered):
        raise ValueError("values must contain finite numbers")
    if not ordered:
        return [None] * len(probabilities)
    if method == "upper-index":
        return [ordered[min(floor(len(ordered) * p), len(ordered) - 1)] for p in probabilities]
    return [ordered[ceil(len(ordered) * p) - 1] for p in probabilities]
