#!/usr/bin/env python3
"""Launch the restore/browse UI with mock data for local development.

No git, restic, vault, or env vars required. Just:

    uv run python scripts/dev_ui.py

Then open http://localhost:8080/ui
"""

from __future__ import annotations

import logging
import sys
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import patch

# --- Sample data ---

SAMPLE_COMMITS = [
    {"hash": "a" * 40, "short_hash": "a1b2c3d", "date": "2026-02-09T08:30:00-06:00",
     "message": "vault backup: 3 files changed"},
    {"hash": "b" * 40, "short_hash": "e4f5a6b", "date": "2026-02-08T22:15:00-06:00",
     "message": "vault backup: update daily notes"},
    {"hash": "c" * 40, "short_hash": "c7d8e9f", "date": "2026-02-08T14:00:00-06:00",
     "message": "vault backup: new project notes and templates"},
    {"hash": "d" * 40, "short_hash": "0a1b2c3", "date": "2026-02-07T19:45:00-06:00",
     "message": "vault backup: reorganize folder structure"},
    {"hash": "e" * 40, "short_hash": "d4e5f6a", "date": "2026-02-07T10:20:00-06:00",
     "message": "vault backup: add reading list and book notes"},
    {"hash": "f" * 40, "short_hash": "7b8c9d0", "date": "2026-02-06T16:30:00-06:00",
     "message": "vault backup: meeting notes and action items"},
]

SAMPLE_SNAPSHOTS = [
    {"id": "abcdef12" * 8, "short_id": "abcdef12",
     "time": "2026-02-09T08:35:00Z", "paths": ["/vault"], "tags": ["obsidian"]},
    {"id": "12345678" * 8, "short_id": "12345678",
     "time": "2026-02-08T22:20:00Z", "paths": ["/vault"], "tags": ["obsidian"]},
    {"id": "deadbeef" * 8, "short_id": "deadbeef",
     "time": "2026-02-07T19:50:00Z", "paths": ["/vault"], "tags": ["obsidian"]},
    {"id": "cafebabe" * 8, "short_id": "cafebabe",
     "time": "2026-02-06T16:35:00Z", "paths": ["/vault"], "tags": ["obsidian"]},
]

SAMPLE_FILES = [
    {"path": "/vault/Daily Notes", "type": "dir", "size": 0, "mtime": ""},
    {"path": "/vault/Daily Notes/2026-02-09.md", "type": "file", "size": 2847, "mtime": "2026-02-09T08:30:00Z"},
    {"path": "/vault/Daily Notes/2026-02-08.md", "type": "file", "size": 1523, "mtime": "2026-02-08T22:15:00Z"},
    {"path": "/vault/Projects", "type": "dir", "size": 0, "mtime": ""},
    {"path": "/vault/Projects/Homelab.md", "type": "file", "size": 8192, "mtime": "2026-02-08T14:00:00Z"},
    {"path": "/vault/Projects/Obsidian Backup.md", "type": "file", "size": 4096, "mtime": "2026-02-07T19:45:00Z"},
    {"path": "/vault/Templates", "type": "dir", "size": 0, "mtime": ""},
    {"path": "/vault/Templates/Daily Note.md", "type": "file", "size": 512, "mtime": "2026-01-15T10:00:00Z"},
    {"path": "/vault/Reading List.md", "type": "file", "size": 3200, "mtime": "2026-02-07T10:20:00Z"},
    {"path": "/vault/.obsidian/app.json", "type": "file", "size": 245, "mtime": "2026-02-01T12:00:00Z"},
]

SAMPLE_FILE_CONTENT = """\
# Daily Note - 2026-02-09

## Tasks
- [x] Review vault backup UI
- [ ] Deploy new container image
- [ ] Update homelab documentation

## Notes
Working on the restore/browse web UI for the vault backup sidecar.
The htmx approach keeps things lightweight — no build step, no JS framework,
just HTML fragments swapped in by the server.

## Links
- [[Projects/Obsidian Backup]]
- [[Projects/Homelab]]

## Journal
Pretty productive day. Got the UI working with mock data so we can
actually see what it looks like without needing a full vault + restic setup.
"""


# --- Mock functions ---

def _mock_git_log(_vault_path: Path, _count: int = 20) -> list:
    from vault_backup.restore import GitCommit
    return [GitCommit(**c) for c in SAMPLE_COMMITS]


def _mock_git_file_history(_vault_path: Path, _filepath: str, _count: int = 10) -> list:
    from vault_backup.restore import GitCommit
    return [GitCommit(**c) for c in SAMPLE_COMMITS[:3]]


def _mock_git_show_file(_vault_path: Path, _commit: str, _filepath: str) -> str:
    return SAMPLE_FILE_CONTENT


def _mock_git_restore_file(
    _vault_path: Path, _commit: str, _filepath: str, target: Path,
) -> Path:
    return target


def _mock_restic_snapshots(_tag: str = "obsidian") -> list:
    from vault_backup.restore import ResticSnapshot
    return [ResticSnapshot(**s) for s in SAMPLE_SNAPSHOTS]


def _mock_restic_ls(_snapshot_id: str, _path: str = "/") -> list:
    from vault_backup.restore import ResticEntry
    return [ResticEntry(**f) for f in SAMPLE_FILES]


def _mock_git_log_single(_vault_path: Path, _commit: str) -> list:
    from vault_backup.restore import GitCommit
    # Find matching commit or return first one
    for c in SAMPLE_COMMITS:
        if c["short_hash"] == _commit:
            return [GitCommit(**c)]
    return [GitCommit(**SAMPLE_COMMITS[0])]


def _mock_git_diff_tree(_vault_path: Path, _commit: str) -> list:
    from vault_backup.restore import GitFileChange
    return [
        GitFileChange(path="Daily Notes/2026-02-09.md", status="M"),
        GitFileChange(path="Projects/Homelab.md", status="M"),
        GitFileChange(path="Reading List.md", status="A"),
    ]


def _mock_restic_show_file(_snapshot_id: str, _filepath: str) -> str:
    return SAMPLE_FILE_CONTENT


def _mock_restic_restore_file(_snapshot_id: str, _filepath: str, target: Path) -> Path:
    return target


# --- Main ---

def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080

    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    log = logging.getLogger("dev_ui")

    # Set up health state so RestoreHandler can read vault_path
    import vault_backup.health as health_mod
    from vault_backup.config import Config
    from vault_backup.health import HealthState
    from vault_backup.ui import RestoreHandler

    config = Config(vault_path="/vault", state_dir="/tmp/vault-state")
    health_mod._health_state = HealthState(config=config)

    patches = {
        "vault_backup.ui.git_log": _mock_git_log,
        "vault_backup.ui.git_file_history": _mock_git_file_history,
        "vault_backup.ui.git_show_file": _mock_git_show_file,
        "vault_backup.ui.git_restore_file": _mock_git_restore_file,
        "vault_backup.ui.restic_snapshots": _mock_restic_snapshots,
        "vault_backup.ui.git_log_single": _mock_git_log_single,
        "vault_backup.ui.git_diff_tree": _mock_git_diff_tree,
        "vault_backup.ui.restic_ls": _mock_restic_ls,
        "vault_backup.ui.restic_show_file": _mock_restic_show_file,
        "vault_backup.ui.restic_restore_file": _mock_restic_restore_file,
    }

    with patch.multiple("vault_backup.ui", **{k.split(".")[-1]: v for k, v in patches.items()}):
        server = HTTPServer(("127.0.0.1", port), RestoreHandler)
        log.info("Dev UI server running at http://127.0.0.1:%d/ui", port)
        log.info("Press Ctrl+C to stop")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            log.info("Shutting down")
            server.shutdown()


if __name__ == "__main__":
    main()
