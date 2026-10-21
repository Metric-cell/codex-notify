'use strict';

const fs = require('node:fs');
const http = require('node:http');
const vscode = require('vscode');

async function activate(context) {
  const output = vscode.window.createOutputChannel('Codex Notify');
  context.subscriptions.push(output);
  const cliSocket = process.env.VSCODE_IPC_HOOK_CLI;
  if (!cliSocket || process.platform !== 'linux') {
    output.appendLine('No WSL window connection is available.');
    return;
  }

  // The Codex process inherits this same per-window CLI socket from its extension host.
  const socketPath = `${cliSocket}.notify`;
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
  context.subscriptions.push({ dispose: () => server.close() });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(socketPath, () => {
      server.removeListener('error', reject);
      try {
        fs.chmodSync(socketPath, 0o600);
        output.appendLine(`Ready for workspace: ${vscode.workspace.name || '未打开工作区'}`);
        resolve();
      } catch (error) {
        server.close();
        reject(error);
      }
    });
  });
}

module.exports = { activate };
