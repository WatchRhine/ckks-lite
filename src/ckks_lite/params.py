"""CKKS parameters and presets.

The modulus chain is ``(q0, q1, ..., qL)`` where ``q0`` is a large prime
(61 bits) and every other prime is about 30 bits, matching the default
scale of ``2**30``.  Keeping ``q0`` much larger than the scale leaves
room for messages at the lowest level after all rescaling steps.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: 2**61 - 1 (Mersenne prime). Used as the base (lowest-level) modulus.
BASE_PRIME = (1 << 61) - 1

#: 30-bit primes used for the rescaling chain. All are distinct primes.
CHAIN_PRIMES = (
    1073741789,
    1073741783,
    1073741741,
    1073741723,
    1073741719,
    1073741717,
)


@dataclass(frozen=True)
class CKKSConfig:
    """Parameters of a toy CKKS instance.

    Parameters
    ----------
    degree:
        Polynomial ring degree ``N``. Must be a power of two, ``>= 8``.
        The number of slots is ``N // 2``.
    scale_bits:
        Bit length of the encoding scale ``Delta = 2**scale_bits``.
    primes:
        Modulus chain. ``primes[0]`` is the lowest-level modulus and is
        never used for rescaling; ``primes[1:]`` should be close to the
        scale so that rescaling keeps the scale roughly constant.
    """

    degree: int = 512
    scale_bits: int = 30
    primes: tuple[int, ...] = (BASE_PRIME,) + CHAIN_PRIMES[:3]

    def __post_init__(self) -> None:
        if self.degree < 8 or (self.degree & (self.degree - 1)):
            raise ValueError("degree 必须是 >= 8 的 2 的幂")
        if self.scale_bits < 2:
            raise ValueError("scale_bits 至少为 2")

        try:
            primes = tuple(int(p) for p in self.primes)
        except (TypeError, ValueError) as exc:
            raise ValueError("primes 必须是整数序列") from exc
        if not primes:
            raise ValueError("primes 不能为空")
        if len(set(primes)) != len(primes):
            raise ValueError("primes 不能重复")
        if any(p < 2 for p in primes):
            raise ValueError("每个模数都必须是大于 1 的整数")
        for i in range(len(primes)):
            for j in range(i + 1, len(primes)):
                if math.gcd(primes[i], primes[j]) != 1:
                    raise ValueError("primes 必须两两互素")
        object.__setattr__(self, "primes", primes)

    @property
    def slots(self) -> int:
        """Number of complex values that fit in one ciphertext."""
        return self.degree // 2

    @property
    def scale(self) -> float:
        return float(2 ** self.scale_bits)

    @property
    def max_level(self) -> int:
        return len(self.primes) - 1

    @property
    def top_modulus(self) -> int:
        return math.prod(self.primes)

    def modulus_at(self, level: int) -> int:
        """Modulus ``Q_level = q0 * q1 * ... * q_level``."""
        if not 0 <= level <= self.max_level:
            raise ValueError(f"level 必须在 0..{self.max_level} 之间，收到 {level}")
        return math.prod(self.primes[: level + 1])

    def describe(self) -> str:
        return (
            f"CKKSConfig(degree={self.degree}, slots={self.slots}, "
            f"level={self.max_level}, scale=2^{self.scale_bits}, "
            f"modulus_bits={self.top_modulus.bit_length()})"
        )


PRESETS = {
    # Fast preset: two multiplication levels, 128 slots.
    "toy": CKKSConfig(
        degree=256,
        scale_bits=30,
        primes=(BASE_PRIME,) + CHAIN_PRIMES[:2],
    ),
    # Default preset: three multiplication levels, 256 slots.
    "small": CKKSConfig(
        degree=512,
        scale_bits=30,
        primes=(BASE_PRIME,) + CHAIN_PRIMES[:3],
    ),
}
