"""Obsidian Vault Backup - Main entry point."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

from pythonjsonlogger.json import JsonFormatter

from vault_backup import __version__
from vault_backup.backup import run_backup
from vault_backup.config import Config
from vault_backup.health import HealthServer
from vault_backup.notify import Notifier
from vault_backup.ui import RestoreHandler
from vault_backup.watcher import VaultWatcher


def _configure_logging() -> None:
    """Configure structured JSON logging to stdout."""
    handler = logging.StreamHandler(sys.stdout)
    formatter = JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={
            "asctime": "timestamp",
            "levelname": "level",
            "name": "logger",
        },
        static_fields={"service": "vault-backup", "version": __version__},
        timestamp=True,
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    # Quiet watchdog logging
    logging.getLogger("watchdog").setLevel(logging.WARNING)


_configure_logging()
log = logging.getLogger("vault_backup")


GITIGNORE_CONTENT = """\
# Obsidian workspace files (change frequently, not useful to track)
.obsidian/workspace.json
.obsidian/workspace-mobile.json
.obsidian/workspaces.json

# Trash
.trash/

# System files
.DS_Store
Thumbs.db

# Backup test files
.backup-write-test
"""


def validate_environment() -> None:
    """Validate required environment variables.

    Only RESTIC_REPOSITORY and RESTIC_PASSWORD are universally required.
    Backend-specific vars (Azure, S3, B2, etc.) are validated by restic itself.
    """
    required = [
        "RESTIC_REPOSITORY",
        "RESTIC_PASSWORD",
    ]

    missing = [var for var in required if not os.environ.get(var)]
    if missing:
        log.error("Missing required environment variables", extra={"missing": missing})
        sys.exit(1)

    repo = os.environ["RESTIC_REPOSITORY"]
    log.info("Environment validated", extra={"restic_repository": repo.split(":")[0] + ":***"})


def initialize_state_dir(state_dir: Path) -> None:
    """Create state directory and initialize state files."""
    state_dir.mkdir(parents=True, exist_ok=True)
    log.info("State directory initialized", extra={"state_dir": str(state_dir)})

    # Initialize state files with defaults
    defaults = {
        "last_commit": "0",
        "last_backup": "0",
        "last_change": "0",
        "pending_changes": "false",
    }

    for name, value in defaults.items():
        state_file = state_dir / name
        if not state_file.exists():
            state_file.write_text(value)


def validate_vault(vault_path: Path) -> None:
    """Validate vault directory exists and is writable."""
    if not vault_path.exists():
        log.error("Vault directory does not exist", extra={"vault_path": str(vault_path)})
        sys.exit(1)

    if not vault_path.is_dir():
        log.error("Vault path is not a directory", extra={"vault_path": str(vault_path)})
        sys.exit(1)

    # Check writable
    test_file = vault_path / ".backup-write-test"
    try:
        test_file.touch()
        test_file.unlink()
    except PermissionError:
        log.error(
            "Vault directory is not writable. Remove ':ro' from the volume mount",
            extra={"vault_path": str(vault_path)},
        )
        sys.exit(1)
    log.info("Vault validated", extra={"vault_path": str(vault_path)})


def initialize_git(config: Config) -> None:
    """Initialize git repository in vault if needed."""
    vault_path = Path(config.vault_path)

    # Mark directory as safe (required for Git 2.35.2+)
    # Uses --system to avoid polluting user's global git config (biz)
    # Falls back to --global if --system fails (no permissions outside container)
    sys_result = subprocess.run(
        ["git", "config", "--system", "--add", "safe.directory", str(vault_path)],
        capture_output=True,
    )
    if sys_result.returncode != 0:
        subprocess.run(
            ["git", "config", "--global", "--add", "safe.directory", str(vault_path)],
            check=True,
        )

    git_dir = vault_path / ".git"
    if not git_dir.exists():
        log.info("Initializing git repository in vault")
        subprocess.run(["git", "init"], cwd=vault_path, check=True)

        # Create .gitignore if it doesn't exist
        gitignore = vault_path / ".gitignore"
        if not gitignore.exists():
            log.info("Creating .gitignore")
            gitignore.write_text(GITIGNORE_CONTENT)

    # Configure git
    subprocess.run(
        ["git", "config", "user.name", config.git_user_name],
        cwd=vault_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", config.git_user_email],
        cwd=vault_path,
        check=True,
    )
    subprocess.run(["git", "config", "core.autocrlf", "input"], cwd=vault_path, check=True)
    subprocess.run(["git", "config", "core.safecrlf", "false"], cwd=vault_path, check=True)

    log.info("Git configured: %s <%s>", config.git_user_name, config.git_user_email)


def check_restic(config: Config) -> None:
    """Check if restic repository is initialized."""
    result = subprocess.run(
        ["restic", "snapshots", "--quiet"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.warning(
            "Restic repository not found. Run 'restic init' to initialize. "
            "Continuing without backup functionality"
        )
    else:
        log.info("Restic repository verified")


def main() -> None:
    """Main entry point."""
    try:
        _run()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        pass
    except Exception:
        log.exception("Fatal error in vault backup sidecar")
        sys.exit(1)


def _init_sentry(config: Config) -> None:
    """Initialize Sentry error tracking if DSN is configured."""
    if not config.sentry_dsn:
        return

    import sentry_sdk

    sentry_sdk.init(
        dsn=config.sentry_dsn,
        release=f"vault-backup@{__version__}",
        environment=config.sentry_environment,
        traces_sample_rate=0,
    )
    log.info("Sentry initialized", extra={"environment": config.sentry_environment})


def _run() -> None:
    """Run the backup sidecar."""
    log.info("Starting Obsidian Vault Backup sidecar")

    # Validate environment
    validate_environment()

    # Load configuration
    config = Config.from_env()

    # Initialize Sentry early so it captures all subsequent errors
    _init_sentry(config)

    log.info(
        "Configuration loaded",
        extra={
            "vault_path": config.vault_path,
            "state_dir": config.state_dir,
            "debounce_seconds": config.debounce_seconds,
            "health_port": config.health_port,
            "dry_run": config.dry_run,
            "ai_commits": config.llm.enabled,
            "notifications": config.notify.enabled,
            "sentry": bool(config.sentry_dsn),
        },
    )

    if config.dry_run:
        log.warning("DRY RUN MODE - no actual commits or backups will be made")

    # Initialize
    state_dir = Path(config.state_dir)
    vault_path = Path(config.vault_path)

    initialize_state_dir(state_dir)
    validate_vault(vault_path)
    initialize_git(config)
    check_restic(config)

    # Create notifier
    notifier = Notifier(config.notify)
    if notifier.providers:
        log.info(
            "Notifications enabled: %d provider(s), level=%s",
            len(notifier.providers),
            config.notify.level.value,
        )

    # Backup callback for watcher
    def on_changes() -> None:
        """Called by watcher when changes are detected and debounce period elapses."""
        try:
            result = run_backup(config, state_dir)

            if result.success and result.backup_created:
                notifier.success(
                    "Vault Backup Complete",
                    f"Committed and backed up: {result.changes_summary}",
                )
            elif not result.success:
                notifier.error("Vault Backup Failed", result.error or "Unknown error")
        except Exception:
            log.exception("Unexpected error during backup")
            notifier.error(
                "Vault Backup Error",
                "Unexpected error during backup — check container logs",
            )

    # Start health server with restore UI
    health_server = HealthServer(config, handler_class=RestoreHandler)
    health_server.start()

    # Start watcher
    watcher = VaultWatcher(config, on_changes)
    watcher.start()

    # Handle shutdown signals
    shutdown_event = False

    def shutdown_handler(signum: int, frame: object) -> None:
        nonlocal shutdown_event
        if shutdown_event:
            return
        shutdown_event = True

        sig_name = signal.Signals(signum).name
        log.info("Received %s, shutting down...", sig_name)
        watcher.stop()
        health_server.stop()

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    log.info("Vault backup sidecar ready")
    notifier.success(
        "Vault Backup Online",
        f"Watching `{config.vault_path}` (debounce: {config.debounce_seconds}s)",
    )

    # Wait for watcher (blocks until shutdown)
    watcher.wait()


if __name__ == "__main__":
    main()
