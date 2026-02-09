"""Tests for vault_backup.restore."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vault_backup.restore import (
    GitCommit,
    ResticEntry,
    ResticSnapshot,
    detect_source,
    git_file_history,
    git_log,
    git_restore_file,
    git_show_file,
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
