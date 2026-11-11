#!/usr/bin/env node
/* rhm - console menu launcher for npm/npx. */
const { spawn } = require('child_process');
const path = require('path');

const root = path.resolve(__dirname, '..');
const args = ['-m', 'core.menu'].concat(process.argv.slice(2));
const candidates = [];
if (process.env.MESH_PYTHON) candidates.push([process.env.MESH_PYTHON]);
if (process.platform === 'win32') {
  // Windows ships the "python3" Store alias that prints a stub and exits; the
  // py launcher and "python" are the real candidates.
  candidates.push(['py', '-3'], ['python'], ['python3']);
} else {
  candidates.push(['python3'], ['python']);
}

function tryAt(index) {
  if (index >= candidates.length) {
    console.error('rhm: Python 3 was not found. Set MESH_PYTHON or install Python 3.');
    process.exit(127);
  }
  const entry = candidates[index];
  const cmd = entry[0];
  const cmdArgs = entry.slice(1).concat(args);
  const child = spawn(cmd, cmdArgs, { cwd: root, stdio: 'inherit' });
  child.on('error', () => tryAt(index + 1));
  child.on('exit', (code, signal) => {
    if (signal) process.kill(process.pid, signal);
    process.exit(code === null ? 1 : code);
  });
}
tryAt(0);
