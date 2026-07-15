import { render } from 'ink';
import React from 'react';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { existsSync } from 'node:fs';
import { bridge } from './lib/bridge-process.js';
import App from './app.js';

function findProjectRoot(fromDir: string): string {
  const distRoot = resolve(fromDir, '..', '..');
  if (existsSync(resolve(distRoot, 'smart_bridge.py'))) return distRoot;
  const srcRoot = resolve(fromDir, '..');
  if (existsSync(resolve(srcRoot, 'smart_bridge.py'))) return srcRoot;
  return process.cwd();
}

const _dirname = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = findProjectRoot(_dirname);

function getPythonScript(): string {
  return resolve(PROJECT_ROOT, process.env.PYTHON_SCRIPT || 'smart_bridge.py');
}

function cliMode(): void {
  const args = bridge.pythonArgv;
  const proc = spawn('python3', [getPythonScript(), ...args], {
    cwd: PROJECT_ROOT,
    stdio: ['inherit', 'inherit', 'inherit'],
  });
  proc.on('exit', (code) => process.exit(code ?? 0));
}

type Screen = 'welcome' | 'onboarding' | 'dashboard' | 'model-picker' | 'settings';

function resolveScreenFromArgs(args: string[]): Screen | null {
  if (args.includes('setup') || args.includes('onboarding')) return 'onboarding';
  if (args.includes('dashboard') || args.includes('logs')) return 'dashboard';
  if (args.includes('config') || args.includes('settings')) return 'settings';
  if (args.includes('models') || args.includes('model-picker')) return 'model-picker';
  return null;
}

function main(): void {
  const args = process.argv.slice(2);

  const forceTUI = args.includes('--tui');
  const cliArgs = args.filter(a => a !== '--tui');

  const isPythonFlag = cliArgs.some(a =>
    a.startsWith('-b') || a.startsWith('--best') ||
    a.startsWith('-m') || a.startsWith('--model') ||
    a.startsWith('-t') || a.startsWith('--test') ||
    a.startsWith('-s') || a.startsWith('--setup') ||
    a.startsWith('--target')
  );

  if (!forceTUI && isPythonFlag) {
    cliMode();
    return;
  }

  if (!forceTUI && (cliArgs.includes('--help') || cliArgs.includes('-h'))) {
    cliMode();
    return;
  }

  const screen = resolveScreenFromArgs(cliArgs);

  if (!process.stdin.isTTY && !forceTUI && !screen) {
    console.error(
      'g4f-bridge requires an interactive terminal.\n'
      + 'Usage: g4f-bridge [setup|config|models|dashboard|logs]\n'
      + '       g4f-bridge -b 15 -t claude-code  (CLI mode)'
    );
    process.exit(1);
  }

  try {
    const { waitUntilExit } = render(React.createElement(App, { initialScreen: screen ?? undefined }));
    waitUntilExit().then(() => bridge.destroy());
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`Failed to launch TUI: ${msg}`);
    process.exit(1);
  }
}

main();
