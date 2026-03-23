"""Shared fixtures for vault_backup tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vault_backup.config import Config, LLMConfig, NotifyConfig, NotifyLevel, RetentionPolicy


@pytest.fixture()
def tmp_vault(tmp_path: Path) -> Path:
    """Create a temporary vault directory with git initialized."""
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


@pytest.fixture()
def tmp_state_dir(tmp_path: Path) -> Path:
    """Create a temporary state directory."""
    state = tmp_path / "state"
    state.mkdir()
    return state


@pytest.fixture()
def default_config(tmp_vault: Path, tmp_state_dir: Path) -> Config:
    """Config with defaults pointing to temp directories."""
    return Config(
        vault_path=str(tmp_vault),
        state_dir=str(tmp_state_dir),
        debounce_seconds=1,
        health_port=0,
    )


@pytest.fixture()
def config_with_llm(default_config: Config) -> Config:
    """Config with Anthropic LLM enabled."""
    return Config(
        vault_path=default_config.vault_path,
        state_dir=default_config.state_dir,
        debounce_seconds=default_config.debounce_seconds,
        health_port=default_config.health_port,
        llm=LLMConfig(anthropic_api_key="test-key"),
    )


@pytest.fixture()
def config_with_openai(default_config: Config) -> Config:
    """Config with OpenAI-compatible LLM enabled."""
    return Config(
        vault_path=default_config.vault_path,
        state_dir=default_config.state_dir,
        debounce_seconds=default_config.debounce_seconds,
        health_port=default_config.health_port,
        llm=LLMConfig(llm_api_url="http://localhost:8000/v1/chat/completions"),
    )


@pytest.fixture()
def config_with_remote(default_config: Config) -> Config:
    """Config with git remote URL configured."""
    return Config(
        vault_path=default_config.vault_path,
        state_dir=default_config.state_dir,
        debounce_seconds=default_config.debounce_seconds,
        health_port=default_config.health_port,
        git_remote_url="git@git.sjo.lol:cameron/the-compendium.git",
    )


@pytest.fixture()
def notify_config_discord() -> NotifyConfig:
    """NotifyConfig with Discord webhook."""
    return NotifyConfig(
        level=NotifyLevel.ALL,
        discord_webhook_url="https://discord.com/api/webhooks/test/token",
    )


@pytest.fixture()
def notify_config_all() -> NotifyConfig:
    """NotifyConfig with all providers."""
    return NotifyConfig(
        level=NotifyLevel.ALL,
        discord_webhook_url="https://discord.com/api/webhooks/test/token",
        slack_webhook_url="https://hooks.slack.com/services/T/B/X",
        generic_webhook_url="https://ntfy.example.com/backups",
    )


@pytest.fixture()
def mock_subprocess(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Mock subprocess.run globally."""
    mock = MagicMock()
    mock.return_value.returncode = 0
    mock.return_value.stdout = ""
    mock.return_value.stderr = ""
    monkeypatch.setattr("subprocess.run", mock)
    return mock
