"""Configuration from environment variables."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Self

log = logging.getLogger(__name__)


def _int_env(name: str, default: int) -> int:
    """Parse an integer environment variable with validation."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        msg = f"Environment variable {name} must be an integer, got {raw!r}"
        raise ValueError(msg) from None


class NotifyLevel(Enum):
    """When to send notifications."""

    ALL = "all"
    ERRORS_ONLY = "errors"
    SUCCESS_ONLY = "success"
    NONE = "none"


@dataclass(frozen=True)
class RetentionPolicy:
    """Restic backup retention policy."""

    daily: int = 7
    weekly: int = 4
    monthly: int = 12

    @classmethod
    def from_env(cls) -> Self:
        return cls(
            daily=_int_env("RETENTION_DAILY", 7),
            weekly=_int_env("RETENTION_WEEKLY", 4),
            monthly=_int_env("RETENTION_MONTHLY", 12),
        )


@dataclass(frozen=True)
class LLMConfig:
    """LLM configuration for AI commit messages."""

    # Anthropic native API
    anthropic_api_key: str | None = None
    anthropic_api_url: str = "https://api.anthropic.com/v1/messages"
    anthropic_model: str = "claude-haiku-4-5-latest"

    # OpenAI-compatible API (alternative)
    llm_api_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str = "anthropic/claude-haiku-4.5"

    @property
    def enabled(self) -> bool:
        return bool(self.anthropic_api_key or self.llm_api_url)

    @classmethod
    def from_env(cls) -> Self:
        return cls(
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
            anthropic_api_url=os.environ.get(
                "ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages"
            ),
            anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-latest"),
            llm_api_url=os.environ.get("LLM_API_URL"),
            llm_api_key=os.environ.get("LLM_API_KEY"),
            llm_model=os.environ.get("LLM_MODEL", "anthropic/claude-haiku-4.5"),
        )


@dataclass(frozen=True)
class NotifyConfig:
    """Notification configuration."""

    level: NotifyLevel = NotifyLevel.ALL
    discord_webhook_url: str | None = None
    discord_username: str | None = None
    discord_avatar_url: str | None = None
    slack_webhook_url: str | None = None
    generic_webhook_url: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.discord_webhook_url or self.slack_webhook_url or self.generic_webhook_url)

    @classmethod
    def from_env(cls) -> Self:
        level_str = os.environ.get("NOTIFY_LEVEL", "all").lower()
        try:
            level = NotifyLevel(level_str)
        except ValueError:
            level = NotifyLevel.ALL

        return cls(
            level=level,
            discord_webhook_url=os.environ.get("DISCORD_WEBHOOK_URL"),
            discord_username=os.environ.get("DISCORD_WEBHOOK_USERNAME"),
            discord_avatar_url=os.environ.get("DISCORD_WEBHOOK_AVATAR_URL"),
            slack_webhook_url=os.environ.get("SLACK_WEBHOOK_URL"),
            generic_webhook_url=os.environ.get("WEBHOOK_URL"),
        )


@dataclass(frozen=True)
class Config:
    """Application configuration."""

    # Paths
    vault_path: str = "/vault"
    state_dir: str = "/app/state"

    # Timing
    debounce_seconds: int = 300
    health_port: int = 8080

    # Git
    git_user_name: str = "Obsidian Backup"
    git_user_email: str = "backup@local"

    # Feature flags
    dry_run: bool = False

    # Observability
    sentry_dsn: str | None = None
    sentry_environment: str = "production"

    # Sub-configs
    retention: RetentionPolicy = field(default_factory=RetentionPolicy)
    llm: LLMConfig = field(default_factory=LLMConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)

    @classmethod
    def from_env(cls) -> Self:
        return cls(
            vault_path=os.environ.get("VAULT_PATH", "/vault"),
            state_dir=os.environ.get("STATE_DIR", "/app/state"),
            debounce_seconds=_int_env("DEBOUNCE_SECONDS", 300),
            health_port=_int_env("HEALTH_PORT", 8080),
            git_user_name=os.environ.get("GIT_USER_NAME", "Obsidian Backup"),
            git_user_email=os.environ.get("GIT_USER_EMAIL", "backup@local"),
            dry_run=os.environ.get("DRY_RUN", "").lower() in ("true", "1", "yes"),
            sentry_dsn=os.environ.get("SENTRY_DSN"),
            sentry_environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
            retention=RetentionPolicy.from_env(),
            llm=LLMConfig.from_env(),
            notify=NotifyConfig.from_env(),
        )
