# Codex Notify

为 WSL 中的 Codex 提供 Windows 原生完成通知。通知显示会话标题和真实 VS Code 工作区名称，点击后返回产生通知的窗口并打开对应会话。不显示会话 ID。

仅 `vscode` 和 `cli` 主会话的完成事件触发通知；子代理、嵌套子代理及内部会话完成时不弹窗。通知脚本只读查询 Codex 的 `state_5.sqlite` 确认来源，查不到会话或数据库不可读时跳过通知，避免将子代理完成误报为整个任务完成。

扩展在 WSL 的工作区扩展宿主中运行。每个窗口根据自己的 `VSCODE_IPC_HOOK_CLI` 创建权限为 `0600` 的本地套接字；通知脚本通过继承的同一连接找到所属窗口。工作区名称来自 `vscode.workspace.name`，返回链接由 `vscode.env.asExternalUri()` 当场生成。无需网络服务或运行依赖。

## 验证和安装

```sh
node --test test/extension.test.js
python3 -m unittest discover -s test -p 'test_*.py'
python3 package_vsix.py
code --install-extension /data/home/optane/notify/codex-notify-0.1.0.vsix
```

在 WSL 的 `~/.codex/config.toml` 顶层设置：

```toml
notify = ["python3", "/data/home/optane/notify/notify_windows.py"]
```

已有 VS Code WSL 窗口若未加载扩展，执行 `Developer: Reload Window`。输出面板的 **Codex Notify** 频道显示当前工作区的就绪状态。

扩展未运行或窗口连接失效时，通知仍显示会话标题，但不猜测工作区，也不生成可能指向其他窗口的链接。先前通知的窗口关闭后，其返回链接可能失效。

当前支持 Linux/WSL 扩展宿主与 Windows PowerShell，未覆盖原生 Windows 扩展宿主或其他远程系统。会话来源依赖 `state_5.sqlite` 的 `threads.source`，会话标题依赖本地 `session_index.jsonl`，会话路径依赖当前 Codex 扩展的 `/local/<thread-id>` 路由。格式改变时需要适配。

`package_vsix.py` 只生成标准 VSIX ZIP 容器，不运行数据哈希校验，不下载打包依赖。版本对应的 VSIX 已存在时拒绝覆盖。
