import random

import pytest

from ckks_lite.ring import PolynomialRing


def test_invalid_degrees():
    for degree in (0, 1, 3, 6, 12):
        with pytest.raises(ValueError):
            PolynomialRing(degree)


def test_add_sub_scalar():
    ring = PolynomialRing(8)
    a = [1, 2, 3, 4, 5, 6, 7, 8]
    b = [8, 7, 6, 5, 4, 3, 2, 1]
    q = 17
    assert ring.add(a, b, q) == [9, 9, 9, 9, 9, 9, 9, 9]
    assert ring.sub(a, b, q) == [(x - y) % q for x, y in zip(a, b)]
    assert ring.scalar_mul(a, 3, q) == [(3 * x) % q for x in a]


def test_negacyclic_shift():
    ring = PolynomialRing(8)
    q = 97
    x = [0, 1, 0, 0, 0, 0, 0, 0]  # the polynomial X
    poly = [1, 2, 3, 4, 5, 6, 7, 8]
    expected = [(-poly[-1]) % q] + poly[:-1]
    assert ring.mul(x, poly, q) == expected


def test_matches_naive_convolution():
    rng = random.Random(0)
    ring = PolynomialRing(8)
    q = 97
    a = [rng.randrange(q) for _ in range(8)]
    b = [rng.randrange(q) for _ in range(8)]

    expected = [0] * 8
    for i in range(8):
        for j in range(8):
            degree = i + j
            if degree >= 8:
                degree -= 8
                expected[degree] -= a[i] * b[j]
            else:
                expected[degree] += a[i] * b[j]
    expected = [x % q for x in expected]

    assert ring.mul(a, b, q) == expected


def test_negative_coefficients_normalized():
    ring = PolynomialRing(8)
    q = 17
    a = [-1, -2, 0, 0, 0, 0, 0, 0]
    b = [1, 0, 0, 0, 0, 0, 0, 0]
    result = ring.mul(a, b, q)
    assert all(0 <= x < q for x in result)
    assert result == [16, 15, 0, 0, 0, 0, 0, 0]


def test_length_validation():
    ring = PolynomialRing(8)
    with pytest.raises(ValueError):
        ring.mul([1, 2, 3], [1, 2, 3], 17)
    with pytest.raises(ValueError):
        ring.add([1, 2, 3], [1, 2, 3], 17)
