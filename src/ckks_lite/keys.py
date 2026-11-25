"""Key containers for the toy CKKS scheme.

All containers use compact ``repr`` implementations so that printing a
key does not dump thousands of big integers.
"""

from __future__ import annotations

from typing import List, Tuple

from .ring import Polynomial


class SecretKey:
    """Ternary secret polynomial ``s``."""

    __slots__ = ("poly",)

    def __init__(self, poly: Polynomial) -> None:
        self.poly = poly

    def __repr__(self) -> str:
        return f"<SecretKey degree={len(self.poly)}>"


class PublicKey:
    """Public key ``(b, a)`` with ``b + a*s ~= 0 (mod Q)``."""

    __slots__ = ("b", "a")

    def __init__(self, b: Polynomial, a: Polynomial) -> None:
        self.b = b
        self.a = a

    def __repr__(self) -> str:
        return f"<PublicKey degree={len(self.a)}>"


class RelinearizationKey:
    """Digit-decomposed encryption of ``s^2`` used after multiplication.

    ``digits[j]`` encrypts ``T^j * s^2`` under ``s`` with
    ``T = 2**base_bits``.
    """

    __slots__ = ("digits", "base_bits")

    def __init__(
        self,
        digits: List[Tuple[Polynomial, Polynomial]],
        base_bits: int,
    ) -> None:
        self.digits = digits
        self.base_bits = base_bits

    def __repr__(self) -> str:
        return (
            f"<RelinearizationKey digits={len(self.digits)}, "
            f"base_bits={self.base_bits}>"
        )


class KeySet:
    """Secret, public and evaluation keys belonging together."""

    __slots__ = ("secret_key", "public_key", "relinearization_key")

    def __init__(
        self,
        secret_key: SecretKey,
        public_key: PublicKey,
        relinearization_key: RelinearizationKey,
    ) -> None:
        self.secret_key = secret_key
        self.public_key = public_key
        self.relinearization_key = relinearization_key

    def __repr__(self) -> str:
        return (
            "<KeySet secret_key, public_key, relinearization_key "
            f"({len(self.relinearization_key.digits)} digits)>"
        )
