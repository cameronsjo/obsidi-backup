# obsidi-backup

[![Build and Push](https://github.com/cameronsjo/obsidi-backup/actions/workflows/build.yml/badge.svg)](https://github.com/cameronsjo/obsidi-backup/actions/workflows/build.yml)
[![License: PolyForm Noncommercial](https://img.shields.io/badge/License-PolyForm%20NC%201.0-blue.svg)](https://polyformproject.org/licenses/noncommercial/1.0.0)
[![Container Registry](https://img.shields.io/badge/ghcr.io-obsidi--backup-blue)](https://ghcr.io/cameronsjo/obsidi-backup)

A lightweight sidecar container that automatically backs up your Obsidian vault with git versioning and cloud storage via restic.

## Why?

Obsidian Sync is great, but it's not a backup. This container provides:

- **Git history** - Every change tracked, browse history, diff versions
- **Cloud backup** - Encrypted, deduplicated backups to Azure, S3, B2, or any restic backend
- **AI commit messages** - Optional Claude-powered summaries of what changed
- **Notifications** - Know when backups succeed or fail

## Features

- **File watching** - Real-time monitoring with configurable debounce
- **Git versioning** - Auto-commits with AI-generated or timestamp messages
- **Cloud backup** - Restic to Azure, S3, B2, SFTP, or local storage
- **Health endpoint** - HTTP `/health` for monitoring and orchestration
- **Configurable retention** - Customize daily, weekly, monthly snapshots
- **Notifications** - Discord, Slack, or generic webhooks
- **Dry run mode** - Test configuration without making changes
- **Multi-arch** - Runs on amd64 and arm64 (Raspberry Pi, Apple Silicon)

## Quick Start

```yaml
services:
  vault-backup:
    image: ghcr.io/cameronsjo/obsidi-backup:latest
    environment:
      TZ: America/Chicago
      # Storage backend (Azure example)
      AZURE_ACCOUNT_NAME: mystorageaccount
      AZURE_ACCOUNT_KEY: ${AZURE_ACCOUNT_KEY}
      RESTIC_REPOSITORY: azure:obsidian-backup:/vault
      RESTIC_PASSWORD: ${RESTIC_PASSWORD}
      # Optional: AI commit messages
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      # Optional: Notifications
      DISCORD_WEBHOOK_URL: ${DISCORD_WEBHOOK_URL}
    volumes:
      - /path/to/your/vault:/vault
    ports:
      - "8080:8080"
```

After starting, initialize the restic repository:

```bash
docker exec vault-backup restic init
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Obsidian Vault Backup                    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────┐    ┌──────────┐    ┌────────┐    ┌─────────┐  │
│  │ Watchdog│───▶│ Debounce │───▶│  Git   │───▶│ Restic  │  │
│  │ Monitor │    │ (5 min)  │    │ Commit │    │ Backup  │  │
│  └─────────┘    └──────────┘    └────────┘    └─────────┘  │
│       │                              │             │        │
│       │                              ▼             │        │
│       │                        ┌──────────┐       │        │
│       │                        │   LLM    │       │        │
│       │                        │ (Haiku)  │       │        │
│       │                        └──────────┘       │        │
│       │                                           ▼        │
│       │         ┌──────────┐              ┌───────────┐    │
│       └────────▶│  Health  │              │  Notify   │    │
│                 │  Server  │              │ (Discord) │    │
│                 └──────────┘              └───────────┘    │
│                      │                                      │
└──────────────────────┼──────────────────────────────────────┘
                       ▼
                  :8080/health
```

**Flow:**

1. **Watchdog** monitors the vault for file changes
2. **Debounce** waits for 5 minutes of inactivity (configurable)
3. **Git commit** stages all changes, generates commit message (AI or timestamp)
4. **Restic backup** encrypts and uploads to cloud storage
5. **Prune** removes old snapshots per retention policy
6. **Notify** sends success/failure alerts

## Configuration

### Storage Backends

Restic supports many backends. Set `RESTIC_REPOSITORY` accordingly:

| Backend | Repository Format | Additional Env Vars |
|---------|-------------------|---------------------|
| Azure Blob | `azure:container:/path` | `AZURE_ACCOUNT_NAME`, `AZURE_ACCOUNT_KEY` |
| AWS S3 | `s3:s3.amazonaws.com/bucket` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` |
| Backblaze B2 | `b2:bucket:/path` | `B2_ACCOUNT_ID`, `B2_ACCOUNT_KEY` |
| SFTP | `sftp:user@host:/path` | SSH key or `RESTIC_PASSWORD` |
| Local | `/path/to/backup` | - |
| REST Server | `rest:http://host:8000/` | - |

See [restic documentation](https://restic.readthedocs.io/en/latest/030_preparing_a_new_repo.html) for all options.

### Environment Variables

#### Required

| Variable | Description |
|----------|-------------|
| `RESTIC_REPOSITORY` | Restic repository URL |
| `RESTIC_PASSWORD` | Restic encryption password |

Plus backend-specific variables (see table above).

#### Paths & Timing

| Variable | Default | Description |
|----------|---------|-------------|
| `VAULT_PATH` | `/vault` | Path to Obsidian vault |
| `DEBOUNCE_SECONDS` | `300` | Wait time after last change (5 min) |
| `HEALTH_PORT` | `8080` | Health endpoint port |

#### Git

| Variable | Default | Description |
|----------|---------|-------------|
| `GIT_USER_NAME` | `Obsidian Backup` | Commit author name |
| `GIT_USER_EMAIL` | `backup@local` | Commit author email |

#### AI Commit Messages

Uses Claude Haiku 4.5 to generate meaningful commit messages from changed filenames.

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | - | Anthropic API key |
| `ANTHROPIC_MODEL` | `claude-haiku-4-5-20251001` | Model to use |

Or use any OpenAI-compatible API:

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_API_URL` | - | API endpoint URL |
| `LLM_API_KEY` | - | API key |
| `LLM_MODEL` | `anthropic/claude-haiku-4.5` | Model identifier |

#### Retention Policy

| Variable | Default | Description |
|----------|---------|-------------|
| `RETENTION_DAILY` | `7` | Daily snapshots to keep |
| `RETENTION_WEEKLY` | `4` | Weekly snapshots to keep |
| `RETENTION_MONTHLY` | `12` | Monthly snapshots to keep |

#### Notifications

| Variable | Default | Description |
|----------|---------|-------------|
| `NOTIFY_LEVEL` | `all` | `all`, `errors`, `success`, or `none` |
| `DISCORD_WEBHOOK_URL` | - | Discord webhook |
| `SLACK_WEBHOOK_URL` | - | Slack incoming webhook |
| `WEBHOOK_URL` | - | Generic webhook (JSON POST) |

#### Feature Flags

| Variable | Default | Description |
|----------|---------|-------------|
| `DRY_RUN` | `false` | Test mode - no commits or backups |

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

**Status values:**

- `healthy` - Everything working normally
- `unhealthy` - Changes pending but no backup in 24+ hours

## Notifications

### Discord

Create a webhook in Discord (Server Settings → Integrations → Webhooks):

```yaml
DISCORD_WEBHOOK_URL: https://discord.com/api/webhooks/123456/abcdef
```

### Slack

Create an Incoming Webhook in Slack:

```yaml
SLACK_WEBHOOK_URL: https://hooks.slack.com/services/T.../B.../...
```

### Generic Webhook

For Ntfy, Gotify, Home Assistant, n8n, or any HTTP endpoint:

```yaml
WEBHOOK_URL: https://your-service.com/webhook
```

Payload format:

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
docker exec vault-backup restic snapshots
```

Restore the latest snapshot:

```bash
docker exec vault-backup restic restore latest --target /restore
```

Restore a specific snapshot:

```bash
docker exec vault-backup restic restore abc123 --target /restore
```

Mount snapshots as a filesystem (for browsing):

```bash
docker exec -it vault-backup restic mount /mnt
```

## Troubleshooting

### "Restic repository not initialized"

Run `restic init` after first start:

```bash
docker exec vault-backup restic init
```

### "Vault directory is not writable"

The container needs write access to create git commits. Remove `:ro` from your volume mount:

```yaml
volumes:
  - /path/to/vault:/vault  # Not /path/to/vault:/vault:ro
```

### No backups happening

1. Check if debounce period elapsed (default 5 minutes)
2. Verify health endpoint: `curl http://localhost:8080/health`
3. Check container logs: `docker logs vault-backup`

### AI commit messages not working

1. Verify `ANTHROPIC_API_KEY` is set correctly
2. Check logs for API errors
3. Fallback timestamp messages will be used if AI fails

### Notifications not sending

1. Verify webhook URL is correct
2. Check `NOTIFY_LEVEL` setting
3. Test webhook manually with curl

## Development

```bash
# Install dependencies
pip install -e ".[dev]"

# Run locally
VAULT_PATH=/path/to/vault python -m vault_backup

# Run tests
pytest

# Lint
ruff check src/
```

## License

[PolyForm Noncommercial 1.0.0](LICENSE). Commercial use requires a separate license — [get in touch](https://github.com/cameronsjo)
