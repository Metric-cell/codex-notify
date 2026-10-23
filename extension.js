'use strict';

const fs = require('node:fs');
const http = require('node:http');
const net = require('node:net');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const vscode = require('vscode');

const run = promisify(execFile);

async function activate(context) {
  const output = vscode.window.createOutputChannel('Codex Notify');
  context.subscriptions.push(output);
  const supported = process.platform === 'linux' && vscode.env.remoteName === 'wsl';
  const cliSocket = process.env.VSCODE_IPC_HOOK_CLI;
  const socketPath = cliSocket ? `${cliSocket}.notify` : undefined;
  const scriptPath = path.join(context.globalStorageUri.fsPath, 'notify_windows.py');
  const environment = context.environmentVariableCollection;
  environment.persistent = false;
  environment.delete('CODEX_NOTIFY_SOCKET');

  const requireWSL = () => {
    if (supported) return true;
    vscode.window.showErrorMessage('Codex Notify requires a VS Code window connected to WSL.');
    return false;
  };
  context.subscriptions.push(vscode.commands.registerCommand('codex-notify.copyConfiguration', async () => {
    if (!requireWSL()) return;
    try {
      await installScript(context, scriptPath);
      await vscode.env.clipboard.writeText(`notify = ["python3", ${JSON.stringify(scriptPath)}]`);
      vscode.window.showInformationMessage('Copied. Paste at the top level of $CODEX_HOME/config.toml (default: ~/.codex/config.toml), outside any table. Replace an existing notify entry, then restart Codex.');
    } catch (error) {
      output.appendLine(`Cannot prepare configuration: ${error.message}`);
      vscode.window.showErrorMessage(`Codex Notify: ${error.message}`);
    }
  }));
  context.subscriptions.push(vscode.commands.registerCommand('codex-notify.testNotification', async () => {
    if (!requireWSL()) return;
    try {
      await installScript(context, scriptPath);
      await run('python3', [scriptPath, '--test'], {
        env: { ...process.env, CODEX_NOTIFY_SOCKET: socketPath || '' },
        timeout: 20000,
        maxBuffer: 65536,
      });
      vscode.window.showInformationMessage('Test notification sent. If no toast appeared, check Windows notification settings and Do not disturb.');
    } catch (error) {
      const message = error.stderr?.trim() || error.message;
      output.appendLine(`Test notification failed: ${message}`);
      vscode.window.showErrorMessage(`Codex Notify: ${message}`);
    }
  }));

  if (!supported || !socketPath) {
    output.appendLine('A WSL workspace and VS Code window connection are required.');
    return;
  }

  await installScript(context, scriptPath);
  const server = http.createServer(async (request, response) => {
    try {
      const url = new URL(request.url, 'http://localhost');
      const threadId = url.searchParams.get('threadId');
      if (request.method !== 'GET' || url.pathname !== '/context') {
        response.writeHead(404).end();
        return;
      }
      if (!/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(threadId || '')) {
        response.writeHead(400).end();
        return;
      }
      // shortcut: Codex's installed URI route; revisit if its extension changes the route.
      const target = vscode.Uri.parse(`${vscode.env.uriScheme}://openai.chatgpt/local/${threadId}`);
      const external = await vscode.env.asExternalUri(target);
      response.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
      response.end(JSON.stringify({
        workspace: vscode.workspace.name || '未打开工作区',
        url: external.toString(),
      }));
    } catch (error) {
      output.appendLine(`Cannot resolve window context: ${error.message}`);
      response.writeHead(500).end();
    }
  });
  server.requestTimeout = 5000;
  server.headersTimeout = 5000;
  server.on('error', error => output.appendLine(`Bridge error: ${error.message}`));
  context.subscriptions.push({ dispose: () => {
    environment.delete('CODEX_NOTIFY_SOCKET');
    server.close();
  } });
  const listen = () => new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(socketPath, () => {
      server.removeListener('error', reject);
      resolve();
    });
  });
  try {
    try {
      await listen();
    } catch (error) {
      if (error.code !== 'EADDRINUSE' || !fs.lstatSync(socketPath).isSocket()) throw error;
      const active = await new Promise((resolve, reject) => {
        const probe = net.createConnection(socketPath);
        probe.once('connect', () => { probe.destroy(); resolve(true); });
        probe.once('error', failure => failure.code === 'ECONNREFUSED' ? resolve(false) : reject(failure));
        probe.setTimeout(1000, () => { probe.destroy(); reject(new Error('Window bridge probe timed out.')); });
      });
      if (active) throw error;
      fs.unlinkSync(socketPath);
      await listen();
    }
    fs.chmodSync(socketPath, 0o600);
    environment.replace('CODEX_NOTIFY_SOCKET', socketPath);
    output.appendLine(`Ready for workspace: ${vscode.workspace.name || '未打开工作区'}`);
  } catch (error) {
    server.close();
    throw error;
  }
}

async function installScript(context, destination) {
  await fs.promises.mkdir(path.dirname(destination), { recursive: true });
  const temporary = `${destination}.${randomUUID()}.tmp`;
  try {
    // Multiple windows update the same script; readers must always see a complete file.
    await fs.promises.copyFile(path.join(context.extensionUri.fsPath, 'notify_windows.py'), temporary);
    await fs.promises.rename(temporary, destination);
  } finally {
    await fs.promises.rm(temporary, { force: true });
  }
}

module.exports = { activate };
