#!/usr/bin/env python3
"""Show a Windows toast when a Codex turn finishes in WSL."""

import base64
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from urllib.parse import quote, urlsplit


def session_name(thread_id):
    # shortcut: local index format; show an unnamed session if it changes.
    index = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "session_index.jsonl"
    try:
        lines = index.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "未命名会话"
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and entry.get("id") == thread_id:
            return entry.get("thread_name") or "未命名会话"
    return "未命名会话"


def window_context(thread_id):
    cli_socket = os.environ.get("VSCODE_IPC_HOOK_CLI")
    if not cli_socket:
        return {}
    connection = http.client.HTTPConnection("localhost", timeout=3)
    connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.sock.settimeout(3)
    try:
        connection.sock.connect(cli_socket + ".notify")
        connection.request("GET", "/context?threadId=" + quote(thread_id, safe=""))
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f"bridge returned HTTP {response.status}")
        result = json.loads(response.read(16385))
        if (not isinstance(result, dict) or not isinstance(result.get("workspace"), str)
                or not isinstance(result.get("url"), str)):
            raise ValueError("bridge returned invalid workspace information")
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


def notify(event):
    if event.get("type") != "agent-turn-complete":
        return
    thread_id = event.get("thread-id") or "未知会话"
    window = window_context(thread_id)
    details = base64.b64encode(json.dumps({
        "name": session_name(thread_id),
        "group": thread_id[-16:],
        "workspace": window.get("workspace"),
        "url": window.get("url"),
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
    subprocess.run(
        ["/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
         "-NoLogo", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
         "-EncodedCommand", base64.b64encode(script.encode("utf-16le")).decode("ascii")],
        check=True,
        timeout=15,
    )


if __name__ == "__main__":
    notify(json.loads(sys.argv[1]))
