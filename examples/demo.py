"""ckks-lite quick demo.

Run with::

    python examples/demo.py

Importing ``ckks_lite`` also runs the bundled environment checker on
Windows; set ``CKKS_LITE_ENV_CHECK=skip`` to silence it.
"""

import numpy as np

import ckks_lite as cl


def show(label, ciphertext, expected, length):
    decoded = ciphertext.context.decrypt(ciphertext).real[:length]
    error = np.max(np.abs(decoded - expected))
    print(f"{label:<18} {np.array2string(decoded, precision=6, suppress_small=True)}")
    print(f"{'':<18} 最大误差 = {error:.3e}  (level={ciphertext.level}, "
          f"scale=2^{ciphertext.scale_bits:.2f})")


def main():
    np.set_printoptions(precision=6, suppress=True, linewidth=120)

    ctx = cl.CKKSContext(preset="toy", seed=7)
    print("参数:", ctx.config.describe())
    print("密钥:", ctx.keys)
    print()

    x = np.array([0.1, -0.2, 0.3, 0.5, -0.75, 1.0])
    length = x.size

    ct = ctx.encrypt(x)
    show("解密 x", ct, x, length)

    ct_sum = ct + ct
    show("2x (加法)", ct_sum, 2 * x, length)

    ct_sq = ct * ct
    show("x^2 (乘法)", ct_sq, x * x, length)

    # f(x) = 0.5 * (x^2 - x) + 0.25，全部在密文上计算
    linear = ctx.multiply_plain(ct, np.full(length, -1.0))  # 消耗一层
    combined = ct_sq + linear                                # 同为 level 1
    scaled = ctx.multiply_plain(combined, np.full(length, 0.5))  # 消耗一层
    result = ctx.add_plain(scaled, np.full(length, 0.25))
    show("0.5(x^2-x)+0.25", result, 0.5 * (x * x - x) + 0.25, length)

    print()
    print("说明：每次密文乘法都会消耗一层模数。toy 预设最多支持 2 次乘法。")
    print("本实现仅用于教学，参数不是安全的，请勿用于真实敏感数据。")


if __name__ == "__main__":
    main()
