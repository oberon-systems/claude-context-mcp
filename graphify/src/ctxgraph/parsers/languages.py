"""One parser per tree-sitter grammar.

Each class is a pair of queries; the behaviour lives in CodeParser.
"""

from __future__ import annotations

from typing import Any

import tree_sitter_bash
import tree_sitter_dockerfile
import tree_sitter_embedded_template
import tree_sitter_go
import tree_sitter_hcl
import tree_sitter_javascript
import tree_sitter_make
import tree_sitter_markdown
import tree_sitter_puppet
import tree_sitter_python
import tree_sitter_ruby
import tree_sitter_rust
import tree_sitter_toml
import tree_sitter_typescript
import tree_sitter_yaml
from tree_sitter import Language, Node, Parser, Query

from ctxgraph.parsers.base import CodeParser, node_text, strip_literal, unique_pairs


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

    def __init__(self) -> None:
        """Initialize Makefile parser."""
        super().__init__(tree_sitter_make.language())


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


class PuppetParser(CodeParser):
    """Puppet manifest AST parser.

    Two things the query pair cannot express on its own, so both are handled
    in the overrides below: a resource is named after two of its fields rather
    than a single captured node, and a template reference is the only Puppet
    relation whose target is a path instead of a symbol.
    """

    FAMILY = "puppet"
    ENTITY_QUERY = """
        (class_definition (class_identifier) @class)
        (defined_resource_type (class_identifier) @define)
        (node_definition (node_name) @node)
        (function_declaration (class_identifier) @function)
    """
    # `include stdlib` yields a bare identifier while `include a::b` yields a
    # class_identifier, so both alternatives have to be listed or every
    # reference to an unqualified class is lost.
    RELATION_QUERY = """
        (class_inherits (class_identifier) @inherits)
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
    """
    # `require =>` and `notify =>` are the ordering metaparameters worth an
    # edge; the rest of an attribute list is configuration data.
    ATTRIBUTE_RELATIONS = {"require": "requires", "notify": "notifies"}
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
