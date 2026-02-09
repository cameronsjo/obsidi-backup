"""Tests for vault_backup.ui."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch

import pytest

from vault_backup.config import Config
from vault_backup.health import HealthState
from vault_backup.restore import GitCommit, GitFileChange, ResticEntry, ResticSnapshot
from vault_backup.ui import (
    RestoreHandler,
    _format_size,
    _format_time,
    _render_commit_files,
    _render_error,
    _render_files,
    _render_log,
    _render_preview,
    _render_restore_result,
    _restic_ls_cache,
    _render_snapshots,
)


@pytest.fixture()
def ui_server(default_config: Config):
    """Start a real HTTP server with RestoreHandler for testing."""
    import vault_backup.health as health_mod

    health_mod._health_state = HealthState(config=default_config)
    server = HTTPServer(("127.0.0.1", 0), RestoreHandler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    health_mod._health_state = None
    _restic_ls_cache.clear()


def _get(url: str) -> tuple[int, str, dict[str, str]]:
    """GET request helper. Returns (status, body, headers)."""
    try:
        resp = urllib.request.urlopen(url)
        return resp.status, resp.read().decode(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


def _post(url: str, data: str) -> tuple[int, str]:
    """POST request helper. Returns (status, body)."""
    try:
        req = urllib.request.Request(
            url, data=data.encode(), method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp = urllib.request.urlopen(req)
        return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


# --- Helpers ---


class TestFormatTime:
    def test_iso_with_z(self) -> None:
        assert _format_time("2025-01-15T10:30:00Z") == "2025-01-15 10:30"

    def test_iso_with_offset(self) -> None:
        assert _format_time("2025-01-15T10:30:00+00:00") == "2025-01-15 10:30"

    def test_empty(self) -> None:
        assert _format_time("") == ""

    def test_invalid(self) -> None:
        assert _format_time("not-a-date") == "not-a-date"


class TestFormatSize:
    def test_zero(self) -> None:
        assert _format_size(0) == "-"

    def test_bytes(self) -> None:
        assert _format_size(512) == "512 B"

    def test_kilobytes(self) -> None:
        result = _format_size(2048)
        assert "KB" in result

    def test_megabytes(self) -> None:
        result = _format_size(5 * 1024 * 1024)
        assert "MB" in result


# --- Render functions ---


class TestRenderSnapshots:
    def test_empty(self) -> None:
        assert "No snapshots found" in _render_snapshots([])

    def test_table(self) -> None:
        snaps = [
            ResticSnapshot(
                id="a" * 64, short_id="abcdef12",
                time="2025-01-15T10:30:00Z", paths=["/vault"], tags=["obsidian"],
            ),
        ]
        result = _render_snapshots(snaps)
        assert "abcdef12" in result
        assert "/vault" in result
        assert "obsidian" in result
        assert "hx-get" in result


class TestRenderFiles:
    def test_empty(self) -> None:
        result = _render_files([], "abcdef12")
        assert "No files found" in result
        assert "abcdef12" in result

    def test_file_and_dir(self) -> None:
        entries = [
            ResticEntry(path="/vault/note.md", type="file", size=2048, mtime="2025-01-15T10:30:00Z"),
            ResticEntry(path="/vault/dir", type="dir", size=0, mtime=""),
        ]
        result = _render_files(entries, "abcdef12")
        assert "/vault/note.md" in result
        assert "clickable" in result
        assert "/vault/dir" in result


class TestRenderLog:
    def test_empty(self) -> None:
        result = _render_log([])
        assert "No commits found" in result
        assert 'name="file"' in result  # filter input still present

    def test_without_file(self) -> None:
        commits = [
            GitCommit(hash="a" * 40, short_hash="abc123d", date="2025-01-15T10:30:00+00:00", message="update"),
        ]
        result = _render_log(commits)
        assert "abc123d" in result
        assert "update" in result
        assert "clickable" in result  # all commits are clickable
        assert "/ui/commit?hash=abc123d" in result

    def test_with_file_filter(self) -> None:
        commits = [
            GitCommit(hash="a" * 40, short_hash="abc123d", date="2025-01-15T10:30:00+00:00", message="edit"),
        ]
        result = _render_log(commits, file_path="notes/daily.md")
        assert "clickable" in result
        assert "notes/daily.md" in result


class TestRenderPreview:
    def test_content_escaped(self) -> None:
        result = _render_preview("<script>alert(1)</script>", "abc123d", "note.md")
        assert "&lt;script&gt;" in result
        assert "<script>alert" not in result

    def test_has_download_link(self) -> None:
        result = _render_preview("content", "abc123d", "notes/daily.md")
        assert "Download" in result
        assert "/ui/download" in result

    def test_has_restore_button(self) -> None:
        result = _render_preview("content", "abc123d", "notes/daily.md")
        assert "Restore in place" in result
        assert "hx-post" in result


class TestRenderError:
    def test_escapes_html(self) -> None:
        result = _render_error("<b>bad</b>")
        assert "&lt;b&gt;" in result
        assert "<b>bad" not in result


class TestRenderRestoreResult:
    def test_shows_path(self) -> None:
        result = _render_restore_result(Path("/vault/note.md"), "git commit")
        assert "/vault/note.md" in result
        assert "git commit" in result


# --- UI page ---


class TestUIPage:
    def test_returns_html(self, ui_server: str) -> None:
        status, body, headers = _get(f"{ui_server}/ui")
        assert status == 200
        assert "text/html" in headers.get("Content-Type", "")

    def test_contains_htmx(self, ui_server: str) -> None:
        _, body, _ = _get(f"{ui_server}/ui")
        assert "htmx" in body

    def test_has_tabs(self, ui_server: str) -> None:
        _, body, _ = _get(f"{ui_server}/ui")
        assert "Git History" in body
        assert "Snapshots" in body


# --- Health fallthrough ---


class TestHealthFallthrough:
    def test_health_still_works(self, ui_server: str) -> None:
        status, body, _ = _get(f"{ui_server}/health")
        assert status == 200
        data = json.loads(body)
        assert data["status"] == "healthy"

    def test_ready_still_works(self, ui_server: str) -> None:
        status, body, _ = _get(f"{ui_server}/ready")
        assert status == 200
        data = json.loads(body)
        assert data["ready"] is True

    def test_404_for_unknown(self, ui_server: str) -> None:
        status, _, _ = _get(f"{ui_server}/nonexistent")
        assert status == 404


# --- Snapshots endpoint ---


class TestSnapshotsEndpoint:
    def test_returns_table(self, ui_server: str) -> None:
        snaps = [
            ResticSnapshot(
                id="a" * 64, short_id="abcdef12",
                time="2025-01-15T10:30:00Z", paths=["/vault"], tags=["obsidian"],
            ),
        ]
        with patch("vault_backup.ui.restic_snapshots", return_value=snaps):
            status, body, _ = _get(f"{ui_server}/ui/snapshots")
        assert status == 200
        assert "abcdef12" in body

    def test_empty(self, ui_server: str) -> None:
        with patch("vault_backup.ui.restic_snapshots", return_value=[]):
            _, body, _ = _get(f"{ui_server}/ui/snapshots")
        assert "No snapshots found" in body


# --- Files endpoint ---


class TestFilesEndpoint:
    def test_returns_directory_listing(self, ui_server: str) -> None:
        entries = [
            ResticEntry(path="/vault", type="dir", size=0, mtime=""),
            ResticEntry(path="/vault/note.md", type="file", size=1024, mtime="2025-01-15T00:00:00Z"),
        ]
        with patch("vault_backup.ui.restic_ls", return_value=entries):
            status, body, _ = _get(f"{ui_server}/ui/files?snapshot=abcdef12")
        assert status == 200
        assert "vault/" in body  # shows dir at root level
        assert "clickable" in body

    def test_drills_into_directory(self, ui_server: str) -> None:
        entries = [
            ResticEntry(path="/vault", type="dir", size=0, mtime=""),
            ResticEntry(path="/vault/note.md", type="file", size=1024, mtime="2025-01-15T00:00:00Z"),
        ]
        with patch("vault_backup.ui.restic_ls", return_value=entries):
            status, body, _ = _get(f"{ui_server}/ui/files?snapshot=abcdef12&path=/vault")
        assert status == 200
        assert "note.md" in body

    def test_missing_param(self, ui_server: str) -> None:
        status, body, _ = _get(f"{ui_server}/ui/files")
        assert status == 400
        assert "Missing" in body

    def test_bad_snapshot(self, ui_server: str) -> None:
        with patch("vault_backup.ui.restic_ls", side_effect=ValueError("not found")):
            status, body, _ = _get(f"{ui_server}/ui/files?snapshot=bad")
        assert status == 404
        assert "not found" in body

    def test_empty(self, ui_server: str) -> None:
        with patch("vault_backup.ui.restic_ls", return_value=[]):
            _, body, _ = _get(f"{ui_server}/ui/files?snapshot=abcdef12")
        assert "No files found" in body

    def test_caches_restic_ls(self, ui_server: str) -> None:
        entries = [
            ResticEntry(path="/vault/note.md", type="file", size=100, mtime=""),
        ]
        with patch("vault_backup.ui.restic_ls", return_value=entries) as mock_ls:
            _get(f"{ui_server}/ui/files?snapshot=cached01")
            _get(f"{ui_server}/ui/files?snapshot=cached01&path=/vault")
            assert mock_ls.call_count == 1  # only called once due to cache


# --- Log endpoint ---


class TestLogEndpoint:
    def test_returns_commits(self, ui_server: str) -> None:
        commits = [
            GitCommit(hash="a" * 40, short_hash="abc123d", date="2025-01-15T10:30:00+00:00", message="update notes"),
        ]
        with patch("vault_backup.ui.git_log", return_value=commits):
            status, body, _ = _get(f"{ui_server}/ui/log")
        assert status == 200
        assert "abc123d" in body
        assert "update notes" in body

    def test_with_file_filter(self, ui_server: str) -> None:
        commits = [
            GitCommit(hash="a" * 40, short_hash="abc123d", date="2025-01-15T10:30:00+00:00", message="edit daily"),
        ]
        with patch("vault_backup.ui.git_file_history", return_value=commits) as mock_hist:
            _, body, _ = _get(f"{ui_server}/ui/log?file=notes/daily.md")
            mock_hist.assert_called_once()
        assert "clickable" in body

    def test_empty(self, ui_server: str) -> None:
        with patch("vault_backup.ui.git_log", return_value=[]):
            _, body, _ = _get(f"{ui_server}/ui/log")
        assert "No commits found" in body


# --- Preview endpoint ---


class TestPreviewEndpoint:
    def test_git_preview(self, ui_server: str) -> None:
        with patch("vault_backup.ui.git_show_file", return_value="# Hello\n"):
            status, body, _ = _get(f"{ui_server}/ui/preview?source={'a' * 40}&path=note.md")
        assert status == 200
        assert "# Hello" in body
        assert "Download" in body

    def test_restic_preview(self, ui_server: str) -> None:
        with patch("vault_backup.ui.restic_show_file", return_value="restic content"):
            status, body, _ = _get(f"{ui_server}/ui/preview?source=latest&path=/vault/note.md")
        assert status == 200
        assert "restic content" in body

    def test_ambiguous_tries_git_first(self, ui_server: str) -> None:
        with patch("vault_backup.ui.git_show_file", return_value="from git"):
            _, body, _ = _get(f"{ui_server}/ui/preview?source=abcdef12&path=note.md")
        assert "from git" in body

    def test_missing_params(self, ui_server: str) -> None:
        status, body, _ = _get(f"{ui_server}/ui/preview?source=abc")
        assert status == 400

    def test_not_found(self, ui_server: str) -> None:
        with patch("vault_backup.ui.git_show_file", side_effect=FileNotFoundError):
            status, body, _ = _get(f"{ui_server}/ui/preview?source={'a' * 40}&path=gone.md")
        assert status == 404


# --- Download endpoint ---


class TestDownloadEndpoint:
    def test_has_content_disposition(self, ui_server: str) -> None:
        with patch("vault_backup.ui.git_show_file", return_value="file content"):
            status, body, headers = _get(
                f"{ui_server}/ui/download?source={'a' * 40}&path=notes/daily.md"
            )
        assert status == 200
        assert 'filename="daily.md"' in headers.get("Content-Disposition", "")
        assert body == "file content"

    def test_not_found(self, ui_server: str) -> None:
        with patch("vault_backup.ui.git_show_file", side_effect=FileNotFoundError):
            status, _, _ = _get(f"{ui_server}/ui/download?source={'a' * 40}&path=gone.md")
        assert status == 404


# --- Restore endpoint ---


class TestRestoreEndpoint:
    def test_git_restore(self, ui_server: str, tmp_vault: Path) -> None:
        target = tmp_vault / "note.md"
        with patch("vault_backup.ui.git_restore_file", return_value=target):
            status, body = _post(
                f"{ui_server}/ui/restore",
                f"source={'a' * 40}&path=note.md",
            )
        assert status == 200
        assert "git commit" in body

    def test_restic_restore(self, ui_server: str, tmp_vault: Path) -> None:
        restic_path = str(tmp_vault / "note.md")
        with patch("vault_backup.ui.restic_restore_file", return_value=Path(restic_path)):
            status, body = _post(
                f"{ui_server}/ui/restore",
                f"source=latest&path={restic_path}",
            )
        assert status == 200
        assert "restic snapshot" in body

    def test_missing_params(self, ui_server: str) -> None:
        status, body = _post(f"{ui_server}/ui/restore", "source=abc")
        assert status == 400

    def test_failure(self, ui_server: str) -> None:
        with patch("vault_backup.ui.git_restore_file", side_effect=FileNotFoundError("nope")):
            status, body = _post(
                f"{ui_server}/ui/restore",
                f"source={'a' * 40}&path=gone.md",
            )
        assert status == 404
        assert "nope" in body


# --- Render commit files ---


class TestRenderCommitFiles:
    def test_shows_changed_files(self) -> None:
        commit = GitCommit(hash="a" * 40, short_hash="abc123d", date="2025-01-15T10:30:00+00:00", message="update notes")
        changes = [
            GitFileChange(path="notes/daily.md", status="M"),
            GitFileChange(path="notes/new.md", status="A"),
        ]
        result = _render_commit_files(commit, changes)
        assert "abc123d" in result
        assert "notes/daily.md" in result
        assert "notes/new.md" in result
        assert "modified" in result
        assert "added" in result

    def test_deleted_files_not_clickable(self) -> None:
        commit = GitCommit(hash="a" * 40, short_hash="abc123d", date="2025-01-15T10:30:00+00:00", message="cleanup")
        changes = [GitFileChange(path="old/removed.md", status="D")]
        result = _render_commit_files(commit, changes)
        assert "deleted" in result
        assert "old/removed.md" in result
        assert "clickable" not in result

    def test_breadcrumb_links_to_log(self) -> None:
        commit = GitCommit(hash="a" * 40, short_hash="abc123d", date="2025-01-15T10:30:00+00:00", message="test")
        result = _render_commit_files(commit, [])
        assert "/ui/log" in result
        assert "Git History" in result

    def test_empty_changes(self) -> None:
        commit = GitCommit(hash="a" * 40, short_hash="abc123d", date="2025-01-15T10:30:00+00:00", message="empty")
        result = _render_commit_files(commit, [])
        assert "No files changed" in result


# --- Render files (directory browsing) ---


class TestRenderFilesDirectoryBrowsing:
    def test_shows_basename_not_full_path(self) -> None:
        entries = [
            ResticEntry(path="/vault/notes/daily.md", type="file", size=100, mtime=""),
        ]
        result = _render_files(entries, "abcdef12", "/vault/notes")
        assert "daily.md" in result
        assert "/vault/notes/daily.md" not in result.split("breadcrumb")[1].split("table")[0]

    def test_dirs_are_clickable(self) -> None:
        entries = [
            ResticEntry(path="/vault/notes", type="dir", size=0, mtime=""),
        ]
        result = _render_files(entries, "abcdef12", "/vault")
        assert "clickable" in result
        assert "/ui/files?snapshot=abcdef12&path=/vault/notes" in result

    def test_breadcrumb_has_segments(self) -> None:
        result = _render_files([], "abcdef12", "/vault/notes/archive")
        assert "Snapshots" in result
        assert "abcdef12" in result
        assert "vault" in result
        assert "notes" in result
        assert "archive" in result

    def test_hides_dotfiles_by_default(self) -> None:
        entries = [
            ResticEntry(path="/vault/.obsidian", type="dir", size=0, mtime=""),
            ResticEntry(path="/vault/notes", type="dir", size=0, mtime=""),
        ]
        result = _render_files(entries, "abcdef12", "/vault")
        assert "notes" in result
        assert ".obsidian" not in result

    def test_shows_dotfiles_when_toggled(self) -> None:
        entries = [
            ResticEntry(path="/vault/.obsidian", type="dir", size=0, mtime=""),
            ResticEntry(path="/vault/notes", type="dir", size=0, mtime=""),
        ]
        result = _render_files(entries, "abcdef12", "/vault", show_hidden=True)
        assert ".obsidian" in result
        assert "notes" in result


# --- Commit endpoint ---


class TestCommitEndpoint:
    def test_returns_changed_files(self, ui_server: str) -> None:
        commit = GitCommit(hash="a" * 40, short_hash="abc123d", date="2025-01-15T10:30:00+00:00", message="update")
        changes = [GitFileChange(path="notes/daily.md", status="M")]
        with (
            patch("vault_backup.ui.git_log_single", return_value=[commit]),
            patch("vault_backup.ui.git_diff_tree", return_value=changes),
        ):
            status, body, _ = _get(f"{ui_server}/ui/commit?hash=abc123d")
        assert status == 200
        assert "notes/daily.md" in body
        assert "modified" in body

    def test_missing_hash(self, ui_server: str) -> None:
        status, body, _ = _get(f"{ui_server}/ui/commit")
        assert status == 400
        assert "Missing" in body

    def test_commit_not_found(self, ui_server: str) -> None:
        with patch("vault_backup.ui.git_log_single", return_value=[]):
            status, body, _ = _get(f"{ui_server}/ui/commit?hash=badbeef")
        assert status == 404
        assert "not found" in body
