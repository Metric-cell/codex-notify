# Codex Notify

为 WSL 中的 Codex 提供 Windows 原生完成通知，显示会话标题和 VS Code 工作区名称。点击通知可返回产生通知的窗口并打开对应会话。非 OpenAI 官方扩展。

只通知 `vscode` 和 `cli` 主会话的完成事件，子代理和内部会话不弹窗。同一会话的新通知会替换旧通知，减少通知中心堆积。

## 环境要求

- Windows 上的 VS Code，以及已连接到 WSL 的工作区窗口。
- WSL 中可运行的 `python3`，使用默认 Windows PowerShell，并已启用 WSL 的 Windows 程序互操作。
- Codex CLI 或 Codex VS Code 扩展；Codex 用户配置中需要设置 `notify`。
- Windows 通知设置允许 VS Code 发送通知。勿扰模式可能隐藏弹窗，通知仍可能出现在通知中心。

运行时只使用 Node.js、Python 标准库及 Windows 内置 API，无需 pip 或 npm 安装运行依赖。原生 Windows、macOS、SSH 和容器扩展宿主暂不支持。

## 安装与配置

1. 从 [GitHub Releases](https://github.com/Metric-cell/codex-notify/releases/latest) 下载 `.vsix` 安装包。在连接到 WSL 的 VS Code 窗口，打开扩展面板菜单，选择 **Install from VSIX…**，并确认扩展安装在 WSL 端。
2. 在命令面板运行 **Codex Notify: Copy Codex Configuration**。
3. 将复制的 `notify = [...]` 行粘贴到 WSL 中的用户配置 `$CODEX_HOME/config.toml`；未设置 `CODEX_HOME` 时是 `~/.codex/config.toml`。放在文件顶层、任何 `[table]` 之前，并替换已有的 `notify`，不要重复添加。
4. 运行 **Codex Notify: Send Test Notification**，确认 Windows 能显示测试通知。
5. 重启 Codex。若从 VS Code 集成终端使用 CLI，请新建终端，使窗口连接环境变量生效。

扩展会把随包分发的通知脚本安装到自己的全局存储目录，复制的配置使用该路径。扩展更新时会刷新脚本，源码目录和带版本号的安装目录都不需要保留。扩展不会自动修改 Codex 配置。

`notify` 是 Codex 的用户级设置，不能放进项目的 `.codex/config.toml`；详见 [Codex 官方配置文档](https://developers.openai.com/codex/config-advanced/)。

## 窗口与会话行为

扩展在 WSL 工作区扩展宿主中运行，每个窗口创建权限为 `0600` 的 Unix 套接字。Codex 扩展宿主中的进程优先使用与自身 `VSCODE_IPC_HOOK_CLI` 对应的桥接；集成终端通过专用环境变量 `CODEX_NOTIFY_SOCKET` 找到所属窗口，避免继承其他窗口的连接。选定桥接后连接失败时，不尝试其他窗口。工作区名称来自 `vscode.workspace.name`，返回链接由 `vscode.env.asExternalUri()` 生成。

扩展未运行、独立终端没有窗口连接或原窗口已关闭时，仍可显示会话标题，但不会猜测工作区或跳转到其他窗口。已关闭窗口的旧通知链接可能失效。测试通知只验证 Windows 投递，不打开虚构会话。

会话来源通过只读查询 `$CODEX_HOME/state_5.sqlite` 确认。查不到来源或数据库不可用时，跳过通知，避免把子代理完成误报成任务完成。标题优先读取 `session_index.jsonl` 的最新有效记录，再用数据库标题兜底，最后显示“未命名会话”。损坏的索引行会被跳过，显示文字最多保留 256 个字符。

兼容性目前依赖 Codex 的 `state_5.sqlite` / `threads.source`、`threads.title`、索引格式，以及 Codex 扩展的 `/local/<thread-id>` 路由。Codex 更改这些接口时可能需要适配。

## 排查问题

- **测试通知失败：** 错误提示会显示 Python 或 PowerShell 的失败原因。确认 WSL 中 `python3` 和 `powershell.exe` 可用；未在 PATH 中发现 PowerShell 时，脚本尝试默认的 `/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe`。
- **测试成功但没有弹窗：** 检查 Windows 的 VS Code 通知权限和勿扰模式，以及通知中心。
- **测试成功但任务完成没通知：** 检查用户配置中 `notify` 的位置和路径，重启 Codex；数据库不兼容时脚本会在标准错误中说明跳过原因。
- **缺少工作区或不能返回窗口：** 确认扩展已在相同 WSL 窗口运行，并为 CLI 新建终端。输出面板的 **Codex Notify** 频道显示桥接就绪状态和测试命令错误。

卸载前，移除用户配置中的 `notify` 行，并重启 Codex，避免继续调用残留配置。

## 隐私

扩展不发送网络请求、不收集遥测。只读访问本机 Codex 会话来源和标题，通过本地套接字取得工作区信息，再把标题、工作区名和返回链接交给 Windows 通知系统。通知文字可能出现在锁屏和通知中心，可在 Windows 设置中调整。任务正文和助手回复不会写入通知。

## 开发与打包

开发需要 Node.js 22 或更新版本，以及 Python 3。无需 Python 虚拟环境或额外 Python 包。

```sh
npm ci
npm test
npm run package
code --install-extension ./codex-notify-0.2.0.vsix
```

测试覆盖主会话筛选、损坏索引、标题兜底、无窗口降级、错误处理、安装命令，以及 Python 与 Node.js 实际 Unix 套接字通信。Windows 通知投递和点击返回需要在 WSL / Windows 上手动验证。

使用官方 `@vscode/vsce` 打包，只包含运行文件、文档和 MIT 许可证。GitHub Actions 会执行测试和打包，并保留 VSIX 构建产物。

公开发布前，需要确认 `package.json` 的 `publisher` 对应自己的 [Visual Studio Marketplace 发布者](https://code.visualstudio.com/api/working-with-extensions/publishing-extension)。GitHub 账号与 Marketplace 发布者是不同身份；当前 `optane` 沿用本地扩展标识，尚未验证 Marketplace 归属。变更发布者后需重新复制配置。首次发布前，还应手动验证两个窗口打开同一目录、集成终端 CLI、Codex 扩展会话和关闭原窗口后的行为。

## 许可证

[MIT](LICENSE) · Copyright © 2026 Metric-cell
