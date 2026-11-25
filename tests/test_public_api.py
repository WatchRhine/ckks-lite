import numpy as np

import ckks_lite as cl


def test_version():
    assert cl.__version__ == "0.1.0"


def test_exports():
    assert cl.CKKSContext is not None
    assert cl.Ciphertext is not None
    assert cl.PRESETS["toy"].degree == 256
    assert cl.BASE_PRIME == (1 << 61) - 1


def test_readme_quickstart():
    ctx = cl.CKKSContext(preset="toy", seed=1)
    ct = ctx.encrypt([0.1, -0.2, 0.3])
    result = ctx.decrypt((ct + ct) * ct).real[:3]
    expected = np.array([0.02, 0.08, 0.18])
    assert np.max(np.abs(result - expected)) < 1e-4
