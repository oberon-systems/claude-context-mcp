"""What a page and a PHP template contribute to the graph."""

from __future__ import annotations

import pytest

from ctxgraph.indexer import is_graphifyy_source
from ctxgraph.parsers.languages import HtmlParser, PhtmlParser, asset_path
from ctxgraph.parsers.registry import get_parser, parser_class
from ctxgraph.resolution import html_candidates

PAGE = """<!doctype html>
<html>
  <head>
    <title>Context   dashboard</title>
    <link rel="stylesheet" href="css/site.css" />
    <style>
      .card > .title {
        color: red;
      }
    </style>
  </head>
  <body>
    <div id="root"></div>
    <my-widget data-x="1" />
    <img src="img/logo.png?v=2" />
    <iframe src="/static/frame.html#top"></iframe>
    <form action="handlers/save.php"></form>
    <script src="js/app.js"></script>
    <script src="https://cdn.example.com/x.js"></script>
    <script>
      function boot() {
        init();
        boot();
      }
    </script>
  </body>
</html>
"""

TEMPLATE = """<h1>Widget</h1>
<?php
use App\\Model\\User;
require_once 'partials/head.phtml';

class Widget extends Base implements Renderable
{
    public function render(): string
    {
        return $this->format();
    }

    private function format(): string
    {
        return helper();
    }
}

function helper()
{
    return 1;
}
?>
<p><?= $x ?></p>
"""

PAGE_PATH = "web/index.html"
TEMPLATE_PATH = "views/widget.phtml"


@pytest.fixture
def page() -> HtmlParser:
    """Return the HTML parser, as the registry hands it out."""
    parser = get_parser(PAGE_PATH)
    assert isinstance(parser, HtmlParser)
    return parser


@pytest.fixture
def template() -> PhtmlParser:
    """Return the PHP template parser, as the registry hands it out."""
    parser = get_parser(TEMPLATE_PATH)
    assert isinstance(parser, PhtmlParser)
    return parser


def entity_names(entities: list[dict[str, str]]) -> dict[str, str]:
    """Index entities by name so a test can name the one it means."""
    return {entity["name"]: entity["type"] for entity in entities}


def relation_pairs(relations: list[dict[str, str]]) -> set[tuple[str, str, str]]:
    """Flatten relations to (target, type, scope) triples."""
    return {(r["target"], r["type"], r["scope"]) for r in relations}


def test_routing() -> None:
    """Pages get the HTML parser, templates the PHP one, `.php` neither."""
    assert parser_class("index.html") is HtmlParser
    assert parser_class("page.htm") is HtmlParser
    assert parser_class("views/widget.phtml") is PhtmlParser
    # PHP itself stays with the upstream extractor, which is what reads it.
    assert parser_class("src/app.php") is None
    assert is_graphifyy_source("src/app.php")
    assert not is_graphifyy_source(PAGE_PATH)


def test_page_entities(page: HtmlParser) -> None:
    """The page contributes its structure and what its inline code declares."""
    entities = entity_names(page.get_entities(PAGE, PAGE_PATH))
    assert entities["title.Context dashboard"] == "title"
    assert entities["anchor.root"] == "anchor"
    assert entities["element.my-widget"] == "element"
    assert entities["boot"] == "function"
    assert entities["style..card > .title"] == "style"
    # A standard tag is structure, not a declaration.
    assert "element.div" not in entities


def test_page_assets(page: HtmlParser) -> None:
    """Every local asset becomes a file scoped edge, and no remote one does."""
    relations = relation_pairs(page.get_relations(PAGE, PAGE_PATH))
    assert ("css/site.css", "uses_style", "file") in relations
    assert ("js/app.js", "uses_script", "file") in relations
    assert ("img/logo.png", "uses_file", "file") in relations
    assert ("/static/frame.html", "uses_file", "file") in relations
    assert ("handlers/save.php", "uses_file", "file") in relations
    assert not [target for target, _, _ in relations if "cdn.example.com" in target]


def test_page_inline_calls(page: HtmlParser) -> None:
    """An inline call resolves only to what the page itself declares."""
    relations = relation_pairs(page.get_relations(PAGE, PAGE_PATH))
    assert ("boot", "calls", "symbol") in relations
    # `init` lives in some other file, whose symbols the extractor owns.
    assert ("init", "calls", "symbol") not in relations


def test_template_entities(template: PhtmlParser) -> None:
    """The PHP islands of a template declare classes, methods and functions."""
    entities = entity_names(template.get_entities(TEMPLATE, TEMPLATE_PATH))
    assert entities["Widget"] == "class"
    assert entities["render"] == "method"
    assert entities["helper"] == "function"


def test_template_relations(template: PhtmlParser) -> None:
    """An include reaches another file; a call reaches only this one."""
    relations = relation_pairs(template.get_relations(TEMPLATE, TEMPLATE_PATH))
    assert ("partials/head.phtml", "includes", "file") in relations
    assert ("format", "calls", "symbol") in relations
    assert ("helper", "calls", "symbol") in relations
    # Base and Renderable are declared elsewhere, so they get no edge here.
    assert not [target for target, kind, _ in relations if kind == "inherits"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("css/site.css", "css/site.css"),
        ("img/logo.png?v=2", "img/logo.png"),
        ("/static/app.js#top", "/static/app.js"),
        ("https://cdn.example.com/x.js", ""),
        ("//cdn.example.com/x.js", ""),
        ("data:text/css,body{}", ""),
        ("mailto:someone@example.com", ""),
        ("#section", ""),
        ("{{ asset }}", ""),
        ("   ", ""),
    ],
)
def test_asset_path(value: str, expected: str) -> None:
    """Only a value naming a file of this project survives."""
    assert asset_path(value) == expected


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("/static/app.js", ["web/static/app.js", "static/app.js"]),
        ("js/app.js?v=2", ["web/js/app.js", "js/app.js"]),
        ("../shared/x.js", ["shared/x.js"]),
        ("../../outside.js", []),
    ],
)
def test_html_candidates(target: str, expected: list[str]) -> None:
    """A reference is tried next to the page, then from the project root.

    A duplicate of the first candidate, and anything still climbing out of
    the tree, is left out.
    """
    assert html_candidates(target, PAGE_PATH) == expected
