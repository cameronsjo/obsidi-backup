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

    # Paths to ignore (Obsidian internals, git, etc.)
    IGNORE_PATTERNS = {
        ".git",
        ".obsidian/workspace.json",
        ".obsidian/workspace-mobile.json",
        ".trash",
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

    def _should_ignore(self, path: str) -> bool:
        """Check if path should be ignored."""
        for pattern in self.IGNORE_PATTERNS:
            if pattern in path:
                return True
        return False

    def on_any_event(self, event: FileSystemEvent) -> None:
        """Handle any file system event."""
        if event.is_directory:
            return

        if self._should_ignore(event.src_path):
            return

        log.debug("File event: %s %s", event.event_type, event.src_path)
        self._schedule_backup()

    def _schedule_backup(self) -> None:
        """Schedule a backup after debounce period."""
        with self._lock:
            self._last_event_time = time.time()
            self._pending = True

            # Update state file
            (self.state_dir / "last_change").write_text(str(int(self._last_event_time)))
            (self.state_dir / "pending_changes").write_text("true")

            # Cancel existing timer
            if self._timer:
                self._timer.cancel()

            # Start new timer
            self._timer = threading.Timer(self.debounce_seconds, self._trigger_backup)
            self._timer.start()

            log.info(
                "Change detected, backup scheduled in %d seconds",
                self.debounce_seconds,
            )

    def _trigger_backup(self) -> None:
        """Trigger the backup callback."""
        with self._lock:
            if not self._pending:
                return

            self._pending = False
            (self.state_dir / "pending_changes").write_text("false")

        log.info("Debounce period elapsed, triggering backup")
        try:
            self.on_changes()
        except Exception:
            log.exception("Backup callback failed")

    def cancel(self) -> None:
        """Cancel any pending backup."""
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None


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
