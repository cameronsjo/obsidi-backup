"""Notification providers for backup events."""

from __future__ import annotations

import json
import logging
import urllib.request
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from vault_backup import __version__

if TYPE_CHECKING:
    from vault_backup.config import NotifyConfig

log = logging.getLogger(__name__)

_USER_AGENT = f"ObsidianBackup/{__version__}"


def _post_json(url: str, payload: dict) -> bool:
    """POST JSON to a URL. Returns True if successful."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status < 400


class NotificationProvider(ABC):
    """Base class for notification providers."""

    @abstractmethod
    def send(self, title: str, message: str, *, is_error: bool = False) -> bool:
        """Send a notification. Returns True if successful."""
        ...


class DiscordWebhook(NotificationProvider):
    """Discord webhook notifications.

    Supports per-request username and avatar overrides, allowing multiple
    services to share one webhook while appearing as distinct senders.
    """

    COLOR_SUCCESS = 5763719  # Green (#57F287)
    COLOR_ERROR = 15548997  # Red (#ED4245)

    def __init__(
        self,
        webhook_url: str,
        username: str | None = None,
        avatar_url: str | None = None,
    ) -> None:
        self.webhook_url = webhook_url
        self.username = username
        self.avatar_url = avatar_url

    def send(self, title: str, message: str, *, is_error: bool = False) -> bool:
        payload: dict = {
            "embeds": [
                {
                    "title": title,
                    "description": message,
                    "color": self.COLOR_ERROR if is_error else self.COLOR_SUCCESS,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            ]
        }
        if self.username:
            payload["username"] = self.username
        if self.avatar_url:
            payload["avatar_url"] = self.avatar_url
        try:
            return _post_json(self.webhook_url, payload)
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
        try:
            return _post_json(self.webhook_url, payload)
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
        try:
            return _post_json(self.webhook_url, payload)
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
            self.providers.append(
                DiscordWebhook(
                    config.discord_webhook_url,
                    username=config.discord_username,
                    avatar_url=config.discord_avatar_url,
                )
            )
        if config.slack_webhook_url:
            self.providers.append(SlackWebhook(config.slack_webhook_url))
        if config.generic_webhook_url:
            self.providers.append(GenericWebhook(config.generic_webhook_url))

        self._notify_level = NotifyLevel

        provider_names = [type(p).__name__ for p in self.providers]
        log.info(
            "Notifier initialized",
            extra={
                "provider_count": len(self.providers),
                "providers": provider_names,
                "level": self.level.value,
            },
        )

    def send(self, title: str, message: str, *, is_error: bool = False) -> None:
        """Send notification to all configured providers if level permits."""
        if not self.providers:
            return

        # Check notification level filter
        if self.level == self._notify_level.NONE:
            log.debug("Notification suppressed by level=none", extra={"title": title})
            return
        if self.level == self._notify_level.ERRORS_ONLY and not is_error:
            log.debug("Success notification suppressed by level=errors", extra={"title": title})
            return
        if self.level == self._notify_level.SUCCESS_ONLY and is_error:
            log.debug("Error notification suppressed by level=success", extra={"title": title})
            return

        for provider in self.providers:
            provider_name = type(provider).__name__
            ok = provider.send(title, message, is_error=is_error)
            if ok:
                log.info(
                    "Notification sent",
                    extra={"provider": provider_name, "title": title, "is_error": is_error},
                )
            else:
                log.warning(
                    "Notification delivery failed",
                    extra={"provider": provider_name, "title": title},
                )

    def success(self, title: str, message: str) -> None:
        """Send a success notification."""
        self.send(title, message, is_error=False)

    def error(self, title: str, message: str) -> None:
        """Send an error notification."""
        self.send(title, message, is_error=True)
