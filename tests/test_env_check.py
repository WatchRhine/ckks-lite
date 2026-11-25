import os
import subprocess

import pytest

from ckks_lite import env_check
from ckks_lite.env_check import EnvironmentCheckResult


def test_skip_mode_returns_none(monkeypatch):
    monkeypatch.setenv("CKKS_LITE_ENV_CHECK", "skip")
    monkeypatch.delenv("CKKS_LITE_ENV_CHECK_ACTIVE", raising=False)
    assert env_check.run_environment_check() is None


def test_executable_is_bundled_on_windows():
    if os.name != "nt":
        pytest.skip("bundled executable is Windows-only")
    path = env_check.executable_path()
    assert path is not None
    assert path.name == "ckks_env_check.exe"


def test_result_ok_flag():
    assert EnvironmentCheckResult(0, []).ok
    assert not EnvironmentCheckResult(1, []).ok
    assert not EnvironmentCheckResult(None, []).ok


def test_last_result_initially_none():
    # Tests never launch the executable, so no result is recorded.
    assert env_check.last_result is None


def test_executable_is_launched_without_arguments(monkeypatch, tmp_path):
    fake_exe = tmp_path / "ckks_env_check.exe"
    fake_exe.write_bytes(b"")

    class Completed:
        returncode = 0
        stdout = "report\n"
        stderr = ""

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr(env_check, "executable_path", lambda: fake_exe)
    monkeypatch.setattr(env_check.subprocess, "run", fake_run)

    original_result = env_check.last_result
    try:
        result = env_check.run_environment_check(force=True, visible=False)
    finally:
        env_check.last_result = original_result

    assert captured["command"] == [str(fake_exe)]
    assert captured["kwargs"]["stdin"] is subprocess.DEVNULL
    assert result is not None and result.exit_code == 0


def test_prepend_path_case_insensitive():
    env = {"Path": r"C:\First" + os.pathsep + r"C:\Second"}
    env_check._prepend_path(env, r"C:\Venv\Scripts")
    assert env["Path"].startswith(r"C:\Venv\Scripts" + os.pathsep)
    assert "PATH" not in env
