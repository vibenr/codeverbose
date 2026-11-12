"""Aggregate per-function rows into an AI vs human comparison."""

from __future__ import annotations

import csv
import html
import json
import math
import statistics
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from codeverbose import __version__
from codeverbose.labeling import AI, HUMAN, MIXED, UNLABELED

GROUP_ORDER = (AI, HUMAN, UNLABELED, MIXED)


@dataclass(frozen=True)
class Metric:
    key: str
    title: str
    help: str
    # Per-function value used for medians, distributions and the significance test.
    value: Callable[[dict], float | None]
    # If set, the headline number is a pooled rate per 100 LOC (sum(count)/sum(loc)*100).
    pooled_count: str | None = None
    fmt: str = "{:.2f}"


def _f(row: dict, key: str) -> float:
    return float(row[key])


def _per_loc(key: str) -> Callable[[dict], float | None]:
    def fn(row: dict) -> float | None:
        loc = _f(row, "loc")
        return _f(row, key) / loc * 100 if loc else None

    return fn


METRICS: tuple[Metric, ...] = (
    Metric(
        "loc",
        "Function length (LOC)",
        "Median lines of code per function.",
        lambda r: _f(r, "loc"),
        fmt="{:.1f}",
    ),
    Metric(
        "loc_per_complexity",
        "Lines per decision point",
        "LOC / cyclomatic complexity. Higher = more lines spent per unit of logic.",
        lambda r: _f(r, "loc_per_complexity"),
    ),
    Metric(
        "comment_ratio",
        "Comment lines / code line",
        "In-body comment lines divided by LOC (docs excluded).",
        lambda r: _f(r, "comment_lines") / _f(r, "loc") if _f(r, "loc") else None,
        fmt="{:.3f}",
    ),
    Metric(
        "doc_ratio",
        "Doc lines / code line",
        "Javadoc / docstring lines divided by LOC.",
        lambda r: _f(r, "doc_lines") / _f(r, "loc") if _f(r, "loc") else None,
        fmt="{:.3f}",
    ),
    Metric(
        "avg_identifier_len",
        "Avg identifier length",
        "Mean length of distinct identifiers in the function.",
        lambda r: _f(r, "avg_identifier_len"),
        fmt="{:.1f}",
    ),
    Metric(
        "compression_ratio",
        "Compression ratio",
        "zlib size / raw size. LOWER = more repetitive / boilerplate-heavy.",
        lambda r: _f(r, "compression_ratio") if _f(r, "loc") >= 5 else None,
        fmt="{:.3f}",
    ),
    Metric(
        "narrating_comments",
        "Narrating comments / 100 LOC",
        "Comments that restate the next line of code.",
        _per_loc("narrating_comments"),
        pooled_count="narrating_comments",
    ),
    Metric(
        "null_checks",
        "Null checks / 100 LOC",
        "Null/None comparisons and requireNonNull-style calls.",
        _per_loc("null_checks"),
        pooled_count="null_checks",
    ),
    Metric(
        "log_calls",
        "Log calls / 100 LOC",
        "Logger / print calls.",
        _per_loc("log_calls"),
        pooled_count="log_calls",
    ),
    Metric(
        "trivial_catches",
        "Trivial catches / 100 LOC",
        "Catch blocks that only log, rethrow or do nothing.",
        _per_loc("trivial_catches"),
        pooled_count="trivial_catches",
    ),
    Metric(
        "style_signals",
        "AI style signals / 100 LOC",
        "'Step 1:', 'This function...', emoji, banner comments. Heuristic only.",
        _per_loc("style_signals"),
        pooled_count="style_signals",
    ),
)


@dataclass
class GroupStat:
    n: int
    headline: float | None
    median: float | None
    values: list[float]


def load_rows(paths: list[str | Path]) -> list[dict]:
    rows: list[dict] = []
    for p in paths:
        with open(p, newline="", encoding="utf-8") as fh:
            rows.extend(csv.DictReader(fh))
    return rows


def group_rows(rows: list[dict], min_loc: int = 3) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        if float(r["loc"]) < min_loc:
            continue
        groups.setdefault(r["label"], []).append(r)
    return groups


def stat_for(metric: Metric, rows: list[dict]) -> GroupStat:
    values = [v for v in (metric.value(r) for r in rows) if v is not None]
    median = statistics.median(values) if values else None
    if metric.pooled_count:
        loc = sum(float(r["loc"]) for r in rows)
        headline = sum(float(r[metric.pooled_count]) for r in rows) / loc * 100 if loc else None
    else:
        headline = median
    return GroupStat(n=len(values), headline=headline, median=median, values=values)


def mann_whitney_p(a: list[float], b: list[float]) -> float | None:
    """Two-sided Mann-Whitney U test, normal approximation with tie correction."""
    n1, n2 = len(a), len(b)
    if n1 < 8 or n2 < 8:
        return None
    combined = sorted([(v, 0) for v in a] + [(v, 1) for v in b])
    ranks = [0.0] * len(combined)
    tie_term = 0.0
    i = 0
    while i < len(combined):
        j = i
        while j + 1 < len(combined) and combined[j + 1][0] == combined[i][0]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[k] = avg
        t = j - i + 1
        tie_term += t**3 - t
        i = j + 1
    r1 = sum(r for r, (_, g) in zip(ranks, combined, strict=True) if g == 0)
    u1 = r1 - n1 * (n1 + 1) / 2
    mu = n1 * n2 / 2
    n = n1 + n2
    sigma = math.sqrt(n1 * n2 / 12 * ((n + 1) - tie_term / (n * (n - 1))))
    if sigma == 0:
        return 1.0
    z = (abs(u1 - mu) - 0.5) / sigma
    return max(0.0, min(1.0, math.erfc(z / math.sqrt(2))))


def build_report(rows: list[dict], min_loc: int = 3) -> dict:
    groups = group_rows(rows, min_loc)
    labels = [g for g in GROUP_ORDER if g in groups] + sorted(set(groups) - set(GROUP_ORDER))
    result = {
        "version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "min_loc": min_loc,
        "groups": {
            g: {"functions": len(groups[g]), "loc": int(sum(float(r["loc"]) for r in groups[g]))}
            for g in labels
        },
        "metrics": [],
    }
    for m in METRICS:
        entry = {
            "key": m.key,
            "title": m.title,
            "help": m.help,
            "pooled": bool(m.pooled_count),
            "by_group": {},
            "ai_vs_human_pct": None,
            "p_value": None,
        }
        stats = {g: stat_for(m, groups[g]) for g in labels}
        for g, s in stats.items():
            entry["by_group"][g] = {
                "n": s.n,
                "value": _round(s.headline),
                "median": _round(s.median),
                "box": _box(s.values),
            }
        if AI in stats and HUMAN in stats:
            a, h = stats[AI].headline, stats[HUMAN].headline
            if a is not None and h:
                entry["ai_vs_human_pct"] = round((a - h) / h * 100, 1)
            elif a and h == 0:
                entry["ai_only"] = True  # human value is zero: a % change is undefined
            entry["p_value"] = _round(mann_whitney_p(stats[AI].values, stats[HUMAN].values), 4)
        entry["fmt"] = m.fmt
        result["metrics"].append(entry)
    return result


def _round(v: float | None, nd: int = 4) -> float | None:
    return None if v is None else round(v, nd)


def _box(values: list[float]) -> dict | None:
    if len(values) < 2:
        return None
    qs = statistics.quantiles(values, n=20, method="inclusive")  # 5% steps
    return {
        "p5": qs[0],
        "q1": qs[4],
        "median": statistics.median(values),
        "q3": qs[14],
        "p95": qs[18],
    }


# ---------------------------------------------------------------- rendering


def render_text(report: dict) -> str:
    groups = list(report["groups"])
    lines = []
    summary = ", ".join(
        f"{g}: {v['functions']} functions / {v['loc']} LOC" for g, v in report["groups"].items()
    )
    lines.append(f"codeverbose {report['version']}  (functions with LOC >= {report['min_loc']})")
    lines.append(summary or "no functions found")
    lines.append("")
    has_cmp = AI in groups and HUMAN in groups
    header = ["metric", *groups] + (["AI vs human", "p"] if has_cmp else [])
    table = [header]
    for m in report["metrics"]:
        fmt = m["fmt"]
        row = [m["title"]]
        for g in groups:
            v = m["by_group"].get(g, {}).get("value")
            row.append("-" if v is None else fmt.format(v))
        if has_cmp:
            row.append(_pct_text(m))
            p = m["p_value"]
            row.append("-" if p is None else ("<0.001" if p < 0.001 else f"{p:.3f}"))
        table.append(row)
    widths = [max(len(r[i]) for r in table) for i in range(len(header))]
    for i, r in enumerate(table):
        cells = [r[0].ljust(widths[0])] + [
            c.rjust(w) for c, w in zip(r[1:], widths[1:], strict=True)
        ]
        lines.append("  ".join(cells))
        if i == 0:
            lines.append("  ".join("-" * w for w in widths))
    lines.append("")
    lines.append("Rates are pooled per 100 LOC; other values are per-function medians.")
    if not has_cmp:
        missing = "human" if AI in groups else "ai"
        lines.append(
            f"Tip: no '{missing}' group, so there's no AI-vs-human comparison. Try --assume-human, a "
            "--labels rules file, or benchmark mode (README: 'How code gets labeled')."
        )
    return "\n".join(lines)


def _pct_text(m: dict) -> str:
    if m.get("ai_only"):
        return "AI only"
    pct = m["ai_vs_human_pct"]
    return "-" if pct is None else f"{pct:+.1f}%"


def render_json(report: dict) -> str:
    return json.dumps(report, indent=2)


_COLORS = {AI: "var(--ai)", HUMAN: "var(--human)", UNLABELED: "var(--unl)", MIXED: "var(--mixed)"}


def _svg_box(metric: dict, groups: list[str]) -> str:
    boxes = [
        (g, metric["by_group"][g]["box"])
        for g in groups
        if metric["by_group"].get(g, {}).get("box")
    ]
    if not boxes:
        return ""
    lo = min(b["p5"] for _, b in boxes)
    hi = max(b["p95"] for _, b in boxes)
    span = (hi - lo) or 1.0
    width, left, row_h = 520, 90, 26

    def x(v: float) -> float:
        return left + (v - lo) / span * (width - left - 20)

    parts = [
        f'<svg viewBox="0 0 {width} {row_h * len(boxes) + 22}" role="img" '
        f'aria-label="{html.escape(metric["title"])} distribution">'
    ]
    for i, (g, b) in enumerate(boxes):
        y = 8 + i * row_h
        c = _COLORS.get(g, "var(--unl)")
        parts.append(f'<text x="0" y="{y + 13}" class="lbl">{html.escape(g)}</text>')
        parts.append(
            f'<line x1="{x(b["p5"]):.1f}" x2="{x(b["p95"]):.1f}" y1="{y + 9}" y2="{y + 9}" '
            f'stroke="{c}" stroke-width="1.5"/>'
        )
        parts.append(
            f'<rect x="{x(b["q1"]):.1f}" y="{y}" width="{max(x(b["q3"]) - x(b["q1"]), 1.5):.1f}" '
            f'height="18" rx="3" fill="{c}" fill-opacity="0.25" stroke="{c}"/>'
        )
        parts.append(
            f'<line x1="{x(b["median"]):.1f}" x2="{x(b["median"]):.1f}" y1="{y}" y2="{y + 18}" '
            f'stroke="{c}" stroke-width="2.5"/>'
        )
    axis_y = 8 + len(boxes) * row_h + 10
    fmt = metric["fmt"]
    parts.append(f'<text x="{left}" y="{axis_y}" class="ax">{fmt.format(lo)}</text>')
    parts.append(
        f'<text x="{width - 20}" y="{axis_y}" class="ax" text-anchor="end">{fmt.format(hi)}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def render_html(report: dict, title: str = "codeverbose report") -> str:
    groups = list(report["groups"])
    has_cmp = AI in groups and HUMAN in groups
    cards = "".join(
        f'<div class="card"><span class="dot" style="background:{_COLORS.get(g, "var(--unl)")}"></span>'
        f'<b>{html.escape(g)}</b><div class="big">{v["functions"]:,}</div>'
        f'<div class="muted">functions · {v["loc"]:,} LOC</div></div>'
        for g, v in report["groups"].items()
    )
    head = "".join(f"<th>{html.escape(g)}</th>" for g in groups)
    if has_cmp:
        head += "<th>AI vs human</th><th>p-value</th>"
    body = []
    for m in report["metrics"]:
        cells = []
        for g in groups:
            v = m["by_group"].get(g, {}).get("value")
            cells.append(f"<td>{'–' if v is None else m['fmt'].format(v)}</td>")
        if has_cmp:
            pct, p = m["ai_vs_human_pct"], m["p_value"]
            sig = p is not None and p < 0.05
            cls = "up" if (pct or 0) > 0 else "down"
            if m.get("ai_only"):
                cls = "up"
            cells.append(f'<td class="{cls if sig else "ns"}">{html.escape(_pct_text(m))}</td>')
            cells.append(
                f'<td class="{"" if sig else "ns"}">'
                f"{'–' if p is None else ('&lt;0.001' if p < 0.001 else f'{p:.3f}')}</td>"
            )
        body.append(
            f'<tr><th scope="row">{html.escape(m["title"])}'
            f'<div class="muted small">{html.escape(m["help"])}</div></th>{"".join(cells)}</tr>'
        )
    charts = "".join(
        f"<figure><figcaption>{html.escape(m['title'])}</figcaption>{_svg_box(m, groups)}</figure>"
        for m in report["metrics"]
        if _svg_box(m, groups)
    )
    note = (
        ""
        if has_cmp
        else '<p class="warn">No AI-vs-human comparison: the scan found no labeled AI or human code. '
        "See the README section on labeling.</p>"
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{ --bg:#fafaf9; --fg:#1c1917; --muted:#78716c; --line:#e7e5e4; --card:#fff;
  --ai:#7c3aed; --human:#0d9488; --unl:#a8a29e; --mixed:#d97706; --up:#b91c1c; --down:#15803d; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0c0a09; --fg:#f5f5f4; --muted:#a8a29e;
  --line:#292524; --card:#1c1917; --ai:#a78bfa; --human:#2dd4bf; --unl:#78716c; --mixed:#fbbf24;
  --up:#f87171; --down:#4ade80; color-scheme: dark; }} }}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg);
  font:15px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width:1000px; margin:0 auto; padding:32px 20px 64px; }}
h1 {{ font-size:26px; margin:0 0 4px; }} h2 {{ font-size:18px; margin:36px 0 12px; }}
.muted {{ color:var(--muted); }} .small {{ font-size:12px; font-weight:400; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; margin-top:20px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px; }}
.big {{ font-size:26px; font-weight:650; font-variant-numeric:tabular-nums; }}
.dot {{ display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; }}
.tbl {{ overflow-x:auto; }} table {{ width:100%; border-collapse:collapse; background:var(--card);
  border:1px solid var(--line); border-radius:10px; }}
th, td {{ padding:10px 12px; border-bottom:1px solid var(--line); text-align:right;
  font-variant-numeric:tabular-nums; vertical-align:top; }}
th[scope=row], thead th:first-child {{ text-align:left; font-weight:600; }}
thead th {{ font-size:13px; color:var(--muted); font-weight:600; }}
td.up {{ color:var(--up); font-weight:600; }} td.down {{ color:var(--down); font-weight:600; }}
td.ns {{ color:var(--muted); }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(420px,1fr)); gap:14px; }}
figure {{ margin:0; background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }}
figcaption {{ font-weight:600; margin-bottom:6px; }} svg {{ width:100%; height:auto; }}
svg .lbl {{ fill:var(--fg); font-size:12px; }} svg .ax {{ fill:var(--muted); font-size:11px; }}
.warn {{ background:var(--card); border-left:3px solid var(--mixed); padding:10px 14px; }}
footer {{ margin-top:40px; font-size:13px; }}
</style></head>
<body><main>
<h1>{html.escape(title)}</h1>
<div class="muted">Generated {html.escape(report["generated_at"])} by codeverbose {html.escape(report["version"])}
· functions with ≥ {report["min_loc"]} LOC</div>
<div class="cards">{cards}</div>
{note}
<h2>Metrics</h2>
<div class="tbl"><table><thead><tr><th>Metric</th>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>
<p class="muted small">Rates (“/ 100 LOC”) are pooled across all functions in a group; other values are
per-function medians. AI vs human differences are coloured only when a two-sided Mann–Whitney U test gives
p &lt; 0.05. “unlabeled” is code whose authorship could not be determined.</p>
<h2>Distributions</h2>
<p class="muted small">Boxes show the 25th–75th percentile with the median line; whiskers span the 5th–95th percentile.</p>
<div class="grid">{charts}</div>
<footer class="muted">Verbosity is not automatically bad. Read these numbers as a description of style,
not a quality score. Labels only capture <em>disclosed</em> AI use.</footer>
</main></body></html>
"""
