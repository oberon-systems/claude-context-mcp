"""One parser per tree-sitter grammar.

Each class is a pair of queries; the behaviour lives in CodeParser.
"""

from __future__ import annotations

import posixpath
import re
from typing import Any

import tree_sitter_bash
import tree_sitter_css
import tree_sitter_dockerfile
import tree_sitter_embedded_template
import tree_sitter_go
import tree_sitter_hcl
import tree_sitter_html
import tree_sitter_javascript
import tree_sitter_json
import tree_sitter_make
import tree_sitter_markdown
import tree_sitter_php
import tree_sitter_puppet
import tree_sitter_python
import tree_sitter_ruby
import tree_sitter_rust
import tree_sitter_toml
import tree_sitter_typescript
import tree_sitter_yaml
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from ctxgraph.parsers.base import (
    CodeParser,
    node_text,
    qualified,
    strip_literal,
    unique_pairs,
)


class PythonParser(CodeParser):
    """Python specific AST parser."""

    FAMILY = "python"
    ENTITY_QUERY = """
        (module (function_definition name: (identifier) @function))
        (module (decorated_definition
            definition: (function_definition name: (identifier) @function)))
        (module (class_definition name: (identifier) @class))
        (module (decorated_definition
            definition: (class_definition name: (identifier) @class)))
        (class_definition body:
            (block (function_definition name: (identifier) @method)))
        (class_definition body: (block (decorated_definition
            definition: (function_definition name: (identifier) @method))))
    """
    RELATION_QUERY = """
        (call function: (identifier) @calls)
        (call function: (attribute attribute: (identifier) @calls))
        (class_definition superclasses: (argument_list (identifier) @inherits))
        (class_definition superclasses:
            (argument_list (attribute attribute: (identifier) @inherits)))
        (import_statement name: (dotted_name) @imports)
        (import_statement name: (aliased_import name: (dotted_name) @imports))
        (import_from_statement module_name: (dotted_name) @imports)
        (import_from_statement module_name: (relative_import) @imports)
    """

    def __init__(self) -> None:
        """Initialize Python parser."""
        super().__init__(tree_sitter_python.language())


class TypeScriptParser(CodeParser):
    """TypeScript specific AST parser."""

    FAMILY = "ecmascript"
    ENTITY_QUERY = """
        (function_declaration name: (identifier) @function)
        (class_declaration name: (type_identifier) @class)
        (interface_declaration name: (type_identifier) @interface)
        (type_alias_declaration name: (type_identifier) @type)
        (method_definition name: (property_identifier) @method)
    """
    RELATION_QUERY = """
        (call_expression function: (identifier) @calls)
        (call_expression function:
            (member_expression property: (property_identifier) @calls))
        (class_declaration (class_heritage (extends_clause value: (_) @inherits)))
        (class_declaration (class_heritage
            (implements_clause (type_identifier) @inherits)))
        (interface_declaration (extends_type_clause (type_identifier) @inherits))
        (import_statement source: (string) @imports)
        (call_expression
            function: (import)
            arguments: (arguments (string) @imports))
    """

    def __init__(self) -> None:
        """Initialize TypeScript parser."""
        super().__init__(tree_sitter_typescript.language_typescript())


class TSXParser(TypeScriptParser):
    """TSX specific AST parser.

    Same queries as TypeScript, but the TSX grammar is a separate language:
    parsing `.tsx` with the plain TypeScript grammar produces error nodes on
    every JSX element.
    """

    def __init__(self) -> None:
        """Initialize TSX parser."""
        CodeParser.__init__(self, tree_sitter_typescript.language_tsx())


class JavaScriptParser(CodeParser):
    """JavaScript specific AST parser."""

    FAMILY = "ecmascript"
    ENTITY_QUERY = """
        (function_declaration name: (identifier) @function)
        (class_declaration name: (identifier) @class)
        (method_definition name: (property_identifier) @method)
        (lexical_declaration (variable_declarator
            name: (identifier) @function value: (arrow_function)))
    """
    RELATION_QUERY = """
        (call_expression function: (identifier) @calls)
        (call_expression function:
            (member_expression property: (property_identifier) @calls))
        (class_declaration (class_heritage (identifier) @inherits))
        (import_statement source: (string) @imports)
    """

    def __init__(self) -> None:
        """Initialize JavaScript parser."""
        super().__init__(tree_sitter_javascript.language())


# Attributes naming another file, by the tag carrying them and the edge each
# becomes.
HTML_ASSET_ATTRIBUTES: dict[tuple[str, str], str] = {
    ("script", "src"): "uses_script",
    ("link", "href"): "uses_style",
    ("img", "src"): "uses_file",
    ("source", "src"): "uses_file",
    ("iframe", "src"): "uses_file",
    ("form", "action"): "uses_file",
}
# A value that names something other than a file of this project: another
# origin, a bare fragment, a scheme of its own, or a placeholder only the
# engine rendering the page can expand.
HTML_FOREIGN_PREFIXES = ("//", "#")
HTML_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
HTML_PLACEHOLDERS = ("{{", "${", "<?", "<%")


def asset_path(value: str) -> str:
    """Return the project path an attribute points at, or "" when it is not one."""
    target = value.strip()
    if not target or target.startswith(HTML_FOREIGN_PREFIXES):
        return ""
    if HTML_SCHEME.match(target) or any(mark in target for mark in HTML_PLACEHOLDERS):
        return ""
    return target.split("?", 1)[0].split("#", 1)[0]


def tag_header(node: Node) -> Node | None:
    """Return the opening tag of an element, in either of its two forms."""
    for child in node.children:
        if child.type in {"start_tag", "self_closing_tag"}:
            return child
    return None


def tag_name(node: Node) -> str:
    """Return the lowercased name of an element."""
    header = tag_header(node)
    if header is None:
        return ""
    for child in header.children:
        if child.type == "tag_name":
            return node_text(child).lower()
    return ""


def attribute_value(node: Node) -> str:
    """Return the value of an attribute, quoted or bare."""
    for child in node.children:
        if child.type == "attribute_value":
            return node_text(child)
        if child.type == "quoted_attribute_value":
            for inner in child.children:
                if inner.type == "attribute_value":
                    return node_text(inner)
            return ""
    return ""


def tag_attributes(node: Node) -> dict[str, str]:
    """Return the attributes of an element, lowercased names to raw values."""
    header = tag_header(node)
    if header is None:
        return {}
    found: dict[str, str] = {}
    for attribute in header.children:
        if attribute.type != "attribute":
            continue
        for child in attribute.children:
            if child.type == "attribute_name":
                found[node_text(child).lower()] = attribute_value(attribute)
    return found


def element_text(node: Node) -> str:
    """Return the direct text of an element, with its whitespace collapsed."""
    parts = [node_text(child) for child in node.children if child.type == "text"]
    return " ".join(" ".join(parts).split())


def local_relations(
    relations: list[dict[str, str]], declared: set[str]
) -> list[dict[str, str]]:
    """Drop the symbol relations whose target this file does not declare itself.

    A symbol edge resolves against the entities our own parsers wrote, and the
    code in the `.js` and `.php` files next to this one belongs to the upstream
    extractor - so a call leaving the file would become an unresolved
    placeholder rather than an edge worth having.
    """
    return [
        relation
        for relation in relations
        if relation["scope"] == "file" or relation["target"] in declared
    ]


class CssParser(CodeParser):
    """CSS specific AST parser.

    Registered for no extension of its own: it is here for the `<style>`
    blocks HtmlParser hands over.
    """

    FAMILY = "css"
    ENTITY_QUERY = "(rule_set (selectors) @style)"

    def __init__(self) -> None:
        """Initialize CSS parser."""
        super().__init__(tree_sitter_css.language())

    def get_entities(self, content: str, rel_path: str) -> list[dict[str, str]]:
        """Return one node per rule, named by the selector it is written under."""
        if self.entity_query is None:
            return []
        root = self.parse(content)
        return unique_pairs(
            (qualified(kind, " ".join(text.split())), kind)
            for kind, text in self.capture_texts(self.entity_query, root)
        )


class HtmlParser(CodeParser):
    """HTML specific AST parser: the page, its assets and the code inside it.

    A page is markup and a place code is written, so the `<script>` and
    `<style>` bodies are joined in source order and handed to the grammar that
    can read them - the same division of labour TemplateParser makes for
    `<% %>`, without needing to reconstruct a half-open program.
    """

    # Deliberately the ecmascript family: what an inline script declares is a
    # JavaScript symbol, and the markup around it changes nothing about that.
    FAMILY = "ecmascript"
    ENTITY_QUERY = """
        (element) @element
        (script_element) @element
        (style_element) @element
    """

    def __init__(self) -> None:
        """Initialize HTML parser and the two grammars it delegates to."""
        super().__init__(tree_sitter_html.language())
        self.script_parser = JavaScriptParser()
        self.style_parser = CssParser()

    def elements(self, content: str) -> list[Node]:
        """Return every element of the page, tags with a body included."""
        if self.entity_query is None:
            return []
        captures = self.capture_nodes(self.entity_query, self.parse(content))
        return sorted(captures.get("element", []), key=lambda node: node.start_byte)

    @staticmethod
    def embedded(elements: list[Node], tag: str) -> str:
        """Join the bodies of every `<tag>` of the page into one source."""
        spans = [
            node_text(child).strip()
            for element in elements
            if tag_name(element) == tag
            for child in element.children
            if child.type == "raw_text"
        ]
        return "\n".join(span for span in spans if span)

    def get_entities(self, content: str, rel_path: str) -> list[dict[str, str]]:
        """Return the structure of the page plus what its inline code declares."""
        elements = self.elements(content)
        pairs: list[tuple[str, str]] = []
        for element in elements:
            name = tag_name(element)
            if name == "title":
                heading = element_text(element)
                if heading:
                    pairs.append((qualified("title", heading), "title"))
            anchor = tag_attributes(element).get("id", "").strip()
            if anchor:
                pairs.append((qualified("anchor", anchor), "anchor"))
            # A hyphen is what tells a custom element from a standard one, and
            # only the custom ones are a declaration rather than structure.
            if "-" in name:
                pairs.append((qualified("element", name), "element"))
        entities = unique_pairs(iter(pairs))
        script = self.embedded(elements, "script")
        if script:
            entities.extend(self.script_parser.get_entities(script, rel_path))
        style = self.embedded(elements, "style")
        if style:
            entities.extend(self.style_parser.get_entities(style, rel_path))
        return entities

    def get_relations(self, content: str, rel_path: str) -> list[dict[str, str]]:
        """Return the files the page pulls in, and the calls its own code makes."""
        elements = self.elements(content)
        pairs: list[tuple[str, str]] = []
        for element in elements:
            name = tag_name(element)
            for attribute, value in tag_attributes(element).items():
                relation_type = HTML_ASSET_ATTRIBUTES.get((name, attribute))
                if relation_type is not None:
                    pairs.append((asset_path(value), relation_type))
        relations = [
            {"target": entry["name"], "type": entry["type"], "scope": "file"}
            for entry in unique_pairs(iter(pairs))
        ]
        script = self.embedded(elements, "script")
        if script:
            declared = {
                entity["name"]
                for entity in self.script_parser.get_entities(script, rel_path)
            }
            relations.extend(
                local_relations(
                    self.script_parser.get_relations(script, rel_path), declared
                )
            )
        return relations


class GoParser(CodeParser):
    """Go specific AST parser."""

    FAMILY = "go"
    ENTITY_QUERY = """
        (function_declaration name: (identifier) @function)
        (method_declaration name: (field_identifier) @method)
        (type_spec name: (type_identifier) @type)
    """
    RELATION_QUERY = """
        (call_expression function: (identifier) @calls)
        (call_expression function: (selector_expression
            field: (field_identifier) @calls))
        (import_spec path: (interpreted_string_literal) @imports)
    """

    def __init__(self) -> None:
        """Initialize Go parser."""
        super().__init__(tree_sitter_go.language())


class RustParser(CodeParser):
    """Rust specific AST parser."""

    FAMILY = "rust"
    ENTITY_QUERY = """
        (function_item name: (identifier) @function)
        (struct_item name: (type_identifier) @struct)
        (enum_item name: (type_identifier) @enum)
        (trait_item name: (type_identifier) @trait)
    """
    RELATION_QUERY = """
        (call_expression function: (identifier) @calls)
        (call_expression function: (field_expression field: (field_identifier) @calls))
        (use_declaration argument: (_) @imports)
    """

    def __init__(self) -> None:
        """Initialize Rust parser."""
        super().__init__(tree_sitter_rust.language())


class BashParser(CodeParser):
    """Bash specific AST parser."""

    FAMILY = "bash"
    ENTITY_QUERY = "(function_definition name: (word) @function)"

    def __init__(self) -> None:
        """Initialize Bash parser."""
        super().__init__(tree_sitter_bash.language())


class DockerfileParser(CodeParser):
    """Dockerfile specific AST parser."""

    FAMILY = "dockerfile"
    ENTITY_QUERY = """
        (from_instruction (image_spec (image_name) @image))
        (from_instruction (image_alias) @stage)
    """

    def __init__(self) -> None:
        """Initialize Dockerfile parser."""
        super().__init__(tree_sitter_dockerfile.language())


class HCLParser(CodeParser):
    """HCL and Terraform specific AST parser."""

    FAMILY = "hcl"
    ENTITY_QUERY = "(block) @block"

    def __init__(self) -> None:
        """Initialize HCL parser."""
        super().__init__(tree_sitter_hcl.language())

    def get_entities(self, content: str, rel_path: str) -> list[dict[str, str]]:
        """Name a block by its type and labels, e.g. resource.aws_s3_bucket.main."""
        if self.entity_query is None:
            return []
        captures = self.capture_nodes(self.entity_query, self.parse(content))
        pairs: list[tuple[str, str]] = []
        for block in captures.get("block", []):
            kind = ""
            labels: list[str] = []
            for child in block.named_children:
                if child.type == "identifier" and not kind:
                    kind = node_text(child)
                elif child.type == "string_lit":
                    labels.extend(
                        node_text(part)
                        for part in child.named_children
                        if part.type == "template_literal"
                    )
            if kind:
                pairs.append((".".join([kind, *labels]), "block"))
        return unique_pairs(iter(pairs))


class MakeParser(CodeParser):
    """Makefile specific AST parser."""

    FAMILY = "make"
    ENTITY_QUERY = "(rule (targets (word) @target))"
    RELATION_QUERY = """
        (rule (targets (word) @target) (prerequisites (word) @prerequisite))
    """
    # Directives that read as targets. `.PHONY: help init` names three other
    # targets, and taking it for a rule leaves a false node in every Makefile.
    # Matched by name rather than by the leading dot: `.venv:` and `.env:` are
    # ordinary rules and keep their nodes.
    SPECIAL_MAKE_TARGETS = frozenset(
        {
            ".PHONY",
            ".SUFFIXES",
            ".DEFAULT",
            ".PRECIOUS",
            ".INTERMEDIATE",
            ".SECONDARY",
            ".SECONDEXPANSION",
            ".DELETE_ON_ERROR",
            ".IGNORE",
            ".SILENT",
            ".EXPORT_ALL_VARIABLES",
            ".NOTPARALLEL",
            ".ONESHELL",
            ".POSIX",
        }
    )

    def __init__(self) -> None:
        """Initialize Makefile parser."""
        super().__init__(tree_sitter_make.language())

    @staticmethod
    def blank_recipes(content: str) -> str:
        """Replace every recipe line with an empty one.

        A recipe is shell, and the grammar reads it as make. One unbalanced
        quote in it - `psql -tAc "select ..."` continued over four lines - and
        the parse turns into a single string node that swallows the rest of
        the file: the root Makefile here yielded 11 of its 23 targets, the
        ones declared above the first such recipe. Nothing in a recipe is
        wanted anyway, since only targets and prerequisites become nodes.

        The lines are blanked rather than dropped so the line numbering of
        what is left still matches the file on disk. A physical line starting
        with a tab is only a recipe when it starts a logical line: the
        continuation of a `.PHONY` list broken over several lines is indented
        the same way and has to survive, or the trailing backslash left
        hanging swallows the rule below it.
        """
        blanked: list[str] = []
        continued = False
        in_recipe = False
        for line in content.split("\n"):
            if not continued:
                in_recipe = line.startswith("\t")
            continued = line.endswith("\\")
            blanked.append("" if in_recipe else line)
        return "\n".join(blanked)

    def parse(self, content: str) -> Node:
        """Parse the file with its recipes blanked out."""
        return super().parse(self.blank_recipes(content))

    def get_entities(self, content: str, rel_path: str) -> list[dict[str, str]]:
        """Filter out GNU make special targets."""
        entities = super().get_entities(content, rel_path)
        return [e for e in entities if e["name"] not in self.SPECIAL_MAKE_TARGETS]

    def get_relations(self, content: str, rel_path: str) -> list[dict[str, str]]:
        """Return one depends_on relation per prerequisite of a rule.

        The base implementation flattens every capture of the file into one
        bag, which loses the rule a prerequisite belonged to. Matches keep the
        two captures paired, and that pairing is the whole content of the
        edge: the source is the target being defined, not the file.

        A `$(OBJS)` prerequisite is a `variable_reference` in the grammar
        rather than a `word`, so the pattern already leaves it out.
        """
        if self.relation_query is None:
            return []
        seen: set[tuple[str, str]] = set()
        relations: list[dict[str, str]] = []
        for _, captures in QueryCursor(self.relation_query).matches(
            self.parse(content)
        ):
            for target_node in captures.get("target", ()):
                target = node_text(target_node)
                if target in self.SPECIAL_MAKE_TARGETS:
                    continue
                for prerequisite_node in captures.get("prerequisite", ()):
                    prerequisite = node_text(prerequisite_node)
                    if "$(" in prerequisite or "${" in prerequisite:
                        continue
                    if (target, prerequisite) in seen:
                        continue
                    seen.add((target, prerequisite))
                    relations.append(
                        {
                            "source": target,
                            "target": prerequisite,
                            "type": "depends_on",
                            "scope": "symbol",
                        }
                    )
        return relations


class MarkdownParser(CodeParser):
    """Markdown specific AST parser."""

    FAMILY = "markdown"
    ENTITY_QUERY = """
        (atx_heading (atx_h1_marker) (inline) @heading)
        (atx_heading (atx_h2_marker) (inline) @heading)
    """

    def __init__(self) -> None:
        """Initialize Markdown parser."""
        super().__init__(tree_sitter_markdown.language())


class TOMLParser(CodeParser):
    """TOML specific AST parser."""

    FAMILY = "toml"
    ENTITY_QUERY = """
        (table (bare_key) @table)
        (table (dotted_key) @table)
    """

    def __init__(self) -> None:
        """Initialize TOML parser."""
        super().__init__(tree_sitter_toml.language())


class YAMLParser(CodeParser):
    """YAML specific AST parser."""

    FAMILY = "yaml"
    # Top level keys only. Capturing every nested key turns a compose file into
    # hundreds of nodes named `image` or `ports`.
    ENTITY_QUERY = """
        (document (block_node (block_mapping
            (block_mapping_pair key: (flow_node) @key))))
    """

    def __init__(self) -> None:
        """Initialize YAML parser."""
        super().__init__(tree_sitter_yaml.language())


# JSON keeps its meaning in the values rather than in the syntax, so the
# helpers below take a captured pair apart instead of matching ever deeper
# query patterns.
def pair_key(node: Node) -> str:
    """Return the key of a `pair` node, unquoted."""
    key_node = node.child_by_field_name("key")
    return strip_literal(node_text(key_node)) if key_node is not None else ""


def pair_value(node: Node) -> Node | None:
    """Return the value of a `pair` node."""
    return node.child_by_field_name("value")


def object_pairs(node: Node | None) -> list[Node]:
    """Return the `pair` children of an object node."""
    if node is None or node.type != "object":
        return []
    return [child for child in node.named_children if child.type == "pair"]


def object_keys(node: Node | None) -> list[str]:
    """Return the keys an object node declares."""
    return [pair_key(pair) for pair in object_pairs(node)]


def string_value(node: Node | None) -> str:
    """Return the text of a string node, unquoted, and "" for anything else."""
    if node is None or node.type != "string":
        return ""
    return strip_literal(node_text(node))


def local_target(node: Node | None) -> str:
    """Return a string value when it names a path inside the project.

    Only a relative target can resolve to a file of the tree. A bare specifier
    such as `@tsconfig/node20/tsconfig.json` names a package, and keeping it
    would add a placeholder node pointing at nothing.
    """
    target = string_value(node)
    return target if target.startswith((".", "/")) else ""


def extends_targets(value: Node | None) -> list[str]:
    """Return what an `extends` key points at, in either form it takes."""
    if value is None:
        return []
    if value.type == "array":
        candidates = [local_target(child) for child in value.named_children]
    else:
        candidates = [local_target(value)]
    return [target for target in candidates if target]


def reference_targets(value: Node | None) -> list[str]:
    """Return the paths of a tsconfig `references` array."""
    if value is None or value.type != "array":
        return []
    targets: list[str] = []
    for entry in value.named_children:
        for pair in object_pairs(entry):
            if pair_key(pair) == "path":
                target = local_target(pair_value(pair))
                if target:
                    targets.append(target)
    return targets


# The dependency sections worth reading, with the prefix their targets carry,
# keyed by file name so the keys of one ecosystem are never read out of
# another file's JSON.
#
# The prefix is not decoration. Without it `resolve_symbol` would bind a
# dependency called `typescript` to a top level key node of that name in some
# other JSON file of the same family. Prefixed, it matches no declared symbol,
# so one external node is written per package - the same shape the Ansible
# `role:` placeholders have.
DEPENDENCY_SECTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "package.json": (
        "npm:",
        (
            "dependencies",
            "devDependencies",
            "optionalDependencies",
            "peerDependencies",
        ),
    ),
    "composer.json": ("composer:", ("require", "require-dev")),
}
# The sections descended into for entities, and the type their keys become.
# The list is closed on purpose: descending into every second level turns a
# data file into hundreds of nodes named after its keys.
SECTION_ENTITIES = {
    "mcpServers": "mcp_server",
    "scripts": "script",
    "servers": "mcp_server",
}


class JsonParser(CodeParser):
    """JSON specific AST parser.

    JSON declares nothing, so its syntax alone yields no entity worth a node.
    Top level keys become nodes for any file, and the manifests that do carry
    structure - `package.json`, `tsconfig.json`, `composer.json`, `.mcp.json` -
    are read one level deeper, by name.
    """

    FAMILY = "json"
    # Each top level pair is captured whole and taken apart in Python. Deep
    # patterns over nested pairs are brittle, and the query already does the
    # one thing worth doing declaratively: anchoring to the top level, so a
    # document whose root is an array yields nothing.
    ENTITY_QUERY = "(document (object (pair) @entry))"

    def __init__(self) -> None:
        """Initialize JSON parser."""
        super().__init__(tree_sitter_json.language())

    def top_level_pairs(self, content: str) -> list[Node]:
        """Return the pairs of the top level object of a document."""
        if self.entity_query is None:
            return []
        captures = self.capture_nodes(self.entity_query, self.parse(content))
        return captures.get("entry", [])

    def get_entities(self, content: str, rel_path: str) -> list[dict[str, str]]:
        """Return the top level keys, and the keys of the known sections."""
        pairs: list[tuple[str, str]] = []
        for pair in self.top_level_pairs(content):
            key = pair_key(pair)
            pairs.append((key, "key"))
            entity_type = SECTION_ENTITIES.get(key)
            if entity_type is not None:
                pairs.extend(
                    (name, entity_type) for name in object_keys(pair_value(pair))
                )
        return unique_pairs(iter(pairs))

    def get_relations(self, content: str, rel_path: str) -> list[dict[str, str]]:
        """Return what a manifest depends on and what it extends."""
        prefix, sections = DEPENDENCY_SECTIONS.get(
            posixpath.basename(rel_path), ("", ())
        )
        pairs: list[tuple[str, str]] = []
        for pair in self.top_level_pairs(content):
            key = pair_key(pair)
            value = pair_value(pair)
            if key in sections:
                pairs.extend(
                    (f"{prefix}{name}", "depends_on") for name in object_keys(value)
                )
            elif key == "extends":
                pairs.extend((target, "extends") for target in extends_targets(value))
            elif key == "references":
                pairs.extend((target, "extends") for target in reference_targets(value))
        return [
            {
                "target": entry["name"],
                "type": entry["type"],
                # A dependency is a name; an `extends` target is a path, which
                # resolve_file_target tries against this file's directory and
                # then the project root.
                "scope": "symbol" if entry["type"] == "depends_on" else "file",
            }
            for entry in unique_pairs(iter(pairs))
        ]


class PuppetParser(CodeParser):
    """Puppet manifest AST parser.

    Three things the query pair cannot express on its own, so all of them are
    handled in the overrides below: a resource is named after two of its fields
    rather than a single captured node, a template reference is the only Puppet
    relation whose target is a path instead of a symbol, and `class_identifier`
    is one node type serving three unrelated jobs - a declaration's name, a
    resource's type, and a qualified variable read - which only the surrounding
    nodes tell apart.
    """

    FAMILY = "puppet"
    # A single-segment Puppet name is a plain `identifier` node; only a
    # qualified name is a `class_identifier`. Both must be listed or
    # `class package_repo` is lost as an entity.
    ENTITY_QUERY = """
        (class_definition [(class_identifier) (identifier)] @class)
        (defined_resource_type [(class_identifier) (identifier)] @define)
        (node_definition (node_name) @node)
        (function_declaration [(class_identifier) (identifier)] @function)
    """
    # `include stdlib` yields a bare identifier while `include a::b` yields a
    # class_identifier, so both alternatives have to be listed or every
    # reference to an unqualified class is lost.
    RELATION_QUERY = """
        (class_inherits [(class_identifier) (identifier)] @inherits)
        (include_statement [(class_identifier) (identifier)] @includes)
        (require_statement [(class_identifier) (identifier)] @requires)
    """
    # Resources, ordering metaparameters, the -> and ~> chains, and the
    # template calls all need their children inspected, so they are captured
    # whole and taken apart in Python.
    DETAIL_QUERY = """
        (resource_declaration) @resource
        (attribute) @attribute
        (relation) @relation
        (function_call) @call
        (class_identifier) @scoped
    """
    # `require =>` and `notify =>` are the ordering metaparameters worth an
    # edge; the rest of an attribute list is configuration data.
    ATTRIBUTE_RELATIONS = {"require": "requires", "notify": "notifies"}
    # `$a::b` and `"${a::b}"` put the qualified name under one of these, which
    # is what tells a variable read apart from the same node type used for a
    # declaration or a resource type. The top scope form `$::a::b` is parented
    # by whatever consumes it and is recognised by its own sigil instead.
    SCOPED_READ_PARENTS = frozenset({"variable", "interpolation"})
    # The functions that render a template, in either template language.
    TEMPLATE_FUNCTIONS = frozenset({"epp", "template"})

    def __init__(self) -> None:
        """Initialize Puppet parser."""
        super().__init__(tree_sitter_puppet.language())
        self.detail_query = self._compile(self.DETAIL_QUERY)

    def resource_name(self, node: Node) -> str:
        """Name a resource declaration after its type and title.

        `package { 'nginx': }` becomes `package.nginx`, which is what a
        `Package['nginx']` reference elsewhere in the tree resolves to.
        """
        type_node = node.child_by_field_name("type")
        title_node = node.child_by_field_name("title")
        if type_node is None or title_node is None:
            return ""
        return f"{node_text(type_node).lower()}.{strip_literal(node_text(title_node))}"

    def reference_name(self, node: Node) -> str:
        """Name what a `Package['nginx']` style reference points at.

        `Class['a::b']` names a class and resolves to the class entity, while
        every other reference names a resource and matches `resource_name`.
        """
        parts = [child for child in node.named_children if child.type != "comment"]
        if len(parts) < 2:
            return ""
        kind = node_text(parts[0]).lower()
        title = strip_literal(node_text(parts[1]))
        return title if kind == "class" else f"{kind}.{title}"

    def template_argument(self, node: Node) -> str:
        """Return the template a `template(...)` or `epp(...)` call renders."""
        parts = node.named_children
        if not parts or node_text(parts[0]).lower() not in self.TEMPLATE_FUNCTIONS:
            return ""
        for child in parts[1:]:
            if child.type == "string":
                return strip_literal(node_text(child))
        return ""

    def scoped_variable_class(self, node: Node) -> str:
        """Name the class a qualified variable reference reads a parameter of.

        `$::package_repo::user` and `$package_repo::base_dir` both read a
        parameter of class `package_repo`, and `$profile::web::port` reads one
        of `profile::web`: the last segment is the variable, everything before
        it is the class. A single segment therefore names no class at all,
        which is what `$::osfamily` and the other top scope facts are.

        The segments come from the child nodes rather than from splitting the
        text, because the text of the top scope form keeps its `$::` prefix and
        would leave that on the first segment.
        """
        if not (
            node_text(node).startswith("$")
            or (
                node.parent is not None and node.parent.type in self.SCOPED_READ_PARENTS
            )
        ):
            return ""
        segments = [
            node_text(child)
            for child in node.named_children
            if child.type == "identifier"
        ]
        if len(segments) < 2:
            return ""
        return "::".join(segments[:-1])

    def declared_resource_type(self, node: Node) -> str:
        """Name the defined type a resource declaration instantiates.

        `accounts::user { 'repo': }` is an instance of `define accounts::user`,
        so the whole qualified name is the target and nothing is stripped off
        it. A built-in type is a bare `identifier` and never reaches here,
        which leaves exactly the module defined types.

        Compared by equality rather than identity: tree-sitter hands out a
        fresh wrapper object for the same underlying node, so `is` would fail
        on a node that is in fact the type field.
        """
        parent = node.parent
        if parent is None or parent.type != "resource_declaration":
            return ""
        if parent.child_by_field_name("type") != node:
            return ""
        return node_text(node)

    def get_entities(self, content: str, rel_path: str) -> list[dict[str, str]]:
        """Return the declarations and the resources a manifest holds."""
        root = self.parse(content)
        pairs: list[tuple[str, str]] = []
        if self.entity_query is not None:
            for kind, text in self.capture_texts(self.entity_query, root):
                # A node definition is quoted; a class identifier is not.
                pairs.append((strip_literal(text.strip()), kind))
        if self.detail_query is not None:
            for node in self.capture_nodes(self.detail_query, root).get("resource", []):
                pairs.append((self.resource_name(node), "resource"))
        return unique_pairs(iter(pairs))

    def get_relations(self, content: str, rel_path: str) -> list[dict[str, str]]:
        """Return what a manifest inherits, includes, orders after and renders."""
        root = self.parse(content)
        pairs: list[tuple[str, str]] = []
        if self.relation_query is not None:
            for kind, text in self.capture_texts(self.relation_query, root):
                # `include ::stdlib` and `include stdlib` name the same class.
                pairs.append((text.strip().lstrip(":"), kind))

        templates: list[str] = []
        captures = (
            self.capture_nodes(self.detail_query, root)
            if self.detail_query is not None
            else {}
        )

        for node in captures.get("attribute", []):
            name_node = node.child_by_field_name("name")
            value_node = node.child_by_field_name("value")
            if name_node is None or value_node is None:
                continue
            relation = self.ATTRIBUTE_RELATIONS.get(node_text(name_node).lower())
            if relation and value_node.type == "resource_reference":
                pairs.append((self.reference_name(value_node), relation))

        # `A -> B` orders B after A, which is the same statement `require`
        # makes from B, so both arrive as one relation type.
        for node in captures.get("relation", []):
            references = [
                child
                for child in node.named_children
                if child.type == "resource_reference"
            ]
            for left, right in zip(references, references[1:], strict=False):
                pairs.append((self.reference_name(right), "requires"))
                pairs.append((self.reference_name(left), "requires"))

        for node in captures.get("call", []):
            argument = self.template_argument(node)
            if argument:
                templates.append(argument)

        # One capture, two edges: every `::` joined name in the manifest arrives
        # here, including the declarations and whatever a line the grammar
        # cannot parse leaves behind, so each edge admits only the shapes it
        # recognises rather than excluding the ones it does not.
        for node in captures.get("scoped", []):
            owner = self.scoped_variable_class(node)
            if owner:
                pairs.append((owner, "reads_var"))
            declared = self.declared_resource_type(node)
            if declared:
                pairs.append((declared, "references"))

        relations = [
            {"target": entry["name"], "type": entry["type"], "scope": "symbol"}
            for entry in unique_pairs(iter(pairs))
        ]
        relations.extend(
            {"target": entry["name"], "type": entry["type"], "scope": "file"}
            for entry in unique_pairs((target, "uses_template") for target in templates)
        )
        return relations


class TemplateParser(CodeParser):
    """Base class for the `<% %>` template languages.

    The embedded-template grammar isolates the code spans but leaves what is
    inside them opaque, and re-parsing a span on its own fails: `<% items.each
    do |i| %>` is a half-open block. Joining every span back together in source
    order reconstructs a program the inner grammar can read in one parse.
    """

    # Run over the reconstructed buffer by the inner grammar of a subclass.
    INNER_QUERY: str = ""
    CODE_QUERY = "(code) @code"

    def __init__(self, inner_language: Any) -> None:  # noqa: ANN401
        """Compile the outer template queries and the inner grammar's own."""
        super().__init__(tree_sitter_embedded_template.language())
        self.code_query = self._compile(self.CODE_QUERY)
        self.inner_language = Language(inner_language)
        self.inner_parser = Parser(self.inner_language)
        self.inner_query = Query(self.inner_language, self.INNER_QUERY)

    def code_buffer(self, content: str) -> str:
        """Reassemble the code spans of a template into one program."""
        if self.code_query is None:
            return ""
        nodes = self.capture_nodes(self.code_query, self.parse(content)).get("code", [])
        return "\n".join(
            node_text(node).strip()
            for node in sorted(nodes, key=lambda n: n.start_byte)
        )

    def get_entities(self, content: str, rel_path: str) -> list[dict[str, str]]:
        """Return the variables a template reads."""
        buffer = self.code_buffer(content)
        if not buffer:
            return []
        # An error node is expected here and is not a failure: an EPP parameter
        # block is not manifest syntax. Recovery still yields the variables.
        root = self.inner_parser.parse(buffer.encode("utf-8")).root_node
        return unique_pairs(
            (node_text(node).strip(), "variable")
            for node in self.capture_nodes(self.inner_query, root).get("variable", [])
        )


class ErbParser(TemplateParser):
    """ERB template parser, reading the Ruby embedded in one."""

    # Deliberately not "ruby": `.rb` files go to the graphifyy extractor and
    # have no parser here, so no ruby family exists in this pass and claiming
    # one would only let a template variable bind to something unrelated.
    FAMILY = "erb"
    INNER_QUERY = "(instance_variable) @variable"

    def __init__(self) -> None:
        """Initialize ERB parser."""
        super().__init__(tree_sitter_ruby.language())


class EppParser(TemplateParser):
    """EPP template parser, reading the Puppet embedded in one."""

    FAMILY = "puppet"
    INNER_QUERY = "(variable (identifier) @variable)"

    def __init__(self) -> None:
        """Initialize EPP parser."""
        super().__init__(tree_sitter_puppet.language())


class PhtmlParser(CodeParser):
    """PHP template parser, reading the PHP islands of a `.phtml` file.

    `.php` itself belongs to the upstream extractor, which routes on that one
    suffix and so never sees a template. No reassembly is needed here: the
    grammar this uses is the variant that reads text interleaved with
    `<?php ?>` rather than PHP alone.
    """

    FAMILY = "php"
    ENTITY_QUERY = """
        (function_definition name: (name) @function)
        (class_declaration name: (name) @class)
        (interface_declaration name: (name) @interface)
        (trait_declaration name: (name) @trait)
        (method_declaration name: (name) @method)
    """
    RELATION_QUERY = """
        (function_call_expression function: (name) @calls)
        (function_call_expression function: (qualified_name (name) @calls))
        (member_call_expression name: (name) @calls)
        (scoped_call_expression name: (name) @calls)
        (base_clause (name) @inherits)
        (class_interface_clause (name) @inherits)
        (class_interface_clause (qualified_name (name) @inherits))
    """
    # The one relation of a template that reaches another file, and the reason
    # `.phtml` is worth a parser at all.
    INCLUDE_QUERY = """
        (include_expression (string (string_content) @includes))
        (include_once_expression (string (string_content) @includes))
        (require_expression (string (string_content) @includes))
        (require_once_expression (string (string_content) @includes))
    """

    def __init__(self) -> None:
        """Initialize PHP template parser."""
        super().__init__(tree_sitter_php.language_php())
        self.include_query = self._compile(self.INCLUDE_QUERY)

    def get_relations(self, content: str, rel_path: str) -> list[dict[str, str]]:
        """Return the files the template includes, and the calls it answers itself."""
        declared = {entity["name"] for entity in self.get_entities(content, rel_path)}
        relations = local_relations(super().get_relations(content, rel_path), declared)
        if self.include_query is None:
            return relations
        root = self.parse(content)
        includes = unique_pairs(
            (text.strip(), kind)
            for kind, text in self.capture_texts(self.include_query, root)
        )
        relations.extend(
            {"target": entry["name"], "type": entry["type"], "scope": "file"}
            for entry in includes
        )
        return relations
