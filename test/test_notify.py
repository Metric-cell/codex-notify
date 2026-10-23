import base64
from contextlib import closing, contextmanager, redirect_stderr
from http.server import BaseHTTPRequestHandler
import io
import json
import os
from pathlib import Path
import socketserver
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import notify_windows


THREAD_ID = "01a15cd3-a530-7090-93b1-d680f92ccd62"


@contextmanager
def bridge(cli_socket, result):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        def log_message(self, *args):
            pass

    with socketserver.UnixStreamServer(cli_socket + ".notify", Handler) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield
        finally:
            server.shutdown()
            worker.join()


def details_for(event):
    with patch.object(notify_windows, "is_user_session", return_value=True), patch.object(notify_windows.subprocess, "run") as run:
        notify_windows.notify(event)
        script = base64.b64decode(run.call_args.args[0][-1]).decode("utf-16le")
        payload = script.split("FromBase64String('", 1)[1].split("')", 1)[0]
        return json.loads(base64.b64decode(payload)), script


class NotifyTests(unittest.TestCase):
    def test_explicit_test_notification_needs_no_session_and_has_no_callback(self):
        with patch.object(notify_windows, "is_user_session") as source, patch.object(notify_windows, "window_context") as context, patch.object(notify_windows.subprocess, "run") as run:
            self.assertEqual(notify_windows.main(["--test"]), 0)
            source.assert_not_called()
            context.assert_not_called()
            script = base64.b64decode(run.call_args.args[0][-1]).decode("utf-16le")
            payload = script.split("FromBase64String('", 1)[1].split("')", 1)[0]
            details = json.loads(base64.b64decode(payload))
            self.assertEqual(details["name"], "测试通知")
            self.assertIsNone(details["url"])
            self.assertIsNone(details["workspace"])

    def test_corrupt_utf8_index_and_database_title_fallback(self):
        with TemporaryDirectory(prefix="notify-test-") as directory, patch.dict(os.environ, {"CODEX_HOME": directory}):
            index = Path(directory, "session_index.jsonl")
            index.write_bytes(json.dumps({"id": THREAD_ID, "thread_name": "earlier"}).encode() + b'\n{"thread_name":"\xe4\xb8')
            self.assertEqual(notify_windows.session_name(THREAD_ID), "earlier")
            with closing(sqlite3.connect(Path(directory, "state_5.sqlite"))) as connection, connection:
                connection.execute("CREATE TABLE threads (id TEXT, title TEXT)")
                connection.execute("INSERT INTO threads VALUES (?, ?)", (THREAD_ID, "database title"))
            for data in (b'\xff', b'{}', json.dumps({"id": THREAD_ID, "thread_name": 42}).encode()):
                index.write_bytes(data)
                self.assertEqual(notify_windows.session_name(THREAD_ID), "database title")
            index.unlink()
            self.assertEqual(notify_windows.session_name(THREAD_ID), "database title")

    def test_socket_selection_uses_direct_bridge_or_terminal_export_without_guessing(self):
        with TemporaryDirectory(prefix="notify-test-") as directory:
            cli = directory + "/legacy"
            dedicated = directory + "/dedicated"
            context = {"workspace": "dedicated", "url": f"vscode://openai.chatgpt/local/{THREAD_ID}"}
            with bridge(dedicated, context), patch.dict(os.environ, {
                "CODEX_NOTIFY_SOCKET": dedicated + ".notify", "VSCODE_IPC_HOOK_CLI": cli,
            }):
                self.assertEqual(notify_windows.window_context(THREAD_ID), context)
            with patch.dict(os.environ, {
                "CODEX_NOTIFY_SOCKET": dedicated + ".notify", "VSCODE_IPC_HOOK_CLI": cli,
            }), redirect_stderr(io.StringIO()):
                self.assertEqual(notify_windows.window_context(THREAD_ID), {})
            Path(dedicated + ".notify").unlink()
            direct_context = {**context, "workspace": "direct"}
            with bridge(cli, direct_context), bridge(dedicated, context), patch.dict(os.environ, {
                "CODEX_NOTIFY_SOCKET": dedicated + ".notify", "VSCODE_IPC_HOOK_CLI": cli,
            }):
                self.assertEqual(notify_windows.window_context(THREAD_ID), direct_context)
            Path(dedicated + ".notify").unlink()
            with bridge(dedicated, context), patch.dict(os.environ, {
                "CODEX_NOTIFY_SOCKET": dedicated + ".notify", "VSCODE_IPC_HOOK_CLI": cli,
            }), redirect_stderr(io.StringIO()):
                self.assertEqual(notify_windows.window_context(THREAD_ID), {})

    def test_relative_codex_home_supports_source_and_database_title(self):
        with self.session_database(), patch.dict(os.environ, {"CODEX_HOME": os.path.relpath(os.environ["CODEX_HOME"])}):
            self.assertTrue(notify_windows.is_user_session(THREAD_ID))
            self.assertEqual(notify_windows.session_name(THREAD_ID), "未命名会话")

    def test_invalid_cli_and_payload_fail_without_side_effects(self):
        with patch.object(notify_windows, "is_user_session") as source, patch.object(notify_windows.subprocess, "run") as run, redirect_stderr(io.StringIO()) as errors:
            for args in ([], ["{}", "{}"], ["{"], ["null"], ["[]"], ['{"type": 1}']):
                self.assertEqual(notify_windows.main(args), 1)
            for thread in (None, "", " ", 42, [], {}):
                self.assertEqual(notify_windows.main([json.dumps({"type": "agent-turn-complete", "thread-id": thread})]), 1)
            source.assert_not_called()
            run.assert_not_called()
            self.assertNotIn("Traceback", errors.getvalue())

    def test_powershell_errors_return_failure_without_tracebacks(self):
        for error in (FileNotFoundError(), subprocess.TimeoutExpired("powershell", 15), subprocess.CalledProcessError(7, "powershell"), PermissionError(13, "Permission denied")):
            with patch.object(notify_windows, "is_user_session", return_value=True), patch.object(notify_windows, "window_context", return_value={}), patch.object(notify_windows, "session_name", return_value="title"), patch.object(notify_windows.subprocess, "run", side_effect=error), redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(notify_windows.main([json.dumps({"type": "agent-turn-complete", "thread-id": THREAD_ID})]), 1)
                self.assertIn("PowerShell", errors.getvalue())
                self.assertNotIn("Traceback", errors.getvalue())

    def test_powershell_failure_preserves_stderr_diagnostic(self):
        error = subprocess.CalledProcessError(7, "powershell", stderr=b"Windows notifications are disabled")
        with patch.object(notify_windows.subprocess, "run", side_effect=error), redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(notify_windows.main(["--test"]), 1)
            self.assertIn("Windows notifications are disabled", errors.getvalue())

    def test_display_text_is_xml_safe_and_command_is_bounded(self):
        title = "\x00\x01\ud800\ufffe" + "长" * 10000
        with patch.object(notify_windows, "session_name", return_value=title), patch.object(notify_windows, "window_context", return_value={"workspace": title}):
            details, script = details_for({"type": "agent-turn-complete", "thread-id": THREAD_ID})
            self.assertEqual(details["name"], "长" * 256)
            self.assertEqual(details["workspace"], "长" * 256)
            self.assertLess(len(base64.b64encode(script.encode("utf-16le"))), 30000)

    @contextmanager
    def session_database(self):
        with TemporaryDirectory(prefix="notify-test-") as directory, patch.dict(os.environ, {"CODEX_HOME": directory}):
            with closing(sqlite3.connect(Path(directory) / "state_5.sqlite")) as connection, connection:
                connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, source TEXT)")
                connection.executemany("INSERT INTO threads VALUES (?, ?)", [
                    (THREAD_ID, "vscode"),
                    ("main-cli", "cli"),
                    ("child", json.dumps({"subagent": {"thread_spawn": {"parent_thread_id": THREAD_ID, "depth": 1}}})),
                    ("grandchild", json.dumps({"subagent": {"thread_spawn": {"parent_thread_id": "child", "depth": 2}}})),
                    ("review", json.dumps({"subagent": "review"})),
                    ("unknown", "unknown"),
                ])
            yield

    def test_only_user_sessions_are_allowed(self):
        with self.session_database():
            for thread in (THREAD_ID, "main-cli"):
                self.assertTrue(notify_windows.is_user_session(thread))
            for thread in ("child", "grandchild", "review", "unknown", "missing", None, "' OR 1=1 --"):
                self.assertFalse(notify_windows.is_user_session(thread))

    def test_subagents_do_not_query_window_or_send_notifications(self):
        with self.session_database(), patch.object(notify_windows, "window_context") as query, patch.object(notify_windows.subprocess, "run") as run:
            for thread in ("child", "grandchild", "review", "missing", None):
                notify_windows.notify({"type": "agent-turn-complete", "thread-id": thread})
            query.assert_not_called()
            run.assert_not_called()

    def test_primary_sessions_still_notify(self):
        with self.session_database(), patch.object(notify_windows, "window_context", return_value={}), patch.object(notify_windows.subprocess, "run") as run:
            for thread in (THREAD_ID, "main-cli"):
                notify_windows.notify({"type": "agent-turn-complete", "thread-id": thread})
            self.assertEqual(run.call_count, 2)

    def test_missing_or_incompatible_database_does_not_create_files_or_notify(self):
        with TemporaryDirectory(prefix="notify-test-") as directory, patch.dict(os.environ, {"CODEX_HOME": directory}), redirect_stderr(io.StringIO()), patch.object(notify_windows.subprocess, "run") as run:
            notify_windows.notify({"type": "agent-turn-complete", "thread-id": THREAD_ID})
            database = Path(directory) / "state_5.sqlite"
            self.assertFalse(database.exists())
            with closing(sqlite3.connect(database)):
                pass
            notify_windows.notify({"type": "agent-turn-complete", "thread-id": THREAD_ID})
            run.assert_not_called()

    def test_originating_socket_selects_window_even_when_working_directories_match(self):
        with TemporaryDirectory(prefix="notify-test-") as directory:
            first = directory + "/first.sock"
            second = directory + "/second.sock"
            for cli, name, window_id in [(first, "MM", 1), (second, "paper-research", 5)]:
                url = f"vscode://openai.chatgpt/local/{THREAD_ID}?windowId={window_id}"
                with bridge(cli, {"workspace": name, "url": url}), patch.dict(os.environ, {"VSCODE_IPC_HOOK_CLI": cli}):
                    details, _ = details_for({"type": "agent-turn-complete", "thread-id": THREAD_ID, "cwd": "/same/directory"})
                    self.assertEqual(details["workspace"], name)
                    self.assertEqual(details["url"], url)

    def test_unavailable_bridge_keeps_notification_without_guessing_window(self):
        with TemporaryDirectory(prefix="notify-test-") as directory, patch.dict(os.environ, {"VSCODE_IPC_HOOK_CLI": directory + "/missing.sock"}), redirect_stderr(io.StringIO()) as errors:
            details, script = details_for({"type": "agent-turn-complete", "thread-id": THREAD_ID, "cwd": "/misleading/name"})
            self.assertIsNone(details["workspace"])
            self.assertIsNone(details["url"])
            self.assertNotIn("misleading", script)
            self.assertIn("unavailable", errors.getvalue())

    def test_unexpected_callback_and_malformed_responses_are_rejected(self):
        with TemporaryDirectory(prefix="notify-test-") as directory:
            for number, response in enumerate([
                {"workspace": "MM", "url": "https://example.com"},
                {"workspace": "MM", "url": "vscode://openai.chatgpt/local/wrong-thread"},
                {"workspace": "MM", "url": 42},
                [],
            ]):
                cli = directory + f"/{number}.sock"
                with bridge(cli, response), patch.dict(os.environ, {"VSCODE_IPC_HOOK_CLI": cli}), redirect_stderr(io.StringIO()):
                    self.assertEqual(notify_windows.window_context(THREAD_ID), {})

    def test_index_uses_latest_title_and_tolerates_partial_records(self):
        with TemporaryDirectory(prefix="notify-test-") as directory, patch.dict(os.environ, {"CODEX_HOME": directory}):
            self.assertEqual(notify_windows.session_name(THREAD_ID), "未命名会话")
            title = "中文 <&> ' $(throw 'bad')"
            records = [json.dumps({"id": THREAD_ID, "thread_name": "old"}), "null", json.dumps({"id": THREAD_ID, "thread_name": title}), "{partial"]
            Path(directory, "session_index.jsonl").write_text("\n".join(records), encoding="utf-8")
            self.assertEqual(notify_windows.session_name(THREAD_ID), title)

    def test_special_titles_are_data_and_identifiers_are_not_visible(self):
        title = "中文 <&> ' $(throw 'bad')"
        window = {"workspace": "工作区 <&>", "url": f"vscode://openai.chatgpt/local/{THREAD_ID}?windowId=1"}
        with patch.object(notify_windows, "session_name", return_value=title), patch.object(notify_windows, "window_context", return_value=window):
            details, script = details_for({"type": "agent-turn-complete", "thread-id": THREAD_ID})
            self.assertEqual(details["name"], title)
            self.assertNotIn(title, script)
            self.assertNotIn("id", details)
            self.assertNotIn("ID：", script)

    def test_other_events_do_not_query_or_notify(self):
        with patch.object(notify_windows, "window_context") as query, patch.object(notify_windows.subprocess, "run") as run:
            notify_windows.notify({"type": "approval-requested"})
            query.assert_not_called()
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
