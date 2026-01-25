# Contributing

Thanks for your interest in contributing to Obsidian Vault Backup!

## Development Setup

1. Clone the repository:

   ```bash
   git clone https://github.com/cameronsjo/obsidian-vault-backup.git
   cd obsidian-vault-backup
   ```

2. Install Python 3.12+ and dependencies:

   ```bash
   pip install -e ".[dev]"
   ```

3. Run locally:

   ```bash
   export VAULT_PATH=/path/to/test/vault
   export RESTIC_REPOSITORY=/tmp/restic-test
   export RESTIC_PASSWORD=testpassword
   python -m vault_backup
   ```

## Code Style

- **Python 3.12+** with type hints
- **Ruff** for linting and formatting
- **Conventional Commits** for commit messages

Run linting:

```bash
ruff check src/
ruff format src/
```

## Testing

```bash
pytest
```

## Pull Requests

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run linting and tests
5. Commit with a descriptive message
6. Push to your fork
7. Open a Pull Request

## Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` - New feature
- `fix:` - Bug fix
- `docs:` - Documentation only
- `refactor:` - Code change that neither fixes a bug nor adds a feature
- `test:` - Adding or updating tests
- `chore:` - Maintenance tasks

Examples:

```text
feat: add pushover notification support
fix: handle empty vault on first run
docs: add S3 backend example
```

## Architecture

```text
src/vault_backup/
├── __init__.py      # Package version
├── __main__.py      # Entry point, orchestration
├── config.py        # Configuration from env vars
├── backup.py        # Git and restic operations
├── health.py        # HTTP health server
├── notify.py        # Notification providers
└── watcher.py       # File watching with debounce
```

## Adding a Notification Provider

1. Add a new class in `notify.py` implementing `NotificationProvider`
2. Add configuration to `NotifyConfig` in `config.py`
3. Register the provider in `Notifier.__init__`
4. Update README with the new env var
5. Add tests

Example:

```python
class PushoverWebhook(NotificationProvider):
    def __init__(self, user_key: str, api_token: str) -> None:
        self.user_key = user_key
        self.api_token = api_token

    def send(self, title: str, message: str, *, is_error: bool = False) -> bool:
        # Implementation here
        ...
```

## Questions?

Open an issue on GitHub!
