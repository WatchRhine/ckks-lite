#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CKKS 开发环境自检工具（可打包为独立 exe）。

检查内容：
  1. 系统信息、磁盘/内存、常见构建工具（git、cmake、MSVC 等）；
  2. 自动发现本机所有 Python 解释器（当前激活的 venv/conda、PATH、py 启动器、
     注册表、常见安装目录），逐个运行只读探测脚本；
  3. 每个解释器的版本、位数、pip、venv 支持、CKKS 相关包安装情况；
  4. PyPI 官方源与国内镜像的连通性；
  5. 汇总为结论与可执行的下一步建议，可另存文本 / JSON 报告。

本工具只读取信息，不会修改系统，也不会安装任何包。
"""

from __future__ import annotations

import argparse
import ctypes
import glob
import json
import locale
import os
import platform
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import traceback
import unicodedata
import urllib.request
from datetime import datetime

TOOL_ID = "ckks-env-check"
TOOL_VERSION = "0.1.0"

MIN_PY = (3, 9)
MAX_PROBES = 6
MARKER = "@@CKKS_ENV@@"

IS_WIN = os.name == "nt"
FROZEN = bool(getattr(sys, "frozen", False))
# 以当前工作目录作为“项目目录”：双击 exe 时通常就是 exe 所在目录；
# 被 Python 包自动调用时则是用户当前的项目目录，信息更有意义。
BASE_DIR = os.getcwd()
CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0

# (发行包名, 说明, 最低版本, 类别)
PACKAGE_SPECS = [
    ("numpy", "数值运算（必需）", (1, 24, 0), "required"),
    ("scipy", "科学计算（可选）", None, "optional"),
    ("tenseal", "现成 CKKS 库（可选）", None, "he"),
    ("openfhe", "OpenFHE 绑定（可选）", None, "he"),
    ("concrete-python", "TFHE 库（非 CKKS）", None, "he"),
    ("Pyfhel", "SEAL 封装（可选）", None, "he"),
    ("phe", "Paillier 等（非 CKKS）", None, "he"),
    ("pybind11", "C++ 绑定（可选）", None, "dev"),
    ("setuptools", "打包基础", (61, 0, 0), "dev"),
    ("wheel", "构建 wheel", None, "dev"),
    ("build", "python -m build", None, "dev"),
    ("pytest", "测试框架", None, "dev"),
]

TOOL_SPECS = [
    ("git", "版本管理与依赖获取"),
    ("cmake", "编译 OpenFHE / SEAL 等 C++ 库"),
    ("ninja", "加速 C++ 构建"),
    ("uv", "高速 Python 包管理器"),
    ("conda", "Conda 环境管理"),
]

ENDPOINTS = [
    ("PyPI 官方源", "https://pypi.org/simple/pip/"),
    ("清华镜像", "https://pypi.tuna.tsinghua.edu.cn/simple/pip/"),
]

LABELS = {"OK": "[ OK ]", "WARN": "[WARN]", "FAIL": "[FAIL]", "INFO": "[INFO]"}
COLORS = {
    "OK": "\x1b[32m",
    "WARN": "\x1b[33m",
    "FAIL": "\x1b[31m",
    "INFO": "\x1b[36m",
}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def version_tuple(value) -> tuple:
    """把 '1.24.0rc1' 这类版本号转成可比较的 (1, 24, 0)。"""
    if not value:
        return (0, 0, 0)
    nums = re.findall(r"\d+", str(value))
    if not nums:
        return (0, 0, 0)
    parts = [int(x) for x in nums[:3]]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def fmt_version(ver) -> str:
    return ".".join(str(x) for x in ver)


def display_width(text: str) -> int:
    """估算终端显示宽度（中日韩字符按 2 列计）。"""
    width = 0
    for ch in text:
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


def truncate(text: str, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def decode_output(data) -> str:
    """解码外部命令输出（where.exe / py 启动器等），兼容中文路径。"""
    if isinstance(data, str):
        return data
    encodings = [locale.getpreferredencoding(False), "utf-8", "mbcs"]
    for enc in encodings:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace")


def human_gb(size) -> str:
    return f"{size / (1024 ** 3):.1f} GB"


def _prepare_console() -> None:
    """让中文在控制台和重定向输出中都能正确显示。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if not stream or not hasattr(stream, "reconfigure"):
                continue
            enc = (stream.encoding or "").lower()
            if enc not in ("utf-8", "utf8"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if IS_WIN:
        try:
            ctypes.windll.kernel32.SetConsoleTitleW("CKKS 开发环境自检")
        except Exception:
            pass


def stdin_is_console() -> bool:
    """Return True only when stdin is an interactive Windows console.

    ``sys.stdin.isatty()`` is unreliable here: on Windows, NUL (used for
    ``stdin=subprocess.DEVNULL``) can report as a terminal.  Checking
    ``GetConsoleMode`` on the actual standard input handle is precise.
    """
    if not IS_WIN:
        try:
            return sys.stdin is not None and sys.stdin.isatty()
        except Exception:
            return False
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        return bool(kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    except Exception:
        return False


def enable_ansi() -> bool:
    """在 Windows 控制台上启用 ANSI 颜色；管道/重定向时返回 False。"""
    try:
        if not sys.stdout or not sys.stdout.isatty():
            return False
    except Exception:
        return False
    if not IS_WIN:
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------


class Reporter:
    def __init__(self, color: bool):
        self.color = color
        self.lines: list[str] = []

    def line(self, text: str = "") -> None:
        print(text)
        self.lines.append(text)

    def heading(self, text: str) -> None:
        if self.color:
            print("\x1b[1;36m" + text + "\x1b[0m")
        else:
            print(text)
        self.lines.append(text)

    def status(
        self,
        code: str,
        label: str,
        detail: str = "",
        width: int = 12,
        indent: int = 2,
    ) -> None:
        label_text = LABELS.get(code, "[INFO]")
        prefix = " " * indent
        plain = f"{prefix}{label_text} {pad(label, width)} {detail}".rstrip()
        self.lines.append(plain)
        if self.color:
            colored = f"{prefix}{COLORS.get(code, '')}{label_text}\x1b[0m "
            colored += pad(label, width)
            if detail:
                colored += " " + detail
            print(colored.rstrip())
        else:
            print(plain)

    def note(self, text: str = "", indent: int = 22) -> None:
        line = (" " * indent) + text
        line = line.rstrip()
        print(line)
        self.lines.append(line)


# ---------------------------------------------------------------------------
# 系统与构建工具
# ---------------------------------------------------------------------------


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def memory_info():
    if IS_WIN:
        try:
            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return status.ullTotalPhys, status.ullAvailPhys
        except Exception:
            pass
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        return total, None
    except Exception:
        return None, None


def cpu_name() -> str:
    if IS_WIN:
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                return " ".join(str(value).split())
        except Exception:
            pass
    return platform.processor() or platform.machine() or "未知"


def os_label() -> str:
    if IS_WIN:
        try:
            ver = sys.getwindowsversion()
            if ver.major == 10 and ver.build >= 22000:
                name = "Windows 11"
            elif ver.major == 10:
                name = "Windows 10"
            elif ver.major == 6 and ver.minor == 3:
                name = "Windows 8.1"
            else:
                name = f"Windows {ver.major}.{ver.minor}"
            return f"{name}（内部版本 {ver.build}）"
        except Exception:
            pass
    return platform.platform()


def collect_system() -> dict:
    total_mem, avail_mem = memory_info()
    disk = None
    try:
        usage = shutil.disk_usage(BASE_DIR)
        drive = os.path.splitdrive(BASE_DIR)[0]
        disk = {
            "path": (drive + "\\") if (IS_WIN and drive) else BASE_DIR,
            "total": usage.total,
            "free": usage.free,
        }
    except OSError:
        pass
    return {
        "os": os_label(),
        "machine": platform.machine(),
        "cpu": cpu_name(),
        "cpu_count": os.cpu_count(),
        "mem_total": total_mem,
        "mem_avail": avail_mem,
        "disk": disk,
        "base_dir": BASE_DIR,
    }


def render_system(rep: Reporter, info: dict) -> int:
    """输出系统信息，返回警告条数。"""
    warns = 0

    machine = f"，{info['machine']}" if info.get("machine") else ""
    rep.status("OK", "操作系统", info["os"] + machine)

    cpu = info.get("cpu") or "未知"
    if info.get("cpu_count"):
        cpu += f"，{info['cpu_count']} 个逻辑核心"
    rep.status("OK", "处理器", cpu)

    total = info.get("mem_total")
    if total:
        detail = human_gb(total)
        if info.get("mem_avail"):
            detail += f"（当前可用 {human_gb(info['mem_avail'])}）"
        if total < 8 * 1024 ** 3:
            rep.status("WARN", "内存", detail + "；CKKS 参数较大时可能吃紧")
            warns += 1
        else:
            rep.status("OK", "内存", detail)

    disk = info.get("disk")
    if disk:
        detail = f"可用 {human_gb(disk['free'])} / 共 {human_gb(disk['total'])}（{disk['path']}）"
        if disk["free"] < 2 * 1024 ** 3:
            rep.status("WARN", "磁盘", detail + "；空间偏低")
            warns += 1
        else:
            rep.status("OK", "磁盘", detail)

    base_dir = info.get("base_dir") or BASE_DIR
    try:
        ascii_path = base_dir.isascii()
    except Exception:
        ascii_path = all(ord(ch) < 128 for ch in str(base_dir))
    if ascii_path:
        rep.status("OK", "项目目录", base_dir)
    else:
        rep.status("WARN", "项目目录", f"{base_dir}（含非 ASCII 字符，部分 C/C++ 工具链可能出错）")
        warns += 1

    return warns


def find_msvc() -> dict:
    cl = shutil.which("cl")
    if cl:
        return {"status": "found", "path": cl}
    if not IS_WIN:
        return {"status": "missing", "path": None}
    vswhere = os.path.join(
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        "Microsoft Visual Studio",
        "Installer",
        "vswhere.exe",
    )
    if os.path.isfile(vswhere):
        try:
            result = subprocess.run(
                [
                    vswhere,
                    "-latest",
                    "-products",
                    "*",
                    "-requires",
                    "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                    "-property",
                    "installationPath",
                ],
                capture_output=True,
                timeout=15,
                creationflags=CREATE_NO_WINDOW,
            )
            path = decode_output(result.stdout).strip()
            if path:
                return {"status": "installed", "path": path}
        except Exception:
            pass
    return {"status": "missing", "path": None}


def collect_tools() -> dict:
    tools = {}
    for name, purpose in TOOL_SPECS:
        tools[name] = {"path": shutil.which(name), "purpose": purpose}
    tools["msvc"] = find_msvc()
    return tools


def render_tools(rep: Reporter, tools: dict) -> None:
    for name, purpose in TOOL_SPECS:
        path = tools.get(name, {}).get("path")
        if path:
            rep.status("OK", name, path)
        else:
            rep.status("INFO", name, f"未检测到（{purpose}）")

    msvc = tools.get("msvc") or {"status": "missing", "path": None}
    if msvc["status"] == "found":
        rep.status("OK", "MSVC", msvc["path"])
    elif msvc["status"] == "installed":
        rep.status(
            "INFO",
            "MSVC",
            f"已安装 Visual Studio（{msvc['path']}），但 cl.exe 不在 PATH；"
            "需要时请使用 “x64 Native Tools Command Prompt”",
        )
    else:
        rep.status("INFO", "MSVC", "未检测到（仅编译 C++ 同态库如 OpenFHE/SEAL 时需要）")


# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------


def check_endpoint(name: str, url: str, timeout: float = 4.0) -> dict:
    started = time.time()
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": f"{TOOL_ID}/{TOOL_VERSION}"}
        )
        context = ssl.create_default_context()
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            response.read(1)
            status = getattr(response, "status", 200)
        ok = 200 <= status < 400
        detail = f"HTTP {status}，用时 {time.time() - started:.2f}s"
    except Exception as exc:  # 网络错误类型很多，统一展示
        ok = False
        detail = truncate(f"{type(exc).__name__}: {exc}", 120)
    return {"name": name, "url": url, "ok": ok, "detail": detail}


def render_network(rep: Reporter, skip: bool):
    """输出网络检查结果，返回 (数据, 警告数)。"""
    if skip:
        rep.status("INFO", "网络检查", "已跳过（--no-network）")
        return {
            "skipped": True,
            "endpoints": [],
            "official_ok": None,
            "mirror_ok": None,
        }, 0

    warns = 0
    results = []
    for index, (name, url) in enumerate(ENDPOINTS):
        result = check_endpoint(name, url)
        results.append(result)
        if result["ok"]:
            rep.status("OK", name, result["detail"])
        elif index == 0:
            rep.status("WARN", name, f"不可达 - {result['detail']}")
            warns += 1
        else:
            rep.status("INFO", name, f"不可达（通常无影响） - {result['detail']}")

    official_ok = results[0]["ok"]
    mirror_ok = any(r["ok"] for r in results[1:])
    if not any(r["ok"] for r in results):
        rep.note("所有包源均不可达，安装依赖可能失败；请检查代理、防火墙或 DNS。")
    elif not official_ok:
        rep.note("默认 PyPI 不可达；安装依赖时可改用镜像（见下方建议）。")

    return {
        "skipped": False,
        "endpoints": results,
        "official_ok": official_ok,
        "mirror_ok": mirror_ok,
    }, warns


# ---------------------------------------------------------------------------
# Python 解释器探测
# ---------------------------------------------------------------------------

PROBE_TEMPLATE = r'''# -*- coding: utf-8 -*-
import json, os, sys, sysconfig, importlib.util

DISTS = @@DISTS@@


def dist_version(name):
    try:
        import importlib.metadata as metadata
        return metadata.version(name)
    except Exception:
        return None


out = {}
out["version"] = "%d.%d.%d" % sys.version_info[:3]
out["version_raw"] = sys.version.split()[0]
out["implementation"] = sys.implementation.name
out["bits"] = 64 if sys.maxsize > 2 ** 32 else 32
out["executable"] = sys.executable
out["prefix"] = sys.prefix
out["base_prefix"] = sys.base_prefix
out["in_venv"] = os.path.normcase(sys.prefix) != os.path.normcase(sys.base_prefix)
out["virtual_env"] = os.environ.get("VIRTUAL_ENV", "")
out["conda_prefix"] = os.environ.get("CONDA_PREFIX", "")
out["conda_env"] = os.environ.get("CONDA_DEFAULT_ENV", "")
out["pythonpath"] = os.environ.get("PYTHONPATH", "")
out["platform"] = sysconfig.get_platform()
out["pip_index_url"] = os.environ.get("PIP_INDEX_URL", "")
out["packages"] = {}
for dist in DISTS:
    out["packages"][dist] = dist_version(dist)
out["pip"] = dist_version("pip")
out["has_venv_module"] = importlib.util.find_spec("venv") is not None
out["has_ensurepip"] = importlib.util.find_spec("ensurepip") is not None

# numpy 是 CKKS 开发的核心依赖：不仅看元数据，还实际导入并做一次小矩阵乘法。
out["numpy_check"] = None
if out["packages"].get("numpy"):
    try:
        import numpy
        out["numpy_check"] = {
            "ok": True,
            "version": getattr(numpy, "__version__", ""),
            "self_test": False,
        }
        array = numpy.arange(6).reshape(2, 3)
        _ = array @ array.T
        out["numpy_check"]["self_test"] = True
    except Exception as exc:
        out["numpy_check"] = {
            "ok": False,
            "error": ("%s: %s" % (type(exc).__name__, str(exc)[:160])).replace("\n", " "),
        }

pip_config = {}
try:
    import configparser
    for path in (
        os.path.join(os.environ.get("APPDATA", ""), "pip", "pip.ini"),
        os.path.join(os.environ.get("PROGRAMDATA", ""), "pip", "pip.ini"),
    ):
        if path and os.path.isfile(path):
            parser = configparser.ConfigParser()
            try:
                parser.read(path, encoding="utf-8")
                if parser.has_section("global"):
                    for key in ("index-url", "extra-index-url", "trusted-host"):
                        if parser.has_option("global", key):
                            pip_config[key] = parser.get("global", key)
            except Exception:
                pass
except Exception:
    pass
out["pip_config"] = pip_config

print("@@CKKS_ENV@@" + json.dumps(out, ensure_ascii=False))
'''

PROBE_CODE = PROBE_TEMPLATE.replace(
    "@@DISTS@@", json.dumps([spec[0] for spec in PACKAGE_SPECS])
)


def run_probe(exe: str):
    """在指定解释器中运行只读探测脚本，返回 (数据, 错误信息)。"""
    tmp_dir = tempfile.mkdtemp(prefix="ckks_env_check_")
    probe_path = os.path.join(tmp_dir, "env_probe.py")
    try:
        with open(probe_path, "w", encoding="utf-8") as handle:
            handle.write(PROBE_CODE)
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            result = subprocess.run(
                [exe, "-X", "utf8", probe_path],
                cwd=tmp_dir,
                capture_output=True,
                timeout=60,
                env=env,
                creationflags=CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, truncate(f"{type(exc).__name__}: {exc}", 120)

        if result.returncode == 0:
            text = result.stdout.decode("utf-8", "replace")
            for line in reversed(text.splitlines()):
                if line.startswith(MARKER):
                    try:
                        return json.loads(line[len(MARKER):]), None
                    except ValueError:
                        break

        stderr_text = result.stderr.decode("utf-8", "replace").strip()
        message = (
            stderr_text.splitlines()[-1]
            if stderr_text
            else f"探测脚本退出码 {result.returncode}"
        )

        # 旧版本 Python（< 3.8）没有 importlib.metadata，退回最小探测。
        minimal = probe_minimal(exe)
        if minimal:
            minimal["minimal"] = True
            minimal["probe_note"] = truncate(message, 160)
            return minimal, truncate(message, 160)
        return None, truncate(message, 160)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def probe_minimal(exe: str):
    """兜底探测：只取版本与位数，用于不兼容的旧解释器。"""
    code = (
        "import sys;"
        "print('%d.%d.%d|%d' % (sys.version_info[0], sys.version_info[1], "
        "sys.version_info[2], 64 if sys.maxsize > 2**32 else 32))"
    )
    tmp_dir = tempfile.mkdtemp(prefix="ckks_env_min_")
    try:
        result = subprocess.run(
            [exe, "-I", "-c", code],
            cwd=tmp_dir,
            capture_output=True,
            timeout=15,
            creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            return None
        text = result.stdout.decode("ascii", "replace").strip()
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if not lines:
            return None
        version, bits = lines[-1].split("|")
        return {
            "version": version,
            "version_raw": version,
            "implementation": "cpython",
            "bits": int(bits),
            "executable": exe,
            "prefix": "",
            "base_prefix": "",
            "in_venv": False,
            "virtual_env": "",
            "conda_prefix": "",
            "conda_env": "",
            "pythonpath": "",
            "platform": "",
            "pip_index_url": "",
            "packages": {},
            "pip": None,
            "has_venv_module": False,
            "has_ensurepip": False,
            "pip_config": {},
        }
    except Exception:
        return None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def describe_env(probe: dict):
    """判断解释器所处的环境：venv / conda / 系统。"""
    if probe.get("in_venv"):
        return "venv", probe.get("prefix", "")
    conda_prefix = probe.get("conda_prefix") or ""
    if conda_prefix:
        norm = os.path.normcase
        if norm(conda_prefix) in (
            norm(probe.get("prefix") or ""),
            norm(probe.get("base_prefix") or ""),
        ):
            name = probe.get("conda_env") or os.path.basename(conda_prefix.rstrip("\\/"))
            return "conda", name or "base"
    return "system", ""


def path_pythons():
    """从 PATH 中找出所有真实的 python.exe（跳过应用商店占位别名）。"""
    found = []
    names = ["python", "python3"] if IS_WIN else ["python3", "python"]
    if IS_WIN:
        for name in names:
            try:
                result = subprocess.run(
                    ["where.exe", name],
                    capture_output=True,
                    timeout=10,
                    creationflags=CREATE_NO_WINDOW,
                )
                for line in decode_output(result.stdout).splitlines():
                    line = line.strip()
                    if line and os.path.isfile(line):
                        found.append(line)
            except Exception:
                pass
    for name in names:
        candidate = shutil.which(name)
        if candidate:
            found.append(candidate)
    real = []
    for path in found:
        lowered = path.lower()
        if "\\windowsapps\\" in lowered or "/windowsapps/" in lowered:
            continue  # 微软商店的占位程序，运行会弹商店
        real.append(path)
    return real


def py_launcher_pythons():
    """通过 py 启动器枚举已注册的解释器。"""
    if not IS_WIN:
        return []
    launcher = shutil.which("py")
    if not launcher:
        return []
    try:
        result = subprocess.run(
            [launcher, "-0p"],
            capture_output=True,
            timeout=15,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        return []
    paths = []
    for line in decode_output(result.stdout).splitlines():
        match = re.match(r"\s*-V:(\S+)\s+\*?\s*(.+?)\s*$", line)
        if not match:
            continue
        candidate = match.group(2).strip().strip('"')
        if candidate.lower().endswith(".exe") and os.path.isfile(candidate):
            paths.append(candidate)
    return paths


def registry_pythons():
    """从注册表 SOFTWARE\\Python\\PythonCore 枚举解释器。"""
    if not IS_WIN:
        return []
    try:
        import winreg
    except Exception:
        return []
    found = []
    locations = [
        (winreg.HKEY_CURRENT_USER, winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_CURRENT_USER, winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
    ]
    for root, view in locations:
        try:
            parent = winreg.OpenKey(
                root, r"SOFTWARE\Python\PythonCore", 0, winreg.KEY_READ | view
            )
        except OSError:
            continue
        with parent:
            try:
                subkey_count = winreg.QueryInfoKey(parent)[0]
            except OSError:
                continue
            for index in range(subkey_count):
                try:
                    version = winreg.EnumKey(parent, index)
                    with winreg.OpenKey(
                        parent,
                        version + r"\InstallPath",
                        0,
                        winreg.KEY_READ | view,
                    ) as key:
                        exe = None
                        try:
                            exe = winreg.QueryValueEx(key, "ExecutablePath")[0]
                        except OSError:
                            pass
                        if not exe:
                            try:
                                install = winreg.QueryValueEx(key, "")[0]
                                exe = os.path.join(install, "python.exe")
                            except OSError:
                                pass
                        if exe:
                            found.append(exe)
                except OSError:
                    continue
    return found


def common_pythons():
    """在常见安装目录中查找 python.exe。"""
    if not IS_WIN:
        return []
    patterns = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python*\python.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\Python*\python.exe"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\Python*\python.exe"),
        r"C:\Python*\python.exe",
        os.path.expandvars(r"%USERPROFILE%\anaconda3\python.exe"),
        os.path.expandvars(r"%USERPROFILE%\miniconda3\python.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\anaconda3\python.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\miniconda3\python.exe"),
        os.path.expandvars(r"%USERPROFILE%\scoop\apps\python\current\python.exe"),
    ]
    found = []
    for pattern in patterns:
        if "%" in pattern:  # 环境变量不存在时 expandvars 会原样保留
            continue
        found.extend(glob.glob(pattern))
    return found


def discover_interpreters(explicit):
    """汇总所有来源的 Python 解释器，按优先级去重排序。"""
    entries = {}
    order = []

    def add(path, source):
        if not path:
            return
        path = str(path).strip().strip('"').strip("'")
        if not path:
            return
        path = os.path.expandvars(path)
        if os.path.isdir(path):
            path = os.path.join(path, "python.exe" if IS_WIN else "python")
        try:
            path = os.path.abspath(path)
        except OSError:
            return
        if not os.path.isfile(path):
            return
        if IS_WIN and "\\windowsapps\\" in path.lower():
            return
        try:
            key = os.path.normcase(os.path.realpath(path))
        except OSError:
            key = os.path.normcase(path)
        if key not in entries:
            entries[key] = {"exe": path, "sources": []}
            order.append(key)
        if source not in entries[key]["sources"]:
            entries[key]["sources"].append(source)

    if explicit:
        for path in explicit:
            add(path, "命令行指定")
        return [entries[key] for key in order]

    venv = os.environ.get("VIRTUAL_ENV")
    if venv:
        add(
            os.path.join(venv, "Scripts", "python.exe")
            if IS_WIN
            else os.path.join(venv, "bin", "python"),
            "当前激活的 venv",
        )
    conda = os.environ.get("CONDA_PREFIX")
    if conda:
        add(
            os.path.join(conda, "python.exe")
            if IS_WIN
            else os.path.join(conda, "bin", "python"),
            "当前激活的 conda 环境",
        )
    for path in path_pythons():
        add(path, "PATH")
    for path in py_launcher_pythons():
        add(path, "py 启动器")
    for path in registry_pythons():
        add(path, "注册表")
    for path in common_pythons():
        add(path, "常见安装目录")
    return [entries[key] for key in order]


# ---------------------------------------------------------------------------
# 单个解释器检查
# ---------------------------------------------------------------------------


def render_interpreter(rep: Reporter, index: int, entry: dict) -> dict:
    exe = entry["exe"]
    stats = {
        "exe": exe,
        "sources": entry["sources"],
        "probe": None,
        "err": None,
        "usable": False,
        "warns": 0,
        "fails": 0,
        "numpy": None,
        "pip": None,
        "venv": False,
        "he": [],
    }

    rep.line()
    rep.heading(f"  解释器 #{index}")
    rep.note(exe, indent=6)
    rep.note("来源：" + "、".join(entry["sources"]), indent=6)

    probe, err = run_probe(exe)
    stats["probe"], stats["err"] = probe, err
    if probe is None:
        rep.status("FAIL", "运行", f"无法执行探测脚本：{err}", indent=6)
        stats["fails"] += 1
        return stats

    version = str(probe.get("version") or "未知")
    bits = probe.get("bits") or 0
    ver = version_tuple(version)

    if ver < MIN_PY:
        rep.status(
            "FAIL",
            "版本",
            f"{version}（需要 >= {MIN_PY[0]}.{MIN_PY[1]}）",
            indent=6,
        )
        stats["fails"] += 1
    else:
        rep.status("OK", "版本", version, indent=6)

    if bits == 64:
        platform_text = probe.get("platform") or "64 位"
        rep.status("OK", "架构", f"64 位（{platform_text}）", indent=6)
    else:
        rep.status(
            "FAIL",
            "架构",
            f"{bits or '未知'} 位；同态加密库通常只提供 64 位版本",
            indent=6,
        )
        stats["fails"] += 1

    env_kind, env_detail = describe_env(probe)
    if env_kind == "venv":
        rep.status("OK", "环境", f"虚拟环境（{probe.get('prefix') or env_detail}）", indent=6)
        stats["venv"] = True
    elif env_kind == "conda":
        rep.status("OK", "环境", f"conda 环境（{env_detail}）", indent=6)
        stats["venv"] = True
    else:
        rep.status("WARN", "环境", "系统级 Python；建议为项目创建独立虚拟环境", indent=6)
        stats["warns"] += 1

    pip_version = probe.get("pip")
    stats["pip"] = pip_version
    if probe.get("minimal"):
        rep.status("INFO", "pip", "未能获取（探测脚本仅返回基础信息）", indent=6)
    elif pip_version:
        rep.status("OK", "pip", str(pip_version), indent=6)
    elif probe.get("has_ensurepip"):
        rep.status("WARN", "pip", "未安装；可运行 “python -m ensurepip” 修复", indent=6)
        stats["warns"] += 1
    else:
        rep.status("FAIL", "pip", "未安装且缺少 ensurepip；建议修复或重装 Python", indent=6)
        stats["fails"] += 1

    if not probe.get("has_venv_module") and not probe.get("minimal"):
        rep.status("WARN", "venv 模块", "缺少 venv，无法创建虚拟环境", indent=6)
        stats["warns"] += 1

    pip_config = probe.get("pip_config") or {}
    index_url = probe.get("pip_index_url") or pip_config.get("index-url") or ""
    if index_url:
        rep.status("INFO", "包源", truncate(index_url, 80), indent=6)
    extra_url = pip_config.get("extra-index-url")
    if extra_url:
        rep.status("INFO", "附加源", truncate(extra_url, 80), indent=6)

    pythonpath = probe.get("pythonpath")
    if pythonpath:
        rep.status("INFO", "PYTHONPATH", truncate(pythonpath, 80), indent=6)

    packages = probe.get("packages") or {}
    rep.line()
    rep.note("相关 Python 包：", indent=6)
    if probe.get("minimal"):
        rep.note("未能获取包列表。", indent=8)
    else:
        for dist, note, min_ver, kind in PACKAGE_SPECS:
            installed = packages.get(dist)
            if installed and min_ver and version_tuple(installed) < min_ver:
                rep.status(
                    "WARN",
                    dist,
                    f"{installed}（建议 >= {fmt_version(min_ver)}） - {note}",
                    width=18,
                    indent=8,
                )
                stats["warns"] += 1
            elif installed:
                rep.status("OK", dist, f"{installed} - {note}", width=18, indent=8)
                if kind == "he":
                    stats["he"].append(dist)
                if dist == "numpy":
                    stats["numpy"] = installed
            elif kind == "required":
                rep.status("WARN", dist, f"未安装（必需） - {note}", width=18, indent=8)
                stats["warns"] += 1
            else:
                rep.status("INFO", dist, f"未安装 - {note}", width=18, indent=8)

        numpy_check = probe.get("numpy_check")
        if numpy_check:
            if not numpy_check.get("ok"):
                rep.status(
                    "WARN",
                    "numpy 自检",
                    "已安装但无法导入：" + truncate(numpy_check.get("error", ""), 100),
                    width=18,
                    indent=8,
                )
                stats["warns"] += 1
                stats["numpy"] = None
            elif not numpy_check.get("self_test"):
                rep.status(
                    "WARN",
                    "numpy 自检",
                    "可以导入，但基础矩阵运算失败",
                    width=18,
                    indent=8,
                )
                stats["warns"] += 1

    stats["usable"] = bits == 64 and ver >= MIN_PY
    return stats


# ---------------------------------------------------------------------------
# 总结
# ---------------------------------------------------------------------------


def choose_recommended(stats_list):
    usable = [s for s in stats_list if s["usable"]]
    if not usable:
        return None

    def score(item):
        probe = item.get("probe") or {}
        packages = probe.get("packages") or {}
        he_count = sum(
            1
            for dist, _, _, kind in PACKAGE_SPECS
            if kind == "he" and packages.get(dist)
        )
        return (
            (10 if item["venv"] else 0)
            + (6 if item["numpy"] else 0)
            + (2 if item["pip"] else 0)
            + min(he_count, 2)
        )

    return max(usable, key=score)


def build_summary(rep: Reporter, stats_list, net, sys_warns: int, net_warns: int):
    rec = choose_recommended(stats_list)
    fails = 0 if rec else 1
    warns = sys_warns + net_warns + (rec["warns"] if rec else 0)

    if rec:
        code = "FAIL" if fails else ("WARN" if warns else "OK")
        verdict = "环境暂不可用" if fails else ("基本可用" if warns else "环境就绪")
        rep.status(
            code,
            "总体结果",
            f"{verdict}：{warns} 条建议，{fails} 个关键问题",
        )
        rep.status(
            "OK",
            "推荐解释器",
            f"Python {rec['probe']['version']}（{rec['probe']['bits']} 位）",
        )
        rep.note(rec["exe"], indent=22)
    else:
        rep.status(
            "FAIL",
            "总体结果",
            "环境暂不可用：没有检测到可用的 64 位 Python（>= 3.9）",
        )
        if not stats_list:
            rep.note("请安装 64 位 Python 3.10–3.12：https://www.python.org/downloads/")
            rep.note("安装时勾选 “Add python.exe to PATH”，然后重新运行本工具。")

    steps = []
    if rec:
        exe = rec["exe"]
        probe = rec.get("probe") or {}
        minimal = bool(probe.get("minimal"))

        if minimal:
            steps.append("该解释器的探测脚本未能完整运行，建议手动执行 python -m pip --version 确认状态。")
        if not rec["venv"]:
            steps.append("为项目创建虚拟环境（在项目目录中执行）：")
            steps.append(f'    "{exe}" -m venv .venv')
            steps.append(r"    .venv\Scripts\activate")
            steps.append("激活后安装基础依赖：")
            steps.append("    python -m pip install numpy")
        elif not rec["numpy"] and not minimal:
            steps.append("安装数值计算基础：")
            steps.append("    python -m pip install numpy")

        if rec["he"] == [] and not minimal and rec["venv"]:
            steps.append("未检测到现成的同态库（可选）：")
            steps.append("    python -m pip install tenseal")
            steps.append("如果打算用 numpy 从零实现 CKKS，则无需安装。")
        elif rec["he"] == [] and not minimal and not rec["venv"]:
            steps.append("未检测到现成的同态库（可选）；创建虚拟环境后可安装 TenSEAL：")
            steps.append("    python -m pip install tenseal")
            steps.append("如果打算用 numpy 从零实现 CKKS，则无需安装。")

        if net and net.get("official_ok") is False and net.get("mirror_ok"):
            steps.append("PyPI 官方源不可达，安装依赖时可加镜像参数：")
            steps.append(
                "    python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple numpy"
            )
        if not steps:
            steps.append("环境已满足开发需求，可以直接开始编写 CKKS 包。")

    if steps:
        rep.line()
        rep.heading("  下一步建议")
        for step in steps:
            if step.startswith("    "):
                rep.note(step, indent=6)
            else:
                rep.note("- " + step, indent=6)

    exit_code = 2 if fails else (1 if warns else 0)
    return rec, exit_code, {
        "fails": fails,
        "warnings": warns,
        "recommended": rec["exe"] if rec else None,
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def build_json(system, tools, net, stats_list, summary, exit_code) -> dict:
    return {
        "tool": {
            "id": TOOL_ID,
            "version": TOOL_VERSION,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "frozen": FROZEN,
        },
        "system": system,
        "tools": tools,
        "network": net,
        "interpreters": [
            {
                "exe": item["exe"],
                "sources": item["sources"],
                "usable": item["usable"],
                "numpy": item["numpy"],
                "pip": item["pip"],
                "in_virtual_env": item["venv"],
                "he_packages": item["he"],
                "probe": item["probe"],
                "error": item["err"],
            }
            for item in stats_list
        ],
        "summary": {
            "recommended": summary["recommended"],
            "warnings": summary["warnings"],
            "critical_issues": summary["fails"],
            "exit_code": exit_code,
        },
    }


def ensure_parent(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def write_text_report(path: str, lines) -> None:
    ensure_parent(path)
    with open(path, "w", encoding="utf-8-sig", newline="\r\n") as handle:
        handle.write("\n".join(lines) + "\n")


def run(args) -> int:
    rep = Reporter(enable_ansi() and not args.no_color)

    rep.line("=" * 64)
    rep.heading(f"  CKKS 开发环境自检  v{TOOL_VERSION}")
    rep.line(f"  生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
    rep.line(f"  运行目录：{BASE_DIR}")
    rep.line(f"  执行程序：{sys.executable}")
    rep.line("=" * 64)

    # 1. 系统与构建工具
    rep.line()
    rep.heading("[1/4] 系统与构建工具")
    system = collect_system()
    sys_warns = render_system(rep, system)

    tools = collect_tools()
    rep.line()
    rep.heading("  构建与包管理工具")
    render_tools(rep, tools)

    # 2. Python 解释器
    rep.line()
    rep.heading("[2/4] Python 解释器")
    found = discover_interpreters(args.python)
    if found:
        extra = f"（最多检查 {MAX_PROBES} 个）" if len(found) > MAX_PROBES else ""
        rep.status("OK", "发现", f"{len(found)} 个解释器{extra}")
    elif args.python:
        rep.status("FAIL", "发现", "命令行指定的解释器不存在或不可执行")
        for path in args.python:
            rep.note(f"  {path}")
    else:
        rep.status(
            "FAIL",
            "发现",
            "未在 PATH、py 启动器、注册表或常见目录中找到 Python",
        )

    stats_list = []
    for index, entry in enumerate(found[:MAX_PROBES], 1):
        stats_list.append(render_interpreter(rep, index, entry))

    # 3. 网络
    rep.line()
    rep.heading("[3/4] 网络与包源")
    net, net_warns = render_network(rep, args.no_network)

    # 4. 总结
    rep.line()
    rep.heading("[4/4] 结论与建议")
    _, exit_code, summary = build_summary(
        rep, stats_list, net, sys_warns, net_warns
    )

    # 可选报告
    if args.out:
        try:
            write_text_report(args.out, rep.lines)
            print(f"\n文本报告已保存：{os.path.abspath(args.out)}")
        except OSError as exc:
            print(f"\n[WARN] 无法写入文本报告：{exc}")

    if args.json:
        try:
            data = build_json(system, tools, net, stats_list, summary, exit_code)
            ensure_parent(args.json)
            with open(args.json, "w", encoding="utf-8", newline="\r\n") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            print(f"JSON 报告已保存：{os.path.abspath(args.json)}")
        except OSError as exc:
            print(f"\n[WARN] 无法写入 JSON 报告：{exc}")

    return exit_code


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="ckks-env-check",
        description="CKKS 开发环境自检（只读检查，不修改系统，也不安装任何包）",
    )
    parser.add_argument(
        "--python",
        action="append",
        metavar="PATH",
        help="只检查指定的 Python 解释器（可重复；也可传包含 python.exe 的目录）",
    )
    parser.add_argument("--out", metavar="FILE", help="把文本报告保存到文件")
    parser.add_argument("--json", metavar="FILE", help="把结构化结果保存为 JSON")
    parser.add_argument(
        "--no-network", action="store_true", help="跳过网络连通性检查"
    )
    parser.add_argument("--no-color", action="store_true", help="禁用彩色输出")
    parser.add_argument(
        "--no-pause", action="store_true", help="结束时不等待回车（自动化调用）"
    )
    parser.add_argument(
        "--version", action="version", version=f"{TOOL_ID} {TOOL_VERSION}"
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    _prepare_console()
    args = parse_args(argv)
    pause = FROZEN and not args.no_pause
    # 只有双击 / 交互式终端运行时才等待回车；被其他程序以管道方式
    # 启动（例如 Python 包导入时）则直接退出，避免卡住调用方。
    pause = pause and stdin_is_console()

    exit_code = 2
    try:
        exit_code = run(args)
    except KeyboardInterrupt:
        print("\n已取消。")
        exit_code = 130
    except Exception as exc:  # 最外层兜底，避免双击运行时窗口一闪而过
        print(f"\n[FAIL] 检查过程出错：{type(exc).__name__}: {exc}")
        traceback.print_exc()
        exit_code = 2
    finally:
        if pause and sys.stdin is not None:
            try:
                input("\n按回车键退出 ...")
            except (EOFError, KeyboardInterrupt):
                pass
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
