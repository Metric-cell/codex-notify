#!/usr/bin/env python3
"""Show a Windows toast when a Codex turn finishes in WSL."""

import base64
from contextlib import closing
import http.client
import json
import os
from pathlib import Path
import socket
import shutil
import sqlite3
import subprocess
import sys
from urllib.parse import quote, urlsplit


def is_user_session(thread_id):
    # shortcut: Codex's current state schema; update if its database version changes.
    database = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve() / "state_5.sqlite"
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
            row = connection.execute("SELECT source FROM threads WHERE id = ?", (thread_id,)).fetchone()
    except sqlite3.Error as error:
        print(f"Codex Notify: cannot verify session source ({error}); notification skipped.", file=sys.stderr)
        return False
    return row is not None and row[0] in ("vscode", "cli")


def session_name(thread_id):
    index = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve() / "session_index.jsonl"
    try:
        lines = index.read_bytes().splitlines()
    except OSError:
        lines = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(entry, dict) and entry.get("id") == thread_id:
            title = entry.get("thread_name")
            if isinstance(title, str) and title.strip():
                return title
    database = index.parent / "state_5.sqlite"
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
            row = connection.execute("SELECT title FROM threads WHERE id = ?", (thread_id,)).fetchone()
        if row and isinstance(row[0], str) and row[0].strip():
            return row[0]
    except sqlite3.Error:
        pass
    return "未命名会话"


def window_context(thread_id):
    notify_socket = os.environ.get("CODEX_NOTIFY_SOCKET")
    cli_socket = os.environ.get("VSCODE_IPC_HOOK_CLI")
    direct_socket = cli_socket + ".notify" if cli_socket else None
    # The extension host's own bridge beats a socket inherited from another window.
    # Terminals have a separate CLI socket, so use their exported bridge instead.
    if direct_socket and (Path(direct_socket).is_socket() or notify_socket is None):
        notify_socket = direct_socket
    if not notify_socket:
        return {}
    connection = http.client.HTTPConnection("localhost", timeout=3)
    connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.sock.settimeout(3)
    try:
        connection.sock.connect(notify_socket)
        connection.request("GET", "/context?threadId=" + quote(thread_id, safe=""))
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f"bridge returned HTTP {response.status}")
        result = json.loads(response.read(16385))
        if (not isinstance(result, dict) or not isinstance(result.get("workspace"), str)
                or not isinstance(result.get("url"), str)):
            raise ValueError("bridge returned invalid workspace information")
        if len(result["url"]) > 2048 or any(ord(char) < 0x20 for char in result["url"]):
            raise ValueError("bridge returned an invalid session link")
        url = urlsplit(result["url"])
        if (url.scheme not in ("vscode", "vscode-insiders") or url.netloc != "openai.chatgpt"
                or url.path != "/local/" + thread_id):
            raise ValueError("bridge returned an unexpected session link")
        return result
    except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError) as error:
        print(f"Codex Notify: window context unavailable ({error}); showing session only.", file=sys.stderr)
        return {}
    finally:
        connection.close()


def display_text(value):
    # Bound the encoded Windows command and exclude characters forbidden by XML 1.0.
    return "".join(char for char in value if char in "\t\n\r" or
                   0x20 <= ord(char) <= 0xD7FF or 0xE000 <= ord(char) <= 0xFFFD or
                   0x10000 <= ord(char) <= 0x10FFFF)[:256]


def notify(event):
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        print("Codex Notify: payload must be an object with a string type.", file=sys.stderr)
        return 1
    if event.get("type") != "agent-turn-complete":
        return
    thread_id = event.get("thread-id")
    if not isinstance(thread_id, str) or not thread_id.strip():
        print("Codex Notify: thread-id must be a nonempty string.", file=sys.stderr)
        return 1
    if not is_user_session(thread_id):
        return
    window = window_context(thread_id)
    return send_notification(session_name(thread_id), thread_id[-16:], window.get("workspace"), window.get("url"))


def send_notification(name, group, workspace=None, url=None):
    details = base64.b64encode(json.dumps({
        "name": display_text(name),
        "group": display_text(group),
        "workspace": display_text(workspace) if workspace else None,
        "url": url,
    }, ensure_ascii=False).encode("utf-8")).decode("ascii")
    # Encode data separately so session titles cannot become PowerShell code.
    script = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$session = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__SESSION_DATA__')) | ConvertFrom-Json
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('<toast><visual><binding template="ToastGeneric"><text>Codex 任务已完成</text><text/></binding></visual></toast>')
if ($session.url) {
    $xml.DocumentElement.SetAttribute('activationType', 'protocol')
    $xml.DocumentElement.SetAttribute('launch', $session.url)
}
$xml.SelectSingleNode('//text[2]').InnerText = "会话：$($session.name)"
if ($session.workspace) {
    $workspaceText = $xml.CreateElement('text')
    $workspaceText.InnerText = "工作区：$($session.workspace)"
    $xml.SelectSingleNode('//binding').AppendChild($workspaceText) > $null
}
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$toast.Tag = 'codex-complete'
$toast.Group = $session.group
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Microsoft.VisualStudioCode')
if ($notifier.Setting -ne 'Enabled') { throw "Windows notifications are disabled: $($notifier.Setting)" }
$notifier.Show($toast)
""".replace('__SESSION_DATA__', details)
    try:
        subprocess.run(
            [shutil.which("powershell.exe") or "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
             "-NoLogo", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
             "-EncodedCommand", base64.b64encode(script.encode("utf-16le")).decode("ascii")],
            check=True,
            timeout=15,
            capture_output=True,
        )
    except FileNotFoundError:
        print("Codex Notify: Windows PowerShell was not found.", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print("Codex Notify: Windows PowerShell timed out.", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or b"").decode("utf-8", errors="replace").strip()[:1500]
        print(f"Codex Notify: Windows PowerShell failed (exit {error.returncode}). {detail}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"Codex Notify: cannot start Windows PowerShell ({error.strerror}).", file=sys.stderr)
        return 1


def main(args=None):
    args = sys.argv[1:] if args is None else args
    if args == ["--test"]:
        return send_notification("测试通知", "codex-test") or 0
    if len(args) != 1:
        print("Usage: notify_windows.py '<event JSON>' | --test", file=sys.stderr)
        return 1
    try:
        event = json.loads(args[0])
    except (json.JSONDecodeError, UnicodeDecodeError):
        print("Codex Notify: invalid event JSON.", file=sys.stderr)
        return 1
    return notify(event) or 0


if __name__ == "__main__":
    sys.exit(main())
