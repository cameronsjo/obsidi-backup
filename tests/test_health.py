"""Tests for vault_backup.health."""

from __future__ import annotations

import json
import time
from http.server import HTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import MagicMock, patch

import pytest

from vault_backup.config import Config
from vault_backup.health import HealthHandler, HealthServer, HealthState, _health_state


class TestHealthState:
    def test_to_dict_no_state_files(self, default_config: Config, tmp_state_dir: Path) -> None:
        state = HealthState(config=default_config)
        result = state.to_dict()
        assert result["status"] == "healthy"
        assert result["last_commit"] is None
        assert result["last_backup"] is None
        assert result["last_change"] is None
        assert result["pending_changes"] is False
        assert result["commits_since_backup"] == 0
        assert result["sync_state"] is None
        assert "uptime_seconds" in result

    def test_to_dict_with_state_files(self, default_config: Config, tmp_state_dir: Path) -> None:
        now = time.time()
        (tmp_state_dir / "last_commit").write_text(str(now))
        (tmp_state_dir / "last_backup").write_text(str(now))
        (tmp_state_dir / "last_change").write_text(str(now - 100))
        (tmp_state_dir / "pending_changes").write_text("true")

        state = HealthState(config=default_config)
        result = state.to_dict()
        assert result["last_commit"] is not None
        assert result["last_backup"] is not None
        assert result["pending_changes"] is True

    def test_unhealthy_when_stale_backup(self, default_config: Config, tmp_state_dir: Path) -> None:
        now = time.time()
        old_backup = now - 100_000  # >24h ago
        (tmp_state_dir / "last_backup").write_text(str(old_backup))
        (tmp_state_dir / "last_change").write_text(str(now))  # Recent change

        state = HealthState(config=default_config)
        result = state.to_dict()
        assert result["status"] == "unhealthy"

    def test_healthy_when_stale_backup_but_no_changes(
        self, default_config: Config, tmp_state_dir: Path
    ) -> None:
        old_backup = time.time() - 100_000
        (tmp_state_dir / "last_backup").write_text(str(old_backup))
        # No last_change file -> no changes since backup

        state = HealthState(config=default_config)
        result = state.to_dict()
        assert result["status"] == "healthy"


class TestHealthStateHelpers:
    def test_read_timestamp_valid(self, tmp_path: Path) -> None:
        f = tmp_path / "ts"
        f.write_text("1700000000.5\n")
        assert HealthState._read_timestamp(f) == 1700000000.5

    def test_read_timestamp_missing(self, tmp_path: Path) -> None:
        assert HealthState._read_timestamp(tmp_path / "nope") is None

    def test_read_timestamp_invalid(self, tmp_path: Path) -> None:
        f = tmp_path / "ts"
        f.write_text("not-a-number")
        assert HealthState._read_timestamp(f) is None

    def test_read_bool_true(self, tmp_path: Path) -> None:
        for val in ("true", "1", "yes", "TRUE", "Yes"):
            f = tmp_path / "flag"
            f.write_text(val)
            assert HealthState._read_bool(f) is True

    def test_read_bool_false(self, tmp_path: Path) -> None:
        f = tmp_path / "flag"
        f.write_text("false")
        assert HealthState._read_bool(f) is False

    def test_read_bool_missing(self, tmp_path: Path) -> None:
        assert HealthState._read_bool(tmp_path / "nope") is False

    def test_timestamp_to_iso(self) -> None:
        result = HealthState._timestamp_to_iso(1700000000.0)
        assert result is not None
        assert result.endswith("Z")
        assert "2023-11-14" in result

    def test_timestamp_to_iso_none(self) -> None:
        assert HealthState._timestamp_to_iso(None) is None

    def test_timestamp_to_iso_zero(self) -> None:
        assert HealthState._timestamp_to_iso(0) is None

    def test_read_sync_state_exists(self, tmp_vault: Path) -> None:
        obsidian_dir = tmp_vault / ".obsidian"
        obsidian_dir.mkdir()
        (obsidian_dir / "sync.json").write_text('{"status": "synced"}')
        result = HealthState._read_sync_state(tmp_vault)
        assert result == {"status": "synced"}

    def test_read_sync_state_missing(self, tmp_vault: Path) -> None:
        assert HealthState._read_sync_state(tmp_vault) is None

    def test_read_sync_state_malformed(self, tmp_vault: Path) -> None:
        obsidian_dir = tmp_vault / ".obsidian"
        obsidian_dir.mkdir()
        (obsidian_dir / "sync.json").write_text("not json{{{")
        assert HealthState._read_sync_state(tmp_vault) is None

    def test_count_commits_since_subprocess_failure(self, tmp_path: Path) -> None:
        # Non-git directory should return 0
        result = HealthState._count_commits_since(tmp_path, time.time() - 3600)
        assert result == 0


class TestHealthHandler:
    @pytest.fixture()
    def health_server(self, default_config: Config):
        """Start a real health server for testing."""
        import vault_backup.health as health_mod

        health_mod._health_state = HealthState(config=default_config)
        server = HTTPServer(("127.0.0.1", 0), HealthHandler)
        port = server.server_address[1]
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{port}"
        server.shutdown()
        health_mod._health_state = None

    def test_health_endpoint(self, health_server: str) -> None:
        import urllib.request

        resp = urllib.request.urlopen(f"{health_server}/health")
        assert resp.status == 200
        body = json.loads(resp.read())
        assert body["status"] == "healthy"
        assert "uptime_seconds" in body

    def test_health_endpoint_trailing_slash(self, health_server: str) -> None:
        import urllib.request

        resp = urllib.request.urlopen(f"{health_server}/health/")
        assert resp.status == 200

    def test_ready_endpoint(self, health_server: str) -> None:
        import urllib.request

        resp = urllib.request.urlopen(f"{health_server}/ready")
        assert resp.status == 200
        body = json.loads(resp.read())
        assert body["ready"] is True

    def test_ready_503_when_not_initialized(self) -> None:
        import urllib.error
        import urllib.request
        import vault_backup.health as health_mod

        health_mod._health_state = None
        server = HTTPServer(("127.0.0.1", 0), HealthHandler)
        port = server.server_address[1]
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/ready")
            assert exc_info.value.code == 503
        finally:
            server.shutdown()

    def test_not_found(self, health_server: str) -> None:
        import urllib.error
        import urllib.request

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"{health_server}/nonexistent")
        assert exc_info.value.code == 404

    def test_500_when_state_not_initialized(self) -> None:
        import urllib.error
        import urllib.request
        import vault_backup.health as health_mod

        health_mod._health_state = None
        server = HTTPServer(("127.0.0.1", 0), HealthHandler)
        port = server.server_address[1]
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health")
            assert exc_info.value.code == 500
        finally:
            server.shutdown()


class TestHealthServer:
    def test_start_and_stop(self, default_config: Config) -> None:
        hs = HealthServer(config=default_config)
        hs.start()
        assert hs.server is not None
        assert hs.thread is not None
        assert hs.thread.is_alive()
        hs.stop()


class TestStatusDict:
    """Tests for HealthState.to_status_dict() — the /status payload."""

    def test_unknown_when_no_watcher_event(self, default_config: Config) -> None:
        """sync_pipeline_status is unknown when last_watcher_event has never fired."""
        state = HealthState(config=default_config)
        result = state.to_status_dict()
        assert result["sync_pipeline_status"] == "unknown"
        assert result["seconds_since_watcher_event"] is None
        assert result["last_watcher_event_at"] is None

    def test_unknown_when_watcher_event_is_zero(
        self, default_config: Config, tmp_state_dir: Path
    ) -> None:
        """Sentinel value 0 (written by initialize_state_dir) counts as unknown."""
        (tmp_state_dir / "last_watcher_event").write_text("0")
        state = HealthState(config=default_config)
        result = state.to_status_dict()
        assert result["sync_pipeline_status"] == "unknown"

    def test_healthy_when_recent_watcher_event(
        self, default_config: Config, tmp_state_dir: Path
    ) -> None:
        """sync_pipeline_status healthy when event is within threshold."""
        (tmp_state_dir / "last_watcher_event").write_text(str(int(time.time() - 60)))
        state = HealthState(config=default_config)
        result = state.to_status_dict()
        assert result["sync_pipeline_status"] == "healthy"
        assert result["seconds_since_watcher_event"] is not None
        assert result["seconds_since_watcher_event"] < default_config.pipeline_stale_threshold_seconds

    def test_stale_when_old_watcher_event(
        self, default_config: Config, tmp_state_dir: Path
    ) -> None:
        """sync_pipeline_status stale when event exceeds threshold."""
        old_ts = time.time() - (default_config.pipeline_stale_threshold_seconds + 1)
        (tmp_state_dir / "last_watcher_event").write_text(str(int(old_ts)))
        state = HealthState(config=default_config)
        result = state.to_status_dict()
        assert result["sync_pipeline_status"] == "stale"
        assert result["seconds_since_watcher_event"] >= default_config.pipeline_stale_threshold_seconds

    def test_stale_at_exact_threshold(
        self, default_config: Config, tmp_state_dir: Path
    ) -> None:
        """Event exactly at threshold boundary is stale (>=)."""
        old_ts = time.time() - default_config.pipeline_stale_threshold_seconds
        (tmp_state_dir / "last_watcher_event").write_text(str(int(old_ts)))
        state = HealthState(config=default_config)
        result = state.to_status_dict()
        assert result["sync_pipeline_status"] == "stale"

    def test_full_shape(self, default_config: Config) -> None:
        """All expected keys are present in the /status response."""
        state = HealthState(config=default_config)
        result = state.to_status_dict()
        expected_keys = {
            "status",
            "sync_pipeline_status",
            "last_watcher_event_at",
            "last_change_detected_at",
            "last_commit_at",
            "last_push_at",
            "last_restic_snapshot_at",
            "pipeline_stale_threshold_seconds",
            "seconds_since_watcher_event",
            "pending_changes",
            "uptime_seconds",
            "upstream_heartbeat",
        }
        assert expected_keys.issubset(result.keys())

    def test_last_push_at_roundtrips(self, default_config: Config, tmp_state_dir: Path) -> None:
        """last_push_at reflects the last_push state file."""
        ts = int(time.time())
        (tmp_state_dir / "last_push").write_text(str(ts))
        state = HealthState(config=default_config)
        result = state.to_status_dict()
        assert result["last_push_at"] is not None
        assert result["last_push_at"].endswith("Z")

    def test_upstream_heartbeat_is_null(self, default_config: Config) -> None:
        """upstream_heartbeat is null — placeholder for future coupling."""
        state = HealthState(config=default_config)
        result = state.to_status_dict()
        assert result["upstream_heartbeat"] is None

    def test_pipeline_stale_threshold_present(self, default_config: Config) -> None:
        """pipeline_stale_threshold_seconds echoes config value."""
        state = HealthState(config=default_config)
        result = state.to_status_dict()
        assert result["pipeline_stale_threshold_seconds"] == default_config.pipeline_stale_threshold_seconds


class TestStatusEndpoint:
    """HTTP-level tests for /status route."""

    @pytest.fixture()
    def health_server(self, default_config: Config):
        """Start a real health server for testing."""
        import vault_backup.health as health_mod

        health_mod._health_state = HealthState(config=default_config)
        server = HTTPServer(("127.0.0.1", 0), HealthHandler)
        port = server.server_address[1]
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{port}"
        server.shutdown()
        health_mod._health_state = None

    def test_status_endpoint_returns_200(self, health_server: str) -> None:
        import urllib.request

        resp = urllib.request.urlopen(f"{health_server}/status")
        assert resp.status == 200

    def test_status_endpoint_shape(self, health_server: str) -> None:
        import urllib.request

        resp = urllib.request.urlopen(f"{health_server}/status")
        body = json.loads(resp.read())
        assert "sync_pipeline_status" in body
        assert "last_watcher_event_at" in body
        assert "last_change_detected_at" in body
        assert "last_commit_at" in body
        assert "last_push_at" in body
        assert "last_restic_snapshot_at" in body
        assert "pipeline_stale_threshold_seconds" in body
        assert "upstream_heartbeat" in body
        assert body["upstream_heartbeat"] is None

    def test_status_trailing_slash(self, health_server: str) -> None:
        import urllib.request

        resp = urllib.request.urlopen(f"{health_server}/status/")
        assert resp.status == 200

    def test_health_backcompat(self, health_server: str) -> None:
        """Existing /health callers still get status + uptime_seconds."""
        import urllib.request

        resp = urllib.request.urlopen(f"{health_server}/health")
        body = json.loads(resp.read())
        assert body["status"] == "healthy"
        assert "uptime_seconds" in body

    def test_status_500_when_not_initialized(self) -> None:
        import urllib.error
        import urllib.request
        import vault_backup.health as health_mod

        health_mod._health_state = None
        server = HTTPServer(("127.0.0.1", 0), HealthHandler)
        port = server.server_address[1]
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/status")
            assert exc_info.value.code == 500
        finally:
            server.shutdown()
