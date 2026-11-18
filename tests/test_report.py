import json

from codeverbose.labeling import AI, HUMAN
from codeverbose.report import (
    _pct_text,
    _svg_box,
    build_report,
    load_rows,
    mann_whitney_p,
    render_html,
    render_json,
)


def _dummy_row(label, loc=10, complexity=2, comment_lines=1, doc_lines=0, ident_len=5.0):
    return {
        "file": "app.py",
        "function": f"fn_{label}",
        "language": "python",
        "label": label,
        "label_share": "1.0",
        "lines_total": str(loc + comment_lines + doc_lines),
        "loc": str(loc),
        "comment_lines": str(comment_lines),
        "doc_lines": str(doc_lines),
        "complexity": str(complexity),
        "loc_per_complexity": str(loc / complexity),
        "tokens": "20",
        "avg_identifier_len": str(ident_len),
        "compression_ratio": "0.6",
        "null_checks": "1",
        "log_calls": "0",
        "catch_blocks": "0",
        "trivial_catches": "0",
        "narrating_comments": "1",
        "style_signals": "0",
    }


def test_build_report_empty():
    rep = build_report([])
    assert rep["groups"] == {}
    assert len(rep["metrics"]) > 0


def test_build_report_ai_vs_human_comparison():
    rows = [
        _dummy_row(AI, loc=20, complexity=2, comment_lines=4),
        _dummy_row(AI, loc=24, complexity=2, comment_lines=6),
        _dummy_row(HUMAN, loc=8, complexity=2, comment_lines=0),
        _dummy_row(HUMAN, loc=10, complexity=2, comment_lines=0),
    ]
    rep = build_report(rows, min_loc=1)
    assert rep["groups"][AI]["functions"] == 2
    assert rep["groups"][HUMAN]["functions"] == 2

    loc_metric = next(m for m in rep["metrics"] if m["key"] == "loc")
    assert loc_metric["ai_vs_human_pct"] is not None
    assert loc_metric["ai_vs_human_pct"] > 0  # AI is longer in LOC

    # Test JSON and HTML output renders without error
    json_out = render_json(rep)
    data = json.loads(json_out)
    assert data["groups"][AI]["functions"] == 2

    html_out = render_html(rep, title="Test Report")
    assert "<!doctype html>" in html_out
    assert "Test Report" in html_out
    assert "Function length (LOC)" in html_out


def test_mann_whitney_p_edge_cases():
    # Less than 8 samples returns None
    assert mann_whitney_p([1.0] * 5, [2.0] * 5) is None
    # Identical groups (>= 8 samples, zero tie variance returns 1.0)
    assert mann_whitney_p([5.0] * 8, [5.0] * 8) == 1.0
    # Clear difference with >= 8 samples
    a = [100.0 + i for i in range(10)]
    b = [1.0 + i for i in range(10)]
    p = mann_whitney_p(a, b)
    assert p is not None and p < 0.05


def test_pct_text_ai_only():
    m = {"ai_only": True, "ai_vs_human_pct": 100.0}
    assert _pct_text(m) == "AI only"

    m = {"ai_only": False, "ai_vs_human_pct": 15.2}
    assert _pct_text(m) == "+15.2%"

    m = {"ai_only": False, "ai_vs_human_pct": None}
    assert _pct_text(m) == "-"


def test_svg_box_empty():
    metric = {"title": "Test", "fmt": "{:.1f}", "by_group": {}}
    assert _svg_box(metric, [AI, HUMAN]) == ""


def test_svg_box_renders_elements():
    metric = {
        "title": "LOC",
        "fmt": "{:.1f}",
        "by_group": {
            AI: {"box": {"p5": 5.0, "q1": 10.0, "median": 15.0, "q3": 20.0, "p95": 25.0}},
            HUMAN: {"box": {"p5": 2.0, "q1": 4.0, "median": 6.0, "q3": 8.0, "p95": 10.0}},
        },
    }
    svg = _svg_box(metric, [AI, HUMAN])
    assert "<svg" in svg
    assert "</svg>" in svg
    assert 'class="lbl">ai</text>' in svg
    assert 'class="lbl">human</text>' in svg


def test_load_rows_from_csv(tmp_path):
    csv_file = tmp_path / "test.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        f.write("file,function,loc,label\n")
        f.write("a.py,foo,12,ai\n")
        f.write("b.py,bar,6,human\n")

    rows = load_rows([str(csv_file)])
    assert len(rows) == 2
    assert rows[0]["function"] == "foo"
    assert rows[1]["function"] == "bar"
