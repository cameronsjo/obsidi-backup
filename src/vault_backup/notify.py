"""Notification providers for backup events."""

from __future__ import annotations

import json
import logging
import urllib.request
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vault_backup.config import NotifyConfig

log = logging.getLogger(__name__)


class NotificationProvider(ABC):
    """Base class for notification providers."""

    @abstractmethod
    def send(self, title: str, message: str, *, is_error: bool = False) -> bool:
        """Send a notification. Returns True if successful."""
        ...


class DiscordWebhook(NotificationProvider):
    """Discord webhook notifications."""

    COLOR_SUCCESS = 5763719  # Green (#57F287)
    COLOR_ERROR = 15548997  # Red (#ED4245)

    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    def send(self, title: str, message: str, *, is_error: bool = False) -> bool:
        payload = {
            "embeds": [
                {
                    "title": title,
                    "description": message,
                    "color": self.COLOR_ERROR if is_error else self.COLOR_SUCCESS,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            ]
        }
        return self._post(payload)

    def _post(self, payload: dict) -> bool:
        try:
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                self.webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status < 400
        except Exception:
            log.warning("Failed to send Discord notification", exc_info=True)
            return False


class SlackWebhook(NotificationProvider):
    """Slack incoming webhook notifications."""

    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    def send(self, title: str, message: str, *, is_error: bool = False) -> bool:
        emoji = ":x:" if is_error else ":white_check_mark:"
        payload = {
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"{emoji} {title}"},
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": message},
                },
            ]
        }
        return self._post(payload)

    def _post(self, payload: dict) -> bool:
        try:
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                self.webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status < 400
        except Exception:
            log.warning("Failed to send Slack notification", exc_info=True)
            return False


class GenericWebhook(NotificationProvider):
    """Generic webhook - POSTs JSON to any URL.

    Compatible with: Ntfy, Gotify, Home Assistant, n8n, etc.
    """

    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    def send(self, title: str, message: str, *, is_error: bool = False) -> bool:
        payload = {
            "title": title,
            "message": message,
            "status": "error" if is_error else "success",
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return self._post(payload)

    def _post(self, payload: dict) -> bool:
        try:
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                self.webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status < 400
        except Exception:
            log.warning("Failed to send webhook notification", exc_info=True)
            return False


class Notifier:
    """Aggregates multiple notification providers."""

    def __init__(self, config: NotifyConfig) -> None:
        from vault_backup.config import NotifyLevel

        self.config = config
        self.level = config.level
        self.providers: list[NotificationProvider] = []

        if config.discord_webhook_url:
            self.providers.append(DiscordWebhook(config.discord_webhook_url))
        if config.slack_webhook_url:
            self.providers.append(SlackWebhook(config.slack_webhook_url))
        if config.generic_webhook_url:
            self.providers.append(GenericWebhook(config.generic_webhook_url))

        self._notify_level = NotifyLevel

    def send(self, title: str, message: str, *, is_error: bool = False) -> None:
        """Send notification to all configured providers if level permits."""
        if not self.providers:
            return

        # Check notification level filter
        if self.level == self._notify_level.NONE:
            return
        if self.level == self._notify_level.ERRORS_ONLY and not is_error:
            return
        if self.level == self._notify_level.SUCCESS_ONLY and is_error:
            return

        for provider in self.providers:
            provider.send(title, message, is_error=is_error)

    def success(self, title: str, message: str) -> None:
        """Send a success notification."""
        self.send(title, message, is_error=False)

    def error(self, title: str, message: str) -> None:
        """Send an error notification."""
        self.send(title, message, is_error=True)
