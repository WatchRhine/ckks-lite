"""Automatic environment check executed when ``ckks_lite`` is imported.

The Windows executable ``_bin/ckks_env_check.exe`` (built from
``tools/env_check.py``) inspects the Python installations on the machine
and prints a report.  Importing the package runs it once per process.

The executable is started **without any command-line arguments**.  To
make sure the interpreter that is currently importing the package is
still considered, its directory is temporarily prepended to the child
process ``PATH`` (an environment override, not an argument).  The default
check also tests PyPI connectivity, so the first import may take a few
seconds.

Controls (environment variables):

``CKKS_LITE_ENV_CHECK``
    ``auto`` (default) show the full report, ``short`` print one summary
    line, ``skip`` do not run at all.

``CKKS_LITE_ENV_CHECK_TIMEOUT``
    Timeout in seconds for the executable (default 30).

``CKKS_LITE_ENV_CHECK_ACTIVE``
    Internal re-entry guard; do not set manually.

Because the bundled binary is Windows x64 only, the check is skipped on
other operating systems.  The executable only reads environment data; it
does not install anything or modify the system.
"""

from __future__ import annotations

import os
import subprocess
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

EXE_NAME = "ckks_env_check.exe"

ENV_MODE = "CKKS_LITE_ENV_CHECK"
ENV_TIMEOUT = "CKKS_LITE_ENV_CHECK_TIMEOUT"
ENV_ACTIVE = "CKKS_LITE_ENV_CHECK_ACTIVE"

DEFAULT_TIMEOUT = 30.0

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class EnvironmentCheckResult:
    """Outcome of one execution of the bundled checker."""

    exit_code: Optional[int]
    command: list
    output: Optional[str] = None
    error: Optional[str] = None
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def __repr__(self) -> str:
        return (
            f"<EnvironmentCheckResult exit_code={self.exit_code!r} "
            f"timed_out={self.timed_out}>"
        )


#: Result of the most recent run (``None`` if it was skipped or failed to start).
last_result: Optional[EnvironmentCheckResult] = None

_auto_ran = False
_warned_missing = False


def _safe_print(text: str = "", end: str = "\n") -> None:
    """Print without ever letting an encoding error break the import."""
    if sys.stdout is None:
        return
    try:
        print(text, end=end, flush=True)
    except UnicodeEncodeError:
        try:
            sys.stdout.write(text.encode("ascii", "replace").decode("ascii") + end)
            sys.stdout.flush()
        except Exception:
            pass
    except Exception:
        pass


def executable_path() -> Optional[Path]:
    """Return the bundled checker executable, if present."""
    path = Path(__file__).resolve().parent / "_bin" / EXE_NAME
    return path if path.is_file() else None


def _prepend_path(env: dict, directory: str) -> None:
    """Prepend ``directory`` to PATH in ``env`` (case-insensitive on Windows)."""
    if not directory:
        return
    for key in list(env):
        if key.upper() == "PATH":
            current = env[key]
            first = current.split(os.pathsep, 1)[0] if current else ""
            if first and os.path.normcase(first) == os.path.normcase(directory):
                return
            env[key] = directory + (os.pathsep + current if current else "")
            return
    env["PATH"] = directory


def _mode() -> str:
    raw = os.environ.get(ENV_MODE, "auto").strip().lower()
    if raw in {"", "auto", "on", "1", "true", "yes"}:
        return "auto"
    if raw in {"short", "quiet", "silent"}:
        return "short"
    if raw in {"skip", "off", "0", "false", "no", "disable", "disabled"}:
        return "skip"
    return "auto"


def _timeout() -> float:
    raw = os.environ.get(ENV_TIMEOUT, "").strip()
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT
    return value if value > 0 else DEFAULT_TIMEOUT


def run_environment_check(
    *,
    force: bool = False,
    visible: Optional[bool] = None,
    timeout: Optional[float] = None,
) -> Optional[EnvironmentCheckResult]:
    """Execute the bundled environment checker.

    Returns ``None`` when the check is skipped (non-Windows, disabled,
    re-entry, or missing executable).  Exceptions while starting the
    process are converted to warnings so that importing the library never
    fails because of the environment checker.
    """
    global last_result, _warned_missing

    mode = _mode()
    if not force:
        if mode == "skip":
            return None
        if os.environ.get(ENV_ACTIVE) == "1":
            return None
        if os.name != "nt":
            return None

    exe = executable_path()
    if exe is None:
        if os.name == "nt" and not _warned_missing:
            _warned_missing = True
            warnings.warn(
                f"未找到随包附带的环境检查程序 {EXE_NAME}，已跳过自动检查。"
                "可运行 tools/build_env_check_exe.bat 重新生成，"
                "或设置 CKKS_LITE_ENV_CHECK=skip 关闭此提示。",
                RuntimeWarning,
                stacklevel=2,
            )
        return None

    if visible is None:
        visible = mode == "auto" if not force else True
    if sys.stdout is None or sys.stderr is None:
        visible = False
    if timeout is None:
        timeout = _timeout()

    # Requirement: launch the executable with no command-line arguments.
    command = [str(exe)]

    env = os.environ.copy()
    env[ENV_ACTIVE] = "1"
    # The checker scans interpreters on PATH.  Put the interpreter that is
    # running this import first so an inactive venv is still discovered.
    if sys.executable:
        _prepend_path(env, os.path.dirname(sys.executable))

    if visible:
        _safe_print(
            "[ckks-lite] 正在执行环境自检（ckks_env_check.exe）。"
            "设置 CKKS_LITE_ENV_CHECK=skip 可关闭。"
        )

    try:
        # Always capture: this keeps Jupyter/IDE sessions from spawning a
        # separate console window and lets us store the full report.
        completed = subprocess.run(
            command,
            env=env,
            timeout=timeout,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,  # never let the checker wait for Enter
            creationflags=_CREATE_NO_WINDOW,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        result = EnvironmentCheckResult(
            exit_code=completed.returncode,
            command=command,
            output=output,
        )
    except subprocess.TimeoutExpired:
        message = f"环境自检超过 {timeout:.0f} 秒，已终止（可调整 CKKS_LITE_ENV_CHECK_TIMEOUT）"
        warnings.warn(message, RuntimeWarning, stacklevel=2)
        result = EnvironmentCheckResult(
            exit_code=None,
            command=command,
            error=message,
            timed_out=True,
        )
    except OSError as exc:
        message = f"无法启动环境自检程序：{exc}"
        warnings.warn(message, RuntimeWarning, stacklevel=2)
        result = EnvironmentCheckResult(
            exit_code=None,
            command=command,
            error=message,
        )

    last_result = result

    if visible:
        if result.output:
            _safe_print(
                result.output,
                end="" if result.output.endswith("\n") else "\n",
            )
        if result.exit_code == 2:
            _safe_print(
                "[ckks-lite] 环境自检发现关键问题；"
                "设置 CKKS_LITE_ENV_CHECK=short 可只看摘要。"
            )
    else:
        if result.exit_code is None:
            status = "执行失败"
        elif result.exit_code == 0:
            status = "通过"
        elif result.exit_code == 1:
            status = "完成，有建议项"
        elif result.exit_code == 2:
            status = "发现问题"
        else:
            status = f"异常退出（退出码 {result.exit_code}）"
        _safe_print(f"[ckks-lite] 环境自检：{status}")

    return result


def auto_run() -> None:
    """Called from ``ckks_lite/__init__.py``; never raises."""
    global _auto_ran

    if _auto_ran or _mode() == "skip":
        return
    _auto_ran = True
    try:
        run_environment_check()
    except Exception as exc:  # 导入过程绝不能因为自检崩溃
        warnings.warn(f"环境自检出现异常：{exc}", RuntimeWarning, stacklevel=2)
