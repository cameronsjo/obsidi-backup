"""Tests for vault_backup.config."""

from __future__ import annotations

import pytest

from vault_backup.config import Config, LLMConfig, NotifyConfig, NotifyLevel, RetentionPolicy


class TestRetentionPolicy:
    def test_defaults(self) -> None:
        policy = RetentionPolicy()
        assert policy.daily == 7
        assert policy.weekly == 4
        assert policy.monthly == 12

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RETENTION_DAILY", "14")
        monkeypatch.setenv("RETENTION_WEEKLY", "8")
        monkeypatch.setenv("RETENTION_MONTHLY", "24")
        policy = RetentionPolicy.from_env()
        assert policy.daily == 14
        assert policy.weekly == 8
        assert policy.monthly == 24

    def test_from_env_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RETENTION_DAILY", raising=False)
        monkeypatch.delenv("RETENTION_WEEKLY", raising=False)
        monkeypatch.delenv("RETENTION_MONTHLY", raising=False)
        policy = RetentionPolicy.from_env()
        assert policy.daily == 7

    def test_from_env_invalid_value_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-numeric values raise a clear ValueError."""
        monkeypatch.setenv("RETENTION_DAILY", "abc")
        with pytest.raises(ValueError, match="RETENTION_DAILY must be an integer"):
            RetentionPolicy.from_env()

    def test_frozen(self) -> None:
        policy = RetentionPolicy()
        with pytest.raises(AttributeError):
            policy.daily = 10  # type: ignore[misc]


class TestLLMConfig:
    def test_disabled_by_default(self) -> None:
        config = LLMConfig()
        assert not config.enabled

    def test_enabled_with_anthropic_key(self) -> None:
        config = LLMConfig(anthropic_api_key="sk-ant-test")
        assert config.enabled

    def test_enabled_with_openai_url(self) -> None:
        config = LLMConfig(llm_api_url="http://localhost:8000/v1/chat/completions")
        assert config.enabled

    def test_from_env_anthropic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
        config = LLMConfig.from_env()
        assert config.anthropic_api_key == "sk-ant-test"
        assert config.anthropic_model == "claude-sonnet-4-5-20250929"
        assert config.enabled

    def test_from_env_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("LLM_API_URL", "http://localhost:8000/v1/chat/completions")
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
        config = LLMConfig.from_env()
        assert config.llm_api_url == "http://localhost:8000/v1/chat/completions"
        assert config.llm_model == "gpt-4o-mini"
        assert config.enabled

    def test_default_models(self) -> None:
        config = LLMConfig()
        assert config.anthropic_model == "claude-haiku-4-5-latest"
        assert config.llm_model == "anthropic/claude-haiku-4.5"


class TestNotifyConfig:
    def test_disabled_by_default(self) -> None:
        config = NotifyConfig()
        assert not config.enabled

    def test_enabled_with_discord(self) -> None:
        config = NotifyConfig(discord_webhook_url="https://discord.com/api/webhooks/1/abc")
        assert config.enabled

    def test_enabled_with_slack(self) -> None:
        config = NotifyConfig(slack_webhook_url="https://hooks.slack.com/services/T/B/X")
        assert config.enabled

    def test_enabled_with_generic(self) -> None:
        config = NotifyConfig(generic_webhook_url="https://ntfy.example.com/test")
        assert config.enabled

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NOTIFY_LEVEL", "errors")
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/abc")
        monkeypatch.setenv("DISCORD_WEBHOOK_USERNAME", "Backup Bot")
        monkeypatch.setenv("DISCORD_WEBHOOK_AVATAR_URL", "https://example.com/avatar.png")
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/X")
        monkeypatch.setenv("WEBHOOK_URL", "https://ntfy.example.com/test")
        config = NotifyConfig.from_env()
        assert config.level == NotifyLevel.ERRORS_ONLY
        assert config.discord_webhook_url == "https://discord.com/api/webhooks/1/abc"
        assert config.discord_username == "Backup Bot"
        assert config.discord_avatar_url == "https://example.com/avatar.png"
        assert config.slack_webhook_url == "https://hooks.slack.com/services/T/B/X"
        assert config.generic_webhook_url == "https://ntfy.example.com/test"

    def test_invalid_level_falls_back_to_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Invalid NOTIFY_LEVEL silently falls back to ALL - no warning logged."""
        monkeypatch.setenv("NOTIFY_LEVEL", "banana")
        config = NotifyConfig.from_env()
        assert config.level == NotifyLevel.ALL

    def test_level_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NOTIFY_LEVEL", "ERRORS")
        config = NotifyConfig.from_env()
        assert config.level == NotifyLevel.ERRORS_ONLY


class TestConfig:
    def test_defaults(self) -> None:
        config = Config()
        assert config.vault_path == "/vault"
        assert config.state_dir == "/app/state"
        assert config.debounce_seconds == 300
        assert config.health_port == 8080
        assert config.dry_run is False
        assert config.sentry_dsn is None
        assert config.sentry_environment == "production"

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VAULT_PATH", "/my/vault")
        monkeypatch.setenv("STATE_DIR", "/my/state")
        monkeypatch.setenv("DEBOUNCE_SECONDS", "60")
        monkeypatch.setenv("HEALTH_PORT", "9090")
        monkeypatch.setenv("GIT_USER_NAME", "Test User")
        monkeypatch.setenv("GIT_USER_EMAIL", "test@example.com")
        monkeypatch.setenv("DRY_RUN", "true")
        config = Config.from_env()
        assert config.vault_path == "/my/vault"
        assert config.state_dir == "/my/state"
        assert config.debounce_seconds == 60
        assert config.health_port == 9090
        assert config.git_user_name == "Test User"
        assert config.git_user_email == "test@example.com"
        assert config.dry_run is True

    def test_dry_run_variants(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for truthy in ("true", "1", "yes", "TRUE", "Yes"):
            monkeypatch.setenv("DRY_RUN", truthy)
            assert Config.from_env().dry_run is True

        for falsy in ("false", "0", "no", ""):
            monkeypatch.setenv("DRY_RUN", falsy)
            assert Config.from_env().dry_run is False

    def test_invalid_debounce_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-numeric DEBOUNCE_SECONDS raises a clear ValueError."""
        monkeypatch.setenv("DEBOUNCE_SECONDS", "not-a-number")
        with pytest.raises(ValueError, match="DEBOUNCE_SECONDS must be an integer"):
            Config.from_env()

    def test_invalid_health_port_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-numeric HEALTH_PORT raises a clear ValueError."""
        monkeypatch.setenv("HEALTH_PORT", "abc")
        with pytest.raises(ValueError, match="HEALTH_PORT must be an integer"):
            Config.from_env()

    def test_frozen(self) -> None:
        config = Config()
        with pytest.raises(AttributeError):
            config.vault_path = "/other"  # type: ignore[misc]

    def test_sentry_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SENTRY_DSN", "https://key@sentry.io/123")
        monkeypatch.setenv("SENTRY_ENVIRONMENT", "staging")
        config = Config.from_env()
        assert config.sentry_dsn == "https://key@sentry.io/123"
        assert config.sentry_environment == "staging"

    def test_sub_configs_loaded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RETENTION_DAILY", "30")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://example.com")
        config = Config.from_env()
        assert config.retention.daily == 30
        assert config.llm.enabled
        assert config.notify.enabled
