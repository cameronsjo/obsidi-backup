# Obsidian Vault Backup

Sidecar container that watches an Obsidian vault for changes, commits to git, and backs up to cloud storage via restic.

## Features

- **File watching** - Watchdog-based monitoring with configurable debounce
- **Git versioning** - Auto-commits changes with optional AI-generated messages
- **Cloud backup** - Restic to Azure Blob Storage (or any restic-supported backend)
- **Health endpoint** - HTTP `/health` endpoint for monitoring
- **Configurable retention** - Customize daily, weekly, monthly snapshot retention
- **Notifications** - Discord, Slack, or generic webhook alerts
- **Dry run mode** - Test configuration without making changes

## Quick Start

```yaml
services:
  obsidian-vault-backup:
    image: ghcr.io/cameronsjo/obsidian-vault-backup:latest
    environment:
      TZ: America/Chicago
      VAULT_PATH: /vault
      DEBOUNCE_SECONDS: 300
      # Azure Storage
      AZURE_ACCOUNT_NAME: your-account
      AZURE_ACCOUNT_KEY: your-key
      RESTIC_REPOSITORY: azure:container-name:/obsidian
      RESTIC_PASSWORD: your-restic-password
      # Optional: AI commit messages
      ANTHROPIC_API_KEY: sk-ant-...
      # Optional: Discord notifications
      DISCORD_WEBHOOK_URL: https://discord.com/api/webhooks/...
    volumes:
      - /path/to/vault:/vault  # Must be writable (no :ro)
    ports:
      - "8080:8080"
```

## Environment Variables

### Required

| Variable | Description |
|----------|-------------|
| `AZURE_ACCOUNT_NAME` | Azure storage account name |
| `AZURE_ACCOUNT_KEY` | Azure storage account key |
| `RESTIC_REPOSITORY` | Restic repository URL |
| `RESTIC_PASSWORD` | Restic encryption password |

### Paths & Timing

| Variable | Default | Description |
|----------|---------|-------------|
| `VAULT_PATH` | `/vault` | Path to Obsidian vault |
| `STATE_DIR` | `/app/state` | State file directory |
| `DEBOUNCE_SECONDS` | `300` | Wait time after last change before backup |
| `HEALTH_PORT` | `8080` | Health endpoint port |

### Git

| Variable | Default | Description |
|----------|---------|-------------|
| `GIT_USER_NAME` | `Obsidian Backup` | Git commit author name |
| `GIT_USER_EMAIL` | `backup@local` | Git commit author email |

### AI Commit Messages

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | - | Anthropic API key |
| `ANTHROPIC_API_URL` | `https://api.anthropic.com/v1/messages` | Anthropic API URL |
| `ANTHROPIC_MODEL` | `claude-haiku-4-5-latest` | Model for commit messages |
| `LLM_API_URL` | - | OpenAI-compatible API URL (alternative) |
| `LLM_API_KEY` | - | API key for OpenAI-compatible endpoint |
| `LLM_MODEL` | `anthropic/claude-haiku-4.5` | Model for OpenAI-compatible API |

### Retention Policy

| Variable | Default | Description |
|----------|---------|-------------|
| `RETENTION_DAILY` | `7` | Daily snapshots to keep |
| `RETENTION_WEEKLY` | `4` | Weekly snapshots to keep |
| `RETENTION_MONTHLY` | `12` | Monthly snapshots to keep |

### Notifications

| Variable | Default | Description |
|----------|---------|-------------|
| `NOTIFY_LEVEL` | `all` | When to notify: `all`, `errors`, `success`, `none` |
| `DISCORD_WEBHOOK_URL` | - | Discord webhook URL |
| `SLACK_WEBHOOK_URL` | - | Slack incoming webhook URL |
| `WEBHOOK_URL` | - | Generic webhook URL (JSON POST) |

### Feature Flags

| Variable | Default | Description |
|----------|---------|-------------|
| `DRY_RUN` | `false` | Test mode - no actual commits or backups |

## Health Endpoint

```bash
curl http://localhost:8080/health
```

```json
{
  "status": "healthy",
  "last_commit": "2024-01-15T10:30:00Z",
  "last_backup": "2024-01-15T10:30:15Z",
  "last_change": "2024-01-15T10:25:00Z",
  "pending_changes": false,
  "commits_since_backup": 0,
  "uptime_seconds": 3600
}
```

Status is `unhealthy` if changes exist but no backup in 24 hours.

## Initial Setup

After first run, initialize the restic repository:

```bash
docker exec obsidian-vault-backup restic init
```

## How It Works

```
File Change -> Watchdog -> Debounce (5min) -> Git Commit -> Restic Backup -> Prune -> Notify
```

1. **Watchdog** monitors vault for file changes
2. **Debounce timer** waits for inactivity (default 5 minutes)
3. **Git commit** with AI-generated or timestamp message
4. **Restic backup** to Azure (or configured backend)
5. **Prune** old snapshots per retention policy
6. **Notify** via Discord/Slack/webhook (if configured)

## Notifications

### Discord

```yaml
DISCORD_WEBHOOK_URL: https://discord.com/api/webhooks/ID/TOKEN
```

### Slack

```yaml
SLACK_WEBHOOK_URL: https://hooks.slack.com/services/T.../B.../...
```

### Generic Webhook

For Ntfy, Gotify, Home Assistant, n8n, etc:

```yaml
WEBHOOK_URL: https://your-service.com/webhook
```

POSTs JSON:

```json
{
  "title": "Vault Backup Complete",
  "message": "Committed and backed up: 3 files changed",
  "status": "success",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

## Restore

List available snapshots:

```bash
docker exec obsidian-vault-backup restic snapshots
```

Restore to a directory:

```bash
docker exec obsidian-vault-backup restic restore latest --target /restore
```

## License

MIT
