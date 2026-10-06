import pytest

from analyze import get_ranks, spearman_rank_correlation


def test_ranks_without_ties():
    assert get_ranks([30, 10, 20]) == [3.0, 1.0, 2.0]


def test_tied_values_share_the_average_rank():
    assert get_ranks([5, 5, 1, 9]) == [2.5, 2.5, 1.0, 4.0]
    assert get_ranks([7, 7, 7]) == [2.0, 2.0, 2.0]


def test_ranks_of_empty_list():
    assert get_ranks([]) == []


def test_perfect_monotone_correlation():
    assert spearman_rank_correlation([1, 2, 3, 4], [10, 200, 3000, 40000]) == pytest.approx(1.0)


def test_perfect_inverse_correlation():
    assert spearman_rank_correlation([1, 2, 3, 4], [9, 7, 5, 1]) == pytest.approx(-1.0)


def test_constant_series_gives_zero():
    assert spearman_rank_correlation([1, 2, 3], [4, 4, 4]) == 0.0
    assert spearman_rank_correlation([2, 2, 2], [1, 2, 3]) == 0.0


@pytest.mark.parametrize("x,y", [([], []), ([1, 2], [1]), ([1], [1, 2])])
def test_empty_or_mismatched_lengths_give_zero(x, y):
    assert spearman_rank_correlation(x, y) == 0.0


def test_matches_textbook_value():
    # Wikipedia's IQ vs hours-of-TV example: rho = -29/165
    iq = [106, 86, 100, 101, 99, 103, 97, 113, 112, 110]
    tv = [7, 0, 27, 50, 28, 29, 20, 12, 6, 17]
    assert spearman_rank_correlation(iq, tv) == pytest.approx(-29 / 165)
