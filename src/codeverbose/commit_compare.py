"""Compare verbosity metrics between two states of the codebase (e.g. before/after a commit)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from codeverbose.git_diff import ChangedFile, changed_files_between, changed_files_in_commit
from codeverbose.languages import for_path
from codeverbose.metrics import FunctionMetrics, measure_source

if TYPE_CHECKING:
    pass

# Metrics shown in the comparison table (subset of FunctionMetrics fields)
COMPARE_KEYS = [
    "loc",
    "comment_lines",
    "doc_lines",
    "complexity",
    "avg_identifier_len",
    "compression_ratio",
    "null_checks",
    "log_calls",
    "narrating_comments",
    "style_signals",
]

METRIC_LABELS: dict[str, str] = {
    "loc": "Lines of code (LOC)",
    "comment_lines": "Inline comment lines",
    "doc_lines": "Doc/docstring lines",
    "complexity": "Cyclomatic complexity",
    "avg_identifier_len": "Avg identifier length",
    "compression_ratio": "Compression ratio",
    "null_checks": "Null checks",
    "log_calls": "Log calls",
    "narrating_comments": "Narrating comments",
    "style_signals": "AI style signals",
}


@dataclass
class FunctionDiff:
    file: str
    function: str
    language: str
    before: FunctionMetrics | None
    after: FunctionMetrics | None

    def delta(self, key: str) -> float | None:
        """after - before for a numeric metric; None if either side is absent."""
        b = getattr(self.before, key, None)
        a = getattr(self.after, key, None)
        if b is None or a is None:
            return None
        try:
            return float(a) - float(b)
        except (TypeError, ValueError):
            return None

    def as_dict(self) -> dict:
        def _val(m: FunctionMetrics | None) -> dict:
            if m is None:
                return {}
            return {k: getattr(m, k) for k in COMPARE_KEYS}

        return {
            "file": self.file,
            "function": self.function,
            "language": self.language,
            "before": _val(self.before),
            "after": _val(self.after),
            "delta": {k: self.delta(k) for k in COMPARE_KEYS},
        }


@dataclass
class CompareResult:
    ref_before: str
    ref_after: str
    diffs: list[FunctionDiff] = field(default_factory=list)
    files_added: int = 0
    files_deleted: int = 0
    files_modified: int = 0
    ai_summary: str | None = None

    # --- aggregate helpers ---

    def aggregate_delta(self, key: str) -> float | None:
        """Mean delta for *key* across all functions that exist on both sides."""
        deltas = [d.delta(key) for d in self.diffs if d.delta(key) is not None]
        return sum(deltas) / len(deltas) if deltas else None

    def functions_added(self) -> list[FunctionDiff]:
        return [d for d in self.diffs if d.before is None]

    def functions_deleted(self) -> list[FunctionDiff]:
        return [d for d in self.diffs if d.after is None]

    def functions_changed(self) -> list[FunctionDiff]:
        return [d for d in self.diffs if d.before is not None and d.after is not None]


def _measure(source: bytes | None, spec, file: str) -> dict[str, FunctionMetrics]:
    if source is None:
        return {}
    try:
        fns = measure_source(source, spec, file)
    except Exception:
        return {}
    return {f.function: f for f in fns}


def compare_commit(repo: Path, commit: str) -> CompareResult:
    """Compare verbosity for all changed files in a single commit vs its parent."""
    changed = changed_files_in_commit(repo, commit)
    short = commit[:10]
    return _compare_changed(changed, f"{short}^", short)


def compare_refs(repo: Path, ref_a: str, ref_b: str) -> CompareResult:
    """Compare verbosity of files that changed between two refs."""
    changed = changed_files_between(repo, ref_a, ref_b)
    return _compare_changed(changed, ref_a, ref_b)


def _compare_changed(changed: list[ChangedFile], ref_before: str, ref_after: str) -> CompareResult:
    result = CompareResult(ref_before=ref_before, ref_after=ref_after)

    for cf in changed:
        spec = for_path(cf.path)
        if spec is None:
            continue  # skip non-supported languages

        if cf.before is None:
            result.files_added += 1
        elif cf.after is None:
            result.files_deleted += 1
        else:
            result.files_modified += 1

        before_fns = _measure(cf.before, spec, cf.path)
        after_fns = _measure(cf.after, spec, cf.path)

        all_names = set(before_fns) | set(after_fns)
        for name in sorted(all_names):
            result.diffs.append(
                FunctionDiff(
                    file=cf.path,
                    function=name,
                    language=spec.name,
                    before=before_fns.get(name),
                    after=after_fns.get(name),
                )
            )

    return result


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_text(result: CompareResult, top_n: int = 20) -> str:
    lines: list[str] = []
    lines.append(f"Verbosity comparison: {result.ref_before} → {result.ref_after}")
    lines.append(
        f"Files: +{result.files_added} added  ~{result.files_modified} modified  "
        f"-{result.files_deleted} deleted"
    )
    lines.append(
        f"Functions: +{len(result.functions_added())} added  "
        f"~{len(result.functions_changed())} modified  "
        f"-{len(result.functions_deleted())} deleted"
    )
    lines.append("")

    # Aggregate deltas table
    lines.append("── Aggregate metric deltas (mean across modified functions) ──")
    col_w = 30
    lines.append(f"  {'Metric':<{col_w}}  {'Δ (after − before)':>20}")
    lines.append(f"  {'-' * col_w}  {'─' * 20}")
    for key in COMPARE_KEYS:
        delta = result.aggregate_delta(key)
        if delta is None:
            val = "n/a"
        elif abs(delta) < 0.001:
            val = "  ±0.000"
        else:
            sign = "+" if delta > 0 else ""
            val = f"{sign}{delta:>+.3f}"
        lines.append(f"  {METRIC_LABELS[key]:<{col_w}}  {val:>20}")
    lines.append("")

    # Top changed functions
    changed = result.functions_changed()
    if changed:
        # Sort by absolute LOC delta descending
        changed_sorted = sorted(
            changed,
            key=lambda d: abs(d.delta("loc") or 0),
            reverse=True,
        )[:top_n]
        lines.append(
            f"── Top {min(top_n, len(changed_sorted))} most-changed functions (by |ΔLOC|) ──"
        )
        lines.append(f"  {'Function':<45}  {'ΔLOC':>6}  {'ΔComments':>10}  {'ΔComplexity':>12}")
        lines.append(f"  {'-' * 45}  {'─' * 6}  {'─' * 10}  {'─' * 12}")
        for d in changed_sorted:
            fn = f"{d.file}::{d.function}"
            if len(fn) > 44:
                fn = "…" + fn[-43:]
            dloc = d.delta("loc")
            dcom = d.delta("comment_lines")
            dcmx = d.delta("complexity")
            lines.append(f"  {fn:<45}  {_fmt(dloc):>6}  {_fmt(dcom):>10}  {_fmt(dcmx):>12}")
        lines.append("")

    # New code verbosity — full metric table for added functions
    added = result.functions_added()
    if added:
        lines.append(f"── New code verbosity ({len(added)} functions added) ──")
        lines.append(
            f"  {'Function':<45}  {'LOC':>5}  {'Comments':>9}  {'Docs':>5}"
            f"  {'Cmplx':>6}  {'AvgIdLen':>9}  {'Narrating':>10}  {'StyleSig':>9}"
        )
        lines.append(
            f"  {'-' * 45}  {'─' * 5}  {'─' * 9}  {'─' * 5}"
            f"  {'─' * 6}  {'─' * 9}  {'─' * 10}  {'─' * 9}"
        )
        for d in added[:top_n]:
            m = d.after
            fn = f"{d.file}::{d.function}"
            if len(fn) > 44:
                fn = "…" + fn[-43:]
            lines.append(
                f"  {fn:<45}  {m.loc:>5}  {m.comment_lines:>9}  {m.doc_lines:>5}"
                f"  {m.complexity:>6}  {m.avg_identifier_len:>9.1f}"
                f"  {m.narrating_comments:>10}  {m.style_signals:>9}"
            )

        # Aggregate profile of new code
        lines.append("")
        lines.append("  New code aggregate profile:")
        col_w = 30
        for key in COMPARE_KEYS:
            vals = [getattr(d.after, key) for d in added if d.after is not None]
            if not vals:
                continue
            try:
                avg = sum(float(v) for v in vals) / len(vals)
            except (TypeError, ValueError):
                continue
            lines.append(f"    {METRIC_LABELS[key]:<{col_w}}  avg {avg:>8.2f}")
        lines.append("")

    if result.functions_deleted():
        lines.append(f"── {len(result.functions_deleted())} functions deleted ──")
        for d in result.functions_deleted()[:top_n]:
            lines.append(f"  - {d.file}::{d.function}")
        lines.append("")

    if result.ai_summary:
        lines.append("── AI assessment ──")
        lines.append(result.ai_summary)
        lines.append("")

    return "\n".join(lines)


def render_json(result: CompareResult) -> str:
    obj = {
        "ref_before": result.ref_before,
        "ref_after": result.ref_after,
        "files": {
            "added": result.files_added,
            "modified": result.files_modified,
            "deleted": result.files_deleted,
        },
        "aggregate_deltas": {key: result.aggregate_delta(key) for key in COMPARE_KEYS},
        "new_code_profile": {
            key: (lambda vals: sum(vals) / len(vals) if vals else None)(
                [float(getattr(d.after, key)) for d in result.functions_added() if d.after]
            )
            for key in COMPARE_KEYS
        },
        "functions": [d.as_dict() for d in result.diffs],
        "ai_summary": result.ai_summary,
    }
    return json.dumps(obj, indent=2, default=lambda x: None if math.isnan(x) else x)


def _fmt(v: float | None) -> str:
    if v is None:
        return "n/a"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.1f}"
