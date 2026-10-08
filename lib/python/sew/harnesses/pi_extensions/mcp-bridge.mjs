// MCP stdio uses newline-delimited JSON-RPC. No provider SDK is needed.
import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { createInterface } from 'node:readline';

export default function (pi) {
  const config = JSON.parse(readFileSync(process.env.SEW_PI_CELL_CONFIG, 'utf8'));
  let child;
  const pending = new Map();
  let sequence = 0;
  const fail = () => {
    for (const { reject, timer } of pending.values()) {
      clearTimeout(timer);
      reject(new Error('MCP bridge transport failed'));
    }
    pending.clear();
  };
  const close = () => { fail(); child?.kill(); };
  function request(method, params, signal) {
    if (signal?.aborted) return Promise.reject(new Error('MCP call cancelled'));
    const id = ++sequence;
    return new Promise((resolve, reject) => {
      const finish = (error, value) => {
        clearTimeout(timer);
        signal?.removeEventListener('abort', abort);
        pending.delete(id);
        error ? reject(error) : resolve(value);
      };
      const abort = () => {
        child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/cancelled',
          params: { requestId: id, reason: 'cancelled' } }) + '\n');
        finish(new Error('MCP call cancelled'));
      };
      const timer = setTimeout(() => finish(new Error('MCP request timed out')), 30000);
      pending.set(id, { resolve: value => finish(null, value),
        reject: error => finish(error), timer });
      signal?.addEventListener('abort', abort, { once: true });
      child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
    });
  }
  pi.on('session_start', async () => {
    if (!config.server) { pi.setActiveTools([]); return; }
    const server = config.server;
    // The server gets only its configured environment and basic runtime paths;
    // it never inherits the model credential.
    const env = Object.fromEntries(['PATH', 'HOME', 'TMPDIR', 'LANG'].filter(k => process.env[k])
      .map(k => [k, process.env[k]]));
    child = spawn(server.command, server.args || [], {
      env: { ...env, ...JSON.parse(process.env.SEW_PI_MCP_ENV_SECRET || '{}') }, stdio: ['pipe', 'pipe', 'inherit'],
    });
    child.on('error', fail);
    child.on('exit', fail);
    child.stdin.on('error', fail);
    createInterface({ input: child.stdout }).on('line', line => {
      if (!line.trim()) return;
      let response;
      try { response = JSON.parse(line); }
      catch {
        // Do not echo server output: it may contain credentials.
        console.error('MCP bridge ignored invalid JSON on server stdout');
        return;
      }
      try {
        if (response.method && response.id !== undefined) {
          child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id: response.id,
            ...(response.method === 'ping' ? { result: {} } :
              { error: { code: -32601, message: 'Method not supported' } }) }) + '\n');
          return;
        }
        const entry = pending.get(response.id);
        if (entry) response.error ? entry.reject(new Error('MCP request failed')) : entry.resolve(response.result);
      } catch { close(); }
    });
    try {
      await request('initialize', { protocolVersion: '2024-11-05', capabilities: {},
        clientInfo: { name: 'searchlight', version: '1' } });
      child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');
      const names = [];
      let cursor;
      const cursors = new Set();
      do {
        const result = await request('tools/list', cursor ? { cursor } : {});
        for (const tool of result.tools) {
          if (!/^[A-Za-z0-9_-]+$/.test(tool.name)) throw new Error('Invalid MCP tool name');
          const name = `mcp__${config.serverName}__${tool.name}`;
          if (names.includes(name)) throw new Error('Duplicate MCP tool name');
          names.push(name);
          pi.registerTool({ name, label: tool.name, description: tool.description || tool.name,
            parameters: tool.inputSchema,
            async execute(id, args, signal) {
              const result = await request('tools/call', { name: tool.name, arguments: args }, signal);
              if (result.isError) {
                const diagnostic = (result.content || []).map(block =>
                  block.type === 'text' ? block.text : JSON.stringify(block)).join('\n');
                throw new Error('MCP tool returned an error' + (diagnostic ? `: ${diagnostic}` : ''));
              }
              return { content: result.content, details: {} };
            },
          });
        }
        cursor = result.nextCursor;
        if (cursor && (cursors.has(cursor) || cursors.size >= 100)) {
          throw new Error('Invalid MCP pagination');
        }
        if (cursor) cursors.add(cursor);
      } while (cursor);
      pi.setActiveTools(names);
    } catch { close(); throw new Error('MCP bridge initialization failed'); }
  });
  pi.on('session_shutdown', close);
  process.once('exit', close);
}
