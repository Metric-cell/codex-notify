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

async function startHost(t, directory, name, windowId, options = {}) {
  const cli = path.join(directory, `window-${windowId}.sock`);
  const workspace = { name };
  const variables = new Map();
  const commands = new Map();
  const messages = [];
  const calls = [];
  const clipboard = { text: '', async writeText(text) { this.text = text; } };
  const context = {
    subscriptions: [],
    extensionUri: { fsPath: path.join(__dirname, '..') },
    globalStorageUri: { fsPath: path.join(directory, 'storage') },
    environmentVariableCollection: {
      delete: key => variables.delete(key),
      replace: (key, value) => variables.set(key, value),
    },
  };
  const vscode = {
    workspace,
    commands: { registerCommand: (name, callback) => {
      commands.set(name, callback);
      return { dispose: () => commands.delete(name) };
    } },
    window: {
      createOutputChannel: () => ({ appendLine() {}, dispose() {} }),
      showInformationMessage: message => messages.push(message),
      showErrorMessage: message => messages.push(message),
    },
    Uri: { parse: value => new URL(value) },
    env: {
      uriScheme: 'vscode',
      remoteName: options.remoteName || 'wsl',
      clipboard,
      async asExternalUri(uri) {
        // The real API adds the current window's routing information.
        uri.searchParams.set('windowId', String(windowId));
        return uri;
      },
    },
  };
  const module = { exports: {} };
  vm.runInNewContext(source, {
    require: id => {
      if (id === 'vscode') return vscode;
      if (id === 'node:child_process') return { execFile: (...args) => {
        calls.push(args.slice(0, -1));
        args.at(-1)(options.runError || null, '', '');
      } };
      return require(id);
    },
    process: { env: { VSCODE_IPC_HOOK_CLI: cli }, platform: options.platform || 'linux' },
    module, URL,
  });
  t.after(() => { for (const item of context.subscriptions) item.dispose(); });
  await module.exports.activate(context);
  return { socket: `${cli}.notify`, workspace, context, variables, commands, clipboard, messages, calls };
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

test('setup uses a bundled script at a stable path and exports the terminal bridge', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-notify-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const host = await startHost(t, directory, 'Workspace', 1);
  assert.equal(host.variables.get('CODEX_NOTIFY_SOCKET'), host.socket);
  assert.equal(host.context.environmentVariableCollection.persistent, false);
  await host.commands.get('codex-notify.copyConfiguration')();
  const scriptPath = path.join(directory, 'storage', 'notify_windows.py');
  assert.equal(host.clipboard.text, `notify = ["python3", ${JSON.stringify(scriptPath)}]`);
  assert.equal(fs.readFileSync(scriptPath, 'utf8'), fs.readFileSync(path.join(__dirname, '..', 'notify_windows.py'), 'utf8'));
  assert.deepEqual(fs.readdirSync(path.dirname(scriptPath)), ['notify_windows.py']);
  await host.commands.get('codex-notify.testNotification')();
  assert.equal(host.calls[0][0], 'python3');
  assert.deepEqual(Array.from(host.calls[0][1]), [scriptPath, '--test']);
  assert.equal(host.calls[0][2].env.CODEX_NOTIFY_SOCKET, host.socket);
  assert.equal(host.calls[0][2].timeout, 20000);
});

test('unsupported hosts show a useful message without installing scripts or starting a bridge', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-notify-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const host = await startHost(t, directory, 'Workspace', 1, { remoteName: 'ssh-remote' });
  await host.commands.get('codex-notify.copyConfiguration')();
  await host.commands.get('codex-notify.testNotification')();
  assert.equal(host.variables.size, 0);
  assert.equal(fs.existsSync(host.socket), false);
  assert.equal(fs.existsSync(path.join(directory, 'storage')), false);
  assert.equal(host.calls.length, 0);
  assert.match(host.messages.join('\n'), /connected to WSL/);
});

test('test notification failures surface the diagnostic', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-notify-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const error = Object.assign(new Error('exit 1'), { stderr: 'Windows notifications are disabled' });
  const host = await startHost(t, directory, 'Workspace', 1, { runError: error });
  await host.commands.get('codex-notify.testNotification')();
  assert.match(host.messages.at(-1), /Windows notifications are disabled/);
});

test('Python resolves the real Node bridge even when a terminal has a different CLI socket', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-notify-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const host = await startHost(t, directory, 'Actual bridge', 7);
  const result = await new Promise((resolve, reject) => {
    require('node:child_process').execFile('python3', ['-B', '-c',
      'import json, notify_windows; print(json.dumps(notify_windows.window_context("' + threadId + '")))'], {
      cwd: path.join(__dirname, '..'),
      env: { ...process.env, CODEX_NOTIFY_SOCKET: host.socket, VSCODE_IPC_HOOK_CLI: path.join(directory, 'different-terminal.sock') },
      timeout: 10000,
    }, (error, stdout) => error ? reject(error) : resolve(JSON.parse(stdout)));
  });
  assert.equal(result.workspace, 'Actual bridge');
  assert.equal(new URL(result.url).searchParams.get('windowId'), '7');
});

test('activation recovers a stale socket but preserves an active bridge and regular files', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-notify-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const stalePath = path.join(directory, 'window-1.sock.notify');
  require('node:child_process').execFileSync(process.execPath, ['-e',
    "require('node:net').createServer().listen(process.argv[1], () => process.exit(0))", stalePath]);
  assert.equal(fs.lstatSync(stalePath).isSocket(), true);
  const host = await startHost(t, directory, 'Recovered', 1);
  assert.equal(JSON.parse((await query(host.socket)).body).workspace, 'Recovered');
  await assert.rejects(startHost(t, directory, 'Duplicate', 1), { code: 'EADDRINUSE' });
  assert.equal(JSON.parse((await query(host.socket)).body).workspace, 'Recovered');
  const file = path.join(directory, 'window-2.sock.notify');
  fs.writeFileSync(file, 'preserve');
  await assert.rejects(startHost(t, directory, 'Not a socket', 2), { code: 'EADDRINUSE' });
  assert.equal(fs.readFileSync(file, 'utf8'), 'preserve');
});
