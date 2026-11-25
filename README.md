# ckks-lite

一个用于**学习 CKKS 近似同态加密**的小型 Python 实现，基于 NumPy 和纯 Python 大整数运算。代码尽量保持可读性，适合课程作业、实验和原理演示。

> ⚠️ **仅用于教学与实验。** 参数为玩具规模（N=256/512），随机数不是密码学安全的，没有经过任何安全审计。**禁止用于生产环境或真实敏感数据。**

## 特性

- CKKS 编解码：复数槽位，基于 FFT 的规范嵌入（canonical embedding）
- 密钥生成：私钥、公钥、重线性化密钥（数字分解）
- 加密 / 解密，支持复数消息
- 同态运算：加法、减法、取负、乘法（自动重线性化 + 重缩放）
- 明文运算：`add_plain`、`multiply_plain`
- 模数链与层级管理，密文可查看 `level`、`scale`、`modulus_bits`
- 两种预设参数：`toy` 与 `small`
- **导入包时自动执行随包附带的环境自检 exe**（Windows，可关闭）

## 安装

```bash
git clone https://github.com/WatchRhine/ckks-lite.git
cd ckks-lite
pip install -e ".[dev]"
```

也可以直接把 `src` 加入 `PYTHONPATH` 后运行测试 / 示例。

## 快速开始

```python
import numpy as np
import ckks_lite as cl

ctx = cl.CKKSContext(preset="toy", seed=1)
x = np.array([0.1, -0.2, 0.3, 2.0])

ct = ctx.encrypt(x)
print("解密:", ctx.decrypt(ct).real[:4])

ct_sum = ct + ct                  # 同态加法
ct_sq = ct * ct                   # 同态乘法，自动重线性化 + 重缩放
print("2x  :", ctx.decrypt(ct_sum).real[:4])
print("x^2 :", ctx.decrypt(ct_sq).real[:4])

# 明文常量运算
ct_poly = ctx.add_plain(ctx.multiply_plain(ct_sq, [0.5] * 4), [1.0] * 4)
print("0.5x^2+1 :", ctx.decrypt(ct_poly).real[:4])
```

完整示例见 [`examples/demo.py`](examples/demo.py)。

## 导入时自动执行环境自检

这是本项目的特殊行为：每次在新的 Python 进程中 `import ckks_lite`，都会在 **Windows** 上执行一次随包附带的 `ckks_env_check.exe`：

```
src/ckks_lite/_bin/ckks_env_check.exe
```

exe 默认会扫描本机所有 Python 解释器，并检查 PyPI/镜像连通性，因此首次导入可能多花几秒。它只读取环境信息，不会安装或修改任何东西。

使用以下环境变量控制行为：

| 变量 | 取值 | 说明 |
| --- | --- | --- |
| `CKKS_LITE_ENV_CHECK` | `auto`（默认） | 显示完整的自检报告 |
| | `short` / `quiet` | 静默运行，只打印一行摘要 |
| | `skip` / `off` / `0` | 完全不执行 |
| `CKKS_LITE_ENV_CHECK_TIMEOUT` | 秒数 | 执行超时，默认 30 |
| `CKKS_LITE_ENV_CHECK_ACTIVE` | 内部使用 | 防止递归执行，请勿手动设置 |

注意事项：

- exe 为 **Windows x64** 程序；其他系统会自动跳过，不影响包本身的使用。
- exe 以无参数方式启动，因此会执行它自己的默认流程（包含网络检查）；离线环境下只会给出警告，不会阻止导入。
- 自检结果保存在 `ckks_lite.env_check.last_result`，也可以手动调用 `ckks_lite.run_environment_check(force=True)`。
- 自检源码在 [`tools/env_check.py`](tools/env_check.py)，可审计。

## 参数预设

| 预设 | N | 槽位数 | 层级 `level` | 最多乘法次数 | 默认缩放 |
| --- | --- | --- | --- | --- | --- |
| `toy` | 256 | 128 | 2 | 2 | 2^30 |
| `small`（默认） | 512 | 256 | 3 | 3 | 2^30 |

模数链为 `(q0, q1, ...)`：`q0 = 2^61 - 1`，其余为约 30 位的素数。`q0` 特意远大于 scale，用于在最低层保留消息余量。

实测精度（普通浮点消息，仅供参考）：

- 单次加解密：约 `1e-6`
- 一次乘法（平方）：约 `1e-6 ~ 1e-5`
- 链式乘法误差随层级增加

自定义参数：

```python
from ckks_lite import CKKSConfig, CKKSContext
from ckks_lite.params import BASE_PRIME, CHAIN_PRIMES

config = CKKSConfig(
    degree=256,
    scale_bits=30,
    primes=(BASE_PRIME,) + CHAIN_PRIMES[:2],
)
ctx = CKKSContext(config=config, seed=123)
```

`primes` 必须两两互素；`primes[0]` 是最后一层的模数，不参与重缩放。

## API 概览

```python
ctx = cl.CKKSContext(preset="small", seed=None)

ctx.config                # CKKSConfig
ctx.encoder               # CKKSEncoder
ctx.keys                  # KeySet(secret_key, public_key, relinearization_key)
ctx.keygen(seed=None)     # 重新生成密钥并返回 KeySet

ctx.encrypt(values)       # Ciphertext（顶层）
ctx.decrypt(ct, real=False)
ctx.add(a, b) / ctx.sub(a, b) / ctx.neg(a)
ctx.multiply(a, b)        # 自动重线性化 + 重缩放，消耗一层
ctx.rescale(ct)           # 手动重缩放
ctx.add_plain(ct, values)
ctx.multiply_plain(ct, values)   # 消耗一层
ctx.encode(values) / ctx.decode(coeffs)

ct + ct, ct - ct, ct * ct, -ct
ct.level, ct.scale, ct.modulus_bits, ct.remaining_levels
```

密码学流程说明：`CKKSContext` 为了演示方便在构造时自动生成一套密钥；加密默认使用 `ctx.keys.public_key`，解密默认使用 `ctx.keys.secret_key`，乘法默认使用 `ctx.keys.relinearization_key`。上述方法也接受显式传入的密钥对象。

## 项目结构

```
ckks-lite/
├── src/ckks_lite/
│   ├── __init__.py        # 公共 API + 导入时执行环境自检
│   ├── params.py          # 参数、模数链、预设
│   ├── ring.py            # R_q = Z_q[X]/(X^N+1) 多项式运算
│   ├── encoder.py         # CKKS 编解码（FFT）
│   ├── keys.py            # 密钥容器
│   ├── ciphertext.py      # 密文容器与运算符
│   ├── context.py         # 密钥生成、加解密、同态运算
│   ├── env_check.py       # 导入时调用 exe 的逻辑
│   └── _bin/
│       └── ckks_env_check.exe
├── tools/
│   ├── env_check.py       # 环境自检 exe 的源码
│   └── build_env_check_exe.bat
├── tests/
├── examples/demo.py
├── pyproject.toml
└── README.md
```

## 测试

```bash
pip install -e ".[dev]"
pytest
```

测试通过 `tests/conftest.py` 自动设置 `CKKS_LITE_ENV_CHECK=skip`，因此不会反复启动 exe。

## 已知限制与路线图

- 不支持 Galois 自同构 / 槽位旋转（`rotate`）
- 未实现 RNS/NTT 加速，环乘法为朴素负循环卷积（适合玩具参数）
- 没有密文/密钥序列化
- 参数不是安全的，未做 RLWE 安全评估
- 可考虑：旋转与 Galois 密钥、NTT、序列化、更大的参数预设

## 许可证

[MIT](LICENSE)
