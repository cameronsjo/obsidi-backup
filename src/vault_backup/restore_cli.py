"""CLI for browsing and restoring from git and restic backups."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from vault_backup import __version__
from vault_backup.restore import (
    detect_source,
    git_file_history,
    git_log,
    git_restore_file,
    git_show_file,
    restic_ls,
    restic_restore_file,
    restic_snapshots,
)

log = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Configure human-readable logging for CLI use."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.WARNING)


def _vault_path() -> Path:
    """Read VAULT_PATH from environment."""
    raw = os.environ.get("VAULT_PATH", "/vault")
    path = Path(raw)
    if not path.is_dir():
        print(f"error: vault path '{raw}' is not a directory", file=sys.stderr)
        sys.exit(1)
    return path


def _format_time(iso_time: str) -> str:
    """Format an ISO timestamp into a shorter human-readable form."""
    try:
        dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, AttributeError):
        return iso_time


# --- Subcommands ---


def cmd_snapshots(args: argparse.Namespace) -> None:
    """List restic snapshots."""
    tag = getattr(args, "tag", "obsidian")
    snaps = restic_snapshots(tag=tag)
    if not snaps:
        print("No snapshots found.")
        return

    # Table header
    print(f"{'ID':<10} {'Time':<18} {'Paths':<40} {'Tags'}")
    print("-" * 80)
    for s in snaps:
        paths = ", ".join(s.paths) if s.paths else "-"
        tags = ", ".join(s.tags) if s.tags else "-"
        print(f"{s.short_id:<10} {_format_time(s.time):<18} {paths:<40} {tags}")


def cmd_files(args: argparse.Namespace) -> None:
    """List files in a restic snapshot."""
    try:
        entries = restic_ls(args.snapshot_id, path=getattr(args, "path", "/"))
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    if not entries:
        print("No files found.")
        return

    print(f"{'Type':<6} {'Size':>10} {'Modified':<18} {'Path'}")
    print("-" * 80)
    for e in entries:
        size_str = f"{e.size:,}" if e.type == "file" else "-"
        print(f"{e.type:<6} {size_str:>10} {_format_time(e.mtime):<18} {e.path}")


def cmd_log(args: argparse.Namespace) -> None:
    """Show git commit history."""
    vault = _vault_path()
    filepath = getattr(args, "file", None)

    if filepath:
        commits = git_file_history(vault, filepath, count=getattr(args, "count", 20))
    else:
        commits = git_log(vault, count=getattr(args, "count", 20))

    if not commits:
        print("No commits found.")
        return

    print(f"{'Hash':<10} {'Date':<18} {'Message'}")
    print("-" * 70)
    for c in commits:
        print(f"{c.short_hash:<10} {_format_time(c.date):<18} {c.message}")


def cmd_show(args: argparse.Namespace) -> None:
    """Show file content at a specific git commit."""
    vault = _vault_path()
    try:
        content = git_show_file(vault, args.commit, args.path)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    print(content, end="")


def cmd_restore(args: argparse.Namespace) -> None:
    """Restore a file from git or restic."""
    source = args.source
    filepath = args.path
    output = Path(args.output) if args.output else Path(filepath).name
    output = Path(output)

    source_type = detect_source(source)

    if source_type == "ambiguous":
        # Try git first, fall back to restic
        vault = _vault_path()
        try:
            git_restore_file(vault, source, filepath, output)
            print(f"Restored {filepath} from git commit {source} -> {output}")
            return
        except FileNotFoundError:
            pass

        try:
            restic_restore_file(source, filepath, output)
            print(f"Restored {filepath} from restic snapshot {source} -> {output}")
            return
        except FileNotFoundError:
            print(
                f"error: '{filepath}' not found in git commit or restic snapshot '{source}'",
                file=sys.stderr,
            )
            sys.exit(1)

    elif source_type == "git":
        vault = _vault_path()
        try:
            git_restore_file(vault, source, filepath, output)
            print(f"Restored {filepath} from git commit {source} -> {output}")
        except FileNotFoundError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)

    else:
        try:
            restic_restore_file(source, filepath, output)
            print(f"Restored {filepath} from restic snapshot {source} -> {output}")
        except FileNotFoundError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="vault-backup-restore",
        description="Browse and restore files from Obsidian vault backups.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")

    sub = parser.add_subparsers(dest="command")

    # snapshots
    sp = sub.add_parser("snapshots", help="list restic snapshots")
    sp.add_argument("--tag", default="obsidian", help="filter by tag (default: obsidian)")
    sp.set_defaults(func=cmd_snapshots)

    # files
    sp = sub.add_parser("files", help="list files in a restic snapshot")
    sp.add_argument("snapshot_id", help="restic snapshot ID")
    sp.add_argument("--path", default="/", help="filter by path prefix")
    sp.set_defaults(func=cmd_files)

    # log
    sp = sub.add_parser("log", help="show git commit history")
    sp.add_argument("--file", help="show history for a specific file")
    sp.add_argument("--count", type=int, default=20, help="number of commits (default: 20)")
    sp.set_defaults(func=cmd_log)

    # show
    sp = sub.add_parser("show", help="show file content at a git commit")
    sp.add_argument("commit", help="git commit hash")
    sp.add_argument("path", help="file path within the vault")
    sp.set_defaults(func=cmd_show)

    # restore
    sp = sub.add_parser("restore", help="restore a file from git or restic")
    sp.add_argument("source", help="git commit hash or restic snapshot ID")
    sp.add_argument("path", help="file path to restore")
    sp.add_argument("--output", "-o", help="output path (default: filename in current dir)")
    sp.set_defaults(func=cmd_restore)

    return parser


def main() -> None:
    """CLI entry point."""
    _configure_logging()
    parser = build_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)
