"""Git and restic backup operations."""

from __future__ import annotations

import json
import logging
import subprocess
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vault_backup.config import Config

log = logging.getLogger(__name__)


@dataclass
class BackupResult:
    """Result of a backup operation."""

    success: bool
    commit_created: bool = False
    backup_created: bool = False
    changes_summary: str = ""
    error: str | None = None


def run_cmd(
    cmd: list[str], *, cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a command and return result."""
    log.debug("Running: %s", " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)


def has_changes(vault_path: Path) -> bool:
    """Check if there are uncommitted changes in the vault."""
    result = run_cmd(["git", "status", "--porcelain"], cwd=vault_path, check=False)
    return bool(result.stdout.strip())


def get_changed_files(vault_path: Path) -> list[str]:
    """Get list of changed files (staged for commit)."""
    result = run_cmd(["git", "diff", "--cached", "--name-only"], cwd=vault_path, check=False)
    return [f for f in result.stdout.strip().split("\n") if f]


def get_changes_summary(vault_path: Path) -> str:
    """Get human-readable summary of changes."""
    result = run_cmd(["git", "diff", "--cached", "--stat"], cwd=vault_path, check=False)
    lines = result.stdout.strip().split("\n")
    return lines[-1] if lines else "files changed"


def generate_ai_commit_message(config: Config, changed_files: list[str], stats: str) -> str | None:
    """Generate commit message using LLM."""
    if not config.llm.enabled:
        return None

    prompt = f"""Summarize these Obsidian vault changes in one concise commit message line (max 60 chars). Be specific about what changed based on filenames. Use lowercase, no period at end.

Changed files:
{chr(10).join(changed_files)}

Stats: {stats}"""

    try:
        if config.llm.llm_api_url:
            return _call_openai_compatible(config, prompt)
        return _call_anthropic(config, prompt)
    except Exception:
        log.warning("AI commit message generation failed", exc_info=True)
        return None


def _call_anthropic(config: Config, prompt: str) -> str | None:
    """Call Anthropic native API."""
    log.info("Generating commit message via Anthropic API (model: %s)", config.llm.anthropic_model)

    payload = {
        "model": config.llm.anthropic_model,
        "max_tokens": 100,
        "messages": [{"role": "user", "content": prompt}],
    }

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        config.llm.anthropic_api_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "x-api-key": config.llm.anthropic_api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read().decode())
        return result.get("content", [{}])[0].get("text")


def _call_openai_compatible(config: Config, prompt: str) -> str | None:
    """Call OpenAI-compatible API."""
    log.info(
        "Generating commit message via OpenAI-compatible API: %s (model: %s)",
        config.llm.llm_api_url,
        config.llm.llm_model,
    )

    payload = {
        "model": config.llm.llm_model,
        "max_tokens": 100,
        "messages": [{"role": "user", "content": prompt}],
    }

    headers = {"Content-Type": "application/json"}
    if config.llm.llm_api_key:
        headers["Authorization"] = f"Bearer {config.llm.llm_api_key}"

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        config.llm.llm_api_url,
        data=data,
        headers=headers,
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read().decode())
        return result.get("choices", [{}])[0].get("message", {}).get("content")


def git_commit(config: Config, vault_path: Path) -> tuple[bool, str]:
    """Stage and commit all changes. Returns (success, summary)."""
    # Stage all changes
    log.info("Staging changes")
    run_cmd(["git", "add", "-A"], cwd=vault_path)

    changed_files = get_changed_files(vault_path)
    if not changed_files:
        log.info("No changes to commit after staging")
        return False, ""

    stats = get_changes_summary(vault_path)

    # Generate commit message
    ai_message = generate_ai_commit_message(config, changed_files, stats)
    if ai_message:
        commit_msg = f"vault: {ai_message}"
        log.info("AI commit message: %s", ai_message)
    else:
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        commit_msg = f"vault: auto-backup {timestamp}\n\n{stats}"

    if config.dry_run:
        log.info("[DRY RUN] Would commit: %s", commit_msg)
        run_cmd(["git", "reset", "HEAD"], cwd=vault_path, check=False)
        return True, stats

    # Commit
    log.info("Creating commit")
    result = run_cmd(["git", "commit", "-m", commit_msg], cwd=vault_path, check=False)
    if result.returncode != 0:
        log.error("Git commit failed: %s", result.stderr)
        return False, stats

    log.info("Commit created successfully")
    return True, stats


def restic_backup(config: Config, vault_path: Path) -> bool:
    """Backup vault to restic repository."""
    # Check if repo is initialized
    result = run_cmd(["restic", "snapshots", "--quiet"], check=False)
    if result.returncode != 0:
        log.warning("Restic repository not initialized, skipping backup")
        log.warning("Run 'restic init' to initialize the repository")
        return False

    if config.dry_run:
        log.info("[DRY RUN] Would run restic backup")
        return True

    log.info("Starting restic backup")
    result = run_cmd(
        [
            "restic",
            "backup",
            "--tag",
            "obsidian",
            "--tag",
            "auto-backup",
            "--exclude",
            ".git",
            "--verbose",
            str(vault_path),
        ],
        check=False,
    )

    if result.returncode != 0:
        log.error("Restic backup failed: %s", result.stderr)
        return False

    log.info("Restic backup completed successfully")
    return True


def restic_prune(config: Config) -> bool:
    """Prune old backups according to retention policy."""
    if config.dry_run:
        log.info("[DRY RUN] Would prune backups with retention: %s", config.retention)
        return True

    log.info("Pruning old backups")
    result = run_cmd(
        [
            "restic",
            "forget",
            "--tag",
            "obsidian",
            f"--keep-daily={config.retention.daily}",
            f"--keep-weekly={config.retention.weekly}",
            f"--keep-monthly={config.retention.monthly}",
            "--prune",
            "--quiet",
        ],
        check=False,
    )

    if result.returncode != 0:
        log.warning("Restic prune failed: %s", result.stderr)
        return False

    log.info("Prune completed")
    return True


def run_backup(config: Config, state_dir: Path) -> BackupResult:
    """Run full backup: git commit + restic backup + prune."""
    vault_path = Path(config.vault_path)

    if not has_changes(vault_path):
        log.info("No changes to backup")
        return BackupResult(success=True)

    # Git commit
    commit_success, changes_summary = git_commit(config, vault_path)
    if not commit_success and changes_summary:
        return BackupResult(
            success=False,
            commit_created=False,
            changes_summary=changes_summary,
            error="Git commit failed",
        )

    if commit_success:
        # Update state
        (state_dir / "last_commit").write_text(str(int(datetime.now(UTC).timestamp())))

    # Restic backup
    if not restic_backup(config, vault_path):
        return BackupResult(
            success=False,
            commit_created=commit_success,
            backup_created=False,
            changes_summary=changes_summary,
            error="Restic backup failed",
        )

    # Update state
    (state_dir / "last_backup").write_text(str(int(datetime.now(UTC).timestamp())))

    # Prune (non-fatal if it fails)
    restic_prune(config)

    return BackupResult(
        success=True,
        commit_created=commit_success,
        backup_created=True,
        changes_summary=changes_summary,
    )
