"""Label commits (and therefore lines of code) as AI-assisted, human, or unlabeled.

Signals, in priority order:
1. User rules file (``--labels``): commit SHA prefixes, author regexes, path globs.
2. AI markers in the commit: Co-Authored-By trailers, "Generated with ..." footers,
   known AI agent author emails.
3. Date baseline: commits before ``human_before`` (default: Copilot GA, 2022-06-21)
   are treated as human.
4. Everything else is ``unlabeled`` (or ``human`` with ``--assume-human``).

Only *disclosed* AI use can be detected, so the human bucket after the cutoff is
contaminated. Reports always show ``unlabeled`` separately for that reason.
"""

from __future__ import annotations

import csv
import fnmatch
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

AI = "ai"
HUMAN = "human"
UNLABELED = "unlabeled"
MIXED = "mixed"
LABELS = (AI, HUMAN, UNLABELED, MIXED)

DEFAULT_HUMAN_BEFORE = "2022-06-21"  # GitHub Copilot general availability

# Tool names distinctive enough not to collide with human names in trailers.
# Contributions welcome: add new agents here (see CONTRIBUTING.md).
_AI_TOOLS = (
    r"claude|anthropic|copilot|cursor|codex|openai|chatgpt|gpt-?\d|gemini|devin|aider|"
    r"windsurf|codeium|amazon ?q developer|tabnine|sourcegraph|qodo|v0\.dev|replit agent"
)
AI_MESSAGE_PATTERNS = [
    re.compile(rf"^co-authored-by:.*\b(?:{_AI_TOOLS})\b", re.I | re.M),
    re.compile(rf"generated (?:with|by|using) \[?(?:{_AI_TOOLS})", re.I),
    re.compile(r"^aider:", re.I | re.M),
    re.compile(r"\U0001F916"),  # robot face emoji used in AI tool footers
    re.compile(r"^(?:assisted|written)-by:.*\b(?:ai|llm)\b", re.I | re.M),
]
AI_AUTHOR_PATTERNS = [
    re.compile(r"noreply@anthropic\.com", re.I),
    re.compile(r"copilot", re.I),
    re.compile(r"cursoragent|cursor\.com", re.I),
    re.compile(r"devin-ai-integration", re.I),
    re.compile(r"\b(?:codex|openai)\b.*\[bot\]", re.I),
    re.compile(r"\baider\b", re.I),
]


@dataclass
class Rule:
    kind: str  # commit | author | path
    pattern: str
    label: str

    def matches_commit(self, sha: str, author: str) -> bool:
        if self.kind == "commit":
            return sha.startswith(self.pattern.lower())
        if self.kind == "author":
            return re.search(self.pattern, author, re.I) is not None
        return False

    def matches_path(self, path: str) -> bool:
        return self.kind == "path" and fnmatch.fnmatch(path, self.pattern)


def load_rules(path: str | Path | None) -> list[Rule]:
    """Load a CSV of ``kind,pattern,label`` rows (header optional, ``#`` comments allowed)."""
    if not path:
        return []
    rules: list[Rule] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for lineno, row in enumerate(csv.reader(fh), 1):
            if not row or row[0].strip().startswith("#"):
                continue
            if [c.strip().lower() for c in row[:3]] == ["kind", "pattern", "label"]:
                continue
            if len(row) < 3:
                raise ValueError(f"{path}:{lineno}: expected kind,pattern,label")
            kind, pattern, label = (c.strip() for c in row[:3])
            if kind not in {"commit", "author", "path"}:
                raise ValueError(f"{path}:{lineno}: kind must be commit|author|path, got {kind!r}")
            if label not in {AI, HUMAN}:
                raise ValueError(f"{path}:{lineno}: label must be ai|human, got {label!r}")
            rules.append(Rule(kind, pattern, label))
    return rules


@dataclass
class CommitInfo:
    sha: str
    author: str
    timestamp: int
    message: str
    label: str = UNLABELED
    reason: str = ""


@dataclass
class Labeler:
    repo: Path
    human_before: datetime | None = field(
        default_factory=lambda: datetime.fromisoformat(DEFAULT_HUMAN_BEFORE).replace(
            tzinfo=timezone.utc
        )
    )
    assume_human: bool = False
    rules: list[Rule] = field(default_factory=list)
    commits: dict[str, CommitInfo] = field(default_factory=dict)

    def load_history(self) -> None:
        fmt = "%H%x00%an <%ae>%x00%ct%x00%B%x1e"
        out = _git(self.repo, "log", "--all", f"--format={fmt}")
        for record in out.split("\x1e"):
            record = record.strip("\n")
            if not record:
                continue
            parts = record.split("\x00", 3)
            if len(parts) < 4:
                continue
            sha, author, ts, message = parts
            info = CommitInfo(sha=sha, author=author, timestamp=int(ts), message=message)
            info.label, info.reason = self.classify(info)
            self.commits[sha] = info

    def classify(self, c: CommitInfo) -> tuple[str, str]:
        for rule in self.rules:
            if rule.matches_commit(c.sha, c.author):
                return rule.label, f"rule:{rule.kind}"
        for pat in AI_MESSAGE_PATTERNS:
            m = pat.search(c.message)
            if m:
                return AI, f"message:{m.group(0).strip()[:60]}"
        for pat in AI_AUTHOR_PATTERNS:
            if pat.search(c.author):
                return AI, f"author:{c.author}"
        if self.human_before is not None and c.timestamp < self.human_before.timestamp():
            return HUMAN, "before-cutoff"
        if self.assume_human:
            return HUMAN, "assumed"
        return UNLABELED, ""

    def label_for_commit(self, sha: str) -> str:
        info = self.commits.get(sha)
        if info is None:  # uncommitted lines (sha 0000...) or shallow history
            return HUMAN if self.assume_human else UNLABELED
        return info.label

    def path_override(self, rel_path: str) -> str | None:
        for rule in self.rules:
            if rule.matches_path(rel_path):
                return rule.label
        return None

    def blame_labels(self, rel_path: str) -> dict[int, str]:
        """Map 1-based line number -> label for a tracked file."""
        try:
            out = _git(self.repo, "blame", "-w", "--porcelain", "--", rel_path)
        except subprocess.CalledProcessError:
            return {}
        labels: dict[int, str] = {}
        for line in out.splitlines():
            # Header lines: "<40-hex sha> <orig line> <final line> [<group size>]"
            if line.startswith("\t"):
                continue  # source line content
            parts = line.split(" ")
            if len(parts) >= 3 and _is_sha(parts[0]) and parts[2].isdigit():
                labels[int(parts[2])] = self.label_for_commit(parts[0])
        return labels


def function_label(
    line_labels: dict[int, str], start: int, end: int, min_share: float
) -> tuple[str, float]:
    """Majority label for lines [start, end]; ``mixed`` if no label reaches ``min_share``."""
    counts: dict[str, int] = {}
    for ln in range(start, end + 1):
        lab = line_labels.get(ln)
        if lab is not None:
            counts[lab] = counts.get(lab, 0) + 1
    total = sum(counts.values())
    if total == 0:
        return UNLABELED, 0.0
    label, n = max(counts.items(), key=lambda kv: kv[1])
    share = n / total
    return (label if share >= min_share else MIXED), round(share, 3)


def is_git_repo(path: Path) -> bool:
    try:
        _git(path, "rev-parse", "--is-inside-work-tree")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def git_root(path: Path) -> Path:
    return Path(_git(path, "rev-parse", "--show-toplevel").strip())


def tracked_files(repo: Path) -> list[str]:
    return [p for p in _git(repo, "ls-files", "-z").split("\x00") if p]


def _is_sha(s: str) -> bool:
    return len(s) == 40 and all(ch in "0123456789abcdef" for ch in s)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
