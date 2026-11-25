"""Ciphertext container and Python operator integration."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .context import CKKSContext


@dataclass(eq=False, repr=False)
class Ciphertext:
    """A ciphertext ``(c0, c1)`` at a given level with a known scale.

    Ciphertexts are bound to the context that created them.  Arithmetic
    operators delegate to the context, which supplies the evaluation
    keys and validates levels and scales.
    """

    context: "CKKSContext"
    c0: List[int]
    c1: List[int]
    level: int
    scale: float

    @property
    def modulus(self) -> int:
        return self.context.modulus_at(self.level)

    @property
    def modulus_bits(self) -> int:
        return self.modulus.bit_length()

    @property
    def scale_bits(self) -> float:
        return math.log2(self.scale)

    @property
    def slots(self) -> int:
        return self.context.config.slots

    @property
    def remaining_levels(self) -> int:
        """How many more ciphertext multiplications this level allows."""
        return self.level

    def copy(self) -> "Ciphertext":
        return Ciphertext(
            self.context, list(self.c0), list(self.c1), self.level, self.scale
        )

    def __add__(self, other):
        if isinstance(other, Ciphertext):
            return self.context.add(self, other)
        return NotImplemented

    def __sub__(self, other):
        if isinstance(other, Ciphertext):
            return self.context.sub(self, other)
        return NotImplemented

    def __mul__(self, other):
        if isinstance(other, Ciphertext):
            return self.context.multiply(self, other)
        return NotImplemented

    def __neg__(self):
        return self.context.neg(self)

    def __repr__(self) -> str:
        return (
            f"<Ciphertext level={self.level} "
            f"scale=2^{self.scale_bits:.2f} "
            f"modulus_bits={self.modulus_bits}>"
        )
