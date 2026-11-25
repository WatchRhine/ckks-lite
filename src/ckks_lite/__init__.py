"""ckks-lite: a tiny CKKS homomorphic encryption library for learning.

Quick start::

    import numpy as np
    import ckks_lite as cl

    ctx = cl.CKKSContext(preset="toy", seed=1)
    ct = ctx.encrypt([0.1, -0.2, 0.3])
    ct_sum = ct + ct
    ct_sq = ct * ct
    print(ctx.decrypt(ct_sq).real[:3])

On Windows, importing this package automatically runs the bundled
``ckks_env_check.exe`` once per process.  Set the environment variable
``CKKS_LITE_ENV_CHECK=skip`` to disable that behaviour (see
:mod:`ckks_lite.env_check` for details).

This is an educational implementation with toy parameters.  It is **not**
secure and must not be used for real sensitive data.
"""

from __future__ import annotations

from . import env_check as _env_check
from .ciphertext import Ciphertext
from .context import CKKSContext
from .encoder import CKKSEncoder
from .env_check import run_environment_check
from .keys import KeySet, PublicKey, RelinearizationKey, SecretKey
from .params import BASE_PRIME, CHAIN_PRIMES, PRESETS, CKKSConfig

__version__ = "0.1.0"

__all__ = [
    "CKKSConfig",
    "CKKSContext",
    "CKKSEncoder",
    "Ciphertext",
    "KeySet",
    "PublicKey",
    "RelinearizationKey",
    "SecretKey",
    "PRESETS",
    "BASE_PRIME",
    "CHAIN_PRIMES",
    "run_environment_check",
    "__version__",
]

# Required by the project: execute the bundled checker when imported.
# It never raises and can be disabled with CKKS_LITE_ENV_CHECK=skip.
_env_check.auto_run()
