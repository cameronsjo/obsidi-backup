"""Tests for vault_backup.hooks."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from vault_backup.hooks import (
    HookEvent,
    HookEventType,
    HookRegistry,
    _PathAccum,
    record_event,
    reduce_accumulator,
)


# ---------------------------------------------------------------------------
# record_event + reduce_accumulator — reduction rules
# ---------------------------------------------------------------------------


class TestReduceRules:
    """Every reduction rule from the plan spec."""

    def test_created_only_yields_new_file(self) -> None:
        acc: dict[str, _PathAccum] = {}
        record_event(acc, "created", "/vault/note.md")
        events = reduce_accumulator(acc)
        assert len(events) == 1
        assert events[0].type == HookEventType.NEW_FILE
        assert events[0].path == "/vault/note.md"
        assert events[0].renamed_from is None

    def test_created_then_modified_yields_new_file(self) -> None:
        acc: dict[str, _PathAccum] = {}
        record_event(acc, "created", "/vault/note.md")
        record_event(acc, "modified", "/vault/note.md")
        events = reduce_accumulator(acc)
        assert len(events) == 1
        assert events[0].type == HookEventType.NEW_FILE

    def test_modified_only_yields_file_modified(self) -> None:
        acc: dict[str, _PathAccum] = {}
        record_event(acc, "modified", "/vault/note.md")
        events = reduce_accumulator(acc)
        assert len(events) == 1
        assert events[0].type == HookEventType.FILE_MODIFIED

    def test_deleted_only_yields_file_deleted(self) -> None:
        acc: dict[str, _PathAccum] = {}
        record_event(acc, "deleted", "/vault/note.md")
        events = reduce_accumulator(acc)
        assert len(events) == 1
        assert events[0].type == HookEventType.FILE_DELETED

    def test_deleted_not_created_is_file_deleted_even_with_modified(self) -> None:
        acc: dict[str, _PathAccum] = {}
        record_event(acc, "modified", "/vault/note.md")
        record_event(acc, "deleted", "/vault/note.md")
        events = reduce_accumulator(acc)
        assert len(events) == 1
        assert events[0].type == HookEventType.FILE_DELETED

    def test_created_then_deleted_yields_nothing(self) -> None:
        """Created + deleted within the window is net nothing."""
        acc: dict[str, _PathAccum] = {}
        record_event(acc, "created", "/vault/tmp.md")
        record_event(acc, "deleted", "/vault/tmp.md")
        events = reduce_accumulator(acc)
        assert events == []

    def test_created_modified_deleted_yields_nothing(self) -> None:
        acc: dict[str, _PathAccum] = {}
        record_event(acc, "created", "/vault/tmp.md")
        record_event(acc, "modified", "/vault/tmp.md")
        record_event(acc, "deleted", "/vault/tmp.md")
        events = reduce_accumulator(acc)
        assert events == []

    def test_moved_decomposes_to_deleted_and_new_file(self) -> None:
        """A moved event decomposes to FILE_DELETED(src) + NEW_FILE(dest)."""
        acc: dict[str, _PathAccum] = {}
        record_event(acc, "moved", "/vault/old.md", "/vault/new.md")
        events = reduce_accumulator(acc)
        types = {e.type for e in events}
        assert HookEventType.FILE_DELETED in types
        assert HookEventType.NEW_FILE in types

        deleted = next(e for e in events if e.type == HookEventType.FILE_DELETED)
        new_file = next(e for e in events if e.type == HookEventType.NEW_FILE)
        assert deleted.path == "/vault/old.md"
        assert new_file.path == "/vault/new.md"

    def test_moved_carries_renamed_from_on_new_file(self) -> None:
        acc: dict[str, _PathAccum] = {}
        record_event(acc, "moved", "/vault/old.md", "/vault/new.md")
        events = reduce_accumulator(acc)
        new_file = next(e for e in events if e.type == HookEventType.NEW_FILE)
        assert new_file.renamed_from == "/vault/old.md"

    def test_renamed_from_preserved_through_new_file(self) -> None:
        """created entry that carries renamed_from emits it in NEW_FILE."""
        acc: dict[str, _PathAccum] = {}
        record_event(acc, "created", "/vault/note.md")
        acc["/vault/note.md"].renamed_from = "/vault/src.md"
        events = reduce_accumulator(acc)
        assert len(events) == 1
        assert events[0].renamed_from == "/vault/src.md"

    def test_multiple_paths_reduced_independently(self) -> None:
        acc: dict[str, _PathAccum] = {}
        record_event(acc, "created", "/vault/a.md")
        record_event(acc, "modified", "/vault/b.md")
        record_event(acc, "deleted", "/vault/c.md")
        events = reduce_accumulator(acc)
        assert len(events) == 3
        event_types = {e.type for e in events}
        assert event_types == {
            HookEventType.NEW_FILE,
            HookEventType.FILE_MODIFIED,
            HookEventType.FILE_DELETED,
        }

    def test_empty_accumulator_yields_no_events(self) -> None:
        assert reduce_accumulator({}) == []

    def test_moved_with_no_dest_is_noop(self) -> None:
        """record_event ignores a moved event with dest=None."""
        acc: dict[str, _PathAccum] = {}
        record_event(acc, "moved", "/vault/old.md", None)
        # src is not recorded, dest is None — nothing to reduce
        assert reduce_accumulator(acc) == []


# ---------------------------------------------------------------------------
# HookRegistry
# ---------------------------------------------------------------------------


class TestHookRegistry:
    def test_is_empty_when_no_handlers(self) -> None:
        registry = HookRegistry()
        assert registry.is_empty()

    def test_not_empty_after_register(self) -> None:
        registry = HookRegistry()
        registry.register(HookEventType.NEW_FILE, MagicMock())
        assert not registry.is_empty()

    def test_routes_events_to_correct_handler(self) -> None:
        new_file_handler = MagicMock(return_value=[])
        modified_handler = MagicMock(return_value=[])
        deleted_handler = MagicMock(return_value=[])

        registry = HookRegistry()
        registry.register(HookEventType.NEW_FILE, new_file_handler)
        registry.register(HookEventType.FILE_MODIFIED, modified_handler)
        registry.register(HookEventType.FILE_DELETED, deleted_handler)

        events = [
            HookEvent(type=HookEventType.NEW_FILE, path="/vault/a.md"),
            HookEvent(type=HookEventType.FILE_MODIFIED, path="/vault/b.md"),
        ]
        registry.dispatch(events)

        new_file_handler.assert_called_once()
        modified_handler.assert_called_once()
        deleted_handler.assert_not_called()

    def test_handler_receives_only_its_typed_events(self) -> None:
        captured: list[list[HookEvent]] = []

        def handler(batch: list[HookEvent]) -> list[str]:
            captured.append(list(batch))
            return []

        registry = HookRegistry()
        registry.register(HookEventType.NEW_FILE, handler)

        events = [
            HookEvent(type=HookEventType.NEW_FILE, path="/vault/a.md"),
            HookEvent(type=HookEventType.FILE_MODIFIED, path="/vault/b.md"),
            HookEvent(type=HookEventType.NEW_FILE, path="/vault/c.md"),
        ]
        registry.dispatch(events)

        assert len(captured) == 1
        assert all(e.type == HookEventType.NEW_FILE for e in captured[0])
        assert len(captured[0]) == 2

    def test_raising_handler_is_isolated(self) -> None:
        """A handler that raises must not prevent other handlers from running."""
        bad_handler = MagicMock(side_effect=RuntimeError("boom"))
        good_handler = MagicMock(return_value=["mutated_path"])

        registry = HookRegistry()
        registry.register(HookEventType.NEW_FILE, bad_handler)
        registry.register(HookEventType.NEW_FILE, good_handler)

        events = [HookEvent(type=HookEventType.NEW_FILE, path="/vault/note.md")]
        result = registry.dispatch(events)

        bad_handler.assert_called_once()
        good_handler.assert_called_once()
        # Good handler's mutated path should be in the return
        assert "mutated_path" in result

    def test_empty_registry_dispatch_returns_empty(self) -> None:
        registry = HookRegistry()
        events = [HookEvent(type=HookEventType.NEW_FILE, path="/vault/note.md")]
        assert registry.dispatch(events) == []

    def test_dispatch_aggregates_mutated_paths_across_handlers(self) -> None:
        h1 = MagicMock(return_value=["/vault/old.md", "/vault/new.md"])
        h2 = MagicMock(return_value=["/vault/other.md"])

        registry = HookRegistry()
        registry.register(HookEventType.NEW_FILE, h1)
        registry.register(HookEventType.NEW_FILE, h2)

        events = [HookEvent(type=HookEventType.NEW_FILE, path="/vault/a.md")]
        result = registry.dispatch(events)
        assert set(result) == {"/vault/old.md", "/vault/new.md", "/vault/other.md"}

    def test_dispatch_with_empty_event_list_is_noop(self) -> None:
        handler = MagicMock(return_value=[])
        registry = HookRegistry()
        registry.register(HookEventType.NEW_FILE, handler)
        result = registry.dispatch([])
        handler.assert_not_called()
        assert result == []

    def test_suppress_paths_aggregated_across_handlers(self) -> None:
        """Both handlers' mutated paths are returned for suppression."""
        paths_a = ["/vault/a_old.md", "/vault/a_new.md"]
        paths_b = ["/vault/b_old.md", "/vault/b_new.md"]

        h_a = MagicMock(return_value=paths_a)
        h_b = MagicMock(return_value=paths_b)

        registry = HookRegistry()
        registry.register(HookEventType.NEW_FILE, h_a)
        registry.register(HookEventType.NEW_FILE, h_b)

        events = [HookEvent(type=HookEventType.NEW_FILE, path="/vault/Untitled.md")]
        result = registry.dispatch(events)
        assert set(result) == set(paths_a + paths_b)
