"""What a project made of several directories selects, and from where.

Each source is walked from its own root, so the selection files of one slice
say nothing about another - and every path that comes back opens with the
alias it came from, which is what keeps two files of the same name apart.
"""

from __future__ import annotations

from pathlib import Path

from ctxgraph.config import IGNORE_FILE, KEEP_FILE
from ctxgraph.discovery import SpecPair, iter_project_files, load_spec, selects, to_spec


def build(root: Path) -> None:
    """Lay out a mount holding two slices of one monorepo."""
    (root / "configs").mkdir(parents=True)
    (root / "configs" / "prod.yaml").write_text("a: 1\n")
    (root / "configs" / "vendor").mkdir()
    (root / "configs" / "vendor" / "third.yaml").write_text("b: 2\n")
    (root / "configs" / ".ctxignore").write_text("vendor/\n")
    (root / "agents" / "src").mkdir(parents=True)
    (root / "agents" / "src" / "run.py").write_text("x = 1\n")
    (root / "agents" / "notes.txt").write_text("nothing to parse\n")


def specs(base: Path) -> SpecPair:
    """Load one source's pair off disk, as `ctxgraph.selection` does."""
    return load_spec(str(base), KEEP_FILE), load_spec(str(base), IGNORE_FILE)


def selected(root: Path, aliases: list[str]) -> list[str]:
    """Return the project relative paths, sorted for comparison."""
    pairs = [(alias, specs(root / alias if alias else root)) for alias in aliases]
    return sorted(rel for _, rel in iter_project_files(str(root), pairs))


def test_every_path_opens_with_its_alias(tmp_path: Path) -> None:
    """A node id says which directory of the project the file came from."""
    build(tmp_path)
    assert selected(tmp_path, ["configs", "agents"]) == [
        "agents/src/run.py",
        "configs/prod.yaml",
    ]


def test_each_directory_reads_its_own_selection(tmp_path: Path) -> None:
    """The .ctxignore of one slice is not the .ctxignore of the project."""
    build(tmp_path)
    assert "configs/vendor/third.yaml" not in selected(tmp_path, ["configs"])
    # The same pattern from the other slice selects nothing of this one.
    (tmp_path / "agents" / ".ctxignore").write_text("prod.yaml\n")
    assert "configs/prod.yaml" in selected(tmp_path, ["configs", "agents"])


def test_the_unnamed_source_is_the_mount_itself(tmp_path: Path) -> None:
    """A project reading one directory keeps the ids it has always had."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n")
    assert selected(tmp_path, [""]) == ["src/app.py"]


def test_a_project_reading_nothing_selects_nothing(tmp_path: Path) -> None:
    """An empty selection is empty rather than the whole mount."""
    build(tmp_path)
    assert selected(tmp_path, []) == []


def test_a_watched_path_under_a_skipped_directory_selects_nothing() -> None:
    """Every commit writes under .git, and none of it is worth a re-index."""
    assert not selects(".git/refs/heads/main.py", None, None)
    assert not selects("node_modules/left-pad/index.js", None, None)


def test_a_watched_path_obeys_the_two_specs() -> None:
    """The rule a walk applies, for a path that arrives without one."""
    keep = to_spec(["*.py"])
    ignore = to_spec(["build/"])
    assert selects("src/run.py", keep, ignore)
    assert not selects("src/run.txt", keep, ignore)
    assert not selects("build/run.py", keep, ignore)


def test_a_watched_path_without_a_keep_list_uses_the_built_in_set() -> None:
    """No keep list is the built-in extension set, not everything."""
    assert selects("src/run.py", None, None)
    assert not selects("notes.txt", None, None)
