# Changelog

## 0.2.0

- Bundle the notification script and install it at a path that survives extension updates.
- Add commands to copy Codex configuration and send a test notification.
- Give integrated terminals a dedicated connection to their originating VS Code window.
- Skip corrupt index records and fall back to the session database for titles.
- Validate event payloads, bound notification text, and report PowerShell failures.
- Limit activation to WSL and document setup, privacy, and compatibility requirements.
- Adopt official VSIX packaging, automated tests, and the MIT license.

## 0.1.0

- Send Windows notifications for main Codex sessions in WSL.
- Show session titles and workspaces, with links back to the originating window.
- Suppress subagent completion notifications.
