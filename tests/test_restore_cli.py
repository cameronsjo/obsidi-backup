"""Tests for vault_backup.restore_cli."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest

from vault_backup.restore import GitCommit, ResticEntry, ResticSnapshot
from vault_backup.restore_cli import (
    _format_time,
    _vault_path,
    build_parser,
    cmd_files,
    cmd_log,
    cmd_restore,
    cmd_show,
    cmd_snapshots,
    main,
)

# --- _format_time ---


class TestFormatTime:
    def test_iso_with_timezone(self) -> None:
        assert _format_time("2025-01-15T10:30:00+00:00") == "2025-01-15 10:30"

    def test_iso_with_z_suffix(self) -> None:
        assert _format_time("2025-01-15T10:30:00Z") == "2025-01-15 10:30"

    def test_invalid_returns_original(self) -> None:
        assert _format_time("not-a-date") == "not-a-date"

    def test_empty_string_returns_empty(self) -> None:
        assert _format_time("") == ""


# --- _vault_path ---


class TestVaultPath:
    def test_reads_from_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))
        assert _vault_path() == tmp_path

    def test_exits_when_path_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VAULT_PATH", "/nonexistent/path/abc123")
        with pytest.raises(SystemExit, match="1"):
            _vault_path()

    def test_exits_with_stderr(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("VAULT_PATH", "/nonexistent/path/abc123")
        with pytest.raises(SystemExit):
            _vault_path()
        assert "not a directory" in capsys.readouterr().err


# --- build_parser ---


class TestBuildParser:
    def test_snapshots_subcommand(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["snapshots"])
        assert args.command == "snapshots"
        assert args.tag == "obsidian"

    def test_snapshots_custom_tag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["snapshots", "--tag", "daily"])
        assert args.tag == "daily"

    def test_files_subcommand(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["files", "abc12345"])
        assert args.snapshot_id == "abc12345"
        assert args.path == "/"

    def test_log_subcommand(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["log", "--file", "notes/daily.md", "--count", "5"])
        assert args.file == "notes/daily.md"
        assert args.count == 5

    def test_show_subcommand(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["show", "abc123d", "notes/daily.md"])
        assert args.commit == "abc123d"
        assert args.path == "notes/daily.md"

    def test_restore_subcommand(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["restore", "abc123d", "notes/daily.md", "-o", "out.md"])
        assert args.source == "abc123d"
        assert args.path == "notes/daily.md"
        assert args.output == "out.md"

    def test_restore_default_output(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["restore", "abc123d", "notes/daily.md"])
        assert args.output is None


# --- main ---


class TestMain:
    def test_no_subcommand_exits(self) -> None:
        with patch("sys.argv", ["vault-backup-restore"]), pytest.raises(SystemExit, match="1"):
            main()

    def test_verbose_sets_debug(self) -> None:
        with (
            patch("sys.argv", ["vault-backup-restore", "-v", "snapshots"]),
            patch("vault_backup.restore_cli.restic_snapshots", return_value=[]),
        ):
            main()
            import logging

            assert logging.getLogger().level == logging.DEBUG


# --- cmd_snapshots ---


class TestCmdSnapshots:
    def test_prints_table(self, capsys: pytest.CaptureFixture[str]) -> None:
        snaps = [
            ResticSnapshot(
                id="a" * 64, short_id="abcdef12",
                time="2025-01-15T10:30:00Z", paths=["/vault"], tags=["obsidian"],
            ),
        ]
        with patch("vault_backup.restore_cli.restic_snapshots", return_value=snaps):
            args = argparse.Namespace(tag="obsidian")
            cmd_snapshots(args)

        out = capsys.readouterr().out
        assert "abcdef12" in out
        assert "2025-01-15 10:30" in out
        assert "/vault" in out
        assert "obsidian" in out

    def test_empty_snapshots(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("vault_backup.restore_cli.restic_snapshots", return_value=[]):
            cmd_snapshots(argparse.Namespace(tag="obsidian"))
        assert "No snapshots found." in capsys.readouterr().out


# --- cmd_files ---


class TestCmdFiles:
    def test_prints_file_table(self, capsys: pytest.CaptureFixture[str]) -> None:
        entries = [
            ResticEntry(path="/vault/note.md", type="file", size=2048, mtime="2025-01-15T10:30:00Z"),
            ResticEntry(path="/vault/dir", type="dir", size=0, mtime="2025-01-15T00:00:00Z"),
        ]
        with patch("vault_backup.restore_cli.restic_ls", return_value=entries):
            cmd_files(argparse.Namespace(snapshot_id="abcdef12", path="/"))

        out = capsys.readouterr().out
        assert "2,048" in out  # formatted size
        assert "/vault/note.md" in out
        assert "-" in out  # dir size shows dash

    def test_empty_files(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("vault_backup.restore_cli.restic_ls", return_value=[]):
            cmd_files(argparse.Namespace(snapshot_id="abcdef12", path="/"))
        assert "No files found." in capsys.readouterr().out

    def test_bad_snapshot_exits(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("vault_backup.restore_cli.restic_ls", side_effect=ValueError("not found")),
            pytest.raises(SystemExit, match="1"),
        ):
            cmd_files(argparse.Namespace(snapshot_id="bad", path="/"))
        assert "not found" in capsys.readouterr().err


# --- cmd_log ---


class TestCmdLog:
    def test_prints_commit_table(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))
        commits = [
            GitCommit(hash="a" * 40, short_hash="abc123d", date="2025-01-15T10:30:00+00:00", message="update notes"),
        ]
        with patch("vault_backup.restore_cli.git_log", return_value=commits):
            cmd_log(argparse.Namespace(file=None, count=20))

        out = capsys.readouterr().out
        assert "abc123d" in out
        assert "update notes" in out

    def test_with_file_filter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))
        commits = [
            GitCommit(hash="b" * 40, short_hash="bbb1234", date="2025-01-14T09:00:00+00:00", message="edit daily"),
        ]
        with patch("vault_backup.restore_cli.git_file_history", return_value=commits) as mock_hist:
            cmd_log(argparse.Namespace(file="notes/daily.md", count=10))
            mock_hist.assert_called_once_with(tmp_path, "notes/daily.md", count=10)

    def test_empty_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))
        with patch("vault_backup.restore_cli.git_log", return_value=[]):
            cmd_log(argparse.Namespace(file=None, count=20))
        assert "No commits found." in capsys.readouterr().out


# --- cmd_show ---


class TestCmdShow:
    def test_prints_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))
        with patch("vault_backup.restore_cli.git_show_file", return_value="# Hello\n"):
            cmd_show(argparse.Namespace(commit="abc123d", path="note.md"))
        assert capsys.readouterr().out == "# Hello\n"

    def test_missing_file_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))
        with (
            patch("vault_backup.restore_cli.git_show_file", side_effect=FileNotFoundError("not found")),
            pytest.raises(SystemExit, match="1"),
        ):
            cmd_show(argparse.Namespace(commit="abc123d", path="gone.md"))
        assert "not found" in capsys.readouterr().err


# --- cmd_restore ---


class TestCmdRestore:
    def test_git_restore(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))
        target = tmp_path / "out.md"
        with patch("vault_backup.restore_cli.git_restore_file", return_value=target):
            cmd_restore(argparse.Namespace(
                source="a" * 40, path="notes/daily.md", output=str(target),
            ))
        assert "git commit" in capsys.readouterr().out

    def test_restic_restore(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        target = tmp_path / "out.md"
        with patch("vault_backup.restore_cli.restic_restore_file", return_value=target):
            cmd_restore(argparse.Namespace(
                source="latest", path="/vault/note.md", output=str(target),
            ))
        assert "restic snapshot" in capsys.readouterr().out

    def test_ambiguous_tries_git_first(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))
        target = tmp_path / "out.md"
        with patch("vault_backup.restore_cli.git_restore_file", return_value=target) as mock_git:
            cmd_restore(argparse.Namespace(
                source="abcdef12", path="note.md", output=str(target),
            ))
            mock_git.assert_called_once()
        assert "git commit" in capsys.readouterr().out

    def test_ambiguous_falls_back_to_restic(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))
        target = tmp_path / "out.md"
        with (
            patch("vault_backup.restore_cli.git_restore_file", side_effect=FileNotFoundError),
            patch("vault_backup.restore_cli.restic_restore_file", return_value=target),
        ):
            cmd_restore(argparse.Namespace(
                source="abcdef12", path="note.md", output=str(target),
            ))
        assert "restic snapshot" in capsys.readouterr().out

    def test_ambiguous_both_fail_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))
        with (
            patch("vault_backup.restore_cli.git_restore_file", side_effect=FileNotFoundError),
            patch("vault_backup.restore_cli.restic_restore_file", side_effect=FileNotFoundError),
            pytest.raises(SystemExit, match="1"),
        ):
            cmd_restore(argparse.Namespace(
                source="abcdef12", path="note.md", output=str(tmp_path / "out.md"),
            ))
        assert "not found in git commit or restic snapshot" in capsys.readouterr().err

    def test_git_restore_failure_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))
        with (
            patch("vault_backup.restore_cli.git_restore_file", side_effect=FileNotFoundError("nope")),
            pytest.raises(SystemExit, match="1"),
        ):
            cmd_restore(argparse.Namespace(
                source="a" * 40, path="gone.md", output=str(tmp_path / "out.md"),
            ))
        assert "nope" in capsys.readouterr().err

    def test_restic_restore_failure_exits(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        with (
            patch("vault_backup.restore_cli.restic_restore_file", side_effect=FileNotFoundError("nope")),
            pytest.raises(SystemExit, match="1"),
        ):
            cmd_restore(argparse.Namespace(
                source="latest", path="/vault/gone.md", output=str(tmp_path / "out.md"),
            ))
        assert "nope" in capsys.readouterr().err

    def test_default_output_uses_filename(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))
        with patch("vault_backup.restore_cli.git_restore_file") as mock_restore:
            mock_restore.return_value = Path("daily.md")
            cmd_restore(argparse.Namespace(
                source="a" * 40, path="notes/daily.md", output=None,
            ))
            # Output path should be just the filename, not the full vault path
            call_target = mock_restore.call_args[0][3]
            assert call_target == Path("daily.md")
