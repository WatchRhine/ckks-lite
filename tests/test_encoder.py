import numpy as np
import pytest

from ckks_lite.encoder import CKKSEncoder
from ckks_lite.params import CKKSConfig


@pytest.fixture()
def encoder():
    config = CKKSConfig(degree=32, scale_bits=20, primes=(2 ** 61 - 1,))
    return CKKSEncoder(config)


def test_roundtrip_real(encoder):
    values = [0.1, -2.5, 3.25, 0.0, 1e-3]
    encoded = encoder.encode(values, 2 ** 61 - 1, 2 ** 20)
    decoded = encoder.decode(encoded, 2 ** 61 - 1, 2 ** 20)
    assert np.max(np.abs(decoded[: len(values)].real - values)) < 1e-5


def test_roundtrip_complex(encoder):
    values = [1 + 2j, -0.5 + 0.25j, 3 - 4j]
    encoded = encoder.encode(values, 2 ** 61 - 1, 2 ** 20)
    decoded = encoder.decode(encoded, 2 ** 61 - 1, 2 ** 20)
    assert np.max(np.abs(decoded[: len(values)] - values)) < 1e-5


def test_slots_padding(encoder):
    encoded = encoder.encode([1.0], 2 ** 61 - 1, 2 ** 20)
    decoded = encoder.decode(encoded, 2 ** 61 - 1, 2 ** 20)
    assert decoded.shape == (encoder.slots,)
    assert abs(decoded[0].real - 1.0) < 1e-5
    assert np.max(np.abs(decoded[1:].real)) < 1e-5


def test_too_many_values(encoder):
    with pytest.raises(ValueError, match="槽位"):
        encoder.encode([0.0] * (encoder.slots + 1), 2 ** 61 - 1, 2 ** 20)


def test_invalid_values(encoder):
    with pytest.raises(ValueError, match="NaN"):
        encoder.encode([0.0, float("nan")], 2 ** 61 - 1, 2 ** 20)
    with pytest.raises(ValueError, match="幅度"):
        encoder.encode([1e30], 2 ** 61 - 1, 2 ** 20)


def test_decode_length_validation(encoder):
    with pytest.raises(ValueError, match="长度"):
        encoder.decode([0] * 8, 2 ** 61 - 1, 2 ** 20)


def test_invalid_scale(encoder):
    with pytest.raises(ValueError, match="scale"):
        encoder.encode([1.0], 2 ** 61 - 1, 0)
    with pytest.raises(ValueError, match="scale"):
        encoder.decode([0] * 32, 2 ** 61 - 1, -1)
