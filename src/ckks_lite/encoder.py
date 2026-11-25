"""CKKS encoder/decoder.

The encoder maps up to ``N / 2`` complex slots to a polynomial in
``R = Z[X]/(X^N + 1)`` using the canonical embedding.  With the
2N-th root of unity ``xi_k = exp((2k + 1) * pi * i / N)`` the slot
vector is placed on the first half of the roots and completed by its
conjugate on the second half, which guarantees real coefficients.

``numpy.fft`` conventions are used:

* encode: ``coeffs = FFT(conj_symmetric_slots) / N * exp(-i*pi*j/N)``
* decode: evaluate at all ``xi_k`` via an inverse FFT.
"""

from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np

from .params import CKKSConfig


class CKKSEncoder:
    """Encode lists of complex numbers into ring elements and back."""

    def __init__(self, config: CKKSConfig) -> None:
        self.config = config
        self.degree = config.degree
        self.slots = config.slots

        j = np.arange(self.degree, dtype=np.float64)
        self._encode_phase = np.exp(-1j * np.pi * j / self.degree)
        self._decode_phase = np.exp(1j * np.pi * j / self.degree)

    def encode(
        self,
        values: Iterable[complex],
        modulus: int,
        scale: float,
    ) -> list[int]:
        """Encode ``values`` as integers modulo ``modulus`` scaled by ``scale``."""
        if modulus < 2:
            raise ValueError("modulus 必须 >= 2")
        if not scale > 0:
            raise ValueError("scale 必须为正数")

        array = np.asarray(values, dtype=np.complex128).reshape(-1)
        if array.size > self.slots:
            raise ValueError(
                f"最多可编码 {self.slots} 个槽位，收到 {array.size} 个值"
            )
        if array.size and not np.all(np.isfinite(array)):
            raise ValueError("values 中不能包含 NaN 或 Inf")

        max_abs = float(np.max(np.abs(array))) if array.size else 0.0
        if max_abs * scale >= modulus / 2:
            raise ValueError(
                "消息幅度超出当前模数可表示的范围："
                f"max|value| * scale = {max_abs * scale:.3g}, "
                f"但 modulus / 2 = {modulus / 2:.3g}；"
                "请减小数值、降低 scale 或使用更大的模数链"
            )
        if max_abs * scale > 2.0 ** 52:
            raise ValueError("消息幅度过大，转换为 float64 会损失精度")

        padded = np.zeros(self.slots, dtype=np.complex128)
        if array.size:
            padded[: array.size] = array
        symmetric = np.concatenate([padded, np.conj(padded[::-1])])

        coeffs = np.fft.fft(symmetric) / self.degree * self._encode_phase
        coeffs = np.real(coeffs)  # conjugate symmetry makes these real
        return [int(round(float(c) * scale)) % modulus for c in coeffs]

    def decode(
        self,
        coefficients: Sequence[int],
        modulus: int,
        scale: float,
    ) -> np.ndarray:
        """Decode a ring element into all ``N / 2`` complex slots."""
        if len(coefficients) != self.degree:
            raise ValueError(
                f"多项式长度必须是 {self.degree}，收到 {len(coefficients)}"
            )
        if modulus < 2:
            raise ValueError("modulus 必须 >= 2")
        if not scale > 0:
            raise ValueError("scale 必须为正数")

        half = modulus // 2
        centered = [c if c <= half else c - modulus for c in coefficients]
        scaled = np.asarray(centered, dtype=np.float64) / scale
        values = np.fft.ifft(scaled * self._decode_phase) * self.degree
        return values[: self.slots]
