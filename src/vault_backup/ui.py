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
    GitFileChange,
    ResticEntry,
    ResticSnapshot,
    detect_source,
    git_diff_file,
    git_diff_tree,
    git_file_history,
    git_log,
    git_log_single,
    git_restore_file,
    git_show_file,
    group_entries_by_directory,
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
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600&family=Iosevka+Web:wght@400;500&display=swap');
:root {
    --bg: #0d1117;
    --surface: #161b22;
    --surface-raised: #1c2129;
    --text: #c9d1d9;
    --text-bright: #e6edf3;
    --accent: #4493f8;
    --accent-dim: rgba(68, 147, 248, 0.15);
    --accent-glow: rgba(68, 147, 248, 0.08);
    --border: #21262d;
    --border-accent: #30363d;
    --success: #3fb950;
    --error: #f85149;
    --warning: #d29922;
    --muted: #484f58;
    --muted-text: #8b949e;
    --diff-add-bg: rgba(63, 185, 80, 0.15);
    --diff-add-text: #3fb950;
    --diff-del-bg: rgba(248, 81, 73, 0.15);
    --diff-del-text: #f85149;
    --diff-hunk-bg: rgba(68, 147, 248, 0.1);
    --diff-hunk-text: #8b949e;
    --diff-meta-text: #8b949e;
    --font-mono: 'Iosevka Web', 'JetBrains Mono', 'Fira Code', monospace;
    --font-sans: 'DM Sans', -apple-system, sans-serif;
}
@media (prefers-color-scheme: light) {
    :root:not([data-theme="dark"]) {
        --bg: #ffffff;
        --surface: #f6f8fa;
        --surface-raised: #eef1f5;
        --text: #1f2328;
        --text-bright: #0d1117;
        --accent: #0969da;
        --accent-dim: rgba(9, 105, 218, 0.1);
        --accent-glow: rgba(9, 105, 218, 0.06);
        --border: #d1d9e0;
        --border-accent: #b1bac4;
        --success: #1a7f37;
        --error: #d1242f;
        --warning: #9a6700;
        --muted: #b1bac4;
        --muted-text: #656d76;
        --diff-add-bg: rgba(26, 127, 55, 0.1);
        --diff-add-text: #1a7f37;
        --diff-del-bg: rgba(209, 36, 47, 0.1);
        --diff-del-text: #d1242f;
        --diff-hunk-bg: rgba(9, 105, 218, 0.08);
        --diff-hunk-text: #656d76;
        --diff-meta-text: #656d76;
    }
}
[data-theme="light"] {
    --bg: #ffffff;
    --surface: #f6f8fa;
    --surface-raised: #eef1f5;
    --text: #1f2328;
    --text-bright: #0d1117;
    --accent: #0969da;
    --accent-dim: rgba(9, 105, 218, 0.1);
    --accent-glow: rgba(9, 105, 218, 0.06);
    --border: #d1d9e0;
    --border-accent: #b1bac4;
    --success: #1a7f37;
    --error: #d1242f;
    --warning: #9a6700;
    --muted: #b1bac4;
    --muted-text: #656d76;
    --diff-add-bg: rgba(26, 127, 55, 0.1);
    --diff-add-text: #1a7f37;
    --diff-del-bg: rgba(209, 36, 47, 0.1);
    --diff-del-text: #d1242f;
    --diff-hunk-bg: rgba(9, 105, 218, 0.08);
    --diff-hunk-text: #656d76;
    --diff-meta-text: #656d76;
}
[data-theme="dark"] {
    --bg: #0d1117;
    --surface: #161b22;
    --surface-raised: #1c2129;
    --text: #c9d1d9;
    --text-bright: #e6edf3;
    --accent: #4493f8;
    --accent-dim: rgba(68, 147, 248, 0.15);
    --accent-glow: rgba(68, 147, 248, 0.08);
    --border: #21262d;
    --border-accent: #30363d;
    --success: #3fb950;
    --error: #f85149;
    --warning: #d29922;
    --muted: #484f58;
    --muted-text: #8b949e;
    --diff-add-bg: rgba(63, 185, 80, 0.15);
    --diff-add-text: #3fb950;
    --diff-del-bg: rgba(248, 81, 73, 0.15);
    --diff-del-text: #f85149;
    --diff-hunk-bg: rgba(68, 147, 248, 0.1);
    --diff-hunk-text: #8b949e;
    --diff-meta-text: #8b949e;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    background: var(--bg); color: var(--text);
    font-family: var(--font-mono);
    padding: 2rem 2.5rem; max-width: 1200px; margin: 0 auto;
    min-height: 100vh;
}
.header {
    display: flex; align-items: center; gap: 0.75rem;
    margin-bottom: 1.75rem; padding-bottom: 1.25rem;
    border-bottom: 1px solid var(--border);
}
.header-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--accent);
    box-shadow: 0 0 8px var(--accent), 0 0 16px var(--accent-dim);
    animation: pulse 3s ease-in-out infinite;
}
@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
}
h1 {
    color: var(--text-bright); font-size: 1.1rem;
    font-family: var(--font-sans); font-weight: 600;
    letter-spacing: 0.02em;
}
h1 span { color: var(--muted-text); font-weight: 400; }
h3 {
    color: var(--text-bright); margin-bottom: 0.75rem;
    font-size: 0.95rem; font-family: var(--font-mono);
    font-weight: 500;
}
.tabs {
    display: flex; gap: 0; margin-bottom: 1.5rem;
    border-bottom: 1px solid var(--border);
    position: relative;
}
.tab {
    background: transparent; color: var(--muted-text);
    border: none; padding: 0.65rem 1.25rem;
    cursor: pointer; font-family: var(--font-sans);
    font-size: 0.85rem; font-weight: 500;
    position: relative; transition: color 0.2s;
    letter-spacing: 0.01em;
}
.tab::after {
    content: ''; position: absolute;
    bottom: -1px; left: 0; right: 0; height: 2px;
    background: transparent; transition: background 0.2s;
}
.tab:hover { color: var(--text-bright); }
.tab.active { color: var(--accent); }
.tab.active::after { background: var(--accent); }
table {
    width: 100%; border-collapse: separate;
    border-spacing: 0 2px;
}
thead th {
    padding: 0.5rem 1rem; text-align: left;
    font-size: 0.7rem; font-weight: 500;
    color: var(--muted-text); text-transform: uppercase;
    letter-spacing: 0.08em; font-family: var(--font-sans);
    border: none;
}
tbody td {
    padding: 0.65rem 1rem; font-size: 0.82rem;
    background: var(--surface);
    border-top: 1px solid var(--border);
    border-bottom: 1px solid var(--border);
    transition: background 0.15s, border-color 0.15s;
}
tbody td:first-child {
    border-left: 2px solid transparent;
    border-top-left-radius: 4px; border-bottom-left-radius: 4px;
}
tbody td:last-child {
    border-top-right-radius: 4px; border-bottom-right-radius: 4px;
}
tr.clickable { cursor: pointer; }
tr.clickable:hover td {
    background: var(--surface-raised);
    border-color: var(--border-accent);
}
tr.clickable:hover td:first-child {
    border-left-color: var(--accent);
}
.empty {
    color: var(--muted-text); padding: 3rem 2rem;
    text-align: center; font-size: 0.85rem;
    border: 1px dashed var(--border); border-radius: 6px;
    margin-top: 0.5rem;
}
pre {
    background: var(--surface); padding: 1.25rem;
    border-radius: 6px; border: 1px solid var(--border);
    overflow-x: auto; font-size: 0.82rem; line-height: 1.65;
    max-height: 520px; overflow-y: auto;
    color: var(--text-bright);
    font-family: var(--font-mono);
}
pre::-webkit-scrollbar { width: 6px; height: 6px; }
pre::-webkit-scrollbar-track { background: transparent; }
pre::-webkit-scrollbar-thumb { background: var(--border-accent); border-radius: 3px; }
.actions { display: flex; gap: 0.75rem; margin-top: 1rem; }
.btn {
    display: inline-flex; align-items: center; gap: 0.4rem;
    background: transparent; color: var(--accent);
    border: 1px solid var(--accent); padding: 0.5rem 1.1rem;
    border-radius: 4px; cursor: pointer;
    font-family: var(--font-sans); font-size: 0.8rem;
    font-weight: 500; text-decoration: none;
    transition: background 0.15s, color 0.15s;
    letter-spacing: 0.01em;
}
.btn:hover {
    background: var(--accent); color: var(--bg);
}
.btn-danger {
    color: var(--error); border-color: var(--error);
}
.btn-danger:hover {
    background: var(--error); color: #fff;
}
.success {
    color: var(--success); padding: 1rem 1.25rem;
    border-left: 3px solid var(--success);
    background: rgba(86, 211, 100, 0.06);
    border-radius: 0 4px 4px 0;
    font-size: 0.85rem;
}
.error {
    color: var(--error); padding: 1rem 1.25rem;
    border-left: 3px solid var(--error);
    background: rgba(248, 81, 73, 0.06);
    border-radius: 0 4px 4px 0;
    font-size: 0.85rem;
}
input[type="text"] {
    background: var(--surface); color: var(--text);
    border: 1px solid var(--border); padding: 0.55rem 0.9rem;
    border-radius: 4px; font-family: var(--font-mono);
    font-size: 0.82rem; width: 100%; max-width: 420px;
    margin-bottom: 1rem; transition: border-color 0.2s;
}
input[type="text"]::placeholder { color: var(--muted); }
input[type="text"]:focus {
    outline: none; border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-glow);
}
#preview {
    margin-top: 2rem; padding-top: 1.5rem;
    border-top: 1px solid var(--border);
}
#preview:empty { border-top: none; margin-top: 0; padding-top: 0; }
.breadcrumb {
    color: var(--muted-text); margin-bottom: 0.75rem;
    font-size: 0.8rem; font-family: var(--font-sans);
}
.breadcrumb a {
    color: var(--accent); text-decoration: none;
    cursor: pointer; transition: opacity 0.15s;
}
.breadcrumb a:hover { opacity: 0.8; }
code {
    font-family: var(--font-mono); font-size: 0.82rem;
    color: var(--accent); font-weight: 400;
}
.status-added { color: var(--success); }
.status-modified { color: var(--warning); }
.status-deleted { color: var(--error); }
.status-renamed { color: var(--accent); }
.theme-toggle {
    margin-left: auto; background: transparent;
    color: var(--muted-text); border: 1px solid var(--border);
    padding: 0.35rem 0.6rem; border-radius: 4px;
    cursor: pointer; font-size: 0.9rem; line-height: 1;
    transition: color 0.15s, border-color 0.15s;
}
.theme-toggle:hover { color: var(--text-bright); border-color: var(--border-accent); }
.diff-view { white-space: pre; font-family: var(--font-mono); font-size: 0.82rem; line-height: 1.65; }
.diff-add { color: var(--diff-add-text); background: var(--diff-add-bg); display: inline-block; width: 100%; }
.diff-del { color: var(--diff-del-text); background: var(--diff-del-bg); display: inline-block; width: 100%; }
.diff-hunk { color: var(--diff-hunk-text); background: var(--diff-hunk-bg); display: inline-block; width: 100%; }
.diff-meta { color: var(--diff-meta-text); }
.toggle-group {
    display: flex; gap: 0; margin-bottom: 0.75rem;
    border: 1px solid var(--border); border-radius: 4px; width: fit-content;
}
.toggle-btn {
    background: transparent; color: var(--muted-text);
    border: none; padding: 0.4rem 0.9rem;
    cursor: pointer; font-family: var(--font-sans);
    font-size: 0.78rem; font-weight: 500;
    transition: background 0.15s, color 0.15s;
}
.toggle-btn:first-child { border-radius: 3px 0 0 3px; }
.toggle-btn:last-child { border-radius: 0 3px 3px 0; }
.toggle-btn.active { background: var(--accent-dim); color: var(--accent); }
.toggle-btn:hover:not(.active) { color: var(--text-bright); }
.htmx-request { opacity: 0.6; transition: opacity 0.2s; }
.htmx-settling { opacity: 1; transition: opacity 0.15s; }
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

_THEME_JS = """\
(function() {
    var saved = localStorage.getItem('theme');
    if (saved) document.documentElement.setAttribute('data-theme', saved);
})();
function toggleTheme() {
    var el = document.documentElement;
    var current = el.getAttribute('data-theme');
    var icons = {light: '\\u263E', dark: '\\u2600', '': '\\u25D0'};
    var next;
    if (!current) next = 'light';
    else if (current === 'light') next = 'dark';
    else next = '';
    if (next) {
        el.setAttribute('data-theme', next);
        localStorage.setItem('theme', next);
    } else {
        el.removeAttribute('data-theme');
        localStorage.removeItem('theme');
    }
    var btn = document.querySelector('.theme-toggle');
    if (btn) btn.textContent = icons[next] || icons[''];
}
(function() {
    var saved = localStorage.getItem('theme');
    var icons = {light: '\\u263E', dark: '\\u2600', '': '\\u25D0'};
    document.addEventListener('DOMContentLoaded', function() {
        var btn = document.querySelector('.theme-toggle');
        if (btn) btn.textContent = icons[saved || ''];
    });
})();
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


# --- State ---

_restic_ls_cache: dict[str, list[ResticEntry]] = {}

_STATUS_LABELS = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}
_STATUS_CSS = {"A": "status-added", "M": "status-modified", "D": "status-deleted", "R": "status-renamed"}


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
        f"<script>{_THEME_JS}</script>"
        "</head><body>"
        "<div class='header'>"
        "<div class='header-dot'></div>"
        "<h1>Vault Backup <span>/ restore</span></h1>"
        "<button class='theme-toggle' onclick='toggleTheme()' "
        "title='Toggle theme'></button>"
        "</div>"
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
        "<div id='content' hx-get='/ui/log' hx-trigger='load once' hx-target='this'></div>"
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
            f"<td>{paths}</td><td><code>{tags}</code></td></tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>ID</th><th>Time</th><th>Paths</th><th>Tags</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table>"
    )


def _render_files(
    entries: list[ResticEntry],
    snapshot_id: str,
    prefix: str = "/",
    *,
    show_hidden: bool = False,
) -> str:
    """Render file listing fragment for a restic snapshot directory."""
    sid = html.escape(snapshot_id)
    esc_prefix = html.escape(prefix)

    # Build breadcrumb with clickable path segments
    crumbs = ['<a hx-get="/ui/snapshots" hx-target="#content">Snapshots</a>']
    crumbs.append(
        f'<a hx-get="/ui/files?snapshot={sid}" hx-target="#content">{sid}</a>'
    )
    if prefix != "/":
        segments = prefix.strip("/").split("/")
        for i, seg in enumerate(segments):
            seg_path = "/" + "/".join(segments[: i + 1])
            if i < len(segments) - 1:
                crumbs.append(
                    f'<a hx-get="/ui/files?snapshot={sid}'
                    f"&path={html.escape(seg_path)}"
                    f'" hx-target="#content">{html.escape(seg)}</a>'
                )
            else:
                crumbs.append(html.escape(seg))

    breadcrumb = '<div class="breadcrumb">' + " / ".join(crumbs) + "</div>"

    # Dotfile toggle (hide .obsidian, .trash, etc.)
    checked = " checked" if show_hidden else ""
    hidden_param = "&show_hidden=1" if not show_hidden else ""
    toggle = (
        '<label style="display:inline-flex;align-items:center;gap:0.4rem;'
        "font-size:0.78rem;color:var(--muted-text);margin-bottom:0.75rem;"
        'font-family:var(--font-sans)">'
        f'<input type="checkbox"{checked} hx-get="/ui/files?snapshot={sid}'
        f"&path={esc_prefix}{hidden_param}"
        f'" hx-target="#content"> Show dotfiles</label>'
    )

    if not entries:
        return breadcrumb + toggle + '<div class="empty">No files found.</div>'

    rows = ""
    for e in entries:
        name = Path(e.path).name
        if not show_hidden and name.startswith("."):
            continue
        esc_name = html.escape(name)
        size = _format_size(e.size) if e.type == "file" else "-"
        mtime = html.escape(_format_time(e.mtime))

        if e.type == "dir":
            rows += (
                f'<tr class="clickable" '
                f'hx-get="/ui/files?snapshot={sid}'
                f"&path={html.escape(e.path)}"
                f'" hx-target="#content">'
                f"<td><code>{esc_name}/</code></td>"
                f"<td>-</td><td>{mtime}</td></tr>"
            )
        else:
            rows += (
                f'<tr class="clickable" '
                f'hx-get="/ui/preview?source={sid}'
                f"&path={html.escape(e.path)}"
                f'" hx-target="#preview">'
                f"<td><code>{esc_name}</code></td>"
                f"<td>{size}</td><td>{mtime}</td></tr>"
            )

    if not rows:
        return breadcrumb + toggle + '<div class="empty">No visible files.</div>'

    return (
        breadcrumb
        + toggle
        + "<table><thead><tr>"
        "<th>Name</th><th>Size</th><th>Modified</th>"
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
            rows += (
                f'<tr class="clickable" '
                f'hx-get="/ui/commit?hash={short}" hx-target="#content">'
                f"<td><code>{short}</code></td><td>{date}</td><td>{msg}</td></tr>"
            )
    return (
        filter_input
        + "<table><thead><tr>"
        "<th>Commit</th><th>Date</th><th>Message</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table>"
    )


def _render_commit_files(commit: GitCommit, changes: list[GitFileChange]) -> str:
    """Render the list of files changed in a git commit."""
    short = html.escape(commit.short_hash)
    date = html.escape(_format_time(commit.date))
    msg = html.escape(commit.message)

    breadcrumb = (
        '<div class="breadcrumb">'
        '<a hx-get="/ui/log" hx-target="#content">Git History</a>'
        f" / {short}</div>"
    )
    header = (
        f'<div style="margin-bottom:0.75rem">'
        f"<code>{short}</code> &mdash; {date}<br>{msg}</div>"
    )

    if not changes:
        return breadcrumb + header + '<div class="empty">No files changed.</div>'

    rows = ""
    for ch in changes:
        path = html.escape(ch.path)
        status = html.escape(_STATUS_LABELS.get(ch.status, ch.status))
        css_cls = _STATUS_CSS.get(ch.status, "")
        if ch.status == "D":
            rows += (
                f'<tr class="clickable" '
                f'hx-get="/ui/diff?source={short}&path={path}" '
                f'hx-target="#preview">'
                f'<td><code class="{css_cls}">{status}</code></td>'
                f"<td><code>{path}</code></td></tr>"
            )
        else:
            rows += (
                f'<tr class="clickable" '
                f'hx-get="/ui/preview?source={short}&path={path}" '
                f'hx-target="#preview">'
                f'<td><code class="{css_cls}">{status}</code></td>'
                f"<td><code>{path}</code></td></tr>"
            )

    return (
        breadcrumb + header
        + "<table><thead><tr>"
        "<th>Status</th><th>File</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table>"
    )


def _diff_toggle_buttons(source: str, path: str, active: str = "file") -> str:
    """Render 'Show file' / 'Show diff' toggle button pair."""
    esc_source = html.escape(source)
    esc_path = html.escape(path)
    file_cls = "toggle-btn active" if active == "file" else "toggle-btn"
    diff_cls = "toggle-btn active" if active == "diff" else "toggle-btn"
    return (
        '<div class="toggle-group">'
        f'<button class="{file_cls}" '
        f'hx-get="/ui/preview?source={esc_source}&path={esc_path}" '
        f'hx-target="#preview">Show file</button>'
        f'<button class="{diff_cls}" '
        f'hx-get="/ui/diff?source={esc_source}&path={esc_path}" '
        f'hx-target="#preview">Show diff</button>'
        "</div>"
    )


def _render_diff(diff_text: str, source: str, path: str) -> str:
    """Render a unified diff with syntax-highlighted lines."""
    esc_source = html.escape(source)
    esc_path = html.escape(path)
    toggle = _diff_toggle_buttons(source, path, active="diff")

    if not diff_text.strip():
        return (
            f"<h3>{esc_path}</h3>"
            f'<div class="breadcrumb">Source: <code>{esc_source}</code></div>'
            + toggle
            + '<div class="empty">No changes in this file.</div>'
        )

    lines: list[str] = []
    for line in diff_text.split("\n"):
        escaped = html.escape(line)
        if line.startswith("+++") or line.startswith("---"):
            lines.append(f'<span class="diff-meta">{escaped}</span>')
        elif line.startswith("+"):
            lines.append(f'<span class="diff-add">{escaped}</span>')
        elif line.startswith("-"):
            lines.append(f'<span class="diff-del">{escaped}</span>')
        elif line.startswith("@@"):
            lines.append(f'<span class="diff-hunk">{escaped}</span>')
        elif line.startswith("diff ") or line.startswith("index "):
            lines.append(f'<span class="diff-meta">{escaped}</span>')
        else:
            lines.append(escaped)

    diff_html = "\n".join(lines)
    return (
        f"<h3>{esc_path}</h3>"
        f'<div class="breadcrumb">Source: <code>{esc_source}</code></div>'
        + toggle
        + f'<pre class="diff-view">{diff_html}</pre>'
    )


def _render_preview(content: str, source: str, path: str) -> str:
    """Render file preview fragment with download and restore actions."""
    esc_content = html.escape(content)
    esc_source = html.escape(source)
    esc_path = html.escape(path)
    filename = html.escape(Path(path).name)

    # Show diff toggle for git and ambiguous sources
    source_type = detect_source(source)
    toggle = ""
    if source_type in ("git", "ambiguous"):
        toggle = _diff_toggle_buttons(source, path, active="file")

    return (
        f"<h3>{esc_path}</h3>"
        f'<div class="breadcrumb">Source: <code>{esc_source}</code></div>'
        + toggle
        + f"<pre>{esc_content}</pre>"
        f'<div class="actions">'
        f'<a class="btn" href="/ui/download?source={esc_source}&path={esc_path}">'
        f"Download</a>"
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
            elif path == "/ui/commit":
                self._handle_commit(params)
            elif path == "/ui/preview":
                self._handle_preview(params)
            elif path == "/ui/diff":
                self._handle_diff(params)
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
        prefix = _param(params, "path") or "/"
        show_hidden = bool(_param(params, "show_hidden"))
        if not snapshot_id:
            self._send_html(_render_error("Missing snapshot parameter"), code=400)
            return

        if snapshot_id not in _restic_ls_cache:
            try:
                _restic_ls_cache[snapshot_id] = restic_ls(snapshot_id)
            except ValueError as e:
                self._send_html(_render_error(str(e)), code=404)
                return

        all_entries = _restic_ls_cache[snapshot_id]
        visible = group_entries_by_directory(all_entries, prefix)
        self._send_html(_render_files(visible, snapshot_id, prefix, show_hidden=show_hidden))

    def _handle_log(self, params: dict[str, list[str]]) -> None:
        vault_path = self._get_vault_path()
        if vault_path is None:
            self._send_html(_render_error("Health state not initialized"), code=500)
            return
        file_path = _param(params, "file")
        commits = git_file_history(vault_path, file_path) if file_path else git_log(vault_path)
        self._send_html(_render_log(commits, file_path))

    def _handle_commit(self, params: dict[str, list[str]]) -> None:
        vault_path = self._get_vault_path()
        if vault_path is None:
            self._send_html(_render_error("Health state not initialized"), code=500)
            return
        commit_hash = _param(params, "hash")
        if not commit_hash:
            self._send_html(_render_error("Missing hash parameter"), code=400)
            return
        commits = git_log_single(vault_path, commit_hash)
        if not commits:
            self._send_html(_render_error(f"Commit {commit_hash} not found"), code=404)
            return
        changes = git_diff_tree(vault_path, commit_hash)
        self._send_html(_render_commit_files(commits[0], changes))

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

    def _handle_diff(self, params: dict[str, list[str]]) -> None:
        source = _param(params, "source")
        path = _param(params, "path")
        if not source or not path:
            self._send_html(_render_error("Missing source or path parameter"), code=400)
            return
        vault_path = self._get_vault_path()
        if vault_path is None:
            self._send_html(_render_error("Health state not initialized"), code=500)
            return
        diff_text = git_diff_file(vault_path, source, path)
        self._send_html(_render_diff(diff_text, source, path))

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
