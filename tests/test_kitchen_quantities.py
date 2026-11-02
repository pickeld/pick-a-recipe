import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kitchen.convert import convert_amount, from_base, to_base  # noqa: E402
from kitchen.quantities import (  # noqa: E402
    Quantity,
    format_amount,
    format_quantity,
    is_scalable,
    parse_quantity,
    scale,
)


@pytest.mark.parametrize(
    "text, value, high",
    [
        ("2", 2, None),
        ("1.5", 1.5, None),
        ("1,5", 1.5, None),
        ("1/2", 0.5, None),
        ("1 1/2", 1.5, None),
        ("1½", 1.5, None),
        ("½", 0.5, None),
        ("2-3", 2, 3),
        ("2 – 3", 2, 3),
        ("2 to 3", 2, 3),
        ("חצי", 0.5, None),
        ("1 וחצי", 1.5, None),
        ("שתיים", 2, None),
        ("כ-200", 200, None),
        ("about 2", 2, None),
        ("a dozen", 12, None),
        ("half a dozen", 6, None),
        ("a", 1, None),
    ],
)
def test_parse_quantity(text, value, high):
    q = parse_quantity(text)
    assert q.value == pytest.approx(value)
    if high is None:
        assert q.max is None
    else:
        assert q.max == pytest.approx(high)


@pytest.mark.parametrize("text", ["", "to taste", "לפי הטעם", "some", None])
def test_parse_quantity_unknown(text):
    assert parse_quantity(text).value is None


def test_scale_keeps_ranges():
    q = scale(Quantity(2, 3), 0.5)
    assert (q.value, q.max) == (1, 1.5)


def test_scale_leaves_unknown_alone():
    assert scale(Quantity(None), 3).value is None


def test_is_scalable():
    assert is_scalable(Quantity(2), "cup")
    assert not is_scalable(Quantity(1), "pinch")
    assert not is_scalable(Quantity(1), "tsp", notes="to taste")
    assert not is_scalable(Quantity(None), "g")


@pytest.mark.parametrize(
    "value, unit, text",
    [
        (0.5, "cup", "½"),
        (1.5, "tbsp", "1½"),
        (0.333, "cup", "⅓"),
        (2, "", "2"),
        (1.98, "piece", "2"),
        (333.3, "g", "335"),
        (12.4, "g", "12"),
        (2.26, "g", "2.3"),
        (1.25, "kg", "1.25"),
    ],
)
def test_format_amount(value, unit, text):
    assert format_amount(value, unit) == text


def test_format_quantity_range():
    assert format_quantity(Quantity(1, 1.5), "cup") == "1–1½"


def test_scale_one_serving_from_three():
    q = scale(parse_quantity("1 1/2"), 1 / 3)
    assert format_quantity(q, "cup") == "½"


def test_convert_to_metric():
    assert convert_amount(1, "cup", "metric") == (240, "ml")
    amount, unit = convert_amount(2, "lb", "metric")
    assert unit == "g" and amount == pytest.approx(907.2)
    assert convert_amount(5, "cups", "metric") == (1.2, "l")
    assert convert_amount(2, "tbsp", "metric") == (2, "tbsp")


def test_convert_to_us():
    amount, unit = convert_amount(500, "g", "us")
    assert unit == "lb" and amount == pytest.approx(1.1, abs=0.01)
    amount, unit = convert_amount(100, "g", "us")
    assert unit == "oz" and amount == pytest.approx(3.53, abs=0.01)
    assert convert_amount(5, "g", "us") == (5, "g")
    assert convert_amount(480, "ml", "us") == (2, "cup")
    assert convert_amount(10, "ml", "us") == (2, "tsp")


def test_convert_leaves_counts_and_original():
    assert convert_amount(3, "piece", "us") == (3, "piece")
    assert convert_amount(1, "cup", "original") == (1, "cup")
    assert convert_amount(2, "קורט", "metric") == (2, "קורט")


def test_to_base_and_back():
    a, fam = to_base(0.5, "kg")
    b, _ = to_base(200, "g")
    assert fam == "mass"
    assert from_base(a + b, fam) == (700, "g")
    c, vol = to_base(1, "cup")
    d, _ = to_base(4, "tbsp")
    assert from_base(c + d, vol) == (300, "ml")
    assert to_base(2, "כפות")[1] == "volume"
    assert to_base(3, "")[1] == "count"
    assert to_base(1, "can")[1] == "unit:can"
