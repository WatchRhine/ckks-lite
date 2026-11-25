import numpy as np
import pytest

import ckks_lite as cl


@pytest.fixture()
def ctx():
    return cl.CKKSContext(preset="toy", seed=42)


def test_encrypt_decrypt_real(ctx):
    values = np.array([0.1, -0.2, 0.3, 2.0])
    ct = ctx.encrypt(values)
    decoded = ctx.decrypt(ct, real=True)
    assert np.max(np.abs(decoded[: len(values)] - values)) < 1e-4
    assert ct.level == ctx.config.max_level
    assert ct.scale == pytest.approx(ctx.config.scale)
    assert ct.modulus_bits == ctx.modulus_at(ct.level).bit_length()
    assert ct.remaining_levels == ct.level


def test_encrypt_decrypt_complex(ctx):
    values = np.array([1 + 2j, -0.5 + 0.25j, 3 - 4j])
    ct = ctx.encrypt(values)
    decoded = ctx.decrypt(ct)
    assert np.max(np.abs(decoded[: len(values)] - values)) < 1e-4


def test_addition_subtraction_negation(ctx):
    x = np.array([0.25, -1.5, 2.0])
    left = ctx.encrypt(x)
    right = ctx.encrypt(-x)

    added = ctx.decrypt(left + right, real=True)[: len(x)]
    assert np.max(np.abs(added)) < 1e-4

    doubled = ctx.decrypt(left + left, real=True)[: len(x)]
    assert np.max(np.abs(doubled - 2 * x)) < 1e-4

    subtracted = ctx.decrypt(left - right, real=True)[: len(x)]
    assert np.max(np.abs(subtracted - 2 * x)) < 1e-4

    negated = ctx.decrypt(-left, real=True)[: len(x)]
    assert np.max(np.abs(negated + x)) < 1e-4


def test_multiplication_and_levels(ctx):
    x = np.array([0.5, -1.25, 1.5])
    ct = ctx.encrypt(x)
    squared = ct * ct

    assert squared.level == ct.level - 1
    assert squared.remaining_levels == ct.level - 1
    assert squared.scale == pytest.approx(ctx.config.scale, rel=1e-6)
    decoded = ctx.decrypt(squared, real=True)[: len(x)]
    assert np.max(np.abs(decoded - x * x)) < 1e-4

    fourth = squared * squared
    assert fourth.level == 0
    decoded = ctx.decrypt(fourth, real=True)[: len(x)]
    assert np.max(np.abs(decoded - x ** 4)) < 1e-3

    with pytest.raises(ValueError, match="最低层"):
        fourth * fourth


def test_plaintext_operations(ctx):
    x = np.array([0.5, -1.5, 2.0])
    ct = ctx.encrypt(x)

    added = ctx.add_plain(ct, [1.0, 1.0, 1.0])
    assert added.level == ct.level
    assert np.max(np.abs(ctx.decrypt(added, real=True)[:3] - (x + 1))) < 1e-4

    multiplied = ctx.multiply_plain(ct, [2.0, 2.0, 2.0])
    assert multiplied.level == ct.level - 1
    assert np.max(np.abs(ctx.decrypt(multiplied, real=True)[:3] - 2 * x)) < 1e-4


def test_keys_can_be_passed_explicitly(ctx):
    ct = ctx.encrypt([0.5, 1.5], public_key=ctx.keys.public_key)
    decoded = ctx.decrypt(
        ct, secret_key=ctx.keys.secret_key, real=True
    )[:2]
    assert np.max(np.abs(decoded - [0.5, 1.5])) < 1e-4


def test_seeded_reproducibility():
    left = cl.CKKSContext(preset="toy", seed=7)
    right = cl.CKKSContext(preset="toy", seed=7)
    a = left.encrypt([0.1, 0.2])
    b = right.encrypt([0.1, 0.2])
    assert a.c0 == b.c0
    assert a.c1 == b.c1
    assert left.keys.secret_key.poly == right.keys.secret_key.poly


def test_context_mismatch_rejected(ctx):
    other = cl.CKKSContext(preset="toy", seed=1)
    ct = ctx.encrypt([0.1])
    other_ct = other.encrypt([0.1])
    with pytest.raises(ValueError, match="不属于"):
        ctx.add(ct, other_ct)


def test_level_mismatch_rejected(ctx):
    ct = ctx.encrypt([0.1, 0.2])
    squared = ct * ct
    with pytest.raises(ValueError, match="层级"):
        ct + squared


def test_scale_mismatch_rejected(ctx):
    left = ctx.encrypt([0.1, 0.2])
    right = ctx.encrypt([0.1, 0.2], scale=2 ** 29)
    with pytest.raises(ValueError, match="scale"):
        left + right


def test_encode_decode_convenience(ctx):
    values = np.array([0.25 - 0.5j, 1.5])
    encoded = ctx.encode(values)
    decoded = ctx.decode(encoded)
    assert np.max(np.abs(decoded[: len(values)] - values)) < 1e-6


def test_encoder_limits(ctx):
    with pytest.raises(ValueError, match="槽位"):
        ctx.encrypt(np.zeros(ctx.config.slots + 1))


def test_copy_is_independent(ctx):
    ct = ctx.encrypt([0.1])
    duplicate = ct.copy()
    assert duplicate.c0 is not ct.c0
    assert duplicate.c0 == ct.c0
    assert duplicate.level == ct.level
