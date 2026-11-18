from pathlib import Path

from codeverbose.languages import by_name
from codeverbose.scanner import ScanOptions, _matches_any, _select, _walk_dir, scan


def test_matches_any():
    patterns = ("**/tests/**", "**/build/**", "*.tmp")
    assert _matches_any("tests/test_cli.py", patterns)
    assert _matches_any("build/lib/x.py", patterns)
    assert _matches_any("file.tmp", patterns)
    assert not _matches_any("src/main.py", patterns)


def test_select_language_filtering():
    py = by_name("python")
    opts = ScanOptions(languages=(py,))
    paths = ["src/app.py", "src/App.java", "README.md", "src/nested/util.py"]
    selected = _select(paths, opts)
    assert selected == ["src/app.py", "src/nested/util.py"]


def test_select_include_and_exclude():
    py = by_name("python")
    opts = ScanOptions(
        languages=(py,),
        include=("src/**",),
        exclude=("**/vendor/**",),
    )
    paths = ["src/a.py", "src/vendor/b.py", "tests/c.py"]
    assert _select(paths, opts) == ["src/a.py"]


def test_select_max_files():
    py = by_name("python")
    opts = ScanOptions(languages=(py,), max_files=2)
    paths = [f"src/{i}.py" for i in range(10)]
    assert len(_select(paths, opts)) == 2


def test_walk_dir_skips_hidden_and_build(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("...")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("...")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "secret.py").write_text("...")

    files = _walk_dir(tmp_path)
    assert "src/a.py" in files
    assert not any(".git" in f or "node_modules" in f or ".hidden" in f for f in files)


def test_scan_plain_directory_forced_label(tmp_path):
    (tmp_path / "math.py").write_text("def add(a, b):\n    return a + b\n")
    rows = scan(tmp_path, ScanOptions(force_label="human"))
    assert len(rows) == 1
    assert rows[0].label == "human"
    assert rows[0].metrics.function == "add"
    assert rows[0].metrics.loc == 2


def test_scan_plain_directory_unlabeled(tmp_path):
    (tmp_path / "service.py").write_text("def run():\n    pass\n")
    rows = scan(tmp_path, ScanOptions())
    assert len(rows) == 1
    assert rows[0].label == "unlabeled"
    assert rows[0].label_share == 0.0


def test_scan_unreadable_file_does_not_crash(tmp_path, monkeypatch):
    test_file = tmp_path / "bad.py"
    test_file.write_text("def safe():\n    return 0\n")

    orig_read_bytes = Path.read_bytes

    def fake_read_bytes(self):
        if self.name == "bad.py":
            raise OSError("permission denied")
        return orig_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", fake_read_bytes)
    rows = scan(tmp_path, ScanOptions())
    assert rows == []
