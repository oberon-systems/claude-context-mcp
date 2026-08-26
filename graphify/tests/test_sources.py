"""What may be read off a project's mount, and what may never leave it."""

from __future__ import annotations

import pathlib

import pytest

from ctxgraph import sources


@pytest.mark.parametrize(
    "rel_path",
    [
        ".env",
        "deploy/.env.production",
        "certs/server.pem",
        "infra/terraform.tfvars",
        "home/id_rsa",
        ".htpasswd",
    ],
)
def test_a_secret_is_never_served(rel_path: str) -> None:
    """The mount holds whatever the checkout holds; this list is the guard."""
    assert sources.is_denied(rel_path)


def test_ordinary_source_is_served() -> None:
    """The list is a denial, not an allowance."""
    assert not sources.is_denied("src/app.py")


@pytest.fixture
def mount(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Build a real tree with a secret, and a symlink pointing out of it."""
    root = tmp_path / "kurum"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("import os\n")
    (root / ".env").write_text("TOKEN=hunter2")
    outside = tmp_path / "outside.txt"
    outside.write_text("not yours")
    (root / "escape.txt").symlink_to(outside)
    monkeypatch.setattr(
        sources, "project_mount", lambda project: str(tmp_path / project)
    )
    return root


def test_reads_a_file_of_the_tree(mount: pathlib.Path) -> None:
    """The ordinary case: a path relative to the project root."""
    assert sources.read("kurum", "src/app.py") == ("import os\n", "")


def test_a_limit_truncates(mount: pathlib.Path) -> None:
    """The worker is shown a slice, not the whole file."""
    assert sources.read("kurum", "src/app.py", 6) == ("import", "")


def test_refuses_a_path_that_climbs_out(mount: pathlib.Path) -> None:
    """`..` must not reach another project, or the host."""
    assert sources.read("kurum", "../outside.txt") == (None, sources.ESCAPES)


def test_refuses_a_symlink_out_of_the_tree(mount: pathlib.Path) -> None:
    """os.walk never descends a symlinked directory, but it yields a file."""
    assert sources.read("kurum", "escape.txt") == (None, sources.ESCAPES)


def test_refuses_a_denied_name_before_touching_the_disk(
    mount: pathlib.Path,
) -> None:
    """The file is right there; the answer is still no."""
    assert sources.read("kurum", ".env") == (None, sources.DENIED)


def test_an_unmounted_project_says_so(mount: pathlib.Path) -> None:
    """A project with no mount reads differently from a missing file."""
    assert sources.read("absent", "src/app.py") == (None, sources.UNMOUNTED)


def test_a_missing_file_carries_its_reason(mount: pathlib.Path) -> None:
    """The graph is ahead of the tree, which is not the same as denied."""
    content, reason = sources.read("kurum", "src/gone.py")
    assert content is None
    assert reason not in (sources.DENIED, sources.ESCAPES, sources.UNMOUNTED)
