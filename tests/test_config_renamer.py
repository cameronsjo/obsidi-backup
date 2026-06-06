"""Tests for RenamerConfig and generate_title additions.

Separate file to keep diff reviewable — extends test_config.py and
test_backup.py without touching the originals.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vault_backup.backup import generate_title
from vault_backup.config import Config, LLMConfig, RenamerConfig


# ---------------------------------------------------------------------------
# RenamerConfig
# ---------------------------------------------------------------------------


class TestRenamerConfig:
    def test_defaults(self) -> None:
        cfg = RenamerConfig()
        assert cfg.enabled is False
        assert cfg.min_body_chars == 200
        assert cfg.excerpt_chars == 1500
        assert cfg.max_title_chars == 120
        assert cfg.max_per_batch == 10
        assert cfg.suppress_ttl_seconds == 30
        assert "Untitled" in cfg.pattern

    def test_disabled_by_default(self) -> None:
        cfg = RenamerConfig()
        assert not cfg.enabled

    def test_from_env_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RENAMER_ENABLED", "true")
        cfg = RenamerConfig.from_env()
        assert cfg.enabled is True

    def test_from_env_disabled_variants(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for falsy in ("false", "0", "no", ""):
            monkeypatch.setenv("RENAMER_ENABLED", falsy)
            assert RenamerConfig.from_env().enabled is False

    def test_from_env_enabled_variants(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for truthy in ("true", "1", "yes", "TRUE", "YES"):
            monkeypatch.setenv("RENAMER_ENABLED", truthy)
            assert RenamerConfig.from_env().enabled is True

    def test_from_env_custom_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RENAMER_ENABLED", "true")
        monkeypatch.setenv("RENAMER_MIN_BODY_CHARS", "500")
        monkeypatch.setenv("RENAMER_EXCERPT_CHARS", "2000")
        monkeypatch.setenv("RENAMER_MAX_TITLE_CHARS", "80")
        monkeypatch.setenv("RENAMER_MAX_PER_BATCH", "3")
        monkeypatch.setenv("RENAMER_SUPPRESS_TTL_SECONDS", "60")
        cfg = RenamerConfig.from_env()
        assert cfg.min_body_chars == 500
        assert cfg.excerpt_chars == 2000
        assert cfg.max_title_chars == 80
        assert cfg.max_per_batch == 3
        assert cfg.suppress_ttl_seconds == 60

    def test_from_env_defaults_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (
            "RENAMER_ENABLED",
            "RENAMER_MIN_BODY_CHARS",
            "RENAMER_EXCERPT_CHARS",
            "RENAMER_MAX_TITLE_CHARS",
            "RENAMER_MAX_PER_BATCH",
            "RENAMER_SUPPRESS_TTL_SECONDS",
        ):
            monkeypatch.delenv(var, raising=False)
        cfg = RenamerConfig.from_env()
        assert cfg.enabled is False
        assert cfg.min_body_chars == 200

    def test_frozen(self) -> None:
        cfg = RenamerConfig()
        with pytest.raises(AttributeError):
            cfg.enabled = True  # type: ignore[misc]

    def test_wired_into_config(self) -> None:
        config = Config()
        assert isinstance(config.renamer, RenamerConfig)
        assert config.renamer.enabled is False

    def test_config_from_env_loads_renamer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RENAMER_ENABLED", "true")
        monkeypatch.setenv("RENAMER_MAX_PER_BATCH", "7")
        config = Config.from_env()
        assert config.renamer.enabled is True
        assert config.renamer.max_per_batch == 7


# ---------------------------------------------------------------------------
# generate_title
# ---------------------------------------------------------------------------


class TestGenerateTitle:
    def test_returns_none_when_llm_disabled(self, default_config: Config) -> None:
        assert not default_config.llm.enabled
        result = generate_title(default_config, "Some note body content here.")
        assert result is None

    def test_calls_anthropic_and_returns_text(
        self, config_with_llm: Config
    ) -> None:
        mock_response = json.dumps(
            {"content": [{"text": "My Generated Title"}]}
        ).encode()

        with patch("vault_backup.backup.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = lambda s: s
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value.read.return_value = mock_response

            result = generate_title(config_with_llm, "Note body content for title.")
            assert result == "My Generated Title"

    def test_calls_openai_compatible_and_returns_text(
        self, config_with_openai: Config
    ) -> None:
        mock_response = json.dumps(
            {"choices": [{"message": {"content": "OpenAI Title"}}]}
        ).encode()

        with patch("vault_backup.backup.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = lambda s: s
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value.read.return_value = mock_response

            result = generate_title(config_with_openai, "Note body for OpenAI.")
            assert result == "OpenAI Title"

    def test_returns_none_on_api_error(self, config_with_llm: Config) -> None:
        with patch("vault_backup.backup.urllib.request.urlopen", side_effect=Exception("timeout")):
            result = generate_title(config_with_llm, "Some content.")
            assert result is None

    def test_prompt_does_not_contain_full_note_body(
        self, config_with_llm: Config
    ) -> None:
        """The LLM receives the excerpt, not more (caller's responsibility to bound)."""
        mock_response = json.dumps({"content": [{"text": "Title"}]}).encode()
        body_excerpt = "This is the bounded excerpt passed by the renamer."

        with patch("vault_backup.backup.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = lambda s: s
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value.read.return_value = mock_response

            generate_title(config_with_llm, body_excerpt)

        # Verify the request body included the excerpt
        req_data = mock_urlopen.call_args[0][0].data
        assert body_excerpt.encode() in req_data
