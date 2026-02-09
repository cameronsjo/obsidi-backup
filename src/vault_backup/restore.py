"""Git and restic restore operations."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from vault_backup.backup import run_cmd

log = logging.getLogger(__name__)


# --- Data classes ---


@dataclass(frozen=True)
class GitCommit:
    """A git commit entry."""

    hash: str
    short_hash: str
    date: str
    message: str


@dataclass(frozen=True)
class ResticSnapshot:
    """A restic snapshot entry."""

    id: str
    short_id: str
    time: str
    paths: list[str]
    tags: list[str]


@dataclass(frozen=True)
class ResticEntry:
    """A file entry from restic ls."""

    path: str
    type: str
    size: int
    mtime: str


# --- Git operations ---

_GIT_LOG_FORMAT = "%H%n%h%n%aI%n%s"


def _parse_git_log(output: str) -> list[GitCommit]:
    """Parse git log output using 4-line-per-commit format."""
    lines = output.strip().split("\n")
    commits: list[GitCommit] = []
    # Each commit is 4 consecutive lines: hash, short_hash, date, message
    for i in range(0, len(lines) - 3, 4):
        commits.append(
            GitCommit(
                hash=lines[i],
                short_hash=lines[i + 1],
                date=lines[i + 2],
                message=lines[i + 3],
            )
        )
    return commits


def git_log(vault_path: Path, count: int = 20) -> list[GitCommit]:
    """List recent git commits in the vault."""
    log.debug("Listing git commits", extra={"vault_path": str(vault_path), "count": count})
    result = run_cmd(
        ["git", "log", f"--format={_GIT_LOG_FORMAT}", f"-{count}"],
        cwd=vault_path,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    return _parse_git_log(result.stdout)


def git_file_history(vault_path: Path, filepath: str, count: int = 10) -> list[GitCommit]:
    """List commits that modified a specific file."""
    log.debug(
        "Listing file history",
        extra={"vault_path": str(vault_path), "filepath": filepath, "count": count},
    )
    result = run_cmd(
        ["git", "log", "--follow", f"--format={_GIT_LOG_FORMAT}", f"-{count}", "--", filepath],
        cwd=vault_path,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    return _parse_git_log(result.stdout)


def git_show_file(vault_path: Path, commit: str, filepath: str) -> str:
    """Retrieve file content at a specific commit."""
    log.debug(
        "Showing file at commit",
        extra={"commit": commit, "filepath": filepath},
    )
    result = run_cmd(
        ["git", "show", f"{commit}:{filepath}"],
        cwd=vault_path,
        check=False,
    )
    if result.returncode != 0:
        msg = f"File '{filepath}' not found at commit {commit}"
        raise FileNotFoundError(msg)
    return result.stdout


def git_restore_file(vault_path: Path, commit: str, filepath: str, target: Path) -> Path:
    """Restore a file from a git commit to a target path."""
    log.info(
        "Restoring file from git",
        extra={"commit": commit, "filepath": filepath, "target": str(target)},
    )
    content = git_show_file(vault_path, commit, filepath)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    log.info("File restored from git", extra={"target": str(target)})
    return target


# --- Restic operations ---


def restic_snapshots(tag: str = "obsidian") -> list[ResticSnapshot]:
    """List restic snapshots."""
    log.debug("Listing restic snapshots", extra={"tag": tag})
    cmd = ["restic", "snapshots", "--json"]
    if tag:
        cmd.extend(["--tag", tag])

    result = run_cmd(cmd, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return []

    try:
        entries = json.loads(result.stdout)
    except json.JSONDecodeError:
        log.warning("Failed to parse restic snapshots JSON")
        return []

    return [
        ResticSnapshot(
            id=s["id"],
            short_id=s.get("short_id", s["id"][:8]),
            time=s["time"],
            paths=s.get("paths", []),
            tags=s.get("tags", []),
        )
        for s in entries
    ]


def restic_ls(snapshot_id: str, path: str = "/") -> list[ResticEntry]:
    """List files in a restic snapshot."""
    log.debug("Listing snapshot files", extra={"snapshot_id": snapshot_id, "path": path})
    result = run_cmd(
        ["restic", "ls", "--json", snapshot_id],
        check=False,
    )
    if result.returncode != 0:
        msg = f"Snapshot '{snapshot_id}' not found"
        raise ValueError(msg)

    entries: list[ResticEntry] = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        # restic ls --json emits one JSON object per line; skip the snapshot metadata line
        if "struct_type" in obj and obj["struct_type"] == "snapshot":
            continue
        entries.append(
            ResticEntry(
                path=obj.get("path", ""),
                type=obj.get("type", "file"),
                size=obj.get("size", 0),
                mtime=obj.get("mtime", ""),
            )
        )

    # Filter by path prefix if not root
    if path != "/":
        normalized = path.rstrip("/")
        entries = [e for e in entries if e.path.startswith(normalized)]

    return entries


def restic_show_file(snapshot_id: str, filepath: str) -> str:
    """Retrieve file content from a restic snapshot without writing to disk."""
    log.debug(
        "Showing file from restic",
        extra={"snapshot_id": snapshot_id, "filepath": filepath},
    )
    result = run_cmd(
        ["restic", "dump", snapshot_id, filepath],
        check=False,
    )
    if result.returncode != 0:
        msg = f"File '{filepath}' not found in snapshot {snapshot_id}"
        raise FileNotFoundError(msg)
    return result.stdout


def restic_restore_file(snapshot_id: str, filepath: str, target: Path) -> Path:
    """Restore a single file from a restic snapshot using dump."""
    log.info(
        "Restoring file from restic",
        extra={"snapshot_id": snapshot_id, "filepath": filepath, "target": str(target)},
    )
    result = run_cmd(
        ["restic", "dump", snapshot_id, filepath],
        check=False,
    )
    if result.returncode != 0:
        msg = f"Failed to restore '{filepath}' from snapshot {snapshot_id}"
        raise FileNotFoundError(msg)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(result.stdout)
    log.info("File restored from restic", extra={"target": str(target)})
    return target


def detect_source(source: str) -> str:
    """Detect whether a source identifier is a git commit or restic snapshot.

    Git commits are 7-40 hex chars. Restic short IDs are 8 hex chars.
    We attempt git first, falling back to restic.
    """
    # Restic IDs can contain non-hex chars in some formats, but short_ids are hex
    # Git hashes are always hex. Use length as a heuristic:
    # - 40 chars = definitely full git hash
    # - 7-8 chars = could be either; caller should try git first
    if len(source) == 40 and all(c in "0123456789abcdef" for c in source):
        return "git"
    if len(source) == 8 and all(c in "0123456789abcdef" for c in source):
        return "ambiguous"
    if all(c in "0123456789abcdef" for c in source) and 7 <= len(source) <= 40:
        return "git"
    return "restic"
