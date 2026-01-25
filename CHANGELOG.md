# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-01-25

### Added

- Complete rewrite in Python (from Bash)
- Configurable retention policy via `RETENTION_DAILY`, `RETENTION_WEEKLY`, `RETENTION_MONTHLY`
- Notification level filtering via `NOTIFY_LEVEL` (all, errors, success, none)
- Slack webhook notifications via `SLACK_WEBHOOK_URL`
- Generic webhook notifications via `WEBHOOK_URL` (for Ntfy, Gotify, etc.)
- Dry run mode via `DRY_RUN=true`
- Architecture diagram in README
- Troubleshooting guide in README
- Storage backend documentation (Azure, S3, B2, SFTP, etc.)
- CONTRIBUTING.md with development guide

### Changed

- Default AI model updated to Claude Haiku 4.5 (`claude-haiku-4-5-latest`)
- File watching now uses Python watchdog instead of inotify-tools
- Health server now uses Python stdlib instead of busybox httpd
- Simplified Dockerfile with multi-stage build

### Removed

- Bash scripts (entrypoint.sh, backup.sh, watcher.sh, health-server.sh)
- inotify-tools dependency
- jq dependency
- busybox-extras dependency

## [0.1.0] - 2026-01-25

### Added

- Initial release
- File watching with inotify and debounce
- Git auto-commits with optional AI messages (Claude 3.5 Haiku)
- Restic backup to Azure Blob Storage
- Health endpoint at `/health`
- Docker container with multi-arch support (amd64, arm64)
- GitHub Actions CI/CD with attestations
