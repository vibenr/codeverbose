"""Per-function verbosity metrics.

Every metric is computed per function (method / constructor) so it can be
attributed to whoever wrote that function and normalised by how much logic
it contains. See docs/METRICS.md for the rationale behind each one.
"""

from __future__ import annotations

import re
import zlib
from collections.abc import Iterator
from dataclasses import asdict, dataclass

from tree_sitter import Node

from codeverbose.languages import LanguageSpec, callee_text


@dataclass
class FunctionMetrics:
    file: str
    function: str
    language: str
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    lines_total: int
    loc: int  # lines containing code
    comment_lines: int  # lines containing only comments (inside the function)
    doc_lines: int  # javadoc / docstring lines attached to the function
    complexity: int  # cyclomatic complexity (1 + decision points)
    tokens: int  # non-comment leaf tokens
    avg_identifier_len: float
    compression_ratio: float  # zlib(size)/size; LOWER = more repetitive
    null_checks: int
    log_calls: int
    catch_blocks: int
    trivial_catches: int  # catch bodies that only log / rethrow / pass
    narrating_comments: int  # comments that restate the next line of code
    style_signals: int  # "Step 1:", "This function ...", emoji in comments

    @property
    def loc_per_complexity(self) -> float:
        return self.loc / max(self.complexity, 1)

    def as_row(self) -> dict:
        row = asdict(self)
        row["loc_per_complexity"] = round(self.loc_per_complexity, 3)
        return row


_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_STOPWORDS = frozenset(
    "the a an to of and or for in on is it this that be with as by if then else we our "
    "from at are was will can should not now new get set".split()
)
_STYLE_PATTERNS = [
    re.compile(r"^\s*(?:#|//|/?\*+)?\s*step\s*\d+\b", re.I),
    re.compile(r"\bthis (?:function|method|class|helper|block|code)\b", re.I),
    re.compile(r"\b(?:here we|let's|now we)\b", re.I),
    re.compile(r"[\u2705\u274c\u26a0\U0001F300-\U0001FAFF]"),  # ✅ ❌ ⚠ and most emoji
    re.compile(r"^\s*(?:#|//)\s*={3,}"),  # banner comments
]


def split_identifier(name: str) -> list[str]:
    words: list[str] = []
    for part in name.split("_"):
        words.extend(w.lower() for w in _CAMEL.findall(part))
    return [w for w in words if w]


def _comment_words(text: str) -> set[str]:
    words: set[str] = set()
    for raw in _WORD.findall(text):
        for w in split_identifier(raw):
            if len(w) >= 3 and w not in _STOPWORDS:
                words.add(_stem(w))
    return words


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _has_style_signal(text: str) -> bool:
    return any(p.search(line) for line in text.splitlines() for p in _STYLE_PATTERNS)


def _walk(node: Node) -> Iterator[Node]:
    """Pre-order traversal of ``node`` and all its descendants."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def iter_functions(root: Node, spec: LanguageSpec) -> Iterator[Node]:
    """Yield outermost function nodes (nested functions are measured with their parent)."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in spec.function_types:
            yield node
            continue
        stack.extend(reversed(node.children))


def function_name(node: Node) -> str:
    name = node.child_by_field_name("name")
    if name is None:
        return "<anonymous>"
    text = name.text.decode("utf-8", "replace")
    # Qualify with enclosing class names for readability: Outer.Inner.method
    owners = []
    parent = node.parent
    while parent is not None:
        if parent.type in {
            "class_declaration",
            "class_definition",
            "interface_declaration",
            "enum_declaration",
            "record_declaration",
        }:
            owner = parent.child_by_field_name("name")
            if owner is not None:
                owners.append(owner.text.decode("utf-8", "replace"))
        parent = parent.parent
    return ".".join(list(reversed(owners)) + [text])


def _leading_doc_comments(node: Node, spec: LanguageSpec) -> list[Node]:
    """Comment nodes (e.g. Javadoc) immediately preceding the function."""
    found: list[Node] = []
    prev = node.prev_named_sibling
    expected_row = node.start_point[0]
    # Java modifiers/annotations live inside the declaration, so the doc
    # comment is the previous sibling.
    while prev is not None and prev.type in spec.comment_types:
        if prev.end_point[0] < expected_row - 1:
            break
        found.append(prev)
        expected_row = prev.start_point[0]
        prev = prev.prev_named_sibling
    return found


def _docstring_node(node: Node, spec: LanguageSpec) -> Node | None:
    if not spec.has_docstrings:
        return None
    body = node.child_by_field_name("body")
    if body is None or not body.named_children:
        return None
    first = body.named_children[0]
    if first.type == "expression_statement" and first.named_children:
        inner = first.named_children[0]
        if inner.type == "string":
            return first
    return None


def _is_null_check(node: Node, spec: LanguageSpec) -> bool:
    if node.type in spec.call_types:
        return callee_text(node, spec) in spec.null_check_calls
    if node.type in {"binary_expression", "comparison_operator"}:
        return any(c.type in spec.null_literal_types for c in node.children)
    return False


def _is_log_call(node: Node, spec: LanguageSpec) -> bool:
    if node.type not in spec.call_types:
        return False
    callee = callee_text(node, spec)
    if callee in spec.log_functions:
        return True
    receiver, _, _ = callee.rpartition(".")
    return receiver in spec.log_receivers


def _is_trivial_catch(node: Node, spec: LanguageSpec) -> bool:
    """A catch whose body only logs, rethrows, or does nothing."""
    body = node.child_by_field_name("body")
    if body is None:
        body = next((c for c in node.named_children if c.type in {"block"}), None)
    if body is None:
        return False
    statements = [c for c in body.named_children if c.type not in spec.comment_types]
    if not statements:
        return True
    for stmt in statements:
        if stmt.type in spec.throw_types or stmt.type == "pass_statement":
            continue
        if stmt.type == "expression_statement" and stmt.named_children:
            if _is_log_call(stmt.named_children[0], spec):
                continue
        return False
    return True


def measure_function(node: Node, spec: LanguageSpec, file: str) -> FunctionMetrics:
    start_row, end_row = node.start_point[0], node.end_point[0]
    docstring = _docstring_node(node, spec)
    doc_rows: set[int] = set()
    if docstring is not None:
        doc_rows = set(range(docstring.start_point[0], docstring.end_point[0] + 1))

    code_rows: set[int] = set()
    comment_rows: set[int] = set()
    comments: list[Node] = []
    identifiers: set[str] = set()
    tokens = 0
    complexity = 1
    null_checks = log_calls = catch_blocks = trivial_catches = 0
    # row -> words from code tokens on that row (for narrating-comment detection)
    row_words: dict[int, set[str]] = {}

    def in_docstring(n: Node) -> bool:
        return docstring is not None and docstring.start_byte <= n.start_byte < docstring.end_byte

    for n in _walk(node):
        t = n.type
        if t in spec.comment_types:
            comments.append(n)
            comment_rows.update(range(n.start_point[0], n.end_point[0] + 1))
            continue
        if t in spec.decision_types:
            complexity += 1
        elif t in spec.boolean_op_types:
            op = n.child_by_field_name("operator")
            if op is not None and op.type in spec.boolean_operators:
                complexity += 1
        if _is_null_check(n, spec):
            null_checks += 1
        if _is_log_call(n, spec):
            log_calls += 1
        if t in spec.catch_types:
            catch_blocks += 1
            if _is_trivial_catch(n, spec):
                trivial_catches += 1
        if n.child_count == 0 and not in_docstring(n) and not _inside_comment(n, spec):
            tokens += 1
            code_rows.update(range(n.start_point[0], n.end_point[0] + 1))
            if t in spec.identifier_types:
                text = n.text.decode("utf-8", "replace")
                identifiers.add(text)
                row_words.setdefault(n.start_point[0], set()).update(
                    _stem(w) for w in split_identifier(text) if len(w) >= 3
                )

    pure_comment_rows = comment_rows - code_rows
    narrating = 0
    style = 0
    sorted_code_rows = sorted(code_rows)
    leading_docs = _leading_doc_comments(node, spec)
    doc_texts = [d.text.decode("utf-8", "replace") for d in leading_docs]
    if docstring is not None:
        doc_texts.append(docstring.text.decode("utf-8", "replace"))
    for text in doc_texts:
        if _has_style_signal(text):
            style += 1

    for c in comments:
        text = c.text.decode("utf-8", "replace")
        if _has_style_signal(text):
            style += 1
        cwords = _comment_words(text)
        if len(cwords) < 2:
            continue
        if c.start_point[0] in code_rows:
            target = c.start_point[0]  # trailing comment: compare with its own line
        else:
            target = next((r for r in sorted_code_rows if r > c.end_point[0]), None)
        if target is None:
            continue
        overlap = len(cwords & row_words.get(target, set()))
        if overlap / len(cwords) >= 0.5:
            narrating += 1

    src = node.text or b""
    compression = len(zlib.compress(src, 9)) / len(src) if src else 1.0
    avg_ident = sum(len(i) for i in identifiers) / len(identifiers) if identifiers else 0.0

    doc_lines = sum(d.end_point[0] - d.start_point[0] + 1 for d in leading_docs) + len(doc_rows)

    return FunctionMetrics(
        file=file,
        function=function_name(node),
        language=spec.name,
        start_line=start_row + 1,
        end_line=end_row + 1,
        lines_total=end_row - start_row + 1,
        loc=len(code_rows),
        comment_lines=len(pure_comment_rows - doc_rows),
        doc_lines=doc_lines,
        complexity=complexity,
        tokens=tokens,
        avg_identifier_len=round(avg_ident, 3),
        compression_ratio=round(compression, 4),
        null_checks=null_checks,
        log_calls=log_calls,
        catch_blocks=catch_blocks,
        trivial_catches=trivial_catches,
        narrating_comments=narrating,
        style_signals=style,
    )


def _inside_comment(n: Node, spec: LanguageSpec) -> bool:
    parent = n.parent
    while parent is not None:
        if parent.type in spec.comment_types:
            return True
        if parent.type in spec.function_types:
            return False
        parent = parent.parent
    return False


def measure_source(source: bytes, spec: LanguageSpec, file: str) -> list[FunctionMetrics]:
    tree = spec.parser().parse(source)
    return [measure_function(fn, spec, file) for fn in iter_functions(tree.root_node, spec)]
