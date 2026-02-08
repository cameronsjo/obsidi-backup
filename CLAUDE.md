# Obsidian Vault Backup

A lightweight sidecar container that automatically backs up Obsidian vaults with git versioning and restic cloud storage.

## Quick Reference

```bash
# Run locally (requires vault path)
VAULT_PATH=/path/to/vault RESTIC_REPOSITORY=... RESTIC_PASSWORD=... python -m vault_backup

# Run tests
uv run --extra dev pytest -v

# Run tests with coverage
uv run --extra dev pytest --cov=vault_backup --cov-report=term-missing

# Lint
uv run --extra dev ruff check src/
uv run --extra dev ruff format src/

# Build container
docker build -t vault-backup .
```

## Architecture

```
Watchdog (file monitor) -> Debounce (5min) -> Git Commit -> Restic Backup -> Notify
                                                  |
                                             Claude Haiku
                                          (AI commit messages)
```

**Key modules:**

- `__main__.py` - Entry point, structured logging, Sentry init, signal handling
- `watcher.py` - File system monitoring with watchdog, path-segment ignore patterns
- `backup.py` - Git commit, restic backup, LLM commit messages
- `config.py` - Frozen dataclass configuration from environment variables
- `health.py` - HTTP `/health` and `/ready` endpoints with thread-safe state
- `notify.py` - Discord/Slack/generic webhook notifications via `_post_json()`

## Key Decisions

- **Python 3.12+** - Modern Python with type hints throughout
- **Alpine container** - Minimal image size (~50MB)
- **Watchdog** - Cross-platform file monitoring
- **Restic** - Deduplicated, encrypted backups
- **Claude Haiku** - Optional AI commit messages from file changes
- **python-json-logger** - Structured JSON logging to stdout
- **sentry-sdk** - Optional error tracking (set `SENTRY_DSN`)
- **Frozen dataclasses** - All config objects are immutable

## Environment Variables

**Required:**

- `RESTIC_REPOSITORY` - Restic repository URL
- `RESTIC_PASSWORD` - Restic repository password

**Optional (AI commits):**

- `ANTHROPIC_API_KEY` - Anthropic API key for Claude commit messages
- `ANTHROPIC_MODEL` - Model name (default: `claude-haiku-4-5-latest`)
- `LLM_API_URL` - OpenAI-compatible API URL (alternative to Anthropic)
- `LLM_API_KEY` - API key for OpenAI-compatible endpoint
- `LLM_MODEL` - Model name for OpenAI-compatible API

**Optional (notifications):**

- `DISCORD_WEBHOOK_URL` - Discord webhook URL
- `DISCORD_WEBHOOK_USERNAME` - Custom bot username
- `DISCORD_WEBHOOK_AVATAR_URL` - Custom bot avatar
- `SLACK_WEBHOOK_URL` - Slack incoming webhook URL
- `WEBHOOK_URL` - Generic webhook URL (Ntfy, Gotify, etc.)
- `NOTIFY_LEVEL` - `all`, `errors`, `success`, `none` (default: `all`)

**Optional (observability):**

- `SENTRY_DSN` - Sentry DSN for error tracking
- `SENTRY_ENVIRONMENT` - Sentry environment tag (default: `production`)

**Optional (tuning):**

- `VAULT_PATH` - Path to vault (default: `/vault`)
- `STATE_DIR` - Path to state directory (default: `/app/state`)
- `DEBOUNCE_SECONDS` - Debounce period in seconds (default: `300`)
- `HEALTH_PORT` - Health server port (default: `8080`)
- `DRY_RUN` - `true`/`1`/`yes` to skip actual commits/backups
- `GIT_USER_NAME` - Git author name (default: `Obsidian Backup`)
- `GIT_USER_EMAIL` - Git author email (default: `backup@local`)
- `RETENTION_DAILY` / `RETENTION_WEEKLY` / `RETENTION_MONTHLY` - Restic retention policy

## Testing

145 tests, 93-100% coverage on testable modules. Tests use real HTTP servers for webhook verification and `mock_subprocess` for git/restic operations.

```bash
uv run --extra dev pytest -v                                    # All tests
uv run --extra dev pytest --cov=vault_backup --cov-report=term  # With coverage
uv run --extra dev pytest tests/test_health.py -v               # Single module
```

## Issue Tracking

Uses [beads](https://github.com/synthase/beads) for git-backed issue tracking. Issue prefix: `ovb`.

```bash
bd ready          # Show ready work
bd list --all     # All issues
bd stats          # Project stats
```
