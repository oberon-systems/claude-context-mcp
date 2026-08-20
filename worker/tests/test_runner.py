"""The startup diagnosis, which runs on a machine that has no model at all."""

from __future__ import annotations

from pathlib import Path

import pytest
from ctxworker import runner


def test_a_missing_package_names_the_install_line() -> None:
    """Nothing installed at all is the one case with no directory to read."""
    message = runner.describe_failure(None, "Windows", "No module named 'llama_cpp'")
    assert "not installed" in message
    assert runner.CUDA_INDEX in message


def test_a_wheel_for_another_platform_is_named_as_one(tmp_path: Path) -> None:
    """A Linux wheel on Windows fails with the same message as a broken one."""
    (tmp_path / "libllama.so").write_bytes(b"")
    message = runner.describe_failure(tmp_path, "Windows", "Could not find module")
    assert "another platform" in message
    assert "libllama.so" in message


def test_a_present_dll_blames_the_cuda_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """llama.dll on disk means a dependency is missing, not the library."""
    (tmp_path / "llama.dll").write_bytes(b"")
    (tmp_path / "ggml-cuda.dll").write_bytes(b"")
    monkeypatch.delenv("CUDA_PATH", raising=False)
    message = runner.describe_failure(tmp_path, "Windows", "Could not find module")
    assert "cublas64_12.dll" in message
    assert "CUDA_PATH is not set" in message
    assert "--gpu-layers 0" in message
