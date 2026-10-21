'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const threadId = '01a15cd3-a530-7090-93b1-d680f92ccd62';
const source = fs.readFileSync(path.join(__dirname, '..', 'extension.js'), 'utf8');

async function startHost(t, directory, name, windowId) {
  const cli = path.join(directory, `window-${windowId}.sock`);
  const workspace = { name };
  const context = { subscriptions: [] };
  const vscode = {
    workspace,
    window: { createOutputChannel: () => ({ appendLine() {}, dispose() {} }) },
    Uri: { parse: value => new URL(value) },
    env: {
      uriScheme: 'vscode',
      async asExternalUri(uri) {
        // The real API adds the current window's routing information.
        uri.searchParams.set('windowId', String(windowId));
        return uri;
      },
    },
  };
  const module = { exports: {} };
  vm.runInNewContext(source, {
    require: id => id === 'vscode' ? vscode : require(id),
    process: { env: { VSCODE_IPC_HOOK_CLI: cli }, platform: 'linux' },
    module, URL,
  });
  await module.exports.activate(context);
  t.after(() => { for (const item of context.subscriptions) item.dispose(); });
  return { socket: `${cli}.notify`, workspace };
}

function query(socketPath, route = `/context?threadId=${threadId}`, method = 'GET') {
  return new Promise((resolve, reject) => {
    const request = http.request({ socketPath, path: route, method }, response => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', chunk => { body += chunk; });
      response.on('end', () => resolve({ status: response.statusCode, body }));
    });
    request.on('error', reject);
    request.end();
  });
}

test('two windows return their own workspace and callback, including workspace changes', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-notify-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const first = await startHost(t, directory, '多根工作区 <&>', 1);
  const second = await startHost(t, directory, 'paper-research', 5);
  for (const [host, name, windowId] of [[first, '多根工作区 <&>', '1'], [second, 'paper-research', '5']]) {
    const response = await query(host.socket);
    assert.equal(response.status, 200);
    const result = JSON.parse(response.body);
    assert.equal(result.workspace, name);
    assert.equal(new URL(result.url).searchParams.get('windowId'), windowId);
    assert.equal(new URL(result.url).pathname, `/local/${threadId}`);
    assert.equal(fs.statSync(host.socket).mode & 0o777, 0o600);
  }
  first.workspace.name = '重命名后的工作区';
  assert.equal(JSON.parse((await query(first.socket)).body).workspace, '重命名后的工作区');
});

test('invalid sessions, routes and methods are rejected', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-notify-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const host = await startHost(t, directory, 'MM', 1);
  assert.equal((await query(host.socket, '/context?threadId=bad')).status, 400);
  assert.equal((await query(host.socket, '/context')).status, 400);
  assert.equal((await query(host.socket, '/other')).status, 404);
  assert.equal((await query(host.socket, `/context?threadId=${threadId}`, 'POST')).status, 404);
});
