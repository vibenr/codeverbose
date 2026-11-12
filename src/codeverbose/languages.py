"""Language definitions.

Adding a language = adding one ``LanguageSpec`` to ``_SPECS`` below.
Everything else (metrics, labeling, reports) is language-agnostic and works
off the node-type names declared here. Use ``tree-sitter`` playgrounds or
``codeverbose debug-ast <file>`` to discover node type names for a grammar.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from tree_sitter import Language, Node, Parser


@dataclass(frozen=True)
class LanguageSpec:
    name: str
    extensions: tuple[str, ...]
    # Python module exposing ``language()`` (the official tree-sitter-<lang> wheels do).
    grammar_module: str
    # Nodes that define a unit we measure (methods, functions, constructors).
    function_types: frozenset[str]
    # Comment nodes.
    comment_types: frozenset[str]
    # Each occurrence adds 1 to cyclomatic complexity.
    decision_types: frozenset[str]
    # Binary-operator nodes whose operator is a short-circuit && / || / and / or.
    boolean_op_types: frozenset[str] = frozenset()
    boolean_operators: frozenset[str] = frozenset({"&&", "||", "and", "or"})
    identifier_types: frozenset[str] = frozenset({"identifier"})
    # Literal node types representing null / None.
    null_literal_types: frozenset[str] = frozenset()
    # Call node types and how to get the callee text.
    call_types: frozenset[str] = frozenset()
    catch_types: frozenset[str] = frozenset()
    throw_types: frozenset[str] = frozenset()
    # Callee prefixes / names treated as logging (matched against callee text).
    log_receivers: frozenset[str] = frozenset()
    log_functions: frozenset[str] = frozenset()
    # Null-check helper calls (e.g. Objects.requireNonNull).
    null_check_calls: frozenset[str] = frozenset()
    # Whether the first string expression in a function body is a docstring.
    has_docstrings: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def language(self) -> Language:
        return _load_language(self.grammar_module)

    def parser(self) -> Parser:
        return Parser(self.language)


@cache
def _load_language(module_name: str) -> Language:
    module = importlib.import_module(module_name)
    return Language(module.language())


JAVA = LanguageSpec(
    name="java",
    extensions=(".java",),
    grammar_module="tree_sitter_java",
    function_types=frozenset({"method_declaration", "constructor_declaration"}),
    comment_types=frozenset({"line_comment", "block_comment"}),
    decision_types=frozenset(
        {
            "if_statement",
            "for_statement",
            "enhanced_for_statement",
            "while_statement",
            "do_statement",
            "catch_clause",
            "ternary_expression",
            "switch_label",
        }
    ),
    boolean_op_types=frozenset({"binary_expression"}),
    null_literal_types=frozenset({"null_literal"}),
    call_types=frozenset({"method_invocation"}),
    catch_types=frozenset({"catch_clause"}),
    throw_types=frozenset({"throw_statement"}),
    log_receivers=frozenset(
        {"log", "logger", "LOG", "LOGGER", "Log", "Logger", "System.out", "System.err"}
    ),
    null_check_calls=frozenset(
        {
            "Objects.requireNonNull",
            "Objects.isNull",
            "Objects.nonNull",
            "Optional.ofNullable",
            "Preconditions.checkNotNull",
            "Assert.notNull",
        }
    ),
)

PYTHON = LanguageSpec(
    name="python",
    extensions=(".py",),
    grammar_module="tree_sitter_python",
    function_types=frozenset({"function_definition"}),
    comment_types=frozenset({"comment"}),
    decision_types=frozenset(
        {
            "if_statement",
            "elif_clause",
            "for_statement",
            "while_statement",
            "except_clause",
            "conditional_expression",
            "if_clause",  # comprehension filters
            "case_clause",
        }
    ),
    boolean_op_types=frozenset({"boolean_operator"}),
    null_literal_types=frozenset({"none"}),
    call_types=frozenset({"call"}),
    catch_types=frozenset({"except_clause"}),
    throw_types=frozenset({"raise_statement"}),
    log_receivers=frozenset({"log", "logger", "logging", "LOG", "LOGGER", "_log", "_logger"}),
    log_functions=frozenset({"print"}),
    null_check_calls=frozenset(),
    has_docstrings=True,
)

_SPECS: tuple[LanguageSpec, ...] = (JAVA, PYTHON)


def all_languages() -> tuple[LanguageSpec, ...]:
    return _SPECS


def by_name(name: str) -> LanguageSpec:
    for spec in _SPECS:
        if spec.name == name:
            return spec
    raise KeyError(f"unknown language {name!r}; known: {[s.name for s in _SPECS]}")


def for_path(path: str | Path, enabled: tuple[LanguageSpec, ...] = _SPECS) -> LanguageSpec | None:
    suffix = Path(path).suffix.lower()
    for spec in enabled:
        if suffix in spec.extensions:
            return spec
    return None


def callee_text(node: Node, spec: LanguageSpec) -> str:
    """Return the dotted callee of a call node, e.g. ``log.info`` or ``print``."""
    if spec.name == "java":
        name = node.child_by_field_name("name")
        obj = node.child_by_field_name("object")
        parts = []
        if obj is not None:
            parts.append(obj.text.decode("utf-8", "replace"))
        if name is not None:
            parts.append(name.text.decode("utf-8", "replace"))
        return ".".join(parts)
    fn = node.child_by_field_name("function")
    return fn.text.decode("utf-8", "replace") if fn is not None else ""
