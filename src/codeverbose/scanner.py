"""Walk a repository (or plain directory), measure every function, and attach a label."""

from __future__ import annotations

import csv
import fnmatch
import os
import sys
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from codeverbose import labeling
from codeverbose.languages import LanguageSpec, all_languages, for_path
from codeverbose.metrics import FunctionMetrics, measure_source

DEFAULT_EXCLUDES = (
    "**/node_modules/**",
    "**/vendor/**",
    "**/third_party/**",
    "**/build/**",
    "**/target/**",
    "**/dist/**",
    "**/.venv/**",
    "**/venv/**",
    "**/generated/**",
    "**/*_pb2.py",
    "**/migrations/**",
)

_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "target", "build", "dist", "__pycache__"}


@dataclass
class ScanOptions:
    languages: tuple[LanguageSpec, ...] = field(default_factory=all_languages)
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = DEFAULT_EXCLUDES
    force_label: str | None = None  # label every function with this (no git needed)
    assume_human: bool = False
    human_before: str | None = labeling.DEFAULT_HUMAN_BEFORE
    rules_file: str | None = None
    min_share: float = 0.6
    max_files: int | None = None
    jobs: int = max(4, (os.cpu_count() or 4))
    quiet: bool = False


@dataclass
class ScanRow:
    metrics: FunctionMetrics
    label: str
    label_share: float

    def as_row(self) -> dict:
        row = self.metrics.as_row()
        row["label"] = self.label
        row["label_share"] = self.label_share
        return row


FIELDNAMES = [
    "file",
    "function",
    "language",
    "start_line",
    "end_line",
    "label",
    "label_share",
    "lines_total",
    "loc",
    "comment_lines",
    "doc_lines",
    "complexity",
    "loc_per_complexity",
    "tokens",
    "avg_identifier_len",
    "compression_ratio",
    "null_checks",
    "log_calls",
    "catch_blocks",
    "trivial_catches",
    "narrating_comments",
    "style_signals",
]


def _matches_any(path: str, patterns: Iterable[str]) -> bool:
    # Also try with a leading "x/" so "**/build/**" matches top-level "build/...".
    return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch("x/" + path, p) for p in patterns)


def _select(paths: Iterable[str], opts: ScanOptions) -> list[str]:
    selected = []
    for p in paths:
        if for_path(p, opts.languages) is None:
            continue
        if opts.include and not _matches_any(p, opts.include):
            continue
        if _matches_any(p, opts.exclude):
            continue
        selected.append(p)
    selected.sort()
    if opts.max_files:
        selected = selected[: opts.max_files]
    return selected


def _walk_dir(root: Path) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            out.append(str(Path(dirpath, name).relative_to(root)).replace(os.sep, "/"))
    return out


def scan(path: str | Path, opts: ScanOptions | None = None) -> list[ScanRow]:
    opts = opts or ScanOptions()
    target = Path(path).resolve()
    use_git = opts.force_label is None and labeling.is_git_repo(target)

    labeler: labeling.Labeler | None = None
    if use_git:
        root = labeling.git_root(target)
        prefix = str(target.relative_to(root)).replace(os.sep, "/") if target != root else ""
        files = [
            f for f in labeling.tracked_files(root) if not prefix or f.startswith(prefix + "/")
        ]
        human_before = None
        if opts.human_before:
            from datetime import datetime, timezone

            human_before = datetime.fromisoformat(opts.human_before).replace(tzinfo=timezone.utc)
        labeler = labeling.Labeler(
            repo=root,
            human_before=human_before,
            assume_human=opts.assume_human,
            rules=labeling.load_rules(opts.rules_file),
        )
        _log(opts, "reading git history ...")
        labeler.load_history()
        n_ai = sum(1 for c in labeler.commits.values() if c.label == labeling.AI)
        _log(opts, f"  {len(labeler.commits)} commits, {n_ai} detected as AI-assisted")
    else:
        root = target
        files = _walk_dir(root)
        if opts.force_label is None:
            _log(
                opts, "not a git repository: functions will be 'unlabeled' (use --label to set one)"
            )

    files = _select(files, opts)
    _log(opts, f"measuring {len(files)} files ...")

    def process(rel: str) -> list[ScanRow]:
        spec = for_path(rel, opts.languages)
        assert spec is not None
        try:
            source = (root / rel).read_bytes()
        except OSError:
            return []
        try:
            functions = measure_source(source, spec, rel)
        except Exception as exc:  # a broken file should never kill a whole scan
            _log(opts, f"  skipping {rel}: {exc}")
            return []
        if not functions:
            return []
        if opts.force_label:
            return [ScanRow(m, opts.force_label, 1.0) for m in functions]
        if labeler is None:
            return [ScanRow(m, labeling.UNLABELED, 0.0) for m in functions]
        override = labeler.path_override(rel)
        if override:
            return [ScanRow(m, override, 1.0) for m in functions]
        line_labels = labeler.blame_labels(rel)
        rows = []
        for m in functions:
            label, share = labeling.function_label(
                line_labels, m.start_line, m.end_line, opts.min_share
            )
            rows.append(ScanRow(m, label, share))
        return rows

    results: list[ScanRow] = []
    with ThreadPoolExecutor(max_workers=opts.jobs) as pool:
        for i, rows in enumerate(pool.map(process, files), 1):
            results.extend(rows)
            if not opts.quiet and i % 200 == 0:
                _log(opts, f"  {i}/{len(files)} files")
    _log(opts, f"measured {len(results)} functions")
    return results


def write_csv(rows: list[ScanRow], out) -> None:
    writer = csv.DictWriter(out, fieldnames=FIELDNAMES)
    writer.writeheader()
    for r in rows:
        writer.writerow(r.as_row())


def _log(opts: ScanOptions, msg: str) -> None:
    if not opts.quiet:
        print(msg, file=sys.stderr)
