"""Tests for vault_backup.watcher."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vault_backup.config import Config
from vault_backup.watcher import DebouncedHandler, VaultWatcher


class TestDebouncedHandlerIgnore:
    def test_ignores_git_directory(self) -> None:
        handler = DebouncedHandler(
            debounce_seconds=1,
            on_changes=MagicMock(),
            state_dir=Path("/tmp"),
        )
        assert handler._should_ignore("/vault/.git/objects/abc") is True

    def test_ignores_obsidian_workspace(self) -> None:
        handler = DebouncedHandler(
            debounce_seconds=1,
            on_changes=MagicMock(),
            state_dir=Path("/tmp"),
        )
        assert handler._should_ignore("/vault/.obsidian/workspace.json") is True

    def test_ignores_trash(self) -> None:
        handler = DebouncedHandler(
            debounce_seconds=1,
            on_changes=MagicMock(),
            state_dir=Path("/tmp"),
        )
        assert handler._should_ignore("/vault/.trash/old-note.md") is True

    def test_allows_normal_files(self) -> None:
        handler = DebouncedHandler(
            debounce_seconds=1,
            on_changes=MagicMock(),
            state_dir=Path("/tmp"),
        )
        assert handler._should_ignore("/vault/notes/daily.md") is False

    def test_no_false_positive_on_gitignore(self) -> None:
        """Path-segment matching avoids false positives on .gitignore etc."""
        handler = DebouncedHandler(
            debounce_seconds=1,
            on_changes=MagicMock(),
            state_dir=Path("/tmp"),
        )
        assert handler._should_ignore("/vault/.gitignore") is False
        assert handler._should_ignore("/vault/my-git-notes.md") is False

    def test_still_ignores_git_contents(self) -> None:
        handler = DebouncedHandler(
            debounce_seconds=1,
            on_changes=MagicMock(),
            state_dir=Path("/tmp"),
        )
        assert handler._should_ignore("/vault/.git/HEAD") is True
        assert handler._should_ignore("/vault/.git/objects/abc123") is True


class TestDebouncedHandlerEvents:
    def test_directory_events_ignored(self, tmp_state_dir: Path) -> None:
        callback = MagicMock()
        handler = DebouncedHandler(
            debounce_seconds=1,
            on_changes=callback,
            state_dir=tmp_state_dir,
        )
        event = MagicMock()
        event.is_directory = True
        handler.on_any_event(event)
        # No timer should be started for directory events
        assert handler._timer is None

    def test_schedules_backup_on_file_event(self, tmp_state_dir: Path) -> None:
        callback = MagicMock()
        handler = DebouncedHandler(
            debounce_seconds=60,
            on_changes=callback,
            state_dir=tmp_state_dir,
        )
        event = MagicMock()
        event.is_directory = False
        event.src_path = "/vault/notes/daily.md"
        event.event_type = "modified"
        handler.on_any_event(event)
        assert handler._timer is not None
        assert handler._pending is True
        # State files written
        assert (tmp_state_dir / "last_change").exists()
        assert (tmp_state_dir / "pending_changes").read_text() == "true"
        handler.cancel()

    def test_triggers_callback_after_debounce(self, tmp_state_dir: Path) -> None:
        callback = MagicMock()
        handler = DebouncedHandler(
            debounce_seconds=0,  # Immediate trigger
            on_changes=callback,
            state_dir=tmp_state_dir,
        )
        event = MagicMock()
        event.is_directory = False
        event.src_path = "/vault/notes/test.md"
        event.event_type = "created"
        handler.on_any_event(event)
        # Wait for timer to fire
        time.sleep(0.2)
        callback.assert_called_once()
        assert handler._pending is False
        assert (tmp_state_dir / "pending_changes").read_text() == "false"

    def test_debounce_resets_timer(self, tmp_state_dir: Path) -> None:
        callback = MagicMock()
        handler = DebouncedHandler(
            debounce_seconds=1,
            on_changes=callback,
            state_dir=tmp_state_dir,
        )
        event = MagicMock()
        event.is_directory = False
        event.src_path = "/vault/notes/test.md"
        event.event_type = "modified"

        # Fire two events quickly - should only trigger once
        handler.on_any_event(event)
        first_timer = handler._timer
        handler.on_any_event(event)
        second_timer = handler._timer

        assert first_timer is not second_timer
        handler.cancel()

    def test_cancel_stops_timer(self, tmp_state_dir: Path) -> None:
        callback = MagicMock()
        handler = DebouncedHandler(
            debounce_seconds=60,
            on_changes=callback,
            state_dir=tmp_state_dir,
        )
        event = MagicMock()
        event.is_directory = False
        event.src_path = "/vault/notes/test.md"
        event.event_type = "modified"
        handler.on_any_event(event)
        assert handler._timer is not None
        handler.cancel()
        assert handler._timer is None

    def test_callback_exception_logged(self, tmp_state_dir: Path) -> None:
        callback = MagicMock(side_effect=RuntimeError("boom"))
        handler = DebouncedHandler(
            debounce_seconds=0,
            on_changes=callback,
            state_dir=tmp_state_dir,
        )
        event = MagicMock()
        event.is_directory = False
        event.src_path = "/vault/notes/test.md"
        event.event_type = "modified"
        handler.on_any_event(event)
        time.sleep(0.2)
        callback.assert_called_once()  # Should not propagate exception


class TestWatcherEventSignalSplit:
    """Tests for the watcher-event vs commit-worthy split introduced in the pipeline-health signal."""

    def test_ignored_path_updates_last_watcher_event(self, tmp_state_dir: Path) -> None:
        """Events on ignored paths (e.g. workspace.json) still record last_watcher_event."""
        handler = DebouncedHandler(
            debounce_seconds=60,
            on_changes=MagicMock(),
            state_dir=tmp_state_dir,
        )
        event = MagicMock()
        event.is_directory = False
        event.src_path = "/vault/.obsidian/workspace.json"
        event.event_type = "modified"
        handler.on_any_event(event)

        assert (tmp_state_dir / "last_watcher_event").exists()
        assert (tmp_state_dir / "last_watcher_event").read_text().strip() != "0"

    def test_ignored_path_does_not_update_last_change(self, tmp_state_dir: Path) -> None:
        """Events on ignored paths do NOT write last_change (commit-worthy signal)."""
        handler = DebouncedHandler(
            debounce_seconds=60,
            on_changes=MagicMock(),
            state_dir=tmp_state_dir,
        )
        # Initialize last_change to a sentinel so we can detect if it was touched
        (tmp_state_dir / "last_change").write_text("0")

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/vault/.obsidian/workspace.json"
        event.event_type = "modified"
        handler.on_any_event(event)

        # last_change should remain 0; no backup should be pending
        assert (tmp_state_dir / "last_change").read_text().strip() == "0"
        assert handler._pending is False

    def test_commit_worthy_event_updates_both_signals(self, tmp_state_dir: Path) -> None:
        """Commit-worthy events update both last_watcher_event AND last_change."""
        handler = DebouncedHandler(
            debounce_seconds=60,
            on_changes=MagicMock(),
            state_dir=tmp_state_dir,
        )
        event = MagicMock()
        event.is_directory = False
        event.src_path = "/vault/notes/daily.md"
        event.event_type = "modified"
        handler.on_any_event(event)

        assert (tmp_state_dir / "last_watcher_event").exists()
        assert (tmp_state_dir / "last_change").exists()
        assert handler._pending is True
        handler.cancel()

    def test_directory_event_updates_neither_signal(self, tmp_state_dir: Path) -> None:
        """Directory events update neither last_watcher_event nor last_change."""
        handler = DebouncedHandler(
            debounce_seconds=60,
            on_changes=MagicMock(),
            state_dir=tmp_state_dir,
        )
        (tmp_state_dir / "last_watcher_event").write_text("0")
        (tmp_state_dir / "last_change").write_text("0")

        event = MagicMock()
        event.is_directory = True
        event.src_path = "/vault/notes/"
        event.event_type = "created"
        handler.on_any_event(event)

        assert (tmp_state_dir / "last_watcher_event").read_text().strip() == "0"
        assert (tmp_state_dir / "last_change").read_text().strip() == "0"
        assert handler._pending is False

    def test_git_segment_ignored_but_watcher_event_recorded(self, tmp_state_dir: Path) -> None:
        """Events under .git are filtered for commits but still recorded as watcher activity."""
        handler = DebouncedHandler(
            debounce_seconds=60,
            on_changes=MagicMock(),
            state_dir=tmp_state_dir,
        )
        (tmp_state_dir / "last_change").write_text("0")

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/vault/.git/objects/abc123"
        event.event_type = "created"
        handler.on_any_event(event)

        # Watcher event recorded
        assert (tmp_state_dir / "last_watcher_event").exists()
        assert (tmp_state_dir / "last_watcher_event").read_text().strip() != "0"
        # But last_change NOT updated (commit filter still applies)
        assert (tmp_state_dir / "last_change").read_text().strip() == "0"
        assert handler._pending is False


class TestVaultWatcher:
    def test_creates_handler_and_observer(self, default_config: Config) -> None:
        callback = MagicMock()
        watcher = VaultWatcher(config=default_config, on_changes=callback)
        assert watcher.handler is not None
        assert watcher.observer is not None
        assert watcher.handler.debounce_seconds == default_config.debounce_seconds

    def test_start_and_stop(self, default_config: Config, tmp_vault: Path) -> None:
        callback = MagicMock()
        watcher = VaultWatcher(config=default_config, on_changes=callback)
        watcher.start()
        assert watcher.observer.is_alive()
        watcher.stop()
        assert not watcher.observer.is_alive()
