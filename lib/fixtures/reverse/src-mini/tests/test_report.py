from tally import report


def test_render_sorts_by_name_and_ends_with_the_total():
    assert report.render({"b": 2, "a": 1}) == "a: 1\nb: 2\ntotal: 3"


def test_total_of_nothing_is_zero():
    assert report.total({}) == 0
