#!/usr/bin/env python3
"""Collect commits that touch specific directories between two commits."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Tuple


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


def parse_rewrites(entries: Iterable[str]) -> Dict[str, str]:
    """Parse rewrite directives like 'src=dest' into a mapping."""
    mapping: Dict[str, str] = {}
    for raw in entries:
        if "=" not in raw:
            raise ValueError(f"Invalid rewrite '{raw}'; expected SRC=DEST")
        src, dest = raw.split("=", 1)
        src_norm = str(PurePosixPath(src.strip().strip("/")))
        dest_norm = str(PurePosixPath(dest.strip().strip("/")))
        if not src_norm or not dest_norm:
            raise ValueError(f"Invalid rewrite '{raw}'; empty component")
        mapping[src_norm] = dest_norm
    return mapping


def rewrite_relative_path(path: str, rewrites: Dict[str, str]) -> str:
    """Rewrite a POSIX path according to the provided mapping."""
    normalized = path.strip("/")
    for src, dest in rewrites.items():
        if normalized == src:
            return dest
        if normalized.startswith(f"{src}/"):
            suffix = normalized[len(src) :].lstrip("/")
            return f"{dest}/{suffix}" if suffix else dest
    return normalized


def rewrite_patch_content(
    patch_text: str,
    rewrites: Dict[str, str],
    base_dir: Path,
    commit: str,
) -> Tuple[str, List[str]]:
    """Rewrite patch paths and drop chunks whose targets do not exist."""
    if not rewrites:
        return (patch_text if patch_text.endswith("\n") else patch_text + "\n"), []

    lines = patch_text.splitlines()
    preamble: List[str] = []
    chunks: List[List[str]] = []
    warnings: List[str] = []
    current_chunk: List[str] | None = None
    current_target: str | None = None

    def flush_chunk() -> None:
        nonlocal current_chunk, current_target
        if current_chunk is None:
            return
        if current_target and (base_dir / current_target).exists():
            chunks.append(current_chunk)
        else:
            missing = current_target or "unknown path"
            warnings.append(
                f"Warning: skipping commit {commit} file {missing} (missing target)"
            )
        current_chunk = None
        current_target = None

    for line in lines:
        if line.startswith("diff --git "):
            flush_chunk()
            parts = line.split()
            if len(parts) >= 4:
                for idx in (2, 3):
                    prefixed = parts[idx]
                    if prefixed.startswith(("a/", "b/")):
                        prefix, rel = prefixed[:2], prefixed[2:]
                        rel = rewrite_relative_path(rel, rewrites)
                        parts[idx] = f"{prefix}{rel}"
            current_chunk = [" ".join(parts)]
            continue

        if current_chunk is None:
            preamble.append(line)
            continue

        rewritten = line
        if line.startswith(("--- ", "+++ ")):
            prefix, path_str = line[:4], line[4:]
            if path_str != "/dev/null" and path_str.startswith(("a/", "b/")):
                rel = rewrite_relative_path(path_str[2:], rewrites)
                rewritten = f"{prefix}{path_str[:2]}{rel}"
                if prefix == "+++":
                    current_target = rel
                elif prefix == "---" and current_target is None:
                    current_target = rel
            elif path_str != "/dev/null":
                rel = rewrite_relative_path(path_str, rewrites)
                rewritten = f"{prefix}{rel}"
                if prefix == "+++":
                    current_target = rel
                elif prefix == "---" and current_target is None:
                    current_target = rel
            elif current_target is None and prefix == "---":
                current_target = None
        elif line.startswith(("rename from ", "rename to ")):
            keyword, path_str = line.split(" ", 1)
            rel = rewrite_relative_path(path_str.strip(), rewrites)
            rewritten = f"{keyword} {rel}"
        elif line.startswith(("copy from ", "copy to ")):
            keyword, path_str = line.split(" ", 1)
            rel = rewrite_relative_path(path_str.strip(), rewrites)
            rewritten = f"{keyword} {rel}"

        current_chunk.append(rewritten)

    flush_chunk()

    if not chunks:
        return ("", warnings)

    result_lines = preamble + [""] if preamble and preamble[-1] != "" else preamble[:]
    for chunk in chunks:
        result_lines.extend(chunk)
    result_text = "\n".join(result_lines).rstrip("\n") + "\n"
    return result_text, warnings


def generate_patches(
    commits: Iterable[str],
    targets: List[str],
    output_dir: Path,
    rewrites: Dict[str, str],
) -> List[Path]:
    """Write individual patch files per commit containing only target changes."""
    output_dir.mkdir(parents=True, exist_ok=True)
    pathspecs = [] if targets == ["."] else targets
    written: List[Path] = []
    base_dir = Path.cwd()
    for idx, commit in enumerate(commits, start=1):
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
        patch_content = run_git(cmd)
        warnings: List[str] = []
        if rewrites:
            patch_content, warnings = rewrite_patch_content(
                patch_content, rewrites, base_dir, commit
            )
        for warning in warnings:
            sys.stderr.write(warning + "\n")
        if not patch_content.strip():
            continue
        if not patch_content:
            continue
        patch_name = f"{idx:04d}-{commit[:12]}.patch"
        patch_path = output_dir / patch_name
        patch_path.write_text(patch_content)
        written.append(patch_path)
    return written


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
        "-d",
        "--patch-dir",
        default="patches",
        help="Directory to store per-commit patches (default: patches)",
    )
    parser.add_argument(
        "--rewrite",
        action="append",
        default=[],
        metavar="SRC=DEST",
        help=(
            "Rewrite path prefixes inside generated patches (e.g. "
            "gluten-ut/spark35=gluten-ut/spark40)"
        ),
    )
    args = parser.parse_args()
    try:
        args.rewrite_map = parse_rewrites(args.rewrite)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main() -> None:
    args = parse_args()
    targets = normalize_dirs(args.directories)
    commits = commit_range(args.start_commit, args.end_commit)
    matched = commits_touching_targets(commits, targets)
    write_commits(matched, Path(args.output))
    if matched:
        generate_patches(matched, targets, Path(args.patch_dir), args.rewrite_map)


if __name__ == "__main__":
    main()

