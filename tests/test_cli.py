import csv
import json

from codeverbose.cli import main

from .test_labeling import repo  # noqa: F401  (pytest fixture)


def test_analyze_json(repo, capsys):  # noqa: F811
    assert main(["analyze", str(repo), "--json", "-q", "--min-loc", "1"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["groups"]["ai"]["functions"] == 1
    assert report["groups"]["human"]["functions"] == 1
    keys = {m["key"] for m in report["metrics"]}
    assert {"loc_per_complexity", "compression_ratio", "narrating_comments"} <= keys


def test_scan_then_report_with_forced_labels(tmp_path, capsys):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.py").write_text("def f(a):\n    if a:\n        return 1\n    return 2\n")
    out = tmp_path / "a.csv"
    assert main(["scan", str(tmp_path / "a"), "--label", "ai", "-o", str(out), "-q"]) == 0
    rows = list(csv.DictReader(out.open()))
    assert rows[0]["label"] == "ai" and rows[0]["complexity"] == "2"
    html_path = tmp_path / "r.html"
    assert main(["report", str(out), "--html", str(html_path)]) == 0
    assert "<table>" in html_path.read_text()
    assert "Lines per decision point" in capsys.readouterr().out


def test_cli_version(capsys):
    import pytest

    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "codeverbose" in capsys.readouterr().out


def test_cli_commits_non_git_repo(tmp_path, capsys):
    assert main(["commits", str(tmp_path)]) == 2
    assert "is not a git repository" in capsys.readouterr().err


def test_cli_compare_non_git_repo(tmp_path, capsys):
    assert main(["compare", "HEAD", str(tmp_path)]) == 2
    assert "is not a git repository" in capsys.readouterr().err


def test_scan_stdout(tmp_path, capsys):
    (tmp_path / "mod.py").write_text("def ping():\n    return 'pong'\n")
    assert main(["scan", str(tmp_path), "-o", "-", "-q"]) == 0
    out = capsys.readouterr().out
    assert "file,function,language" in out
    assert "ping" in out
