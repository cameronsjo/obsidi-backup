"""Extended watcher tests — hooks integration.

Tests that are specific to the hook-dispatch layer added in
feat/vault-hooks-untitled-renamer.  The original test_watcher.py tests
remain unchanged; these live here to keep the diff reviewable.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from vault_backup.config import Config, RenamerConfig
from vault_backup.hooks import HookEvent, HookEventType, HookRegistry
from vault_backup.watcher import DebouncedHandler, VaultWatcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_handler(
    state_dir: Path,
    registry: HookRegistry | None = None,
    config: Config | None = None,
    on_changes: MagicMock | None = None,
) -> DebouncedHandler:
    return DebouncedHandler(
        debounce_seconds=0,
        on_changes=on_changes or MagicMock(),
        state_dir=state_dir,
        hook_registry=registry,
        config=config,
    )


def _file_event(src: str, event_type: str = "modified") -> MagicMock:
    evt = MagicMock()
    evt.is_directory = False
    evt.src_path = src
    evt.event_type = event_type
    return evt


def _move_event(src: str, dest: str) -> MagicMock:
    evt = MagicMock()
    evt.is_directory = False
    evt.src_path = src
    evt.dest_path = dest
    evt.event_type = "moved"
    return evt


# ---------------------------------------------------------------------------
# Accumulator populated under lock
# ---------------------------------------------------------------------------


class TestAccumulatorPopulation:
    def test_accumulator_populated_on_file_event(
        self, tmp_state_dir: Path, default_config: Config
    ) -> None:
        registry = HookRegistry()
        registry.register(HookEventType.NEW_FILE, MagicMock(return_value=[]))
        handler = _make_handler(tmp_state_dir, registry, default_config)

        evt = _file_event("/vault/note.md", "created")
        handler.on_any_event(evt)

        assert "/vault/note.md" in handler._event_acc
        handler.cancel()

    def test_accumulator_populated_for_modified_event(
        self, tmp_state_dir: Path, default_config: Config
    ) -> None:
        registry = HookRegistry()
        registry.register(HookEventType.FILE_MODIFIED, MagicMock(return_value=[]))
        handler = _make_handler(tmp_state_dir, registry, default_config)

        evt = _file_event("/vault/note.md", "modified")
        handler.on_any_event(evt)

        acc = handler._event_acc.get("/vault/note.md")
        assert acc is not None
        assert acc.modified  # type: ignore[union-attr]
        handler.cancel()

    def test_move_records_deleted_src_and_created_dest(
        self, tmp_state_dir: Path, default_config: Config
    ) -> None:
        registry = HookRegistry()
        registry.register(HookEventType.NEW_FILE, MagicMock(return_value=[]))
        handler = _make_handler(tmp_state_dir, registry, default_config)

        evt = _move_event("/vault/old.md", "/vault/new.md")
        handler.on_any_event(evt)

        assert "/vault/old.md" in handler._event_acc
        assert "/vault/new.md" in handler._event_acc
        assert handler._event_acc["/vault/old.md"].deleted  # type: ignore[union-attr]
        assert handler._event_acc["/vault/new.md"].created  # type: ignore[union-attr]
        handler.cancel()

    def test_no_accumulation_when_registry_none(
        self, tmp_state_dir: Path
    ) -> None:
        handler = _make_handler(tmp_state_dir, registry=None)
        evt = _file_event("/vault/note.md", "created")
        handler.on_any_event(evt)

        assert handler._event_acc == {}
        handler.cancel()

    def test_no_accumulation_when_registry_empty(
        self, tmp_state_dir: Path
    ) -> None:
        registry = HookRegistry()  # no handlers registered
        handler = _make_handler(tmp_state_dir, registry=registry)
        evt = _file_event("/vault/note.md", "created")
        handler.on_any_event(evt)

        assert handler._event_acc == {}
        handler.cancel()

    def test_accumulator_cleared_each_window(
        self, tmp_state_dir: Path, default_config: Config
    ) -> None:
        """After _trigger_backup fires, the accumulator should be empty."""
        dispatch_calls: list[list[HookEvent]] = []

        def capturing_handler(events: list[HookEvent]) -> list[str]:
            dispatch_calls.append(list(events))
            return []

        registry = HookRegistry()
        registry.register(HookEventType.FILE_MODIFIED, capturing_handler)
        on_changes = MagicMock()
        handler = _make_handler(tmp_state_dir, registry, default_config, on_changes)

        evt = _file_event("/vault/note.md", "modified")
        handler.on_any_event(evt)
        time.sleep(0.2)

        # Accumulator should be cleared
        assert handler._event_acc == {}
        # Handler was called
        assert len(dispatch_calls) == 1


# ---------------------------------------------------------------------------
# Hooks run before on_changes
# ---------------------------------------------------------------------------


class TestHooksRunBeforeOnChanges:
    def test_hooks_invoked_before_on_changes(
        self, tmp_state_dir: Path, default_config: Config
    ) -> None:
        """Assert ordering: hook dispatch must precede on_changes() call."""
        call_order: list[str] = []

        def hook_handler(events: list[HookEvent]) -> list[str]:
            call_order.append("hook")
            return []

        def on_changes() -> None:
            call_order.append("on_changes")

        registry = HookRegistry()
        registry.register(HookEventType.FILE_MODIFIED, hook_handler)
        handler = DebouncedHandler(
            debounce_seconds=0,
            on_changes=on_changes,
            state_dir=tmp_state_dir,
            hook_registry=registry,
            config=default_config,
        )

        evt = _file_event("/vault/note.md", "modified")
        handler.on_any_event(evt)
        time.sleep(0.2)

        assert call_order == ["hook", "on_changes"]

    def test_on_changes_still_called_when_no_hooks_registered(
        self, tmp_state_dir: Path
    ) -> None:
        on_changes = MagicMock()
        handler = DebouncedHandler(
            debounce_seconds=0,
            on_changes=on_changes,
            state_dir=tmp_state_dir,
            hook_registry=None,
        )
        evt = _file_event("/vault/note.md", "modified")
        handler.on_any_event(evt)
        time.sleep(0.2)
        on_changes.assert_called_once()

    def test_on_changes_called_even_when_hook_raises(
        self, tmp_state_dir: Path, default_config: Config
    ) -> None:
        def bad_hook(events: list[HookEvent]) -> list[str]:
            raise RuntimeError("hook explosion")

        on_changes = MagicMock()
        registry = HookRegistry()
        registry.register(HookEventType.FILE_MODIFIED, bad_hook)
        handler = DebouncedHandler(
            debounce_seconds=0,
            on_changes=on_changes,
            state_dir=tmp_state_dir,
            hook_registry=registry,
            config=default_config,
        )
        evt = _file_event("/vault/note.md", "modified")
        handler.on_any_event(evt)
        time.sleep(0.2)
        on_changes.assert_called_once()


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------


class TestSuppression:
    def test_suppressed_path_events_dropped(
        self, tmp_state_dir: Path, default_config: Config
    ) -> None:
        """Events for a suppressed path should not enter the accumulator."""
        registry = HookRegistry()
        registry.register(HookEventType.FILE_MODIFIED, MagicMock(return_value=[]))
        handler = _make_handler(tmp_state_dir, registry, default_config)

        # Manually suppress the path with a long TTL
        handler._suppress_paths["/vault/renamed.md"] = time.time() + 100

        evt = _file_event("/vault/renamed.md", "modified")
        handler.on_any_event(evt)

        assert "/vault/renamed.md" not in handler._event_acc
        handler.cancel()

    def test_suppression_expires_after_ttl(
        self, tmp_state_dir: Path, default_config: Config
    ) -> None:
        """After TTL expiry, events for the path are accepted again."""
        registry = HookRegistry()
        registry.register(HookEventType.FILE_MODIFIED, MagicMock(return_value=[]))
        handler = _make_handler(tmp_state_dir, registry, default_config)

        # Suppress with already-expired TTL
        handler._suppress_paths["/vault/note.md"] = time.time() - 1

        evt = _file_event("/vault/note.md", "modified")
        handler.on_any_event(evt)

        assert "/vault/note.md" in handler._event_acc
        handler.cancel()

    def test_hook_mutated_paths_added_to_suppress(
        self, tmp_state_dir: Path, default_config: Config
    ) -> None:
        """Paths returned by a hook are added to _suppress_paths after dispatch."""
        mutated = ["/vault/old.md", "/vault/new.md"]

        def hook_handler(events: list[HookEvent]) -> list[str]:
            return mutated

        registry = HookRegistry()
        registry.register(HookEventType.FILE_MODIFIED, hook_handler)
        on_changes = MagicMock()
        handler = DebouncedHandler(
            debounce_seconds=0,
            on_changes=on_changes,
            state_dir=tmp_state_dir,
            hook_registry=registry,
            config=default_config,
        )

        evt = _file_event("/vault/a.md", "modified")
        handler.on_any_event(evt)
        time.sleep(0.2)

        assert "/vault/old.md" in handler._suppress_paths
        assert "/vault/new.md" in handler._suppress_paths

    def test_move_into_ignored_dir_skipped(
        self, tmp_state_dir: Path, default_config: Config
    ) -> None:
        """A move whose destination is in .git should be ignored entirely."""
        registry = HookRegistry()
        registry.register(HookEventType.NEW_FILE, MagicMock(return_value=[]))
        handler = _make_handler(tmp_state_dir, registry, default_config)

        evt = _move_event("/vault/note.md", "/vault/.git/note.md")
        handler.on_any_event(evt)

        assert "/vault/note.md" not in handler._event_acc
        assert "/vault/.git/note.md" not in handler._event_acc
        handler.cancel()


# ---------------------------------------------------------------------------
# No / empty registry — unchanged backup path (regression safety)
# ---------------------------------------------------------------------------


class TestNoRegistryRegressionSafety:
    def test_no_registry_backup_fires_normally(
        self, tmp_state_dir: Path
    ) -> None:
        on_changes = MagicMock()
        handler = DebouncedHandler(
            debounce_seconds=0,
            on_changes=on_changes,
            state_dir=tmp_state_dir,
            hook_registry=None,
        )
        evt = _file_event("/vault/note.md", "modified")
        handler.on_any_event(evt)
        time.sleep(0.2)
        on_changes.assert_called_once()

    def test_empty_registry_backup_fires_normally(
        self, tmp_state_dir: Path
    ) -> None:
        on_changes = MagicMock()
        registry = HookRegistry()  # no handlers
        handler = DebouncedHandler(
            debounce_seconds=0,
            on_changes=on_changes,
            state_dir=tmp_state_dir,
            hook_registry=registry,
        )
        evt = _file_event("/vault/note.md", "modified")
        handler.on_any_event(evt)
        time.sleep(0.2)
        on_changes.assert_called_once()

    def test_vault_watcher_uses_default_registry(
        self, default_config: Config
    ) -> None:
        """VaultWatcher builds and wires a default registry without error."""
        on_changes = MagicMock()
        watcher = VaultWatcher(config=default_config, on_changes=on_changes)
        assert watcher.handler._hook_registry is not None
        # Default config has renamer disabled → registry is empty
        assert watcher.handler._hook_registry.is_empty()
