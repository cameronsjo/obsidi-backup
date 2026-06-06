"""UntitledRenamer hook: renames Untitled.md notes using LLM-generated titles.

Registered on both NEW_FILE and FILE_MODIFIED because Obsidian creates
``Untitled.md`` empty; the body arrives via later modify events.  Registering
only on NEW_FILE would hit the body-length gate at 0 chars and always skip.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vault_backup.config import Config
    from vault_backup.hooks import HookEvent

log = logging.getLogger(__name__)

# Characters illegal in most filesystems that must be stripped from titles
_ILLEGAL_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")
_FRONTMATTER = re.compile(r"^---\n.*?\n---\n", re.DOTALL)


def strip_frontmatter(text: str) -> str:
    """Remove a leading YAML front-matter block (``---\\n...\\n---\\n``) if present."""
    return _FRONTMATTER.sub("", text, count=1)


def sanitize_title(raw: str, max_chars: int = 120) -> str:
    """Strip illegal filename chars, collapse whitespace, cap to *max_chars*.

    Removes the characters ``/ \\ : * ? " < > |`` and ASCII control chars
    (0x00–0x1f), collapses any remaining whitespace runs (including tabs and
    newlines) to a single space, strips leading/trailing space, and truncates.

    Whitespace is collapsed *before* control chars are stripped so that tabs
    and newlines are treated as whitespace (→ space) rather than deleted silently.
    """
    # First: normalise whitespace (tabs, newlines, multiple spaces) to single space
    cleaned = _WHITESPACE.sub(" ", raw).strip()
    # Then: remove illegal filename chars and remaining control chars
    cleaned = _ILLEGAL_CHARS.sub("", cleaned).strip()
    return cleaned[:max_chars]


class UntitledRenamer:
    """Hook handler that renames Untitled.md notes with LLM-generated titles.

    Acts as a ``HandlerFn`` — callable with a list of HookEvents, returns the
    list of absolute paths it mutated (old + new paths per rename).
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._pattern = re.compile(config.renamer.pattern)

    def __call__(self, events: list[HookEvent]) -> list[str]:
        """Process a batch of events; return mutated absolute paths."""
        from vault_backup.backup import generate_title

        cfg = self._config.renamer
        mutated: list[str] = []
        rename_count = 0

        for event in events:
            if rename_count >= cfg.max_per_batch:
                log.info(
                    "UntitledRenamer: per-batch cap reached (%d), stopping",
                    cfg.max_per_batch,
                )
                break

            result = self._process_event(event, generate_title)
            if result:
                mutated.extend(result)
                rename_count += 1

        return mutated

    def _process_event(
        self,
        event: HookEvent,
        generate_title_fn: object,
    ) -> list[str] | None:
        """Attempt to rename the file referenced by *event*.

        Returns ``[old_abs_path, new_abs_path]`` on success, ``None`` otherwise.
        """
        cfg = self._config.renamer
        path = Path(event.path)

        # Guard 1: skip if this event is itself the result of a rename
        if event.renamed_from is not None:
            log.debug("Skipping rename result: %s", path.name)
            return None

        # Guard 2: filename must match the Untitled pattern
        if not self._pattern.match(path.name):
            return None

        # Read file content
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            log.debug("Could not read %s, skipping", path)
            return None

        body = strip_frontmatter(content)

        # Gate: body must meet minimum length *before* calling LLM (rail a)
        if len(body) < cfg.min_body_chars:
            log.debug(
                "Body too short for rename (%d < %d chars): %s",
                len(body),
                cfg.min_body_chars,
                path.name,
            )
            return None

        excerpt = body[: cfg.excerpt_chars]

        raw_title = generate_title_fn(self._config, excerpt)  # type: ignore[call-arg]
        if not raw_title:
            log.debug("LLM returned no title for %s", path.name)
            return None

        # Sanitize (rail b)
        title = sanitize_title(raw_title, max_chars=cfg.max_title_chars)

        # Reject empty or still-Untitled after sanitize
        if not title or self._pattern.match(title + ".md"):
            log.debug("Sanitized title empty or still-Untitled: %r", title)
            return None

        # Build destination path with collision suffix
        new_path = self._collision_safe(path.parent, title)

        # Perform the rename
        old_abs = str(path.resolve())
        new_abs = str(new_path.resolve())

        if not self._rename(path, new_path):
            return None

        log.info("Renamed %s → %s", path.name, new_path.name)
        return [old_abs, new_abs]

    def _collision_safe(self, parent: Path, title: str) -> Path:
        """Return a Path that doesn't collide with an existing file.

        Appends `` 1``, `` 2``, … before the ``.md`` extension as needed.
        """
        candidate = parent / f"{title}.md"
        if not candidate.exists():
            return candidate
        n = 1
        while True:
            candidate = parent / f"{title} {n}.md"
            if not candidate.exists():
                return candidate
            n += 1

    def _rename(self, src: Path, dest: Path) -> bool:
        """Rename *src* to *dest*, trying ``git mv`` first then plain fs rename."""
        vault_path = Path(self._config.vault_path)
        try:
            result = subprocess.run(
                ["git", "mv", str(src), str(dest)],
                cwd=vault_path,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                log.debug("git mv succeeded: %s → %s", src.name, dest.name)
                return True
            log.debug(
                "git mv failed (%s), falling back to fs rename", result.stderr.strip()
            )
        except OSError:
            log.debug("git mv not available, using fs rename", exc_info=True)

        # Filesystem fallback
        try:
            src.rename(dest)
            log.debug("fs rename succeeded: %s → %s", src.name, dest.name)
            return True
        except OSError:
            log.warning("Failed to rename %s → %s", src, dest, exc_info=True)
            return False
