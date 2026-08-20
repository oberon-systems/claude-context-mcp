"""The downloader, which must refuse anything that is not weights."""

from __future__ import annotations

from pathlib import Path

import pytest
from ctxworker import download


def test_an_error_page_is_not_installed_as_a_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 404 body saved under the model's name would fail a run later."""

    class Response:
        """An error page, handed over once and then exhausted."""

        def __init__(self) -> None:
            self.body: bytes | None = b"<html>404 Not Found</html>"

        def read(self, size: int = -1) -> bytes:
            body, self.body = self.body, None
            return body or b""

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(download.urllib.request, "urlopen", lambda _: Response())
    with pytest.raises(SystemExit, match="not a GGUF file"):
        download.fetch("qwen-1.5b", tmp_path)
    assert not list(tmp_path.glob("*.gguf"))
    assert list(tmp_path.glob("*.part")), "the partial download is kept to look at"


def test_existing_weights_are_not_downloaded_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file is a gigabyte; a re-run must not fetch it twice."""
    target = tmp_path / download.file_name("qwen-1.5b")
    target.write_bytes(b"GGUF and then some")

    def refuse(_: str) -> None:
        raise AssertionError("downloaded an existing model")

    monkeypatch.setattr(download.urllib.request, "urlopen", refuse)
    assert download.fetch("qwen-1.5b", tmp_path) == target
