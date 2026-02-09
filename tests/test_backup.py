"""Tests for vault_backup.backup."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vault_backup.backup import (
    BackupResult,
    _parse_snapshot_id,
    _write_state,
    generate_ai_commit_message,
    get_changed_files,
    get_changes_summary,
    git_commit,
    has_changes,
    restic_backup,
    restic_prune,
    run_backup,
    run_cmd,
)
from vault_backup.config import Config, LLMConfig


class TestRunCmd:
    def test_captures_output(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.stdout = "hello"
        result = run_cmd(["echo", "hello"])
        assert result.stdout == "hello"
        mock_subprocess.assert_called_once()

    def test_passes_cwd(self, mock_subprocess: MagicMock) -> None:
        run_cmd(["git", "status"], cwd=Path("/tmp"))
        _, kwargs = mock_subprocess.call_args
        assert kwargs["cwd"] == Path("/tmp")


class TestHasChanges:
    def test_detects_changes(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.stdout = " M file.md\n"
        assert has_changes(Path("/vault")) is True

    def test_no_changes(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.stdout = ""
        assert has_changes(Path("/vault")) is False

    def test_whitespace_only(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.stdout = "   \n  "
        assert has_changes(Path("/vault")) is False


class TestGetChangedFiles:
    def test_parses_file_list(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.stdout = "notes/daily.md\nnotes/weekly.md\n"
        files = get_changed_files(Path("/vault"))
        assert files == ["notes/daily.md", "notes/weekly.md"]

    def test_empty_output(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.stdout = ""
        files = get_changed_files(Path("/vault"))
        assert files == []

    def test_filters_blank_lines(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.stdout = "file.md\n\n\n"
        files = get_changed_files(Path("/vault"))
        assert files == ["file.md"]


class TestGetChangesSummary:
    def test_returns_last_line(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value.stdout = (
            " notes/daily.md | 5 +++++\n 1 file changed, 5 insertions(+)"
        )
        summary = get_changes_summary(Path("/vault"))
        assert "1 file changed" in summary


class TestParseSnapshotId:
    def test_parses_standard_output(self) -> None:
        output = "snapshot ab12cd34 saved"
        assert _parse_snapshot_id(output) == "ab12cd34"

    def test_returns_none_for_no_match(self) -> None:
        assert _parse_snapshot_id("no snapshot here") is None

    def test_returns_none_for_empty(self) -> None:
        assert _parse_snapshot_id("") is None

    def test_multiline_output(self) -> None:
        output = """Files:         245 new,     0 changed,     0 unmodified
Added to the repository: 12.345 MiB (6.789 MiB stored)

processed 245 files, 23.456 MiB in 0:02
snapshot ef56gh78 saved"""
        assert _parse_snapshot_id(output) == "ef56gh78"


class TestWriteState:
    def test_writes_file(self, tmp_path: Path) -> None:
        path = tmp_path / "state"
        _write_state(path, "12345")
        assert path.read_text() == "12345"

    def test_handles_permission_error(self, tmp_path: Path) -> None:
        path = tmp_path / "nonexistent_dir" / "state"
        # Should not raise - logs warning instead
        _write_state(path, "12345")


class TestGenerateAiCommitMessage:
    def test_returns_none_when_llm_disabled(self, default_config: Config) -> None:
        assert not default_config.llm.enabled
        result = generate_ai_commit_message(default_config, ["file.md"], "1 file")
        assert result is None

    def test_calls_anthropic_when_key_set(self, config_with_llm: Config) -> None:
        mock_response = json.dumps(
            {"content": [{"text": "update daily notes"}]}
        ).encode()

        with patch("vault_backup.backup.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = lambda s: s
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value.read.return_value = mock_response
            mock_urlopen.return_value.status = 200

            result = generate_ai_commit_message(
                config_with_llm, ["notes/daily.md"], "1 file changed"
            )
            assert result == "update daily notes"

    def test_calls_openai_when_url_set(self, config_with_openai: Config) -> None:
        mock_response = json.dumps(
            {"choices": [{"message": {"content": "update weekly review"}}]}
        ).encode()

        with patch("vault_backup.backup.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = lambda s: s
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value.read.return_value = mock_response
            mock_urlopen.return_value.status = 200

            result = generate_ai_commit_message(
                config_with_openai, ["notes/weekly.md"], "1 file changed"
            )
            assert result == "update weekly review"

    def test_returns_none_on_api_error(self, config_with_llm: Config) -> None:
        with patch("vault_backup.backup.urllib.request.urlopen", side_effect=Exception("timeout")):
            result = generate_ai_commit_message(
                config_with_llm, ["file.md"], "1 file changed"
            )
            assert result is None

    def test_anthropic_empty_content_list(self, config_with_llm: Config) -> None:
        """Empty content list returns None gracefully."""
        mock_response = json.dumps({"content": []}).encode()

        with patch("vault_backup.backup.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = lambda s: s
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value.read.return_value = mock_response
            mock_urlopen.return_value.status = 200

            result = generate_ai_commit_message(
                config_with_llm, ["file.md"], "1 file changed"
            )
            assert result is None

    def test_openai_empty_choices_list(self, config_with_openai: Config) -> None:
        """Empty choices list returns None gracefully."""
        mock_response = json.dumps({"choices": []}).encode()

        with patch("vault_backup.backup.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = lambda s: s
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value.read.return_value = mock_response
            mock_urlopen.return_value.status = 200

            result = generate_ai_commit_message(
                config_with_openai, ["file.md"], "1 file changed"
            )
            assert result is None


class TestGitCommit:
    def test_no_changes_after_staging(self, mock_subprocess: MagicMock, default_config: Config) -> None:
        # git add succeeds, git diff --cached returns empty
        mock_subprocess.return_value.stdout = ""
        success, summary, commit_msg, changed_files = git_commit(
            default_config, Path(default_config.vault_path)
        )
        assert success is False
        assert summary == ""
        assert commit_msg == ""
        assert changed_files == []

    def test_creates_commit(self, mock_subprocess: MagicMock, default_config: Config) -> None:
        # Different responses for different commands
        def side_effect(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            if cmd[0:3] == ["git", "diff", "--cached"]:
                if "--name-only" in cmd:
                    result.stdout = "notes/daily.md\n"
                else:
                    result.stdout = " 1 file changed, 5 insertions(+)"
            elif cmd[0:2] == ["git", "commit"]:
                result.stdout = "[main abc1234] vault: auto-backup\n"
            else:
                result.stdout = ""
            return result

        mock_subprocess.side_effect = side_effect
        success, summary, commit_msg, changed_files = git_commit(
            default_config, Path(default_config.vault_path)
        )
        assert success is True
        assert "1 file changed" in summary
        assert "vault:" in commit_msg
        assert changed_files == ["notes/daily.md"]

    def test_dry_run_resets_staging(self, mock_subprocess: MagicMock) -> None:
        config = Config(vault_path="/vault", state_dir="/state", dry_run=True)

        def side_effect(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            if "--name-only" in cmd:
                result.stdout = "file.md\n"
            elif "--stat" in cmd:
                result.stdout = " 1 file changed"
            else:
                result.stdout = ""
            return result

        mock_subprocess.side_effect = side_effect
        success, _, commit_msg, changed_files = git_commit(config, Path("/vault"))
        assert success is True
        assert changed_files == ["file.md"]
        # Verify git reset was called
        reset_calls = [c for c in mock_subprocess.call_args_list if "reset" in str(c)]
        assert len(reset_calls) >= 1


class TestResticBackup:
    def test_skips_when_not_initialized(self, mock_subprocess: MagicMock, default_config: Config) -> None:
        mock_subprocess.return_value.returncode = 1
        result = restic_backup(default_config, Path(default_config.vault_path))
        assert result is False

    def test_succeeds_when_initialized(self, mock_subprocess: MagicMock, default_config: Config) -> None:
        mock_subprocess.return_value.returncode = 0
        mock_subprocess.return_value.stdout = "snapshot ab12cd34 saved"
        result = restic_backup(default_config, Path(default_config.vault_path))
        assert result is True

    def test_dry_run_skips(self, mock_subprocess: MagicMock) -> None:
        config = Config(vault_path="/vault", state_dir="/state", dry_run=True)
        mock_subprocess.return_value.returncode = 0
        result = restic_backup(config, Path("/vault"))
        assert result is True
        # Only the snapshot check should be called, not the actual backup
        assert mock_subprocess.call_count == 1


class TestResticPrune:
    def test_prune_succeeds(self, mock_subprocess: MagicMock, default_config: Config) -> None:
        mock_subprocess.return_value.returncode = 0
        result = restic_prune(default_config)
        assert result is True

    def test_prune_failure_returns_false(self, mock_subprocess: MagicMock, default_config: Config) -> None:
        mock_subprocess.return_value.returncode = 1
        mock_subprocess.return_value.stderr = "prune error"
        result = restic_prune(default_config)
        assert result is False

    def test_uses_retention_policy(self, mock_subprocess: MagicMock, default_config: Config) -> None:
        mock_subprocess.return_value.returncode = 0
        restic_prune(default_config)
        cmd = mock_subprocess.call_args[0][0]
        assert f"--keep-daily={default_config.retention.daily}" in cmd
        assert f"--keep-weekly={default_config.retention.weekly}" in cmd
        assert f"--keep-monthly={default_config.retention.monthly}" in cmd


class TestRunBackup:
    def test_no_changes_returns_success(
        self, mock_subprocess: MagicMock, default_config: Config, tmp_state_dir: Path
    ) -> None:
        mock_subprocess.return_value.stdout = ""  # no changes
        result = run_backup(default_config, tmp_state_dir)
        assert result.success is True
        assert result.commit_created is False
        assert result.backup_created is False

    def test_writes_state_files_on_success(
        self, mock_subprocess: MagicMock, default_config: Config, tmp_state_dir: Path
    ) -> None:
        call_count = 0

        def side_effect(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            if cmd[:2] == ["git", "status"]:
                result.stdout = " M file.md\n"
            elif "--name-only" in cmd:
                result.stdout = "file.md\n"
            elif "--stat" in cmd:
                result.stdout = " 1 file changed"
            elif cmd[:2] == ["git", "commit"]:
                result.stdout = "[main abc] vault: auto-backup\n"
            elif cmd[:2] == ["restic", "backup"]:
                result.stdout = "snapshot ab12cd34 saved"
            else:
                result.stdout = ""
            return result

        mock_subprocess.side_effect = side_effect
        result = run_backup(default_config, tmp_state_dir)
        assert result.success is True
        assert result.commit_created is True
        assert result.backup_created is True
        assert (tmp_state_dir / "last_commit").exists()
        assert (tmp_state_dir / "last_backup").exists()
