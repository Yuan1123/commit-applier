#!/usr/bin/env python3
"""Collect commits that touch specific directories between two commits."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Iterable, List


def run_git(args: Iterable[str]) -> str:
    """Run a git command and return its stdout, exiting on failure."""
    proc = subprocess.run(
        ["git", *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        sys.exit(proc.returncode)
    return proc.stdout.strip()


def commit_range(start: str, end: str) -> List[str]:
    """Return commits from start to end (inclusive) along the ancestry path."""
    revs = run_git(["rev-list", "--ancestry-path", "--reverse", f"{start}..{end}"])
    commits = [line for line in revs.splitlines() if line]

    if not commits or commits[0] != start:
        commits.insert(0, start)
    if commits[-1] != end:
        commits.append(end)
    return commits


def normalize_dirs(dirs: Iterable[str]) -> List[str]:
    """Normalize directory inputs into Git-style POSIX strings."""
    normalized = []
    for d in dirs:
        p = PurePosixPath(d.strip())
        # Treat "." or empty as current directory (matches everything)
        normalized.append("." if str(p) in {"", "."} else str(p).strip("/"))
    return normalized


def touches_target(file_path: str, targets: Iterable[str]) -> bool:
    """Return True when file_path resides within any target directory."""
    normalized = file_path.strip()
    for target in targets:
        if target == ".":
            return True
        if normalized == target or normalized.startswith(f"{target}/"):
            return True
    return False


def commits_touching_targets(commits: Iterable[str], targets: List[str]) -> List[str]:
    """Filter commits that modify files within targets."""
    matched: List[str] = []
    for commit in commits:
        files_output = run_git(
            ["diff-tree", "--no-commit-id", "--name-only", "-r", commit]
        )
        files = [line for line in files_output.splitlines() if line]
        if any(touches_target(path, targets) for path in files):
            matched.append(commit)
    return matched


def write_commits(commits: Iterable[str], output_path: Path) -> None:
    """Persist commits to a file, one per line."""
    output_path.write_text("\n".join(commits) + ("\n" if commits else ""))


def generate_patch(
    commits: Iterable[str], targets: List[str], output_path: Path
) -> None:
    """Write a patch containing only target-directory changes."""
    pathspecs = [] if targets == ["."] else targets
    patch_chunks: List[str] = []
    for commit in commits:
        cmd = [
            "format-patch",
            "--stdout",
            "--no-stat",
            "--binary",
            "-1",
            commit,
        ]
        if pathspecs:
            cmd.extend(["--", *pathspecs])
        patch_chunks.append(run_git(cmd))

    output_path.write_text("\n".join(chunk for chunk in patch_chunks if chunk))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "List commits between two revisions that touch specific directories "
            "and save them to a file."
        )
    )
    parser.add_argument("start_commit", help="Starting commit hash (inclusive)")
    parser.add_argument("end_commit", help="Ending commit hash (inclusive)")
    parser.add_argument(
        "directories",
        nargs="+",
        help="One or more target directories relative to the repo root",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="target_commits.txt",
        help="Path to the output file (default: target_commits.txt)",
    )
    parser.add_argument(
        "-p",
        "--patch-output",
        default="target_commits.patch",
        help="Path to the patch file (default: target_commits.patch)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    targets = normalize_dirs(args.directories)
    commits = commit_range(args.start_commit, args.end_commit)
    matched = commits_touching_targets(commits, targets)
    write_commits(matched, Path(args.output))
    if matched:
        generate_patch(matched, targets, Path(args.patch_output))
    else:
        Path(args.patch_output).write_text("")


if __name__ == "__main__":
    main()

