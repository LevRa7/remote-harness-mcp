#!/usr/bin/env node
/*
 * gemini-computer-use is the pre-rename name of Remote Harness MCP.
 *
 * This package is a thin alias: it declares remote-harness-mcp as a dependency
 * and forwards every argument to that package's CLI, so an existing
 * `npx gemini-computer-use` (or a global install) keeps working and pulls the
 * current code instead of a frozen copy of it.
 *
 * Nothing but the CLI is duplicated here - the node payload, the installer and
 * the gateway live in remote-harness-mcp.
 */
'use strict';

const { spawn } = require('child_process');

// The real CLI. `remote-harness-mcp` declares "main": "bin/cli.js", so both the
// deep path and the package name resolve to the same file.
const CANDIDATES = ['remote-harness-mcp/bin/cli.js', 'remote-harness-mcp'];

function resolveCli() {
  for (const id of CANDIDATES) {
    try {
      return require.resolve(id);
    } catch (err) {
      // try the next spelling
    }
  }
  return null;
}

const cli = resolveCli();
if (!cli) {
  console.error('gemini-computer-use is an alias for remote-harness-mcp, but the');
  console.error('remote-harness-mcp package is not installed next to it.');
  console.error('Install it directly instead: npm install -g remote-harness-mcp');
  process.exit(1);
}

const child = spawn(process.execPath, [cli].concat(process.argv.slice(2)), {
  stdio: 'inherit'
});

child.on('error', (err) => {
  console.error('gemini-computer-use: could not start ' + cli + ': ' + err.message);
  process.exit(1);
});

child.on('exit', (code, signal) => {
  if (signal) {
    // Mirror the child's death by signal, as a shell would.
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code === null ? 1 : code);
});
