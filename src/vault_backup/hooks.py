"""Hook registry and event accumulation for vault file changes.

Hooks dispatch *inside* DebouncedHandler._trigger_backup, before on_changes()
is called.  When no handlers are registered the accumulation is skipped
entirely so the code path is byte-for-byte identical to the pre-hook
implementation (regression safety).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from vault_backup.config import Config

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class HookEventType(Enum):
    """Types of file-system events dispatched to hook handlers."""

    NEW_FILE = "new_file"
    FILE_MODIFIED = "file_modified"
    FILE_DELETED = "file_deleted"


@dataclass
class HookEvent:
    """A single hook event delivered to a handler."""

    type: HookEventType
    path: str
    renamed_from: str | None = None


# HandlerFn receives a list of typed events and returns the list of absolute
# paths it mutated (so the caller can suppress their subsequent fs events).
HandlerFn = Callable[[list[HookEvent]], list[str]]

# ---------------------------------------------------------------------------
# Per-path accumulator (internal)
# ---------------------------------------------------------------------------


@dataclass
class _PathAccum:
    """Accumulates raw events for a single path within a debounce window."""

    created: bool = False
    modified: bool = False
    deleted: bool = False
    renamed_from: str | None = None


# ---------------------------------------------------------------------------
# Accumulator helpers
# ---------------------------------------------------------------------------


def record_event(
    acc: dict[str, _PathAccum],
    event_type: str,
    src: str,
    dest: str | None = None,
) -> None:
    """Record a raw watchdog event into the per-path accumulator.

    *event_type* is the watchdog event_type string
    (``"created"``, ``"modified"``, ``"deleted"``, ``"moved"``).
    A ``"moved"`` event decomposes to ``deleted(src)`` + ``created(dest)``.
    """
    if event_type == "moved":
        # Decompose: mark src as deleted, dest as created (preserving origin)
        if dest is None:
            return
        _get_or_create(acc, src).deleted = True
        dest_entry = _get_or_create(acc, dest)
        dest_entry.created = True
        dest_entry.renamed_from = src
    elif event_type == "created":
        _get_or_create(acc, src).created = True
    elif event_type == "modified":
        _get_or_create(acc, src).modified = True
    elif event_type == "deleted":
        _get_or_create(acc, src).deleted = True


def _get_or_create(acc: dict[str, _PathAccum], path: str) -> _PathAccum:
    if path not in acc:
        acc[path] = _PathAccum()
    return acc[path]


def reduce_accumulator(acc: dict[str, _PathAccum]) -> list[HookEvent]:
    """Reduce the per-path accumulator to a list of canonical HookEvents.

    Reduction rules (applied per path):
    - created (±modified)            → NEW_FILE (carry renamed_from if set)
    - modified only                  → FILE_MODIFIED
    - deleted (not created)          → FILE_DELETED
    - created + deleted (±modified)  → net nothing (emit no event)
    - moved                          → FILE_DELETED(src) + NEW_FILE(dest, renamed_from=src)
                                       (the decomposition already happened in record_event)
    """
    events: list[HookEvent] = []
    for path, entry in acc.items():
        if entry.created and entry.deleted:
            # Net nothing — created and then deleted within the window
            continue
        if entry.created:
            events.append(
                HookEvent(
                    type=HookEventType.NEW_FILE,
                    path=path,
                    renamed_from=entry.renamed_from,
                )
            )
        elif entry.deleted:
            events.append(HookEvent(type=HookEventType.FILE_DELETED, path=path))
        elif entry.modified:
            events.append(HookEvent(type=HookEventType.FILE_MODIFIED, path=path))
    return events


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class HookRegistry:
    """Holds handler callbacks keyed by event type."""

    def __init__(self) -> None:
        self._handlers: dict[HookEventType, list[HandlerFn]] = {
            t: [] for t in HookEventType
        }

    def register(self, event_type: HookEventType, handler: HandlerFn) -> None:
        """Register *handler* to be called for events of *event_type*."""
        self._handlers[event_type].append(handler)

    def is_empty(self) -> bool:
        """Return True when no handlers have been registered."""
        return all(len(fns) == 0 for fns in self._handlers.values())

    def dispatch(self, events: list[HookEvent]) -> list[str]:
        """Dispatch *events* to registered handlers.

        Events are grouped by type; each handler receives only the events
        it registered for.  A handler that raises is isolated — others
        still run.  Returns the aggregated list of absolute paths that
        handlers report as mutated.
        """
        # Group by type
        by_type: dict[HookEventType, list[HookEvent]] = {t: [] for t in HookEventType}
        for evt in events:
            by_type[evt.type].append(evt)

        mutated: list[str] = []
        for event_type, batch in by_type.items():
            if not batch:
                continue
            for handler in self._handlers[event_type]:
                try:
                    result = handler(batch)
                    if result:
                        mutated.extend(result)
                except Exception:
                    log.exception(
                        "Hook handler raised an exception (isolated)",
                        extra={"event_type": event_type.value},
                    )
        return mutated


def build_default_registry(config: Config) -> HookRegistry:
    """Build and return the default HookRegistry for the given config.

    Registers built-in handlers based on config flags.
    """
    from vault_backup.renamer import UntitledRenamer

    registry = HookRegistry()

    if config.renamer.enabled:
        renamer = UntitledRenamer(config)
        registry.register(HookEventType.NEW_FILE, renamer)
        registry.register(HookEventType.FILE_MODIFIED, renamer)
        log.info("UntitledRenamer registered on NEW_FILE and FILE_MODIFIED")

    return registry
