"""codeverbose command-line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from codeverbose import __version__, labeling, report
from codeverbose.languages import all_languages, by_name, for_path
from codeverbose.scanner import DEFAULT_EXCLUDES, ScanOptions, scan, write_csv
from codeverbose import commit_compare as _cc
from codeverbose.git_diff import resolve_ref


def _add_scan_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "path", nargs="?", default=".", help="repository or directory to scan (default: .)"
    )
    p.add_argument(
        "--lang",
        default=",".join(s.name for s in all_languages()),
        help="comma-separated languages (default: all supported)",
    )
    p.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="GLOB",
        help="only scan paths matching GLOB (repeatable), e.g. 'src/main/**'",
    )
    p.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="skip paths matching GLOB (repeatable); added to the built-in excludes",
    )
    p.add_argument(
        "--no-default-excludes",
        action="store_true",
        help="do not skip vendor/, build/, generated/ etc.",
    )
    lab = p.add_argument_group("labeling")
    lab.add_argument(
        "--label",
        choices=[labeling.AI, labeling.HUMAN],
        help="label everything in PATH with this value (no git needed). "
        "Use for benchmark folders, e.g. LLM outputs vs reference solutions",
    )
    lab.add_argument("--labels", metavar="CSV", help="rules file: kind,pattern,label (see README)")
    lab.add_argument(
        "--human-before",
        default=labeling.DEFAULT_HUMAN_BEFORE,
        metavar="YYYY-MM-DD",
        help="treat commits before this date as human (default: %(default)s). "
        "Pass 'none' to disable",
    )
    lab.add_argument(
        "--assume-human",
        action="store_true",
        help="treat commits without AI markers as human instead of 'unlabeled'",
    )
    lab.add_argument(
        "--min-share",
        type=float,
        default=0.6,
        help="share of a function's lines one label needs, else 'mixed' (default: 0.6)",
    )
    p.add_argument(
        "--max-files", type=int, help="cap the number of files (useful for a quick look)"
    )
    p.add_argument("--jobs", type=int, default=None, help="parallel git blame workers")
    p.add_argument("-q", "--quiet", action="store_true", help="no progress output")


def _scan_options(args: argparse.Namespace) -> ScanOptions:
    langs = tuple(by_name(n.strip()) for n in args.lang.split(",") if n.strip())
    excludes = (
        tuple(args.exclude) if args.no_default_excludes else DEFAULT_EXCLUDES + tuple(args.exclude)
    )
    human_before = None if str(args.human_before).lower() == "none" else args.human_before
    opts = ScanOptions(
        languages=langs,
        include=tuple(args.include),
        exclude=excludes,
        force_label=args.label,
        assume_human=args.assume_human,
        human_before=human_before,
        rules_file=args.labels,
        min_share=args.min_share,
        max_files=args.max_files,
        quiet=args.quiet,
    )
    if args.jobs:
        opts.jobs = args.jobs
    return opts


def _emit_report(rep: dict, args: argparse.Namespace) -> None:
    if args.json:
        print(report.render_json(rep))
    else:
        print(report.render_text(rep))
    if args.html:
        Path(args.html).write_text(report.render_html(rep, title=args.title), encoding="utf-8")
        print(f"\nHTML report written to {args.html}", file=sys.stderr)


def cmd_scan(args: argparse.Namespace) -> int:
    rows = scan(args.path, _scan_options(args))
    if args.output == "-":
        write_csv(rows, sys.stdout)
    else:
        with open(args.output, "w", newline="", encoding="utf-8") as fh:
            write_csv(rows, fh)
        print(f"wrote {len(rows)} rows to {args.output}", file=sys.stderr)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    rows = report.load_rows(args.csv)
    _emit_report(report.build_report(rows, min_loc=args.min_loc), args)
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    rows = scan(args.path, _scan_options(args))
    if args.output:
        with open(args.output, "w", newline="", encoding="utf-8") as fh:
            write_csv(rows, fh)
    dict_rows = [{k: str(v) for k, v in r.as_row().items()} for r in rows]
    _emit_report(report.build_report(dict_rows, min_loc=args.min_loc), args)
    return 0


def cmd_commits(args: argparse.Namespace) -> int:
    repo = Path(args.path).resolve()
    if not labeling.is_git_repo(repo):
        print(f"{repo} is not a git repository", file=sys.stderr)
        return 2
    human_before = None
    if str(args.human_before).lower() != "none":
        from datetime import datetime, timezone

        human_before = datetime.fromisoformat(args.human_before).replace(tzinfo=timezone.utc)
    lab = labeling.Labeler(
        repo=labeling.git_root(repo),
        human_before=human_before,
        assume_human=args.assume_human,
        rules=labeling.load_rules(args.labels),
    )
    lab.load_history()
    counts: dict[str, int] = {}
    for c in lab.commits.values():
        counts[c.label] = counts.get(c.label, 0) + 1
    print("  ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "no commits")
    if args.list:
        for c in sorted(lab.commits.values(), key=lambda c: -c.timestamp):
            if args.list == "all" or c.label == args.list:
                subject = c.message.splitlines()[0] if c.message else ""
                print(f"{c.sha[:10]}  {c.label:<9}  {c.reason[:40]:<40}  {subject[:70]}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    repo = Path(args.path).resolve()
    if not labeling.is_git_repo(repo):
        print(f"{repo} is not a git repository", file=sys.stderr)
        return 2

    commit_arg: str = args.commit
    if ".." in commit_arg:
        parts = commit_arg.split("..", 1)
        ref_a, ref_b = parts[0].strip() or "HEAD^", parts[1].strip() or "HEAD"
        try:
            ref_a = resolve_ref(repo, ref_a)
            ref_b = resolve_ref(repo, ref_b)
        except RuntimeError as exc:
            print(f"error resolving refs: {exc}", file=sys.stderr)
            return 2
        result = _cc.compare_refs(repo, ref_a, ref_b)
    else:
        try:
            commit = resolve_ref(repo, commit_arg)
        except RuntimeError as exc:
            print(f"error resolving ref '{commit_arg}': {exc}", file=sys.stderr)
            return 2
        result = _cc.compare_commit(repo, commit)

    if args.ai:
        result.ai_summary = _ai_summary(result)

    if args.json:
        print(_cc.render_json(result))
    else:
        print(_cc.render_text(result, top_n=args.top))
    return 0


def _ai_summary(result: _cc.CompareResult) -> str:
    import os

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return "(ANTHROPIC_API_KEY not set — skipping AI summary)"

    try:
        import anthropic
    except ImportError:
        return "(install anthropic package: pip install anthropic)"

    changed = result.functions_changed()
    deltas = {k: result.aggregate_delta(k) for k in _cc.COMPARE_KEYS}
    n_added = len(result.functions_added())
    n_deleted = len(result.functions_deleted())
    n_changed = len(changed)

    prompt = (
        f"You are reviewing a git commit verbosity report.\n\n"
        f"Comparing {result.ref_before} → {result.ref_after}.\n"
        f"Functions: {n_added} added, {n_changed} modified, {n_deleted} deleted.\n\n"
        f"Mean metric deltas (after − before) across modified functions:\n"
        + "\n".join(
            f"  {_cc.METRIC_LABELS.get(k, k)}: "
            + (f"{v:+.3f}" if v is not None else "n/a")
            for k, v in deltas.items()
        )
        + "\n\nIn 3-5 concise sentences, assess what these changes mean for code quality "
        "and readability. Note whether the commit makes the code more or less verbose, "
        "and whether that direction looks intentional (refactoring, adding docs, simplifying)."
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text
    except Exception as exc:
        return f"(AI summary failed: {exc})"


def cmd_debug_ast(args: argparse.Namespace) -> int:
    spec = for_path(args.file)
    if spec is None:
        print(f"unsupported file type: {args.file}", file=sys.stderr)
        return 2
    tree = spec.parser().parse(Path(args.file).read_bytes())

    def show(node, depth: int) -> None:
        if depth > args.depth:
            return
        text = (
            node.text.decode("utf-8", "replace").split("\n")[0][:50]
            if node.child_count == 0
            else ""
        )
        print(f"{'  ' * depth}{node.type}  [{node.start_point[0] + 1}]  {text}")
        for child in node.children:
            show(child, depth + 1)

    show(tree.root_node, 0)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codeverbose",
        description="Measure and compare verbosity of AI-assisted vs human-written code.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_report_args(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--min-loc",
            type=int,
            default=3,
            help="ignore functions shorter than this (getters, one-liners). Default: 3",
        )
        p.add_argument("--html", metavar="FILE", help="also write a self-contained HTML report")
        p.add_argument("--title", default="codeverbose report", help="HTML report title")
        p.add_argument("--json", action="store_true", help="print JSON instead of a table")

    p = sub.add_parser("analyze", help="scan + report in one step (most common)")
    _add_scan_args(p)
    add_report_args(p)
    p.add_argument("-o", "--output", metavar="CSV", help="also save per-function rows to CSV")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("scan", help="measure every function and write per-function CSV")
    _add_scan_args(p)
    p.add_argument("-o", "--output", default="codeverbose.csv", help="CSV path, or - for stdout")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("report", help="compare groups from one or more scan CSVs")
    p.add_argument("csv", nargs="+", help="CSV files produced by 'scan' (they are concatenated)")
    add_report_args(p)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser(
        "commits", help="show how commits were labeled (check before trusting a report)"
    )
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--labels", metavar="CSV")
    p.add_argument("--human-before", default=labeling.DEFAULT_HUMAN_BEFORE)
    p.add_argument("--assume-human", action="store_true")
    p.add_argument(
        "--list",
        choices=[labeling.AI, labeling.HUMAN, labeling.UNLABELED, "all"],
        help="list commits with this label",
    )
    p.set_defaults(func=cmd_commits)

    p = sub.add_parser(
        "compare",
        help="compare verbosity before/after a commit (or between two refs)",
    )
    p.add_argument(
        "commit",
        help="commit hash (or ref) to compare against its parent.  "
        "Pass two refs separated by '..' to compare arbitrary refs, e.g. main..HEAD",
    )
    p.add_argument(
        "path", nargs="?", default=".", help="git repository root (default: .)"
    )
    p.add_argument("--json", action="store_true", help="print JSON instead of a table")
    p.add_argument(
        "--ai",
        action="store_true",
        help="request a Claude AI qualitative summary (requires ANTHROPIC_API_KEY)",
    )
    p.add_argument(
        "--top",
        type=int,
        default=20,
        metavar="N",
        help="show top N most-changed functions (default: 20)",
    )
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("debug-ast", help="print the syntax tree of a file (for adding languages)")
    p.add_argument("file")
    p.add_argument("--depth", type=int, default=6)
    p.set_defaults(func=cmd_debug_ast)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
