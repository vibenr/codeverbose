"""Utilities for extracting file content at specific git refs and parsing commit diffs."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ChangedFile:
    path: str
    before: bytes | None  # None = file was added
    after: bytes | None   # None = file was deleted


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip())
    return result.stdout


def resolve_ref(repo: Path, ref: str) -> str:
    """Return the full SHA for a ref (branch, tag, short hash, HEAD, etc.)."""
    return _git(repo, "rev-parse", "--verify", ref).decode().strip()


def file_at_ref(repo: Path, ref: str, path: str) -> bytes | None:
    """Return the raw content of *path* at *ref*, or None if it didn't exist."""
    try:
        return _git(repo, "show", f"{ref}:{path}")
    except RuntimeError:
        return None


def changed_files_in_commit(repo: Path, commit: str) -> list[ChangedFile]:
    """Return changed files between *commit* and its parent (or empty tree for root commit)."""
    parent = f"{commit}^"
    try:
        resolve_ref(repo, parent)
        before_ref = parent
    except RuntimeError:
        # Root commit has no parent — diff against the empty tree
        before_ref = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

    raw = _git(repo, "diff", "--name-status", before_ref, commit).decode("utf-8", "replace")
    return _parse_name_status(repo, raw, before_ref, commit)


def changed_files_between(repo: Path, ref_a: str, ref_b: str) -> list[ChangedFile]:
    """Return files that changed between two arbitrary refs."""
    raw = _git(repo, "diff", "--name-status", ref_a, ref_b).decode("utf-8", "replace")
    return _parse_name_status(repo, raw, ref_a, ref_b)


def _parse_name_status(
    repo: Path, raw: str, before_ref: str, after_ref: str
) -> list[ChangedFile]:
    files: list[ChangedFile] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        status = parts[0][0]  # A, M, D, R, C …
        if status == "R" and len(parts) >= 3:
            old_path, new_path = parts[1], parts[2]
        elif len(parts) >= 2:
            old_path = new_path = parts[1]
        else:
            continue

        before = file_at_ref(repo, before_ref, old_path) if status != "A" else None
        after = file_at_ref(repo, after_ref, new_path) if status != "D" else None
        files.append(ChangedFile(path=new_path, before=before, after=after))
    return files
