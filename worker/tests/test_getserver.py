"""Choosing a llama.cpp build, with no network and no GPU in the room."""

from __future__ import annotations

import pytest
from ctxworker import getserver

NAMES = [
    "llama-b10519-bin-win-cpu-x64.zip",
    "llama-b10519-bin-win-cuda-12.4-x64.zip",
    "llama-b10519-bin-win-cuda-13.3-x64.zip",
    "llama-b10519-bin-win-vulkan-x64.zip",
    "llama-b10519-bin-ubuntu-x64.zip",
    "cudart-llama-bin-win-cuda-12.4-x64.zip",
    "cudart-llama-bin-win-cuda-13.3-x64.zip",
]


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("| NVIDIA-SMI 610.74   CUDA UMD Version: 13.3     |", (13, 3)),
        ("| NVIDIA-SMI 550.54   CUDA Version: 12.4         |", (12, 4)),
        ("no card here", None),
    ],
)
def test_both_nvidia_smi_headers_are_read(
    header: str, expected: tuple[int, int] | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The header was renamed between driver generations; both still appear."""

    class Done:
        stdout = header

    monkeypatch.setattr(
        getserver.subprocess,
        "run",
        lambda *a, **k: Done(),  # noqa: ARG005
    )
    assert getserver.driver_cuda() == expected


def test_the_newest_runnable_cuda_build_wins() -> None:
    """A newer driver takes the newer build."""
    assert getserver.choose_variant(NAMES, (13, 3)) == "cuda-13.3"


def test_a_build_newer_than_the_driver_is_not_taken() -> None:
    """CUDA is backward compatible downwards, not upwards."""
    assert getserver.choose_variant(NAMES, (12, 8)) == "cuda-12.4"


def test_no_card_means_the_cpu_build() -> None:
    """nvidia-smi missing is a machine without an NVIDIA GPU."""
    assert getserver.choose_variant(NAMES, None) == "cpu"


def test_a_driver_older_than_every_build_falls_back_to_the_cpu() -> None:
    """An ancient driver must not be handed a build it cannot load."""
    assert getserver.choose_variant(NAMES, (11, 8)) == "cpu"


def test_the_runtime_travels_with_the_build() -> None:
    """A CUDA build without its cudart is the failure this whole file avoids."""
    assets = [{"name": name} for name in NAMES]
    chosen = [asset["name"] for asset in getserver.pick_assets(assets, "cuda-13.3")]

    assert chosen == [
        "llama-b10519-bin-win-cuda-13.3-x64.zip",
        "cudart-llama-bin-win-cuda-13.3-x64.zip",
    ]


def test_the_cpu_build_travels_alone() -> None:
    """There is no CUDA runtime to fetch for a build that has no CUDA."""
    assets = [{"name": name} for name in NAMES]
    chosen = [asset["name"] for asset in getserver.pick_assets(assets, "cpu")]

    assert chosen == ["llama-b10519-bin-win-cpu-x64.zip"]


def test_an_unknown_variant_lists_the_ones_there_are() -> None:
    """A typo in --variant must not read as "this release has nothing"."""
    assets = [{"name": name} for name in NAMES]

    with pytest.raises(SystemExit) as exit_info:
        getserver.pick_assets(assets, "cuda-9.9")

    message = str(exit_info.value)
    assert "cuda-13.3" in message
    assert "--variant" in message


def test_a_release_whose_binaries_are_not_up_yet_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """llama.cpp tags a release before the zips finish uploading."""
    recent = [
        {"tag_name": "b10520", "assets": []},
        {"tag_name": "b10519", "assets": [{"name": name} for name in NAMES]},
    ]
    monkeypatch.setattr(getserver, "fetch", lambda source: recent)  # noqa: ARG005

    assert getserver.pick_release("", "")["tag_name"] == "b10519"
    assert getserver.pick_release("", "cuda-13.3")["tag_name"] == "b10519"


def test_nothing_recent_with_a_build_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty answer must not read as a broken --variant."""
    monkeypatch.setattr(getserver, "fetch", lambda source: [])  # noqa: ARG005

    with pytest.raises(SystemExit) as exit_info:
        getserver.pick_release("", "cuda-13.3")

    assert "--tag" in str(exit_info.value)
