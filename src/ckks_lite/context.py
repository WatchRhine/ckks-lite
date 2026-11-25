"""High-level CKKS context: keys, encryption, decryption, evaluation.

This is a deliberately small, readable implementation of the leveled
CKKS scheme.  It uses a plain (non-RNS) modulus chain, Python integers
for exact arithmetic, and digit decomposition for relinearization.
"""

from __future__ import annotations

import math
import random
from typing import Iterable, List, Optional

import numpy as np

from .ciphertext import Ciphertext
from .encoder import CKKSEncoder
from .keys import (
    KeySet,
    PublicKey,
    RelinearizationKey,
    SecretKey,
)
from .params import CKKSConfig, PRESETS
from .ring import Polynomial, PolynomialRing


class CKKSContext:
    """Parameters, keys and operations for one CKKS instance.

    A fresh key set is generated in the constructor.  Pass ``seed`` for
    reproducible keys and ciphertexts (useful in tests); this is **not**
    a secure RNG and the parameters are toy-sized.

    Example
    -------
    >>> ctx = CKKSContext(preset="toy", seed=1)
    >>> ct = ctx.encrypt([0.1, -0.2, 0.3])
    >>> (ct + ct).decrypt(ctx.keys.secret_key).real[:3]
    array([ 0.2, -0.4,  0.6])
    """

    DEFAULT_BASE_BITS = 15
    GAUSSIAN_SIGMA = 3.2

    def __init__(
        self,
        preset: str = "small",
        *,
        config: Optional[CKKSConfig] = None,
        seed: Optional[int] = None,
    ) -> None:
        if config is not None:
            if not isinstance(config, CKKSConfig):
                raise TypeError("config 必须是 CKKSConfig")
        else:
            try:
                config = PRESETS[preset]
            except KeyError as exc:
                raise ValueError(
                    f"未知预设 {preset!r}，可选：{', '.join(PRESETS)}"
                ) from exc
        self.config = config
        self.ring = PolynomialRing(config.degree)
        self.encoder = CKKSEncoder(config)
        self._moduli = [
            config.modulus_at(level) for level in range(config.max_level + 1)
        ]
        self._rng = random.Random(seed)
        self.keys = self.keygen(seed=seed)

    # ------------------------------------------------------------------
    # Keys
    # ------------------------------------------------------------------

    def keygen(self, seed: Optional[int] = None) -> KeySet:
        """Generate and install a new key set, returning it.

        Passing ``seed`` restarts the internal RNG, making the generated
        keys deterministic.
        """
        self._rng = random.Random(seed)
        degree = self.config.degree
        q = self._moduli[-1]

        secret = self._sample_ternary(degree)
        a = self._sample_uniform(degree, q)
        e = self._sample_gaussian(degree)
        a_s = self.ring.mul(a, secret, q)
        b = [(ei - ai) % q for ai, ei in zip(a_s, e)]
        public_key = PublicKey(b=b, a=a)

        secret_squared = self.ring.mul(secret, secret, q)
        base_bits = self.DEFAULT_BASE_BITS
        digit_count = (q.bit_length() + base_bits - 1) // base_bits
        factor = 1
        step = (1 << base_bits) % q
        digits = []
        for _ in range(digit_count):
            message = [(x * factor) % q for x in secret_squared]
            digits.append(self._encrypt_poly(message, public_key, q))
            factor = (factor * step) % q

        key_set = KeySet(
            secret_key=SecretKey(secret),
            public_key=public_key,
            relinearization_key=RelinearizationKey(digits, base_bits),
        )
        self.keys = key_set
        return key_set

    def _sample_ternary(self, size: int) -> List[int]:
        rng = self._rng
        return [rng.choice((-1, 0, 1)) for _ in range(size)]

    def _sample_gaussian(self, size: int) -> List[int]:
        rng = self._rng
        sigma = self.GAUSSIAN_SIGMA
        return [int(round(rng.gauss(0.0, sigma))) for _ in range(size)]

    def _sample_uniform(self, size: int, modulus: int) -> List[int]:
        rng = self._rng
        bits = max(1, modulus.bit_length())
        return [rng.getrandbits(bits) % modulus for _ in range(size)]

    def _encrypt_poly(
        self,
        message: Polynomial,
        public_key: PublicKey,
        modulus: int,
    ) -> tuple[Polynomial, Polynomial]:
        degree = self.config.degree
        u = self._sample_ternary(degree)
        e0 = self._sample_gaussian(degree)
        e1 = self._sample_gaussian(degree)
        b_u = self.ring.mul(public_key.b, u, modulus)
        a_u = self.ring.mul(public_key.a, u, modulus)
        c0 = [(x + y + z) % modulus for x, y, z in zip(b_u, e0, message)]
        c1 = [(x + y) % modulus for x, y in zip(a_u, e1)]
        return c0, c1

    # ------------------------------------------------------------------
    # Basic API
    # ------------------------------------------------------------------

    def modulus_at(self, level: int) -> int:
        if not 0 <= level <= self.config.max_level:
            raise ValueError(
                f"level 必须在 0..{self.config.max_level} 之间，收到 {level}"
            )
        return self._moduli[level]

    def encode(self, values: Iterable[complex], *, scale: Optional[float] = None) -> Polynomial:
        """Encode values at the top level (mostly useful for tests)."""
        scale = float(self.config.scale if scale is None else scale)
        return self.encoder.encode(values, self._moduli[-1], scale)

    def decode(
        self,
        coefficients: Iterable[int],
        *,
        level: Optional[int] = None,
        scale: Optional[float] = None,
    ) -> np.ndarray:
        """Decode coefficients at a given level and scale."""
        level = self.config.max_level if level is None else level
        scale = float(self.config.scale if scale is None else scale)
        return self.encoder.decode(list(coefficients), self.modulus_at(level), scale)

    def encrypt(
        self,
        values: Iterable[complex],
        *,
        scale: Optional[float] = None,
        public_key: Optional[PublicKey] = None,
    ) -> Ciphertext:
        """Encrypt values with the public key (fresh encryption at top level)."""
        scale = float(self.config.scale if scale is None else scale)
        if not scale > 0:
            raise ValueError("scale 必须为正数")
        pk = self.keys.public_key if public_key is None else public_key
        if not isinstance(pk, PublicKey):
            raise TypeError("public_key 必须是 PublicKey")

        message = self.encoder.encode(values, self._moduli[-1], scale)
        c0, c1 = self._encrypt_poly(message, pk, self._moduli[-1])
        return Ciphertext(
            context=self,
            c0=c0,
            c1=c1,
            level=self.config.max_level,
            scale=scale,
        )

    def decrypt(
        self,
        ciphertext: Ciphertext,
        *,
        secret_key: Optional[SecretKey] = None,
        real: bool = False,
    ) -> np.ndarray:
        """Decrypt into all slots; ``real=True`` returns only the real part."""
        self._check_ciphertext(ciphertext)
        sk = self.keys.secret_key if secret_key is None else secret_key
        if not isinstance(sk, SecretKey):
            raise TypeError("secret_key 必须是 SecretKey")
        if len(sk.poly) != self.config.degree:
            raise ValueError("secret_key 的环维度与 context 不一致")

        modulus = self.modulus_at(ciphertext.level)
        c1_s = self.ring.mul(ciphertext.c1, sk.poly, modulus)
        message = self.ring.add(ciphertext.c0, c1_s, modulus)
        values = self.encoder.decode(message, modulus, ciphertext.scale)
        return values.real.copy() if real else values

    # ------------------------------------------------------------------
    # Homomorphic operations
    # ------------------------------------------------------------------

    def add(self, left: Ciphertext, right: Ciphertext) -> Ciphertext:
        self._check_pair(left, right)
        modulus = self.modulus_at(left.level)
        return Ciphertext(
            context=self,
            c0=self.ring.add(left.c0, right.c0, modulus),
            c1=self.ring.add(left.c1, right.c1, modulus),
            level=left.level,
            scale=left.scale,
        )

    def sub(self, left: Ciphertext, right: Ciphertext) -> Ciphertext:
        self._check_pair(left, right)
        modulus = self.modulus_at(left.level)
        return Ciphertext(
            context=self,
            c0=self.ring.sub(left.c0, right.c0, modulus),
            c1=self.ring.sub(left.c1, right.c1, modulus),
            level=left.level,
            scale=left.scale,
        )

    def neg(self, ciphertext: Ciphertext) -> Ciphertext:
        self._check_ciphertext(ciphertext)
        modulus = self.modulus_at(ciphertext.level)
        return Ciphertext(
            context=self,
            c0=self.ring.neg(ciphertext.c0, modulus),
            c1=self.ring.neg(ciphertext.c1, modulus),
            level=ciphertext.level,
            scale=ciphertext.scale,
        )

    def add_plain(
        self,
        ciphertext: Ciphertext,
        values: Iterable[complex],
    ) -> Ciphertext:
        """Add a plaintext vector to a ciphertext (no level consumed)."""
        self._check_ciphertext(ciphertext)
        modulus = self.modulus_at(ciphertext.level)
        encoded = self.encoder.encode(values, modulus, ciphertext.scale)
        return Ciphertext(
            context=self,
            c0=self.ring.add(ciphertext.c0, encoded, modulus),
            c1=list(ciphertext.c1),
            level=ciphertext.level,
            scale=ciphertext.scale,
        )

    def multiply_plain(
        self,
        ciphertext: Ciphertext,
        values: Iterable[complex],
    ) -> Ciphertext:
        """Multiply a ciphertext by a plaintext vector (consumes one level)."""
        self._check_ciphertext(ciphertext)
        if ciphertext.level == 0:
            raise ValueError("已达到最低层，无法再执行乘法（没有模数可用于重缩放）")
        modulus = self.modulus_at(ciphertext.level)
        encoded = self.encoder.encode(values, modulus, ciphertext.scale)
        c0 = self.ring.mul(ciphertext.c0, encoded, modulus)
        c1 = self.ring.mul(ciphertext.c1, encoded, modulus)
        product = Ciphertext(
            context=self,
            c0=c0,
            c1=c1,
            level=ciphertext.level,
            scale=ciphertext.scale * ciphertext.scale,
        )
        return self.rescale(product)

    def multiply(
        self,
        left: Ciphertext,
        right: Ciphertext,
        *,
        relinearization_key: Optional[RelinearizationKey] = None,
    ) -> Ciphertext:
        """Ciphertext multiplication with relinearization and rescaling."""
        self._check_pair(left, right)
        if left.level == 0:
            raise ValueError(
                "已达到最低层，无法再执行乘法（没有模数可用于重缩放）；"
                "请使用更大层数的参数或减少乘法次数"
            )
        rk = (
            self.keys.relinearization_key
            if relinearization_key is None
            else relinearization_key
        )
        if not isinstance(rk, RelinearizationKey):
            raise TypeError("relinearization_key 必须是 RelinearizationKey")

        modulus = self.modulus_at(left.level)
        d0 = self.ring.mul(left.c0, right.c0, modulus)
        d1 = self.ring.add(
            self.ring.mul(left.c0, right.c1, modulus),
            self.ring.mul(left.c1, right.c0, modulus),
            modulus,
        )
        d2 = self.ring.mul(left.c1, right.c1, modulus)

        c0, c1 = self._relinearize(d0, d1, d2, modulus, rk)
        product = Ciphertext(
            context=self,
            c0=c0,
            c1=c1,
            level=left.level,
            scale=left.scale * right.scale,
        )
        return self.rescale(product)

    def rescale(self, ciphertext: Ciphertext) -> Ciphertext:
        """Switch to the next smaller modulus and divide the scale."""
        self._check_ciphertext(ciphertext)
        if ciphertext.level == 0:
            raise ValueError("level 0 没有可用于重缩放的模数")
        level = ciphertext.level
        prime = self.config.primes[level]
        next_modulus = self.modulus_at(level - 1)
        half = prime // 2

        def round_div(value: int) -> int:
            return ((value + half) // prime) % next_modulus

        return Ciphertext(
            context=self,
            c0=[round_div(x) for x in ciphertext.c0],
            c1=[round_div(x) for x in ciphertext.c1],
            level=level - 1,
            scale=ciphertext.scale / prime,
        )

    def _relinearize(
        self,
        d0: Polynomial,
        d1: Polynomial,
        d2: Polynomial,
        modulus: int,
        key: RelinearizationKey,
    ) -> tuple[Polynomial, Polynomial]:
        base_bits = key.base_bits
        if base_bits < 1:
            raise ValueError("重线性化密钥的 base_bits 无效")
        needed = (modulus.bit_length() + base_bits - 1) // base_bits
        if needed > len(key.digits):
            raise ValueError(
                "重线性化密钥的位数不足（密钥与模数链不匹配）"
            )
        mask = (1 << base_bits) - 1
        degree = self.config.degree
        acc_b = [0] * degree
        acc_a = [0] * degree
        work = list(d2)
        for index in range(needed):
            digit = [x & mask for x in work]
            if index + 1 < needed:
                work = [x >> base_bits for x in work]
            if not any(digit):
                continue
            b_j = [x % modulus for x in key.digits[index][0]]
            a_j = [x % modulus for x in key.digits[index][1]]
            acc_b = self.ring.add(
                acc_b, self.ring.mul(digit, b_j, modulus), modulus
            )
            acc_a = self.ring.add(
                acc_a, self.ring.mul(digit, a_j, modulus), modulus
            )
        return (
            self.ring.add(d0, acc_b, modulus),
            self.ring.add(d1, acc_a, modulus),
        )

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _check_ciphertext(self, ciphertext: Ciphertext) -> None:
        if not isinstance(ciphertext, Ciphertext):
            raise TypeError("参数必须是 Ciphertext")
        if ciphertext.context is not self:
            raise ValueError("该密文不属于当前 CKKSContext")
        if (
            len(ciphertext.c0) != self.config.degree
            or len(ciphertext.c1) != self.config.degree
        ):
            raise ValueError("密文的环维度与 context 不一致")
        if not 0 <= ciphertext.level <= self.config.max_level:
            raise ValueError("密文 level 越界")
        if not ciphertext.scale > 0:
            raise ValueError("密文 scale 必须为正数")

    def _check_pair(self, left: Ciphertext, right: Ciphertext) -> None:
        self._check_ciphertext(left)
        self._check_ciphertext(right)
        if left.level != right.level:
            raise ValueError(
                "两个密文的层级不同："
                f"{left.level} vs {right.level}；请先统一层级"
            )
        if not math.isclose(left.scale, right.scale, rel_tol=1e-9):
            raise ValueError(
                "两个密文的 scale 不同："
                f"{left.scale:.6g} vs {right.scale:.6g}"
            )

    def __repr__(self) -> str:
        return (
            f"<CKKSContext {self.config.describe()} "
            f"keys={len(self.keys.relinearization_key.digits)} digits>"
        )
