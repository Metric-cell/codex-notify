# Changelog

## 0.2.1

- Change the publisher to Metric-cell, matching the author and GitHub repository identity.
- Document the extension ID change from optane.codex-notify to Metric-cell.codex-notify, including removal of the old extension and refreshing the Codex configuration.
- Update the release package and installation instructions for the new identity.

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
