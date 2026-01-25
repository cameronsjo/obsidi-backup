"""HTTP health server."""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vault_backup.config import Config

log = logging.getLogger(__name__)

# Global reference to current health state (updated by main loop)
_health_state: HealthState | None = None


@dataclass
class HealthState:
    """Current health state of the backup service."""

    config: Config
    start_time: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Generate health status dictionary."""
        state_dir = Path(self.config.state_dir)
        vault_path = Path(self.config.vault_path)
        now = time.time()

        # Read state files
        last_commit = self._read_timestamp(state_dir / "last_commit")
        last_backup = self._read_timestamp(state_dir / "last_backup")
        last_change = self._read_timestamp(state_dir / "last_change")
        pending_changes = self._read_bool(state_dir / "pending_changes")

        # Count commits since last backup
        commits_since_backup = 0
        if last_commit and last_backup and last_commit > last_backup:
            commits_since_backup = self._count_commits_since(vault_path, last_backup)

        # Check Obsidian sync state
        sync_state = self._read_sync_state(vault_path)

        # Determine health status
        status = "healthy"
        if last_backup:
            seconds_since_backup = now - last_backup
            # Unhealthy if no backup in 24 hours and we've had changes
            if seconds_since_backup > 86400 and last_change and last_change > last_backup:
                status = "unhealthy"

        return {
            "status": status,
            "last_commit": self._timestamp_to_iso(last_commit),
            "last_backup": self._timestamp_to_iso(last_backup),
            "last_change": self._timestamp_to_iso(last_change),
            "pending_changes": pending_changes,
            "commits_since_backup": commits_since_backup,
            "sync_state": sync_state,
            "uptime_seconds": int(now - self.start_time),
        }

    @staticmethod
    def _read_timestamp(path: Path) -> float | None:
        """Read timestamp from state file."""
        try:
            return float(path.read_text().strip())
        except (FileNotFoundError, ValueError):
            return None

    @staticmethod
    def _read_bool(path: Path) -> bool:
        """Read boolean from state file."""
        try:
            return path.read_text().strip().lower() in ("true", "1", "yes")
        except FileNotFoundError:
            return False

    @staticmethod
    def _timestamp_to_iso(ts: float | None) -> str | None:
        """Convert Unix timestamp to ISO format."""
        if ts is None or ts <= 0:
            return None
        return datetime.fromtimestamp(ts, tz=UTC).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _count_commits_since(vault_path: Path, since_timestamp: float) -> int:
        """Count git commits since a timestamp."""
        try:
            result = subprocess.run(
                ["git", "rev-list", "--count", f"--since=@{int(since_timestamp)}", "HEAD"],
                cwd=vault_path,
                capture_output=True,
                text=True,
                check=True,
            )
            return int(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError):
            return 0

    @staticmethod
    def _read_sync_state(vault_path: Path) -> dict | None:
        """Read Obsidian sync state if present."""
        sync_file = vault_path / ".obsidian" / "sync.json"
        try:
            return json.loads(sync_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None


class HealthHandler(BaseHTTPRequestHandler):
    """HTTP request handler for health endpoint."""

    def log_message(self, format: str, *args: Any) -> None:
        """Override to use our logger."""
        log.debug("HTTP %s", format % args)

    def do_GET(self) -> None:
        """Handle GET requests."""
        if self.path in ("/health", "/health/"):
            self._send_health()
        else:
            self._send_not_found()

    def _send_health(self) -> None:
        """Send health status response."""
        global _health_state
        if _health_state is None:
            self._send_error(500, "Health state not initialized")
            return

        body = json.dumps(_health_state.to_dict(), indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_not_found(self) -> None:
        """Send 404 response."""
        body = b'{"error": "Not Found"}'
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, code: int, message: str) -> None:
        """Send error response."""
        body = json.dumps({"error": message}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class HealthServer:
    """Health HTTP server running in a background thread."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.server: HTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the health server in a background thread."""
        global _health_state
        _health_state = HealthState(config=self.config)

        self.server = HTTPServer(("0.0.0.0", self.config.health_port), HealthHandler)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()
        log.info("Health server started on port %d", self.config.health_port)

    def _serve(self) -> None:
        """Serve requests forever."""
        if self.server:
            self.server.serve_forever()

    def stop(self) -> None:
        """Stop the health server."""
        if self.server:
            self.server.shutdown()
            log.info("Health server stopped")
