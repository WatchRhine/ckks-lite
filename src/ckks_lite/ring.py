"""Polynomial arithmetic in ``R_q = Z_q[X] / (X^N + 1)``.

Polynomials are plain Python lists of integer coefficients.  Python's
arbitrary-precision integers let us use a single large modulus without
any RNS decomposition; multiplication uses the straightforward
negacyclic convolution, which is fast enough for the toy parameters.
"""

from __future__ import annotations

from typing import List

Polynomial = List[int]


class PolynomialRing:
    """Negacyclic polynomial ring with degree a power of two."""

    def __init__(self, degree: int) -> None:
        if degree < 2 or (degree & (degree - 1)):
            raise ValueError("degree 必须是 >= 2 的 2 的幂")
        self.degree = degree

    def zero(self) -> Polynomial:
        return [0] * self.degree

    def _check_length(self, poly: Polynomial, name: str) -> None:
        if len(poly) != self.degree:
            raise ValueError(
                f"{name} 的长度必须是 {self.degree}，收到 {len(poly)}"
            )

    def add(self, a: Polynomial, b: Polynomial, modulus: int) -> Polynomial:
        self._check_length(a, "a")
        self._check_length(b, "b")
        return [(x + y) % modulus for x, y in zip(a, b)]

    def sub(self, a: Polynomial, b: Polynomial, modulus: int) -> Polynomial:
        self._check_length(a, "a")
        self._check_length(b, "b")
        return [(x - y) % modulus for x, y in zip(a, b)]

    def neg(self, a: Polynomial, modulus: int) -> Polynomial:
        self._check_length(a, "a")
        return [(-x) % modulus for x in a]

    def scalar_mul(self, a: Polynomial, scalar: int, modulus: int) -> Polynomial:
        self._check_length(a, "a")
        return [(x * scalar) % modulus for x in a]

    def mul(self, a: Polynomial, b: Polynomial, modulus: int) -> Polynomial:
        """Multiply in ``Z_q[X]/(X^N + 1)``.

        Coefficients may be negative on input; the result is normalized
        to ``[0, modulus)``.
        """
        n = self.degree
        self._check_length(a, "a")
        self._check_length(b, "b")

        # Full convolution of length 2n-1.
        acc = [0] * (2 * n - 1)
        for i, ai in enumerate(a):
            if not ai:
                continue
            base = i
            for j, bj in enumerate(b):
                acc[base + j] += ai * bj

        # X^n = -1, so fold degrees >= n back with a negative sign.
        out = [0] * n
        for k in range(n - 1):
            out[k] = (acc[k] - acc[k + n]) % modulus
        out[n - 1] = acc[n - 1] % modulus
        return out
