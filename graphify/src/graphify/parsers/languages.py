"""One parser per tree-sitter grammar.

Each class is a pair of queries; the behaviour lives in CodeParser.
"""

from __future__ import annotations

import tree_sitter_bash
import tree_sitter_dockerfile
import tree_sitter_go
import tree_sitter_hcl
import tree_sitter_javascript
import tree_sitter_make
import tree_sitter_markdown
import tree_sitter_python
import tree_sitter_rust
import tree_sitter_toml
import tree_sitter_typescript
import tree_sitter_yaml

from graphify.parsers.base import CodeParser, node_text, unique_pairs


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
