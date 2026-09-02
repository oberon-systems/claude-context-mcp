"""What the mount listing says, which is what the compose override becomes.

The listing is three columns because a project can read more than one
directory: the project names the mount, the alias names the directory inside
it, and the host path is what is bound there. `scripts/mounts.sh` reads it and
checks the paths, so what is pinned here is the shape and the ordering.
"""

from __future__ import annotations

import pytest

from ctxgraph import mounts

STORED = {
    ("kurum", ""): "/src/kurum",
    ("mono", "configs"): "/mono/deploy/configs",
    ("mono", "agents"): "/mono/tools/agents",
}


@pytest.fixture
def listed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer the database lookup with a selection written by hand."""
    monkeypatch.setattr(mounts, "mounted_sources", lambda: dict(STORED))


def run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *argv: str,
) -> list[str]:
    """Run the module as the shell script does, and read its listing back."""
    monkeypatch.setattr("sys.argv", ["ctxgraph.mounts", *argv])
    mounts.main()
    return capsys.readouterr().out.splitlines()


def test_one_line_per_directory(
    listed: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Sorted by project and then alias, so the override is stable.

    The unnamed source is written `-`: a tab is IFS whitespace, so a shell
    reading an empty middle column would collapse the two delimiters into one.
    """
    assert run(monkeypatch, capsys) == [
        "kurum\t-\t/src/kurum",
        "mono\tagents\t/mono/tools/agents",
        "mono\tconfigs\t/mono/deploy/configs",
    ]


def test_an_unregistered_directory_is_carried(
    listed: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A first mount happens before the row exists, which is what --add is."""
    lines = run(monkeypatch, capsys, "--add", "/src/keenv/")
    assert "keenv\t-\t/src/keenv" in lines


def test_an_added_directory_takes_its_alias(
    listed: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The alias decides the mount point as well as the node id prefix."""
    lines = run(
        monkeypatch, capsys, "--add", "/mono/docs", "--name", "mono", "--alias", "Docs"
    )
    assert "mono\tdocs\t/mono/docs" in lines


def test_a_created_project_is_not_mounted(
    listed: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """It reads no directory yet, so the path it was onboarded from is not one."""
    registered: list[tuple[str, ...]] = []

    def record(
        root: str,
        name: str,
        alias: str,
        project_type: str,
        with_source: bool = True,
        selection: tuple[str, str] = ("", ""),
    ) -> str:
        registered.append((root, name, alias, str(with_source)))
        return name

    monkeypatch.setattr(mounts, "register", record)
    lines = run(
        monkeypatch,
        capsys,
        "--add",
        "/mono",
        "--name",
        "mono",
        "--register",
        "--create",
    )
    assert registered == [("/mono", "mono", "", "False")]
    assert not any(line.startswith("mono\t-\t") for line in lines)


def test_the_generated_selection_reaches_the_register_step(
    listed: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Onboarding no longer writes the pair into the tree, so it stores it.

    The documents travel as variable names rather than as values: an argument
    list is neither the place for kilobytes of globs nor safe from the shell
    that has to build it.
    """
    stored: list[tuple[str, str]] = []

    def record(
        root: str,
        name: str,
        alias: str,
        project_type: str,
        with_source: bool = True,
        selection: tuple[str, str] = ("", ""),
    ) -> str:
        stored.append(selection)
        return name

    monkeypatch.setattr(mounts, "register", record)
    monkeypatch.setenv("KEEP_DOC", "*.py\n")
    monkeypatch.setenv("IGNORE_DOC", "*.pem\n")
    run(
        monkeypatch,
        capsys,
        "--add",
        "/src/keenv",
        "--register",
        "--ctxkeep-env",
        "KEEP_DOC",
        "--ctxignore-env",
        "IGNORE_DOC",
    )
    assert stored == [("*.py\n", "*.pem\n")]


def test_an_unnamed_variable_stores_nothing(
    listed: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bare `make mounts` must not touch a project's stored selection."""
    stored: list[tuple[str, str]] = []

    def record(
        root: str,
        name: str,
        alias: str,
        project_type: str,
        with_source: bool = True,
        selection: tuple[str, str] = ("", ""),
    ) -> str:
        stored.append(selection)
        return name

    monkeypatch.setattr(mounts, "register", record)
    run(monkeypatch, capsys, "--add", "/src/keenv", "--register")
    assert stored == [("", "")]
