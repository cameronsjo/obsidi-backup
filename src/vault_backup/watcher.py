"""File watcher with debounce for Obsidian vault changes."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

if TYPE_CHECKING:
    from vault_backup.config import Config

log = logging.getLogger(__name__)


class DebouncedHandler(FileSystemEventHandler):
    """File system event handler with debounce logic.

    Collects file change events and triggers a callback after a period
    of inactivity (debounce period).
    """

    # Path segments to ignore (matched as complete path components)
    IGNORE_SEGMENTS = {".git", ".trash"}
    # Exact relative paths to ignore
    IGNORE_PATHS = {
        ".obsidian/workspace.json",
        ".obsidian/workspace-mobile.json",
    }

    def __init__(
        self,
        debounce_seconds: int,
        on_changes: Callable[[], None],
        state_dir: Path,
    ) -> None:
        self.debounce_seconds = debounce_seconds
        self.on_changes = on_changes
        self.state_dir = state_dir

        self._last_event_time: float = 0
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._pending = False
        self._event_count: int = 0

        log.debug(
            "DebouncedHandler initialized",
            extra={"debounce_seconds": debounce_seconds, "state_dir": str(state_dir)},
        )

    def _should_ignore(self, path: str) -> bool:
        """Check if path should be ignored using path-segment matching."""
        parts = Path(path).parts
        for segment in self.IGNORE_SEGMENTS:
            if segment in parts:
                log.debug("Ignoring path (segment match)", extra={"path": path, "segment": segment})
                return True
        for ignore_path in self.IGNORE_PATHS:
            if path.endswith(ignore_path):
                log.debug("Ignoring path (exact match)", extra={"path": path, "pattern": ignore_path})
                return True
        return False

    def on_any_event(self, event: FileSystemEvent) -> None:
        """Handle any file system event."""
        if event.is_directory:
            return

        # Record every non-directory event BEFORE the ignore filter.
        # This gives us a signal that the upstream writer (obsidi-headless) is
        # alive even when it only writes ephemeral metadata that we filter out
        # for commit purposes (workspace.json, .git internals, etc.).
        (self.state_dir / "last_watcher_event").write_text(str(int(time.time())))

        if self._should_ignore(event.src_path):
            return

        log.debug("File event: %s %s", event.event_type, event.src_path)
        self._schedule_backup()

    def _schedule_backup(self) -> None:
        """Schedule a backup after debounce period."""
        with self._lock:
            self._last_event_time = time.time()
            was_pending = self._pending
            self._pending = True
            self._event_count += 1

            # Only write state files and log on first event in a batch
            if not was_pending:
                (self.state_dir / "last_change").write_text(str(int(self._last_event_time)))
                (self.state_dir / "pending_changes").write_text("true")
                log.info(
                    "Change detected, backup scheduled in %d seconds",
                    self.debounce_seconds,
                )

            # Cancel existing timer
            if self._timer:
                self._timer.cancel()

            # Start new timer
            self._timer = threading.Timer(self.debounce_seconds, self._trigger_backup)
            self._timer.start()

    def _trigger_backup(self) -> None:
        """Trigger the backup callback."""
        with self._lock:
            if not self._pending:
                return

            event_count = self._event_count
            self._pending = False
            self._event_count = 0
            (self.state_dir / "pending_changes").write_text("false")

        log.info("Debounce period elapsed, triggering backup (%d events)", event_count)
        try:
            self.on_changes()
            log.info("Backup callback completed")
        except Exception:
            log.exception("Backup callback failed")

    def cancel(self) -> None:
        """Cancel any pending backup."""
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
                log.debug("Pending backup timer cancelled")


class VaultWatcher:
    """Watches an Obsidian vault for changes and triggers backups."""

    def __init__(
        self,
        config: Config,
        on_changes: Callable[[], None],
    ) -> None:
        self.config = config
        self.vault_path = Path(config.vault_path)
        self.state_dir = Path(config.state_dir)

        self.handler = DebouncedHandler(
            debounce_seconds=config.debounce_seconds,
            on_changes=on_changes,
            state_dir=self.state_dir,
        )
        self.observer = Observer()

    def start(self) -> None:
        """Start watching the vault."""
        self.observer.schedule(self.handler, str(self.vault_path), recursive=True)
        self.observer.start()
        log.info(
            "Watching vault at %s (debounce: %ds)",
            self.vault_path,
            self.config.debounce_seconds,
        )

    def stop(self) -> None:
        """Stop watching the vault."""
        self.handler.cancel()
        self.observer.stop()
        self.observer.join(timeout=5)
        log.info("Vault watcher stopped")

    def wait(self) -> None:
        """Wait for the watcher to finish (blocks until stopped)."""
        self.observer.join()
