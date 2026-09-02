"""How a project row and the directories it reads are registered.

None of it needs a database: the statements are few and shaped by hand, so a
cursor holding two dictionaries answers them and the behaviour that matters
can be pinned - which type a re-index writes, which pairing of a name and a
path is refused, and how a project made of several directories is assembled.
"""

from __future__ import annotations

from typing import Any

import pytest

from ctxgraph.config import BUILTIN_PROJECT_TYPES
from ctxgraph.storage import (
    add_source,
    clear_settings,
    drop_source,
    ensure_project,
    has_settings,
    list_files_without_llm_summary,
    list_sources,
    promote_root,
    read_settings,
    register_project,
    set_selection_origin,
    write_settings,
)


class FakeCursor:
    """Answer the statements `ctxgraph.storage` sends, out of two dictionaries.

    A queue would not do any more: registering one directory now reads the
    projects row, the sources of that project and the owner of that path, in
    an order the tests have no business depending on. Anything not recognised
    falls back to `rows`, which is what the summary-pass tests still use.
    """

    def __init__(
        self,
        rows: list[tuple[Any, ...] | None] | None = None,
        projects: dict[str, tuple[str, str]] | None = None,
        sources: list[tuple[str, str, str]] | None = None,
        settings: dict[tuple[str, str], tuple[str | None, str | None]] | None = None,
    ) -> None:
        """Seed the database this cursor pretends to be."""
        self.rows = list(rows or [])
        # name -> (root_path, type)
        self.projects = dict(projects or {})
        # (project, alias, root_path), in the order they were added
        self.sources = list(sources or [])
        # (project, alias) -> (ctxkeep, ctxignore)
        self.settings = dict(settings or {})
        # (project, alias) -> (keep_source, ignore_source)
        self.origins: dict[tuple[str, str], tuple[str, str]] = {}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.answer: list[tuple[Any, ...]] | None = None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        """Apply the statement to the dictionaries, or record and ignore it."""
        self.calls.append((sql, params))
        text = " ".join(sql.split())
        self.answer = self._run(text, params)

    def _run(self, text: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]] | None:
        if text.startswith("SELECT type FROM projects WHERE name"):
            stored = self.projects.get(params[0])
            return [(stored[1],)] if stored else []
        if text.startswith("SELECT 1 FROM projects WHERE name"):
            return [(1,)] if params[0] in self.projects else []
        if text.startswith("SELECT name FROM projects WHERE root_path"):
            return [
                (name,)
                for name, (root, _) in self.projects.items()
                if root == params[0] and name != params[1]
            ]
        if text.startswith("SELECT alias, root_path FROM project_sources"):
            return [
                (alias, root)
                for project, alias, root in self.sources
                if project == params[0]
            ]
        if text.startswith("SELECT project FROM project_sources WHERE root_path"):
            return [
                (project,) for project, _, root in self.sources if root == params[0]
            ]
        if text.startswith("SELECT alias FROM project_sources WHERE project"):
            return [
                (alias,)
                for project, alias, root in self.sources
                if project == params[0] and root == params[1]
            ]
        if text.startswith("SELECT ctxkeep, ctxignore FROM project_settings"):
            stored = self.settings.get((params[0], params[1]))
            return [stored] if stored else []
        if text.startswith("SELECT 1 FROM project_settings WHERE project"):
            return [(1,) for project, _ in self.settings if project == params[0]][:1]
        if text.startswith("INSERT INTO project_settings"):
            self.settings[(params[0], params[1])] = (params[2], params[3])
            return []
        if text.startswith("DELETE FROM project_settings"):
            self.settings.pop((params[0], params[1]), None)
            return []
        if text.startswith("UPDATE project_sources SET keep_source"):
            self.origins[(params[2], params[3])] = (params[0], params[1])
            return []
        if text.startswith("INSERT INTO projects"):
            name, root, project_type, default, _ = params
            stored = self.projects.get(name)
            if stored is None:
                self.projects[name] = (root, project_type or default)
            else:
                self.projects[name] = (stored[0], project_type or stored[1])
            return []
        if text.startswith("INSERT INTO project_sources"):
            self.sources.append((params[0], params[1], params[2]))
            return []
        if text.startswith("UPDATE projects SET root_path"):
            primary = next(
                (root for project, _, root in self.sources if project == params[0]),
                None,
            )
            if primary is not None:
                self.projects[params[1]] = (primary, self.projects[params[1]][1])
            return []
        if text.startswith("UPDATE project_sources SET alias"):
            renamed = []
            for project, alias, root in self.sources:
                if project == params[1] and alias == "":
                    alias = params[0]
                renamed.append((project, alias, root))
            self.sources = renamed
            return []
        if text.startswith("DELETE FROM project_sources"):
            self.sources = [
                entry
                for entry in self.sources
                if not (entry[0] == params[0] and entry[1] == params[1])
            ]
            return []
        return None

    def fetchone(self) -> tuple[Any, ...] | None:
        """Answer with the first row of the answer, or the next queued one."""
        if self.answer is not None:
            return self.answer[0] if self.answer else None
        return self.rows.pop(0) if self.rows else None

    def fetchall(self) -> list[tuple[Any, ...] | None]:
        """Answer with every row of the answer, or with what is queued."""
        if self.answer is not None:
            return list(self.answer)
        rows, self.rows = self.rows, []
        return rows


def upsert_params(cursor: FakeCursor) -> tuple[Any, ...]:
    """Read back the parameters of the INSERT into projects."""
    for sql, params in reversed(cursor.calls):
        if sql.lstrip().startswith("INSERT INTO projects"):
            return params
    raise AssertionError("no INSERT INTO projects was sent")


def test_a_first_index_defaults_to_codebase() -> None:
    """A project registered without a type is a codebase."""
    cursor = FakeCursor()
    ensure_project(cursor, "kurum", "/src/kurum")
    assert upsert_params(cursor) == ("kurum", "/src/kurum", None, "codebase", None)


def test_a_named_type_is_written() -> None:
    """TYPE= on the command line reaches the statement."""
    cursor = FakeCursor()
    ensure_project(cursor, "kurum", "/src/kurum", "docs")
    assert upsert_params(cursor) == ("kurum", "/src/kurum", "docs", "codebase", "docs")


def test_a_plain_reindex_leaves_the_stored_type_alone() -> None:
    """None on both sides is what makes the COALESCE keep what is stored."""
    cursor = FakeCursor(
        projects={"kurum": ("/src/kurum", "docs")},
        sources=[("kurum", "", "/src/kurum")],
    )
    ensure_project(cursor, "kurum", "/src/kurum")
    assert upsert_params(cursor)[2] is None
    assert upsert_params(cursor)[4] is None


@pytest.mark.parametrize("project_type", sorted(BUILTIN_PROJECT_TYPES))
def test_refuses_to_index_into_a_builtin_project(project_type: str) -> None:
    """There is no tree behind one of these; a run would prune it empty."""
    root = f"{project_type}://agent"
    cursor = FakeCursor(projects={f"_{project_type}": (root, project_type)})
    with pytest.raises(RuntimeError, match=f"agent {project_type}"):
        ensure_project(cursor, f"_{project_type}", root)


def test_still_refuses_a_name_pointing_at_another_path() -> None:
    """A second checkout of the same basename must not merge into the first."""
    cursor = FakeCursor(
        projects={"kurum": ("/src/other", "codebase")},
        sources=[("kurum", "", "/src/other")],
    )
    with pytest.raises(RuntimeError, match="already indexed from"):
        ensure_project(cursor, "kurum", "/src/kurum")


def test_refuses_a_path_another_project_reads() -> None:
    """One directory belongs to one project, whatever it is called there."""
    cursor = FakeCursor(
        projects={"mono": ("/mono/configs", "codebase")},
        sources=[("mono", "configs", "/mono/configs")],
    )
    with pytest.raises(RuntimeError, match="already indexed as 'mono'"):
        ensure_project(cursor, "configs", "/mono/configs")


def test_a_first_index_records_the_tree_as_the_unnamed_source() -> None:
    """The project a plain index run creates reads its whole tree."""
    cursor = FakeCursor()
    ensure_project(cursor, "kurum", "/src/kurum")
    assert list_sources(cursor, "kurum") == [("", "/src/kurum")]


def test_a_project_with_no_directory_is_not_indexed() -> None:
    """One onboarded ahead of its slices must not adopt the path it was given."""
    cursor = FakeCursor(projects={"mono": ("/mono", "codebase")})
    with pytest.raises(RuntimeError, match="reads no directories yet"):
        ensure_project(cursor, "mono", "/mono")


def test_a_project_can_be_registered_without_a_directory() -> None:
    """Onboarding a monorepo writes the row and reads nothing yet."""
    cursor = FakeCursor()
    register_project(cursor, "mono", "/mono", None, "", with_source=False)
    assert list_sources(cursor, "mono") == []


def test_directories_are_added_under_their_alias() -> None:
    """Each slice keeps the name its node ids are prefixed with."""
    cursor = FakeCursor(projects={"mono": ("/mono", "codebase")})
    add_source(cursor, "mono", "configs", "/mono/deploy/configs")
    add_source(cursor, "mono", "agents", "/mono/tools/agents")
    assert list_sources(cursor, "mono") == [
        ("configs", "/mono/deploy/configs"),
        ("agents", "/mono/tools/agents"),
    ]


def test_the_primary_follows_the_first_directory() -> None:
    """projects.root_path is the first source rather than a second truth."""
    cursor = FakeCursor(projects={"mono": ("/mono", "codebase")})
    add_source(cursor, "mono", "configs", "/mono/deploy/configs")
    assert cursor.projects["mono"][0] == "/mono/deploy/configs"


def test_a_named_directory_is_refused_beside_a_whole_tree() -> None:
    """Mixing the two would nest one mount inside another."""
    cursor = FakeCursor(
        projects={"kurum": ("/src/kurum", "codebase")},
        sources=[("kurum", "", "/src/kurum")],
    )
    with pytest.raises(RuntimeError, match="source-promote"):
        add_source(cursor, "kurum", "docs", "/src/kurum-docs")


def test_a_whole_tree_is_refused_beside_named_directories() -> None:
    """The other direction of the same rule."""
    cursor = FakeCursor(
        projects={"mono": ("/mono/configs", "codebase")},
        sources=[("mono", "configs", "/mono/configs")],
    )
    with pytest.raises(RuntimeError, match="pass an alias"):
        add_source(cursor, "mono", "", "/mono")


def test_adding_the_same_directory_twice_is_quiet() -> None:
    """Onboarding is re-run to pick up what is missing, not to fail."""
    cursor = FakeCursor(
        projects={"mono": ("/mono/configs", "codebase")},
        sources=[("mono", "configs", "/mono/configs")],
    )
    add_source(cursor, "mono", "configs", "/mono/configs")
    assert list_sources(cursor, "mono") == [("configs", "/mono/configs")]


def test_an_alias_is_not_repointed_in_place() -> None:
    """The nodes under it would outlive the directory that wrote them."""
    cursor = FakeCursor(
        projects={"mono": ("/mono/configs", "codebase")},
        sources=[("mono", "configs", "/mono/configs")],
    )
    with pytest.raises(RuntimeError, match="drop that source"):
        add_source(cursor, "mono", "configs", "/mono/other")


def test_the_last_directory_is_not_dropped() -> None:
    """A project reading nothing is a project to drop, not to keep."""
    cursor = FakeCursor(
        projects={"mono": ("/mono/configs", "codebase")},
        sources=[("mono", "configs", "/mono/configs")],
    )
    with pytest.raises(RuntimeError, match="only source"):
        drop_source(cursor, "mono", "configs")


def test_dropping_a_directory_moves_the_primary() -> None:
    """The column follows the sources, including when the first one goes."""
    cursor = FakeCursor(
        projects={"mono": ("/mono/configs", "codebase")},
        sources=[
            ("mono", "configs", "/mono/configs"),
            ("mono", "agents", "/mono/agents"),
        ],
    )
    drop_source(cursor, "mono", "configs")
    assert list_sources(cursor, "mono") == [("agents", "/mono/agents")]
    assert cursor.projects["mono"][0] == "/mono/agents"


def test_promoting_names_the_whole_tree() -> None:
    """What turns a one-directory project into one that can take a second."""
    cursor = FakeCursor(
        projects={"kurum": ("/src/kurum", "codebase")},
        sources=[("kurum", "", "/src/kurum")],
    )
    promote_root(cursor, "kurum", "root")
    assert list_sources(cursor, "kurum") == [("root", "/src/kurum")]
    add_source(cursor, "kurum", "docs", "/src/kurum-docs")
    assert [alias for alias, _ in list_sources(cursor, "kurum")] == ["root", "docs"]


def test_promoting_a_project_that_has_no_whole_tree_is_refused() -> None:
    """There is nothing to rename, and no id would change."""
    cursor = FakeCursor(
        projects={"mono": ("/mono/configs", "codebase")},
        sources=[("mono", "configs", "/mono/configs")],
    )
    with pytest.raises(RuntimeError, match="no unnamed source"):
        promote_root(cursor, "mono", "root")


def test_files_without_a_summary_come_back_as_paths() -> None:
    """The pass reads files from the mount, so only the path is needed."""
    cursor = FakeCursor([("src/app.py",), ("README.md",)])
    assert list_files_without_llm_summary(cursor, "kurum") == [
        "src/app.py",
        "README.md",
    ]


def test_refresh_widens_the_selection_to_what_the_model_wrote() -> None:
    """The flag reaches the statement; the CASE in it does the widening."""
    cursor = FakeCursor([])
    list_files_without_llm_summary(cursor, "kurum", True)
    assert cursor.calls[-1][1] == ("kurum", True)


def test_a_level_with_no_row_says_nothing_about_the_selection() -> None:
    """A missing row and a row of two NULLs are the same answer."""
    cursor = FakeCursor(settings={("kurum", ""): (None, None)})
    assert read_settings(cursor, "kurum", "") == (None, None)
    assert read_settings(cursor, "kurum", "docs") == (None, None)


def test_a_stored_pair_reads_back_verbatim() -> None:
    """Comments and blank lines are part of the document, not noise."""
    cursor = FakeCursor()
    keep = "# what this tree holds\n\n*.py\n"
    write_settings(cursor, "kurum", "", keep, "*.pem\n")
    assert read_settings(cursor, "kurum", "") == (keep, "*.pem\n")


def test_clearing_one_half_lets_the_level_above_answer() -> None:
    """Writing NULL is how a level stops speaking for a document."""
    cursor = FakeCursor()
    write_settings(cursor, "kurum", "", "*.py\n", "*.pem\n")
    write_settings(cursor, "kurum", "", None, "*.pem\n")
    assert read_settings(cursor, "kurum", "") == (None, "*.pem\n")


def test_a_project_holding_no_row_anywhere_is_reported_empty() -> None:
    """Onboarding writes the generated pair only into a project with none."""
    cursor = FakeCursor()
    assert has_settings(cursor, "kurum") is False
    write_settings(cursor, "kurum", "configs", "*.yaml\n", None)
    assert has_settings(cursor, "kurum") is True
    clear_settings(cursor, "kurum", "configs")
    assert has_settings(cursor, "kurum") is False


def test_the_run_records_where_each_source_read_its_selection() -> None:
    """The dashboard holds no mount, so the run that looked reports it."""
    cursor = FakeCursor(sources=[("mono", "configs", "/mono/configs")])
    set_selection_origin(cursor, "mono", "configs", "file", "global")
    assert cursor.origins[("mono", "configs")] == ("file", "global")


def test_dropping_a_directory_drops_the_settings_it_had() -> None:
    """A row for a directory nothing reads is listed nowhere.

    It would also decide the selection again if that alias came back.
    """
    cursor = FakeCursor(
        projects={"mono": ("/mono/configs", "codebase")},
        sources=[
            ("mono", "configs", "/mono/configs"),
            ("mono", "agents", "/mono/agents"),
        ],
        settings={
            ("mono", "agents"): ("*.py\n", None),
            ("mono", ""): ("*.md\n", None),
        },
    )
    drop_source(cursor, "mono", "agents")
    assert read_settings(cursor, "mono", "agents") == (None, None)
    # The project level is not a directory and is left exactly as it was.
    assert read_settings(cursor, "mono", "") == ("*.md\n", None)
