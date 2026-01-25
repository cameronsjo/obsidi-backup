"""Obsidian Vault Backup - Main entry point."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

from vault_backup.backup import run_backup
from vault_backup.config import Config
from vault_backup.health import HealthServer
from vault_backup.notify import Notifier
from vault_backup.watcher import VaultWatcher

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger("vault_backup")

# Quiet watchdog logging
logging.getLogger("watchdog").setLevel(logging.WARNING)


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
    """Validate required environment variables."""
    required = [
        "AZURE_ACCOUNT_NAME",
        "AZURE_ACCOUNT_KEY",
        "RESTIC_REPOSITORY",
        "RESTIC_PASSWORD",
    ]

    missing = [var for var in required if not os.environ.get(var)]
    if missing:
        log.error("Missing required environment variables: %s", ", ".join(missing))
        sys.exit(1)


def initialize_state_dir(state_dir: Path) -> None:
    """Create state directory and initialize state files."""
    state_dir.mkdir(parents=True, exist_ok=True)

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
        log.error("Vault directory does not exist: %s", vault_path)
        sys.exit(1)

    if not vault_path.is_dir():
        log.error("Vault path is not a directory: %s", vault_path)
        sys.exit(1)

    # Check writable
    test_file = vault_path / ".backup-write-test"
    try:
        test_file.touch()
        test_file.unlink()
    except PermissionError:
        log.error("Vault directory is not writable: %s", vault_path)
        log.error("The backup service requires write access to create git commits")
        log.error("Remove ':ro' from the volume mount in your compose file")
        sys.exit(1)


def initialize_git(config: Config) -> None:
    """Initialize git repository in vault if needed."""
    vault_path = Path(config.vault_path)

    # Mark directory as safe (required for Git 2.35.2+)
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
        log.warning("Restic repository not found or not initialized")
        log.warning("Run 'restic init' to initialize the repository")
        log.warning("Continuing without backup functionality until initialized")


def main() -> None:
    """Main entry point."""
    log.info("Starting Obsidian Vault Backup sidecar")

    # Validate environment
    validate_environment()

    # Load configuration
    config = Config.from_env()

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
        result = run_backup(config, state_dir)

        if result.success and result.backup_created:
            notifier.success(
                "Vault Backup Complete",
                f"Committed and backed up: {result.changes_summary}",
            )
        elif not result.success:
            notifier.error("Vault Backup Failed", result.error or "Unknown error")

    # Start health server
    health_server = HealthServer(config)
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

    # Wait for watcher (blocks until shutdown)
    try:
        watcher.wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
