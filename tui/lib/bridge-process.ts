import { spawn, type ChildProcess } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { existsSync } from 'node:fs';

function findProjectRoot(fromDir: string): string {
  const distRoot = resolve(fromDir, '..', '..');
  if (existsSync(resolve(distRoot, 'smart_bridge.py'))) {
    return distRoot;
  }
  const srcRoot = resolve(fromDir, '..');
  if (existsSync(resolve(srcRoot, 'smart_bridge.py'))) {
    return srcRoot;
  }
  return process.cwd();
}

const _dirname = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = findProjectRoot(_dirname);

function getPythonScript(): string {
  return resolve(PROJECT_ROOT, process.env.PYTHON_SCRIPT || 'smart_bridge.py');
}

export interface BridgeEvents {
  stdout: [line: string];
  stderr: [line: string];
  exit: [code: number | null, signal: NodeJS.Signals | null];
  error: [error: Error];
}

export class BridgeProcess extends EventEmitter<BridgeEvents> {
  private proc: ChildProcess | null = null;
  private _uptime = 0;
  private _uptimeInterval: ReturnType<typeof setInterval> | null = null;
  private _buffer = '';

  get isRunning(): boolean {
    return this.proc !== null && this.proc.exitCode === null && this.proc.killed === false;
  }

  get uptime(): number {
    return this._uptime;
  }

  get pythonArgv(): string[] {
    return process.argv.slice(2).filter(a => a !== '--tui');
  }

  hasCLIArgs(): boolean {
    const args = this.pythonArgv;
    return args.some(a =>
      a.startsWith('-b') || a.startsWith('--best') ||
      a.startsWith('-m') || a.startsWith('--model') ||
      a.startsWith('-t') || a.startsWith('--test') ||
      a.startsWith('-s') || a.startsWith('--setup') ||
      a.startsWith('--target') ||
      a === '--help' || a === '-h'
    );
  }

  start(extraArgs: string[] = []): void {
    if (this.isRunning) return;
    const args = extraArgs.length > 0 ? extraArgs : this.pythonArgv;

    this.proc = spawn('python3', [getPythonScript(), ...args], {
      cwd: PROJECT_ROOT,
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    this._buffer = '';

    this.proc.stdout!.on('data', (data: Buffer) => {
      this._buffer += data.toString();
      const lines = this._buffer.split('\n');
      this._buffer = lines.pop() ?? '';
      for (const line of lines) {
        this.emit('stdout', line);
      }
    });

    this.proc.stderr!.on('data', (data: Buffer) => {
      const text = data.toString();
      const lines = text.split('\n').filter(l => l.trim());
      for (const line of lines) {
        this.emit('stderr', line);
      }
    });

    this.proc.on('exit', (code, signal) => {
      if (this._buffer.trim()) {
        this.emit('stdout', this._buffer);
        this._buffer = '';
      }
      this.emit('exit', code, signal);
      this.proc = null;
    });

    this.proc.on('error', (err) => {
      this.emit('error', err);
      this.proc = null;
    });

    this._uptime = 0;
    this._uptimeInterval = setInterval(() => {
      this._uptime++;
    }, 1000);
  }

  stop(): void {
    if (this._uptimeInterval) {
      clearInterval(this._uptimeInterval);
      this._uptimeInterval = null;
    }
    if (this.proc) {
      this.proc.kill('SIGTERM');
      setTimeout(() => {
        if (this.proc && !this.proc.killed) {
          this.proc.kill('SIGKILL');
        }
      }, 5000);
    }
  }

  restart(extraArgs: string[] = []): void {
    this.stop();
    setTimeout(() => this.start(extraArgs), 1000);
  }

  destroy(): void {
    this.stop();
    this.removeAllListeners();
  }
}

export const bridge = new BridgeProcess();
