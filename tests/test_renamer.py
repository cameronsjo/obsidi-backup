"""Tests for vault_backup.renamer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vault_backup.config import Config, LLMConfig, RenamerConfig
from vault_backup.hooks import HookEvent, HookEventType
from vault_backup.renamer import UntitledRenamer, sanitize_title, strip_frontmatter


# ---------------------------------------------------------------------------
# strip_frontmatter
# ---------------------------------------------------------------------------


class TestStripFrontmatter:
    def test_removes_leading_yaml_block(self) -> None:
        text = "---\ntitle: Test\n---\nBody content here."
        assert strip_frontmatter(text) == "Body content here."

    def test_leaves_text_without_frontmatter_unchanged(self) -> None:
        text = "Just a plain note."
        assert strip_frontmatter(text) == "Just a plain note."

    def test_does_not_strip_midblock(self) -> None:
        text = "Introduction.\n---\ntitle: test\n---\nMore text."
        result = strip_frontmatter(text)
        assert result == text  # no leading --- block

    def test_multiline_frontmatter(self) -> None:
        text = "---\ntitle: My Note\ntags: [foo, bar]\ndate: 2026-01-01\n---\nBody."
        assert strip_frontmatter(text) == "Body."

    def test_empty_string(self) -> None:
        assert strip_frontmatter("") == ""

    def test_frontmatter_only_returns_empty(self) -> None:
        text = "---\ntitle: Only Frontmatter\n---\n"
        assert strip_frontmatter(text) == ""


# ---------------------------------------------------------------------------
# sanitize_title
# ---------------------------------------------------------------------------


class TestSanitizeTitle:
    def test_strips_forward_slash(self) -> None:
        assert "/" not in sanitize_title("a/b")

    def test_strips_backslash(self) -> None:
        assert "\\" not in sanitize_title("a\\b")

    def test_strips_colon(self) -> None:
        assert ":" not in sanitize_title("a: title")

    def test_strips_asterisk(self) -> None:
        assert "*" not in sanitize_title("a*b")

    def test_strips_question_mark(self) -> None:
        assert "?" not in sanitize_title("what? something")

    def test_strips_double_quote(self) -> None:
        assert '"' not in sanitize_title('she said "hello"')

    def test_strips_angle_brackets(self) -> None:
        result = sanitize_title("<tag>content</tag>")
        assert "<" not in result
        assert ">" not in result

    def test_strips_pipe(self) -> None:
        assert "|" not in sanitize_title("a|b")

    def test_strips_control_characters(self) -> None:
        result = sanitize_title("a\x00b\x1fc")
        assert "\x00" not in result
        assert "\x1f" not in result

    def test_collapses_whitespace(self) -> None:
        assert sanitize_title("a   b\t\tc") == "a b c"

    def test_strips_leading_trailing_whitespace(self) -> None:
        assert sanitize_title("  title  ") == "title"

    def test_caps_at_max_chars(self) -> None:
        long_title = "a" * 200
        result = sanitize_title(long_title, max_chars=120)
        assert len(result) == 120

    def test_default_max_chars_is_120(self) -> None:
        long_title = "a" * 200
        assert len(sanitize_title(long_title)) == 120

    def test_short_title_unchanged(self) -> None:
        assert sanitize_title("My Note Title") == "My Note Title"

    def test_empty_string(self) -> None:
        assert sanitize_title("") == ""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def renamer_config_enabled() -> RenamerConfig:
    return RenamerConfig(
        enabled=True,
        min_body_chars=50,  # low threshold for test bodies
        excerpt_chars=200,
        max_title_chars=80,
        max_per_batch=5,
        suppress_ttl_seconds=30,
    )


@pytest.fixture()
def config_with_renamer(
    tmp_vault: Path, tmp_state_dir: Path, renamer_config_enabled: RenamerConfig
) -> Config:
    return Config(
        vault_path=str(tmp_vault),
        state_dir=str(tmp_state_dir),
        llm=LLMConfig(anthropic_api_key="test-key"),
        renamer=renamer_config_enabled,
    )


@pytest.fixture()
def untitled_file(tmp_vault: Path) -> Path:
    body = "x" * 100  # > min_body_chars=50
    f = tmp_vault / "Untitled.md"
    f.write_text(body)
    return f


@pytest.fixture()
def untitled_event(untitled_file: Path) -> HookEvent:
    return HookEvent(type=HookEventType.NEW_FILE, path=str(untitled_file))


# ---------------------------------------------------------------------------
# UntitledRenamer
# ---------------------------------------------------------------------------


class TestUntitledRenamerSkipGuards:
    def test_skips_when_renamed_from_is_set(
        self, config_with_renamer: Config, tmp_vault: Path
    ) -> None:
        """Guard 1: skip events that are themselves rename results."""
        f = tmp_vault / "Untitled.md"
        f.write_text("x" * 100)
        event = HookEvent(
            type=HookEventType.NEW_FILE,
            path=str(f),
            renamed_from="/vault/old.md",
        )
        renamer = UntitledRenamer(config_with_renamer)
        with patch("vault_backup.renamer.subprocess.run") as mock_run:
            result = renamer([event])
        assert result == []

    def test_skips_non_matching_filename(
        self, config_with_renamer: Config, tmp_vault: Path
    ) -> None:
        """Guard 2: non-Untitled filenames are skipped."""
        f = tmp_vault / "MyNote.md"
        f.write_text("x" * 100)
        event = HookEvent(type=HookEventType.NEW_FILE, path=str(f))
        renamer = UntitledRenamer(config_with_renamer)
        with patch("vault_backup.backup.generate_title") as mock_gen:
            result = renamer([event])
        assert result == []
        mock_gen.assert_not_called()

    def test_skips_when_body_too_short(
        self, config_with_renamer: Config, tmp_vault: Path
    ) -> None:
        """Rail a: body below min_body_chars → no LLM call."""
        f = tmp_vault / "Untitled.md"
        f.write_text("short")  # 5 chars < 50 min_body_chars
        event = HookEvent(type=HookEventType.NEW_FILE, path=str(f))
        renamer = UntitledRenamer(config_with_renamer)
        with patch("vault_backup.renamer.UntitledRenamer._process_event", wraps=renamer._process_event):
            with patch("vault_backup.backup.generate_title") as mock_gen:
                result = renamer([event])
        assert result == []
        mock_gen.assert_not_called()

    def test_llm_not_called_when_body_too_short(
        self, config_with_renamer: Config, tmp_vault: Path
    ) -> None:
        """Explicit assertion that mock was not called when body is short."""
        f = tmp_vault / "Untitled.md"
        f.write_text("hi")  # << min_body_chars
        event = HookEvent(type=HookEventType.NEW_FILE, path=str(f))
        renamer = UntitledRenamer(config_with_renamer)
        mock_gen = MagicMock(return_value="My Title")
        with patch("vault_backup.renamer.subprocess.run"):
            renamer._process_event(event, mock_gen)
        mock_gen.assert_not_called()

    def test_disabled_config_handler_never_registered(
        self, tmp_vault: Path, tmp_state_dir: Path
    ) -> None:
        """When renamer is disabled, build_default_registry leaves it empty."""
        from vault_backup.hooks import build_default_registry

        config = Config(
            vault_path=str(tmp_vault),
            state_dir=str(tmp_state_dir),
            renamer=RenamerConfig(enabled=False),
        )
        registry = build_default_registry(config)
        assert registry.is_empty()


class TestUntitledRenamerExcerpt:
    def test_llm_receives_stripped_bounded_excerpt(
        self, config_with_renamer: Config, tmp_vault: Path
    ) -> None:
        """LLM receives only the frontmatter-stripped, excerpt_chars-bounded body."""
        frontmatter = "---\ntitle: Old\n---\n"
        body = "A" * 300  # > excerpt_chars=200
        f = tmp_vault / "Untitled.md"
        f.write_text(frontmatter + body)

        event = HookEvent(type=HookEventType.NEW_FILE, path=str(f))
        renamer = UntitledRenamer(config_with_renamer)
        mock_gen = MagicMock(return_value="My Title")

        with patch("vault_backup.renamer.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            renamer._process_event(event, mock_gen)

        mock_gen.assert_called_once()
        call_args = mock_gen.call_args
        excerpt_arg = call_args[0][1]  # second positional arg
        # Must not contain frontmatter
        assert "---" not in excerpt_arg
        assert "title: Old" not in excerpt_arg
        # Must be bounded to excerpt_chars
        assert len(excerpt_arg) <= config_with_renamer.renamer.excerpt_chars
        # Must be the first excerpt_chars chars of the stripped body
        assert excerpt_arg == body[: config_with_renamer.renamer.excerpt_chars]

    def test_frontmatter_excluded_from_length_gate(
        self, config_with_renamer: Config, tmp_vault: Path
    ) -> None:
        """Frontmatter does not count toward min_body_chars."""
        frontmatter = "---\n" + "x" * 200 + "\n---\n"
        short_body = "y" * 10  # << min_body_chars=50 without frontmatter
        f = tmp_vault / "Untitled.md"
        f.write_text(frontmatter + short_body)

        event = HookEvent(type=HookEventType.NEW_FILE, path=str(f))
        renamer = UntitledRenamer(config_with_renamer)
        mock_gen = MagicMock()

        renamer._process_event(event, mock_gen)
        # short_body is only 10 chars, below min_body_chars=50 → no LLM call
        mock_gen.assert_not_called()


class TestUntitledRenamerSanitize:
    def test_sanitize_applied_to_llm_output(
        self, config_with_renamer: Config, tmp_vault: Path
    ) -> None:
        """Illegal chars in LLM output are stripped before using as filename."""
        f = tmp_vault / "Untitled.md"
        f.write_text("x" * 100)
        event = HookEvent(type=HookEventType.NEW_FILE, path=str(f))
        renamer = UntitledRenamer(config_with_renamer)
        mock_gen = MagicMock(return_value="My: Note/Title*Here")

        with patch("vault_backup.renamer.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            result = renamer._process_event(event, mock_gen)

        assert result is not None
        new_path = result[1]
        assert ":" not in Path(new_path).name
        assert "/" not in Path(new_path).name
        assert "*" not in Path(new_path).name

    def test_title_still_matching_untitled_is_rejected(
        self, config_with_renamer: Config, tmp_vault: Path
    ) -> None:
        """If LLM returns a title that after sanitize still matches Untitled pattern, skip."""
        f = tmp_vault / "Untitled.md"
        f.write_text("x" * 100)
        event = HookEvent(type=HookEventType.NEW_FILE, path=str(f))
        renamer = UntitledRenamer(config_with_renamer)
        mock_gen = MagicMock(return_value="Untitled")  # sanitized: "Untitled" + ".md" matches

        result = renamer._process_event(event, mock_gen)
        assert result is None

    def test_empty_title_after_sanitize_is_rejected(
        self, config_with_renamer: Config, tmp_vault: Path
    ) -> None:
        f = tmp_vault / "Untitled.md"
        f.write_text("x" * 100)
        event = HookEvent(type=HookEventType.NEW_FILE, path=str(f))
        renamer = UntitledRenamer(config_with_renamer)
        mock_gen = MagicMock(return_value="///**")  # all illegal chars → empty after sanitize

        result = renamer._process_event(event, mock_gen)
        assert result is None

    def test_title_length_capped(
        self, config_with_renamer: Config, tmp_vault: Path
    ) -> None:
        f = tmp_vault / "Untitled.md"
        f.write_text("x" * 100)
        event = HookEvent(type=HookEventType.NEW_FILE, path=str(f))
        renamer = UntitledRenamer(config_with_renamer)
        long_title = "a" * 200
        mock_gen = MagicMock(return_value=long_title)

        with patch("vault_backup.renamer.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            result = renamer._process_event(event, mock_gen)

        assert result is not None
        new_name = Path(result[1]).stem  # filename without .md
        assert len(new_name) <= config_with_renamer.renamer.max_title_chars


class TestUntitledRenamerCollision:
    def test_collision_suffix_appended(
        self, config_with_renamer: Config, tmp_vault: Path
    ) -> None:
        """When target exists, suffix ' 1' is appended."""
        existing = tmp_vault / "My Title.md"
        existing.write_text("existing")
        source = tmp_vault / "Untitled.md"
        source.write_text("x" * 100)

        event = HookEvent(type=HookEventType.NEW_FILE, path=str(source))
        renamer = UntitledRenamer(config_with_renamer)
        mock_gen = MagicMock(return_value="My Title")

        with patch("vault_backup.renamer.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            result = renamer._process_event(event, mock_gen)

        assert result is not None
        assert Path(result[1]).name == "My Title 1.md"

    def test_collision_suffix_increments(
        self, config_with_renamer: Config, tmp_vault: Path
    ) -> None:
        """When both target and target ' 1' exist, suffix ' 2' is used."""
        (tmp_vault / "My Title.md").write_text("existing")
        (tmp_vault / "My Title 1.md").write_text("existing 1")
        source = tmp_vault / "Untitled.md"
        source.write_text("x" * 100)

        event = HookEvent(type=HookEventType.NEW_FILE, path=str(source))
        renamer = UntitledRenamer(config_with_renamer)
        mock_gen = MagicMock(return_value="My Title")

        with patch("vault_backup.renamer.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            result = renamer._process_event(event, mock_gen)

        assert result is not None
        assert Path(result[1]).name == "My Title 2.md"


class TestUntitledRenamerRename:
    def test_git_mv_path_used_on_success(
        self, config_with_renamer: Config, tmp_vault: Path
    ) -> None:
        f = tmp_vault / "Untitled.md"
        f.write_text("x" * 100)
        event = HookEvent(type=HookEventType.NEW_FILE, path=str(f))
        renamer = UntitledRenamer(config_with_renamer)
        mock_gen = MagicMock(return_value="My Note")

        with patch("vault_backup.renamer.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            result = renamer._process_event(event, mock_gen)

        # subprocess.run (git mv) should have been called
        mock_run.assert_called_once()
        call_cmd = mock_run.call_args[0][0]
        assert call_cmd[0:2] == ["git", "mv"]
        assert result is not None

    def test_fs_fallback_used_when_git_mv_fails(
        self, config_with_renamer: Config, tmp_vault: Path
    ) -> None:
        f = tmp_vault / "Untitled.md"
        f.write_text("x" * 100)
        event = HookEvent(type=HookEventType.NEW_FILE, path=str(f))
        renamer = UntitledRenamer(config_with_renamer)
        mock_gen = MagicMock(return_value="My Note")

        with patch("vault_backup.renamer.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1  # git mv fails
            mock_run.return_value.stderr = "not a git repo"
            result = renamer._process_event(event, mock_gen)

        # git mv was attempted
        mock_run.assert_called_once()
        # File should have been renamed via filesystem fallback
        assert result is not None
        assert Path(result[1]).exists()
        assert not f.exists()

    def test_returns_old_and_new_abs_paths(
        self, config_with_renamer: Config, tmp_vault: Path
    ) -> None:
        f = tmp_vault / "Untitled.md"
        f.write_text("x" * 100)
        event = HookEvent(type=HookEventType.NEW_FILE, path=str(f))
        renamer = UntitledRenamer(config_with_renamer)
        mock_gen = MagicMock(return_value="Returned Paths Test")

        with patch("vault_backup.renamer.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            result = renamer._process_event(event, mock_gen)

        assert result is not None
        assert len(result) == 2
        old_path, new_path = result
        assert old_path == str(f.resolve())
        assert new_path.endswith("Returned Paths Test.md")


class TestUntitledRenamerBatchCap:
    def test_per_batch_cap_respected(
        self, config_with_renamer: Config, tmp_vault: Path
    ) -> None:
        """max_per_batch=5 stops after 5 renames in one dispatch."""
        events = []
        for i in range(8):
            name = "Untitled.md" if i == 0 else f"Untitled {i}.md"
            f = tmp_vault / name
            f.write_text("x" * 100)
            events.append(HookEvent(type=HookEventType.NEW_FILE, path=str(f)))

        renamer = UntitledRenamer(config_with_renamer)
        mock_gen = MagicMock(return_value="My Title")

        with patch("vault_backup.renamer.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            result = renamer(events)

        # Each rename returns 2 paths; max_per_batch=5 → at most 10 paths
        rename_count = len(result) // 2
        assert rename_count <= config_with_renamer.renamer.max_per_batch

    def test_untitled_numbered_matches_pattern(
        self, config_with_renamer: Config, tmp_vault: Path
    ) -> None:
        """Untitled 3.md also matches the default pattern."""
        f = tmp_vault / "Untitled 3.md"
        f.write_text("x" * 100)
        event = HookEvent(type=HookEventType.NEW_FILE, path=str(f))
        renamer = UntitledRenamer(config_with_renamer)
        mock_gen = MagicMock(return_value="Numbered Note")

        with patch("vault_backup.renamer.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            result = renamer._process_event(event, mock_gen)

        assert result is not None
        mock_gen.assert_called_once()
