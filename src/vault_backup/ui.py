"""Web UI for browsing and restoring from git and restic backups."""

from __future__ import annotations

import html
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from vault_backup import health as _health_mod
from vault_backup.health import HealthHandler
from vault_backup.restore import (
    GitCommit,
    ResticEntry,
    ResticSnapshot,
    detect_source,
    git_file_history,
    git_log,
    git_restore_file,
    git_show_file,
    restic_ls,
    restic_restore_file,
    restic_show_file,
    restic_snapshots,
)

log = logging.getLogger(__name__)

# htmx 2.0.4 minified — bundled for offline container use
_HTMX_JS = (Path(__file__).parent / "htmx.min.js").read_text()

# --- CSS ---

_PAGE_CSS = """\
:root {
    --bg: #1a1b26;
    --surface: #24283b;
    --text: #a9b1d6;
    --accent: #7aa2f7;
    --border: #414868;
    --success: #9ece6a;
    --error: #f7768e;
    --muted: #565f89;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    background: var(--bg); color: var(--text);
    font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
    padding: 1.5rem; max-width: 1200px; margin: 0 auto;
}
h1 { color: var(--accent); margin-bottom: 1rem; font-size: 1.4rem; }
h3 { color: var(--accent); margin-bottom: 0.75rem; font-size: 1.1rem; }
.tabs {
    display: flex; gap: 0.5rem; margin-bottom: 1rem;
    border-bottom: 1px solid var(--border); padding-bottom: 0.5rem;
}
.tab {
    background: transparent; color: var(--muted);
    border: 1px solid transparent; padding: 0.5rem 1rem;
    cursor: pointer; font-family: inherit; font-size: 0.9rem;
    border-radius: 4px 4px 0 0;
}
.tab:hover, .tab.active { color: var(--accent); border-color: var(--border); }
.tab.active { background: var(--surface); border-bottom-color: var(--surface); }
table {
    width: 100%; border-collapse: collapse;
    background: var(--surface); border-radius: 6px; overflow: hidden;
}
th, td {
    padding: 0.5rem 0.75rem; text-align: left;
    border-bottom: 1px solid var(--border); font-size: 0.85rem;
}
th { color: var(--muted); font-weight: 600; text-transform: uppercase; font-size: 0.75rem; }
tr.clickable { cursor: pointer; }
tr.clickable:hover { background: rgba(122, 162, 247, 0.1); }
.empty { color: var(--muted); padding: 2rem; text-align: center; }
pre {
    background: var(--surface); padding: 1rem; border-radius: 6px;
    overflow-x: auto; font-size: 0.85rem; line-height: 1.5;
    max-height: 500px; overflow-y: auto;
}
.actions { display: flex; gap: 0.5rem; margin-top: 0.75rem; }
.btn {
    display: inline-block; background: var(--accent); color: var(--bg);
    border: none; padding: 0.5rem 1rem; border-radius: 4px;
    cursor: pointer; font-family: inherit; font-size: 0.85rem;
    font-weight: 600; text-decoration: none;
}
.btn:hover { opacity: 0.9; }
.btn-danger { background: var(--error); }
.success { color: var(--success); padding: 1rem; }
.error { color: var(--error); padding: 1rem; }
input[type="text"] {
    background: var(--surface); color: var(--text);
    border: 1px solid var(--border); padding: 0.5rem 0.75rem;
    border-radius: 4px; font-family: inherit; font-size: 0.85rem;
    width: 100%; max-width: 400px; margin-bottom: 0.75rem;
}
input[type="text"]::placeholder { color: var(--muted); }
input[type="text"]:focus { outline: none; border-color: var(--accent); }
#preview { margin-top: 1.5rem; }
.breadcrumb { color: var(--muted); margin-bottom: 0.75rem; font-size: 0.85rem; }
.breadcrumb a { color: var(--accent); text-decoration: none; cursor: pointer; }
.breadcrumb a:hover { text-decoration: underline; }
"""

# --- Tab switching JS (no user content, static only) ---

_TAB_JS = """\
function switchTab(el) {
    document.querySelectorAll('.tab').forEach(function(t) {
        t.classList.remove('active');
    });
    el.classList.add('active');
    var preview = document.getElementById('preview');
    while (preview.firstChild) { preview.removeChild(preview.firstChild); }
}
"""

# --- Helpers ---


def _format_time(iso_str: str) -> str:
    """Format ISO timestamp for display."""
    if not iso_str:
        return ""
    try:
        clean = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return iso_str


def _format_size(size: int) -> str:
    """Format byte size for display."""
    if size == 0:
        return "-"
    s = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if s < 1024:
            return f"{s:,.0f} {unit}" if unit == "B" else f"{s:,.1f} {unit}"
        s /= 1024
    return f"{s:,.1f} TB"


def _param(params: dict[str, list[str]], key: str) -> str:
    """Extract single query parameter value."""
    values = params.get(key, [])
    return values[0] if values else ""


# --- HTML Builders ---


def _page_html() -> str:
    """Full HTML page with tabs and htmx."""
    return (
        "<!DOCTYPE html><html lang='en'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Vault Backup</title>"
        f"<style>{_PAGE_CSS}</style>"
        f"<script>{_HTMX_JS}</script>"
        f"<script>{_TAB_JS}</script>"
        "</head><body>"
        "<h1>Vault Backup</h1>"
        "<nav class='tabs'>"
        "<button class='tab active' id='tab-git'"
        " hx-get='/ui/log' hx-target='#content'"
        " onclick='switchTab(this)'"
        ">Git History</button>"
        "<button class='tab' id='tab-restic'"
        " hx-get='/ui/snapshots' hx-target='#content'"
        " onclick='switchTab(this)'"
        ">Snapshots</button>"
        "</nav>"
        "<div id='content' hx-get='/ui/log' hx-trigger='load' hx-target='this'></div>"
        "<div id='preview'></div>"
        "</body></html>"
    )


def _render_snapshots(snapshots: list[ResticSnapshot]) -> str:
    """Render restic snapshots table fragment."""
    if not snapshots:
        return '<div class="empty">No snapshots found.</div>'
    rows = ""
    for s in snapshots:
        sid = html.escape(s.short_id)
        time_str = html.escape(_format_time(s.time))
        paths = html.escape(", ".join(s.paths))
        tags = html.escape(", ".join(s.tags))
        rows += (
            f'<tr class="clickable" hx-get="/ui/files?snapshot={sid}" hx-target="#content">'
            f"<td><code>{sid}</code></td><td>{time_str}</td>"
            f"<td>{paths}</td><td>{tags}</td></tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>ID</th><th>Time</th><th>Paths</th><th>Tags</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table>"
    )


def _render_files(entries: list[ResticEntry], snapshot_id: str) -> str:
    """Render file listing fragment for a restic snapshot."""
    sid = html.escape(snapshot_id)
    breadcrumb = (
        '<div class="breadcrumb">'
        '<a hx-get="/ui/snapshots" hx-target="#content">Snapshots</a>'
        f" / {sid}</div>"
    )
    if not entries:
        return breadcrumb + '<div class="empty">No files found.</div>'

    rows = ""
    for e in entries:
        path = html.escape(e.path)
        type_str = html.escape(e.type)
        size = _format_size(e.size) if e.type == "file" else "-"
        mtime = html.escape(_format_time(e.mtime))
        if e.type == "file":
            rows += (
                f'<tr class="clickable" '
                f'hx-get="/ui/preview?source={sid}&path={html.escape(e.path)}" '
                f'hx-target="#preview">'
                f"<td><code>{path}</code></td><td>{type_str}</td>"
                f"<td>{size}</td><td>{mtime}</td></tr>"
            )
        else:
            rows += (
                f"<tr><td><code>{path}</code></td><td>{type_str}</td>"
                f"<td>{size}</td><td>{mtime}</td></tr>"
            )
    return (
        breadcrumb
        + "<table><thead><tr>"
        "<th>Path</th><th>Type</th><th>Size</th><th>Modified</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table>"
    )


def _render_log(commits: list[GitCommit], file_path: str = "") -> str:
    """Render git log table fragment with optional file filter."""
    file_val = html.escape(file_path)
    filter_input = (
        f'<input type="text" name="file" value="{file_val}" '
        'placeholder="Filter by file path (e.g. notes/daily.md)..." '
        'hx-get="/ui/log" hx-target="#content" '
        'hx-trigger="keyup changed delay:500ms" hx-include="this">'
    )
    if not commits:
        return filter_input + '<div class="empty">No commits found.</div>'

    rows = ""
    for c in commits:
        short = html.escape(c.short_hash)
        date = html.escape(_format_time(c.date))
        msg = html.escape(c.message)
        if file_path:
            fp = html.escape(file_path)
            rows += (
                f'<tr class="clickable" '
                f'hx-get="/ui/preview?source={short}&path={fp}" hx-target="#preview">'
                f"<td><code>{short}</code></td><td>{date}</td><td>{msg}</td></tr>"
            )
        else:
            rows += f"<tr><td><code>{short}</code></td><td>{date}</td><td>{msg}</td></tr>"
    return (
        filter_input
        + "<table><thead><tr>"
        "<th>Commit</th><th>Date</th><th>Message</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table>"
    )


def _render_preview(content: str, source: str, path: str) -> str:
    """Render file preview fragment with download and restore actions."""
    esc_content = html.escape(content)
    esc_source = html.escape(source)
    esc_path = html.escape(path)
    filename = html.escape(Path(path).name)
    return (
        f"<h3>{esc_path}</h3>"
        f'<div class="breadcrumb">Source: <code>{esc_source}</code></div>'
        f"<pre>{esc_content}</pre>"
        f'<div class="actions">'
        f'<a class="btn" href="/ui/download?source={esc_source}&path={esc_path}">Download</a>'
        f'<form style="display:inline" hx-post="/ui/restore" hx-target="#preview"'
        f' hx-confirm="Restore {filename} to its original location in the vault?">'
        f'<input type="hidden" name="source" value="{esc_source}">'
        f'<input type="hidden" name="path" value="{esc_path}">'
        f'<button type="submit" class="btn btn-danger">Restore in place</button>'
        f"</form></div>"
    )


def _render_restore_result(target: Path, source_type: str) -> str:
    """Render restore success fragment."""
    return (
        f'<div class="success">'
        f"Restored from {html.escape(source_type)} to "
        f"<code>{html.escape(str(target))}</code></div>"
    )


def _render_error(message: str) -> str:
    """Render error fragment."""
    return f'<div class="error">{html.escape(message)}</div>'


# --- Handler ---


class RestoreHandler(HealthHandler):
    """HTTP handler with restore UI routes, extending HealthHandler."""

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler convention
        """Route GET requests to UI or health endpoints."""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        if path == "/ui":
            self._send_html(_page_html())
        elif path.startswith("/ui/"):
            self._route_ui_get(path, params)
        else:
            super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler convention
        """Route POST requests for restore actions."""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/ui/restore":
            self._handle_restore()
        else:
            self._send_html(_render_error("Not found"), code=404)

    def _route_ui_get(self, path: str, params: dict[str, list[str]]) -> None:
        """Dispatch UI GET routes."""
        try:
            if path == "/ui/snapshots":
                self._handle_snapshots()
            elif path == "/ui/files":
                self._handle_files(params)
            elif path == "/ui/log":
                self._handle_log(params)
            elif path == "/ui/preview":
                self._handle_preview(params)
            elif path == "/ui/download":
                self._handle_download(params)
            else:
                self._send_html(_render_error("Not found"), code=404)
        except Exception:
            log.exception("UI request failed", extra={"path": path})
            self._send_html(_render_error("Internal server error"), code=500)

    def _handle_snapshots(self) -> None:
        snaps = restic_snapshots()
        self._send_html(_render_snapshots(snaps))

    def _handle_files(self, params: dict[str, list[str]]) -> None:
        snapshot_id = _param(params, "snapshot")
        if not snapshot_id:
            self._send_html(_render_error("Missing snapshot parameter"), code=400)
            return
        try:
            entries = restic_ls(snapshot_id)
        except ValueError as e:
            self._send_html(_render_error(str(e)), code=404)
            return
        self._send_html(_render_files(entries, snapshot_id))

    def _handle_log(self, params: dict[str, list[str]]) -> None:
        vault_path = self._get_vault_path()
        if vault_path is None:
            self._send_html(_render_error("Health state not initialized"), code=500)
            return
        file_path = _param(params, "file")
        commits = git_file_history(vault_path, file_path) if file_path else git_log(vault_path)
        self._send_html(_render_log(commits, file_path))

    def _handle_preview(self, params: dict[str, list[str]]) -> None:
        source = _param(params, "source")
        path = _param(params, "path")
        if not source or not path:
            self._send_html(_render_error("Missing source or path parameter"), code=400)
            return
        try:
            content = self._get_file_content(source, path)
        except FileNotFoundError:
            self._send_html(_render_error(f"File not found: {path}"), code=404)
            return
        self._send_html(_render_preview(content, source, path))

    def _handle_download(self, params: dict[str, list[str]]) -> None:
        source = _param(params, "source")
        path = _param(params, "path")
        if not source or not path:
            self._send_html(_render_error("Missing source or path parameter"), code=400)
            return
        try:
            content = self._get_file_content(source, path)
        except FileNotFoundError:
            self._send_html(_render_error(f"File not found: {path}"), code=404)
            return
        self._send_download(content, Path(path).name)

    def _handle_restore(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode()
        params = parse_qs(body)

        source = _param(params, "source")
        path = _param(params, "path")
        if not source or not path:
            self._send_html(_render_error("Missing source or path parameter"), code=400)
            return

        vault_path = self._get_vault_path()
        if vault_path is None:
            self._send_html(_render_error("Vault path not configured"), code=500)
            return

        target = self._resolve_restore_target(vault_path, path)
        if target is None:
            self._send_html(_render_error("Invalid restore path"), code=400)
            return

        source_type = detect_source(source)
        try:
            if source_type == "git":
                git_restore_file(vault_path, source, path, target)
                self._send_html(_render_restore_result(target, "git commit"))
            elif source_type == "restic":
                restic_restore_file(source, path, target)
                self._send_html(_render_restore_result(target, "restic snapshot"))
            else:
                try:
                    git_restore_file(vault_path, source, path, target)
                    self._send_html(_render_restore_result(target, "git commit"))
                except FileNotFoundError:
                    restic_restore_file(source, path, target)
                    self._send_html(_render_restore_result(target, "restic snapshot"))
        except FileNotFoundError as e:
            self._send_html(_render_error(str(e)), code=404)

    def _get_file_content(self, source: str, path: str) -> str:
        """Fetch file content from git or restic based on source type."""
        vault_path = self._get_vault_path()
        source_type = detect_source(source)

        if source_type == "git":
            if vault_path is None:
                msg = "Vault path not configured"
                raise FileNotFoundError(msg)
            return git_show_file(vault_path, source, path)

        if source_type == "restic":
            return restic_show_file(source, path)

        # Ambiguous — try git first, fall back to restic
        if vault_path:
            try:
                return git_show_file(vault_path, source, path)
            except FileNotFoundError:
                pass
        return restic_show_file(source, path)

    @staticmethod
    def _resolve_restore_target(vault_path: Path, file_path: str) -> Path | None:
        """Resolve restore target, ensuring it stays within the vault."""
        target = Path(file_path) if file_path.startswith("/") else vault_path / file_path
        target = target.resolve()
        if not str(target).startswith(str(vault_path.resolve())):
            return None
        return target

    def _get_vault_path(self) -> Path | None:
        """Read vault path from health state (thread-safe)."""
        with _health_mod._health_state_lock:
            state = _health_mod._health_state
        if state is None:
            return None
        return Path(state.config.vault_path)

    def _send_html(self, content: str, code: int = 200) -> None:
        """Send HTML response."""
        body = content.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_download(self, content: str, filename: str) -> None:
        """Send file download response."""
        body = content.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
