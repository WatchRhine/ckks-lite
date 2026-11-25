import math

import pytest

from ckks_lite.params import BASE_PRIME, CHAIN_PRIMES, PRESETS, CKKSConfig


def test_default_config():
    config = CKKSConfig()
    assert config.degree == 512
    assert config.slots == 256
    assert config.max_level == 3
    assert config.scale == float(2 ** 30)
    assert config.top_modulus == math.prod(config.primes)
    assert config.modulus_at(0) == BASE_PRIME


def test_presets():
    assert set(PRESETS) == {"toy", "small"}
    assert PRESETS["toy"].degree == 256
    assert PRESETS["toy"].max_level == 2
    assert PRESETS["small"].degree == 512
    assert PRESETS["small"].max_level == 3
    for config in PRESETS.values():
        assert config.primes[0] == BASE_PRIME


def test_invalid_degree():
    for degree in (0, 4, 6, 100):
        with pytest.raises(ValueError, match="degree"):
            CKKSConfig(degree=degree)


def test_invalid_scale():
    with pytest.raises(ValueError, match="scale_bits"):
        CKKSConfig(scale_bits=1)


def test_invalid_primes():
    with pytest.raises(ValueError, match="不能为空"):
        CKKSConfig(primes=())
    with pytest.raises(ValueError, match="重复"):
        CKKSConfig(primes=(17, 17))
    with pytest.raises(ValueError, match="互素"):
        CKKSConfig(primes=(9, 15))
    with pytest.raises(ValueError, match="大于 1"):
        CKKSConfig(primes=(1, 17))


def test_modulus_level_bounds():
    config = CKKSConfig()
    with pytest.raises(ValueError):
        config.modulus_at(-1)
    with pytest.raises(ValueError):
        config.modulus_at(config.max_level + 1)


def test_chain_primes_are_distinct_and_coprime():
    assert len(set(CHAIN_PRIMES)) == len(CHAIN_PRIMES)
    for i, prime in enumerate(CHAIN_PRIMES):
        for other in CHAIN_PRIMES[i + 1 :]:
            assert math.gcd(prime, other) == 1
