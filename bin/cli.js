#!/usr/bin/env node
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const isWindows = os.platform() === 'win32';
const argv = process.argv.slice(2);

// The local maintenance subcommands run scripts that ship next to this CLI:
// in the installer payload, or in a repository checkout. Only the FIRST argument
// is inspected, so every other invocation - including the argument-passing
// bootstrap "-Quick", "-User <name>", "-Gateway <domain>" - is forwarded to the
// published installer exactly as before.
const LOCAL_SCRIPTS = {
  doctor: path.join('ops', 'doctor.ps1'),
  restart: path.join('ops', 'windows', 'agent-watchdog.ps1'),
  update: path.join('ops', 'update.ps1')
};

// Printed for "-h"/"-help"/"--help": the scripts declare their own parameter
// blocks, and forwarding a help flag to them only produced a PowerShell
// "parameter cannot be found" error.
const LOCAL_USAGE = {
  doctor: [
    'usage: remote-harness-mcp doctor [-Json] [-ConfigDir <path>] [-TailLines <n>]',
    '  read-only node diagnostic: autostart interpreter, heartbeat, log tail, gateway state',
    '  exit code 0 when the node is healthy, 2 otherwise'
  ].join('\n'),
  restart: [
    'usage: remote-harness-mcp restart [-Quiet] [-ConfigDir <path>] [-LogFile <path>] [-GraceSeconds <n>]',
    '  runs the watchdog once and starts the agent when the node is offline',
    '  exit code 0 when the node is online, 2 when the node is misconfigured, 3 when it is still offline'
  ].join('\n'),
  update: [
    'usage: remote-harness-mcp update [-Check] [-Force] [-Offline] [-NoRestart] [-Json]',
    '                                [-InstallDir <path>] [-Python <path>] [-ConfigDir <path>]',
    '  self-update from the newest GitHub release: -Check only reports what the',
    '  newest published version is, without -Check the payload is downloaded, its',
    '  SHA-256 verified and installed with a rollback backup, then the node restarts',
    '  exit code 0 up to date or applied, 2 update available (-Check), 3 failed,',
    '            4 applied and the node is restarting'
  ].join('\n')
};

function findLocalScript(relative) {
  // The CLI is "bin/cli.js", so the payload/repository root is its parent; a
  // bundled layout that puts ops/ next to the CLI and the current directory are
  // accepted as well.
  const roots = [path.resolve(__dirname, '..'), path.resolve(__dirname), process.cwd()];
  for (const root of roots) {
    const candidate = path.join(root, relative);
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  return null;
}

function runLocalScript(name, rest) {
  if (!isWindows) {
    console.error(`${name} is Windows-only: it drives Windows PowerShell 5.1 scripts.`);
    process.exit(1);
  }
  if (rest.some(arg => ['-h', '--h', '-help', '--help'].includes(arg.toLowerCase()))) {
    console.log(LOCAL_USAGE[name]);
    process.exit(0);
  }
  const relative = LOCAL_SCRIPTS[name];
  const script = findLocalScript(relative);
  if (!script) {
    console.error(`Cannot find ${relative.split(path.sep).join('/')} next to this CLI.`);
    console.error('Run this from an installed node or from a repository checkout: the');
    console.error('doctor and the watchdog ship with the installer payload.');
    process.exit(1);
  }
  // Windows PowerShell binds parameter names case-insensitively and accepts the
  // documented spelling, so a "--json" style flag only needs its second dash
  // removed to reach -Json. Everything else is passed through untouched.
  const forwarded = rest.map(arg => (arg.startsWith('--') ? arg.slice(1) : arg));
  const ps = spawn(
    'powershell.exe',
    ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script].concat(forwarded),
    { stdio: 'inherit' }
  );
  ps.on('error', (err) => {
    console.error(`${name}: could not start powershell.exe: ${err.message}`);
    process.exit(1);
  });
  ps.on('exit', (code) => process.exit(code === null ? 1 : code));
}

function launchRemoteInstaller(args) {
  const rawArgs = args.map(arg => `"${arg.replace(/"/g, '\\"')}"`).join(' ');

  console.log('🚀 Launching Remote Harness MCP installer...');

  if (isWindows) {
    const psCmd = rawArgs
      ? `& { $s = Invoke-RestMethod https://racknerd-5a24bf9.merino-carob.ts.net/install.ps1; & ([scriptblock]::Create($s)) ${rawArgs} }`
      : `irm https://racknerd-5a24bf9.merino-carob.ts.net/install.ps1 | iex`;
    const ps = spawn('powershell.exe', ['-ExecutionPolicy', 'Bypass', '-Command', psCmd], {
      stdio: 'inherit'
    });
    ps.on('exit', (code) => process.exit(code || 0));
  } else {
    const bashCmd = rawArgs
      ? `curl -fsSL https://racknerd-5a24bf9.merino-carob.ts.net/install.sh | bash -s -- ${rawArgs}`
      : `curl -fsSL https://racknerd-5a24bf9.merino-carob.ts.net/install.sh | bash`;
    const sh = spawn('bash', ['-c', bashCmd], {
      stdio: 'inherit'
    });
    sh.on('exit', (code) => process.exit(code || 0));
  }
}

const subcommand = (argv[0] || '').toLowerCase();

// Subcommands that run a Python module shipped with the node. They are not
// Windows-only (the doctor and the watchdog are), so they take their own path:
// `rotate` has to work on the same four platforms the agent does.
const PY_MODULES = {
  rotate: 'core.rekey'
};

function findPython() {
  // The installer pins a real interpreter by full path and records it nowhere the
  // CLI could read, so the candidates are probed instead of assumed: on Windows
  // `python` may be the Microsoft Store alias, which exits without running
  // anything, and `py -3` is the launcher that always finds a real one.
  const candidates = [];
  if (process.env.MESH_PYTHON) {
    candidates.push({ command: process.env.MESH_PYTHON, prefix: [] });
  }
  if (isWindows) {
    candidates.push({ command: 'py', prefix: ['-3'] });
    candidates.push({ command: 'python', prefix: [] });
  } else {
    candidates.push({ command: 'python3', prefix: [] });
    candidates.push({ command: 'python', prefix: [] });
  }
  for (const candidate of candidates) {
    const probe = require('child_process').spawnSync(
      candidate.command,
      candidate.prefix.concat(['-c', 'import sys']),
      { stdio: 'ignore' }
    );
    if (probe.status === 0) {
      return candidate;
    }
  }
  return null;
}

function runPythonModule(moduleName, rest) {
  const python = findPython();
  if (!python) {
    console.error('No working Python 3 interpreter found.');
    console.error('Install Python 3, or point MESH_PYTHON at one, then retry.');
    process.exit(1);
  }
  const root = path.resolve(__dirname, '..');
  if (!fs.existsSync(path.join(root, 'core', moduleName.split('.').pop() + '.py'))) {
    console.error(`Cannot find ${moduleName} next to this CLI. Run this from an installed node.`);
    process.exit(1);
  }
  const child = spawn(
    python.command,
    python.prefix.concat(['-m', moduleName], rest),
    { stdio: 'inherit', cwd: root }
  );
  child.on('error', (err) => {
    console.error(`${moduleName}: could not start ${python.command}: ${err.message}`);
    process.exit(1);
  });
  child.on('exit', (code) => process.exit(code === null ? 1 : code));
}

if (Object.prototype.hasOwnProperty.call(LOCAL_SCRIPTS, subcommand)) {
  runLocalScript(subcommand, argv.slice(1));
} else if (Object.prototype.hasOwnProperty.call(PY_MODULES, subcommand)) {
  runPythonModule(PY_MODULES[subcommand], argv.slice(1));
} else {
  launchRemoteInstaller(argv);
}
