"""Tests for vault_backup.restore."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vault_backup.restore import (
    GitCommit,
    GitFileChange,
    ResticEntry,
    ResticSnapshot,
    detect_source,
    git_diff_tree,
    git_file_history,
    git_log,
    git_log_single,
    git_restore_file,
    git_show_file,
    group_entries_by_directory,
    restic_ls,
    restic_restore_file,
    restic_show_file,
    restic_snapshots,
)

# --- Data class construction ---


class TestDataClasses:
    def test_git_commit_fields(self) -> None:
        c = GitCommit(hash="abc123def", short_hash="abc123d", date="2025-01-01", message="update")
        assert c.hash == "abc123def"
        assert c.short_hash == "abc123d"
        assert c.message == "update"

    def test_restic_snapshot_fields(self) -> None:
        s = ResticSnapshot(
            id="abcdef12", short_id="abcdef12", time="2025-01-01T00:00:00Z",
            paths=["/vault"], tags=["obsidian"],
        )
        assert s.short_id == "abcdef12"
        assert s.tags == ["obsidian"]

    def test_restic_entry_fields(self) -> None:
        e = ResticEntry(path="/vault/note.md", type="file", size=1024, mtime="2025-01-01T00:00:00Z")
        assert e.size == 1024

    def test_frozen_git_commit(self) -> None:
        c = GitCommit(hash="abc", short_hash="ab", date="2025-01-01", message="msg")
        with pytest.raises(AttributeError):
            c.hash = "xyz"  # type: ignore[misc]

    def test_frozen_restic_snapshot(self) -> None:
        s = ResticSnapshot(id="abc", short_id="ab", time="t", paths=[], tags=[])
        with pytest.raises(AttributeError):
            s.id = "xyz"  # type: ignore[misc]

    def test_git_file_change_fields(self) -> None:
        c = GitFileChange(path="notes/daily.md", status="M")
        assert c.path == "notes/daily.md"
        assert c.status == "M"


# --- Git operations ---


class TestGitLog:
    def test_parses_commits(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.stdout = (
            "abc123def456789012345678901234567890abcd\n"
            "abc123d\n"
            "2025-01-15T10:30:00+00:00\n"
            "update daily notes\n"
            "def456abc789012345678901234567890abcdef12\n"
            "def456a\n"
            "2025-01-14T09:00:00+00:00\n"
            "add weekly review\n"
        )
        mock_subprocess.return_value.returncode = 0
        commits = git_log(Path("/vault"), count=5)
        assert len(commits) == 2
        assert commits[0].short_hash == "abc123d"
        assert commits[0].message == "update daily notes"
        assert commits[1].message == "add weekly review"

    def test_empty_repo(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.stdout = ""
        mock_subprocess.return_value.returncode = 128
        commits = git_log(Path("/vault"))
        assert commits == []

    def test_no_output(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.stdout = ""
        mock_subprocess.return_value.returncode = 0
        assert git_log(Path("/vault")) == []


class TestGitLogSingle:
    def test_returns_single_commit(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.stdout = (
            "abc123def456789012345678901234567890abcd\n"
            "abc123d\n"
            "2025-01-15T10:30:00+00:00\n"
            "update daily notes\n"
        )
        mock_subprocess.return_value.returncode = 0
        commits = git_log_single(Path("/vault"), "abc123d")
        assert len(commits) == 1
        assert commits[0].short_hash == "abc123d"
        cmd = mock_subprocess.call_args[0][0]
        assert "-1" in cmd

    def test_not_found(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.stdout = ""
        mock_subprocess.return_value.returncode = 128
        assert git_log_single(Path("/vault"), "badbeef") == []


class TestGitFileHistory:
    def test_follows_renames(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.stdout = (
            "abc123def456789012345678901234567890abcd\n"
            "abc123d\n"
            "2025-01-15T10:30:00+00:00\n"
            "rename daily note\n"
        )
        mock_subprocess.return_value.returncode = 0
        commits = git_file_history(Path("/vault"), "notes/daily.md")
        assert len(commits) == 1
        # Verify --follow is in the command
        cmd = mock_subprocess.call_args[0][0]
        assert "--follow" in cmd

    def test_file_not_in_history(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.stdout = ""
        mock_subprocess.return_value.returncode = 0
        assert git_file_history(Path("/vault"), "nonexistent.md") == []


class TestGitShowFile:
    def test_returns_content(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.stdout = "# My Note\n\nHello world\n"
        mock_subprocess.return_value.returncode = 0
        content = git_show_file(Path("/vault"), "abc123d", "notes/daily.md")
        assert content == "# My Note\n\nHello world\n"
        cmd = mock_subprocess.call_args[0][0]
        assert "abc123d:notes/daily.md" in cmd

    def test_raises_on_missing_file(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.returncode = 128
        mock_subprocess.return_value.stderr = "fatal: path not found"
        with pytest.raises(FileNotFoundError, match="not found at commit"):
            git_show_file(Path("/vault"), "abc123d", "gone.md")


class TestGitRestoreFile:
    def test_writes_to_target(self, mock_subprocess: MagicMock, tmp_path: Path) -> None:
        mock_subprocess.return_value.stdout = "# Restored content\n"
        mock_subprocess.return_value.returncode = 0
        target = tmp_path / "restored" / "note.md"
        result = git_restore_file(Path("/vault"), "abc123d", "note.md", target)
        assert result == target
        assert target.read_text() == "# Restored content\n"

    def test_creates_parent_dirs(self, mock_subprocess: MagicMock, tmp_path: Path) -> None:
        mock_subprocess.return_value.stdout = "content"
        mock_subprocess.return_value.returncode = 0
        target = tmp_path / "deep" / "nested" / "dir" / "file.md"
        git_restore_file(Path("/vault"), "abc", "file.md", target)
        assert target.exists()


class TestGitDiffTree:
    def test_parses_name_status(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.stdout = "M\tnotes/daily.md\nA\tnotes/new.md\nD\told/removed.md\n"
        mock_subprocess.return_value.returncode = 0
        changes = git_diff_tree(Path("/vault"), "abc123d")
        assert len(changes) == 3
        assert changes[0] == GitFileChange(path="notes/daily.md", status="M")
        assert changes[1] == GitFileChange(path="notes/new.md", status="A")
        assert changes[2] == GitFileChange(path="old/removed.md", status="D")

    def test_handles_rename(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.stdout = "R100\told-name.md\n"
        mock_subprocess.return_value.returncode = 0
        changes = git_diff_tree(Path("/vault"), "abc123d")
        assert len(changes) == 1
        assert changes[0].status == "R"
        assert changes[0].path == "old-name.md"

    def test_empty_commit(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.stdout = ""
        mock_subprocess.return_value.returncode = 0
        assert git_diff_tree(Path("/vault"), "abc123d") == []

    def test_failed_command(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.returncode = 128
        mock_subprocess.return_value.stdout = ""
        assert git_diff_tree(Path("/vault"), "badbeef") == []


# --- Restic operations ---


class TestResticSnapshots:
    def test_parses_json_output(self, mock_subprocess: MagicMock) -> None:
        snapshots_json = json.dumps([
            {
                "id": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890ab",
                "short_id": "abcdef12",
                "time": "2025-01-15T10:30:00.123456Z",
                "paths": ["/vault"],
                "tags": ["obsidian", "auto-backup"],
            },
            {
                "id": "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
                "short_id": "12345678",
                "time": "2025-01-14T09:00:00.000000Z",
                "paths": ["/vault"],
                "tags": ["obsidian"],
            },
        ])
        mock_subprocess.return_value.stdout = snapshots_json
        mock_subprocess.return_value.returncode = 0

        snaps = restic_snapshots(tag="obsidian")
        assert len(snaps) == 2
        assert snaps[0].short_id == "abcdef12"
        assert snaps[0].tags == ["obsidian", "auto-backup"]
        assert snaps[1].paths == ["/vault"]

    def test_empty_when_no_repo(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.returncode = 1
        mock_subprocess.return_value.stdout = ""
        assert restic_snapshots() == []

    def test_empty_json_array(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.stdout = "[]"
        mock_subprocess.return_value.returncode = 0
        assert restic_snapshots() == []

    def test_handles_bad_json(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.stdout = "not json at all"
        mock_subprocess.return_value.returncode = 0
        assert restic_snapshots() == []

    def test_no_tag_filter(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.stdout = "[]"
        mock_subprocess.return_value.returncode = 0
        restic_snapshots(tag="")
        cmd = mock_subprocess.call_args[0][0]
        assert "--tag" not in cmd

    def test_missing_short_id_uses_prefix(self, mock_subprocess: MagicMock) -> None:
        """When short_id is missing from JSON, fall back to first 8 chars of id."""
        snapshots_json = json.dumps([
            {"id": "abcdef1234567890", "time": "2025-01-15T00:00:00Z", "paths": [], "tags": []},
        ])
        mock_subprocess.return_value.stdout = snapshots_json
        mock_subprocess.return_value.returncode = 0
        snaps = restic_snapshots()
        assert snaps[0].short_id == "abcdef12"


class TestResticLs:
    def test_parses_ndjson_output(self, mock_subprocess: MagicMock) -> None:
        # restic ls --json outputs one JSON object per line (NDJSON)
        lines = [
            json.dumps({"struct_type": "snapshot", "id": "abc123"}),
            json.dumps({"path": "/vault/notes", "type": "dir", "size": 0, "mtime": "2025-01-15T00:00:00Z"}),
            json.dumps({"path": "/vault/notes/daily.md", "type": "file", "size": 2048, "mtime": "2025-01-15T10:30:00Z"}),
        ]
        mock_subprocess.return_value.stdout = "\n".join(lines)
        mock_subprocess.return_value.returncode = 0

        entries = restic_ls("abcdef12")
        assert len(entries) == 2  # snapshot metadata line is skipped
        assert entries[0].type == "dir"
        assert entries[1].path == "/vault/notes/daily.md"
        assert entries[1].size == 2048

    def test_filters_by_path_prefix(self, mock_subprocess: MagicMock) -> None:
        lines = [
            json.dumps({"path": "/vault/notes/daily.md", "type": "file", "size": 100, "mtime": ""}),
            json.dumps({"path": "/vault/templates/t.md", "type": "file", "size": 50, "mtime": ""}),
        ]
        mock_subprocess.return_value.stdout = "\n".join(lines)
        mock_subprocess.return_value.returncode = 0

        entries = restic_ls("abcdef12", path="/vault/notes")
        assert len(entries) == 1
        assert entries[0].path == "/vault/notes/daily.md"

    def test_raises_on_bad_snapshot(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.returncode = 1
        mock_subprocess.return_value.stderr = "snapshot not found"
        with pytest.raises(ValueError, match="not found"):
            restic_ls("badid123")

    def test_skips_malformed_json_lines(self, mock_subprocess: MagicMock) -> None:
        lines = [
            json.dumps({"path": "/vault/good.md", "type": "file", "size": 100, "mtime": ""}),
            "this is not json",
            json.dumps({"path": "/vault/also-good.md", "type": "file", "size": 50, "mtime": ""}),
        ]
        mock_subprocess.return_value.stdout = "\n".join(lines)
        mock_subprocess.return_value.returncode = 0
        entries = restic_ls("abcdef12")
        assert len(entries) == 2


class TestResticShowFile:
    def test_returns_content(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.stdout = "# Note content\n"
        mock_subprocess.return_value.returncode = 0
        content = restic_show_file("abcdef12", "/vault/note.md")
        assert content == "# Note content\n"

    def test_raises_on_failure(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.returncode = 1
        with pytest.raises(FileNotFoundError, match="not found in snapshot"):
            restic_show_file("abcdef12", "/vault/gone.md")


class TestResticRestoreFile:
    def test_dumps_to_target(self, mock_subprocess: MagicMock, tmp_path: Path) -> None:
        mock_subprocess.return_value.stdout = "# Restored from restic\n"
        mock_subprocess.return_value.returncode = 0
        target = tmp_path / "restored.md"
        result = restic_restore_file("abcdef12", "/vault/note.md", target)
        assert result == target
        assert target.read_text() == "# Restored from restic\n"

    def test_raises_on_failure(self, mock_subprocess: MagicMock, tmp_path: Path) -> None:
        mock_subprocess.return_value.returncode = 1
        mock_subprocess.return_value.stderr = "dump failed"
        with pytest.raises(FileNotFoundError, match="Failed to restore"):
            restic_restore_file("abcdef12", "/vault/gone.md", tmp_path / "out.md")


# --- Directory grouping ---


class TestGroupEntriesByDirectory:
    def test_root_level(self) -> None:
        entries = [
            ResticEntry(path="/vault", type="dir", size=0, mtime=""),
            ResticEntry(path="/vault/note.md", type="file", size=100, mtime="2025-01-15T00:00:00Z"),
            ResticEntry(path="/vault/deep/nested.md", type="file", size=50, mtime=""),
        ]
        result = group_entries_by_directory(entries, "/")
        assert len(result) == 1  # only /vault dir
        assert result[0].path == "/vault"
        assert result[0].type == "dir"

    def test_subdirectory(self) -> None:
        entries = [
            ResticEntry(path="/vault/notes", type="dir", size=0, mtime=""),
            ResticEntry(path="/vault/notes/daily.md", type="file", size=100, mtime="t1"),
            ResticEntry(path="/vault/notes/weekly.md", type="file", size=200, mtime="t2"),
            ResticEntry(path="/vault/notes/archive/old.md", type="file", size=50, mtime=""),
            ResticEntry(path="/vault/readme.md", type="file", size=80, mtime="t3"),
        ]
        result = group_entries_by_directory(entries, "/vault")
        names = [e.path for e in result]
        assert "/vault/notes" in names
        assert "/vault/readme.md" in names
        assert len(result) == 2  # notes dir + readme.md file

    def test_synthesizes_implicit_dirs(self) -> None:
        """Directories without explicit dir entries are synthesized."""
        entries = [
            ResticEntry(path="/vault/notes/daily.md", type="file", size=100, mtime=""),
            ResticEntry(path="/vault/projects/homelab.md", type="file", size=200, mtime=""),
        ]
        result = group_entries_by_directory(entries, "/vault")
        assert len(result) == 2
        assert all(e.type == "dir" for e in result)
        paths = {e.path for e in result}
        assert "/vault/notes" in paths
        assert "/vault/projects" in paths

    def test_empty_entries(self) -> None:
        assert group_entries_by_directory([], "/") == []

    def test_dirs_sorted_before_files(self) -> None:
        entries = [
            ResticEntry(path="/vault/zebra.md", type="file", size=100, mtime=""),
            ResticEntry(path="/vault/alpha", type="dir", size=0, mtime=""),
            ResticEntry(path="/vault/alpha/file.md", type="file", size=50, mtime=""),
            ResticEntry(path="/vault/beta.md", type="file", size=80, mtime=""),
        ]
        result = group_entries_by_directory(entries, "/vault")
        assert result[0].type == "dir"  # alpha dir first
        assert result[0].path == "/vault/alpha"
        assert result[1].path == "/vault/beta.md"
        assert result[2].path == "/vault/zebra.md"

    def test_no_duplicate_dirs(self) -> None:
        """Multiple files in same dir should produce one dir entry."""
        entries = [
            ResticEntry(path="/vault/notes/a.md", type="file", size=10, mtime=""),
            ResticEntry(path="/vault/notes/b.md", type="file", size=20, mtime=""),
            ResticEntry(path="/vault/notes/c.md", type="file", size=30, mtime=""),
        ]
        result = group_entries_by_directory(entries, "/vault")
        assert len(result) == 1
        assert result[0].path == "/vault/notes"
        assert result[0].type == "dir"


# --- Source detection ---


class TestDetectSource:
    def test_full_git_hash(self) -> None:
        assert detect_source("a" * 40) == "git"

    def test_short_git_hash(self) -> None:
        assert detect_source("abc1234") == "git"

    def test_ambiguous_8_char_hex(self) -> None:
        assert detect_source("abcdef12") == "ambiguous"

    def test_non_hex_defaults_to_restic(self) -> None:
        assert detect_source("latest") == "restic"

    def test_12_char_hex_is_git(self) -> None:
        assert detect_source("abcdef123456") == "git"
