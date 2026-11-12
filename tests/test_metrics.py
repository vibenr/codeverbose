from pathlib import Path

from codeverbose.languages import JAVA, PYTHON
from codeverbose.metrics import measure_source, split_identifier

FIXTURES = Path(__file__).parent / "fixtures"


def _by_name(spec, filename):
    rows = measure_source((FIXTURES / filename).read_bytes(), spec, filename)
    return {m.function: m for m in rows}


def test_split_identifier():
    assert split_identifier("calculateTotalPrice") == ["calculate", "total", "price"]
    assert split_identifier("HTTPServer_url") == ["http", "server", "url"]


def test_java_verbose_method():
    m = _by_name(JAVA, "Sample.java")["Sample.calculateTotalOrderPrice"]
    assert m.complexity == 4  # if + for + catch
    assert m.doc_lines == 5  # javadoc
    assert m.comment_lines == 4
    assert m.null_checks == 1
    assert m.log_calls == 2
    assert m.catch_blocks == 1 and m.trivial_catches == 1
    assert m.narrating_comments == 2
    assert m.style_signals >= 2  # "Step 1:" and "This method ..."


def test_java_terse_method():
    m = _by_name(JAVA, "Sample.java")["Sample.total"]
    assert m.complexity == 4  # for + ternary + &&
    assert m.comment_lines == 0 and m.narrating_comments == 0
    assert m.loc == 5


def test_python_docstring_and_trailing_comment():
    m = _by_name(PYTHON, "sample.py")["calculate_total_order_price"]
    assert m.doc_lines == 5
    assert m.null_checks == 1
    assert m.trivial_catches == 1
    assert m.narrating_comments == 1  # "# add price" on the same line
    # docstring lines are not counted as code
    assert m.loc == m.lines_total - m.doc_lines - m.comment_lines


def test_python_complexity_counts_comprehension_and_boolean():
    m = _by_name(PYTHON, "sample.py")["total"]
    assert m.complexity == 3


def test_nested_functions_measured_with_parent():
    src = b"def outer():\n    def inner():\n        return 1\n    return inner()\n"
    rows = measure_source(src, PYTHON, "x.py")
    assert [r.function for r in rows] == ["outer"]


def test_broken_source_does_not_raise():
    rows = measure_source(b"public class { void x( { ", JAVA, "Broken.java")
    assert isinstance(rows, list)
