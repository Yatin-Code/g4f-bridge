import os from 'node:os';
import path from 'node:path';
import fs from 'node:fs';
import { execSync } from 'node:child_process';
import type { TargetTool, TargetToolInfo } from './types.js';

// ── Config directory ──

function getSettingsPath(): string {
  return path.join(getBridgeConfigDir(), 'settings.json');
}

function loadSettings(): Record<string, unknown> {
  try {
    const p = getSettingsPath();
    if (fs.existsSync(p)) return JSON.parse(fs.readFileSync(p, 'utf-8'));
  } catch {}
  return {};
}

function saveSettings(data: Record<string, unknown>): void {
  const dir = getBridgeConfigDir();
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(getSettingsPath(), JSON.stringify(data, null, 2), 'utf-8');
}

export function getBridgeConfigDir(): string {
  if (process.env.G4F_BRIDGE_CONFIG_DIR) return process.env.G4F_BRIDGE_CONFIG_DIR;
  const s = loadSettings();
  if (typeof s.configDir === 'string' && s.configDir) return s.configDir;
  if (os.platform() === 'win32') {
    return path.join(process.env.APPDATA || os.homedir(), 'g4f-bridge');
  }
  return path.join(os.homedir(), '.g4f-bridge');
}

export function setBridgeConfigDir(dir: string): void {
  const s = loadSettings();
  s.configDir = dir;
  saveSettings(s);
}

// ── Keys (dynamic providers) ──

export function getKeysPath(): string {
  return path.join(getBridgeConfigDir(), 'keys.json');
}

export function loadKeys(): Record<string, string> {
  migrateOldConfig();
  try {
    const p = getKeysPath();
    if (fs.existsSync(p)) return JSON.parse(fs.readFileSync(p, 'utf-8'));
  } catch {}
  return {};
}

export function saveKeys(keys: Record<string, string>): void {
  const dir = getBridgeConfigDir();
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(getKeysPath(), JSON.stringify(keys, null, 2), 'utf-8');
}

export function addKey(name: string, value: string): void {
  const keys = loadKeys();
  keys[name] = value;
  saveKeys(keys);
}

export function removeKey(name: string): void {
  const keys = loadKeys();
  delete keys[name];
  saveKeys(keys);
}

// ── Onboarding state ──

export interface OnboardingState {
  completed: boolean;
  selectedTargets: TargetTool[];
  selectedModels: string[];
}

export function loadOnboardingState(): OnboardingState {
  const s = loadSettings();
  return {
    completed: typeof s.onboardingCompleted === 'boolean' ? s.onboardingCompleted : false,
    selectedTargets: Array.isArray(s.selectedTargets) ? s.selectedTargets : ['opencode'],
    selectedModels: Array.isArray(s.selectedModels) ? s.selectedModels : [],
  };
}

export function saveOnboardingState(state: OnboardingState): void {
  const s = loadSettings();
  s.onboardingCompleted = state.completed;
  s.selectedTargets = state.selectedTargets;
  s.selectedModels = state.selectedModels;
  saveSettings(s);
}

export function isFirstRun(): boolean {
  return !loadOnboardingState().completed;
}

// ── Migration ──

function migrateOldConfig(): void {
  const oldDir = path.join(os.homedir(), '.opencode-g4f-bridge');
  const newDir = getBridgeConfigDir();
  if (fs.existsSync(oldDir) && !fs.existsSync(newDir)) {
    try {
      fs.mkdirSync(newDir, { recursive: true });
      const oldKeys = path.join(oldDir, 'keys.json');
      if (fs.existsSync(oldKeys)) fs.copyFileSync(oldKeys, path.join(newDir, 'keys.json'));
    } catch {}
  }
}

// ── Target IDEs ──

export function getOpenCodeConfigDir(): string {
  const xdg = process.env.XDG_CONFIG_HOME;
  return xdg ? path.join(xdg, 'opencode') : path.join(os.homedir(), '.config', 'opencode');
}

export function getClaudeCodeConfigDir(): string {
  return path.join(os.homedir(), '.claude');
}

export function getCodexConfigDir(): string {
  return path.join(os.homedir(), '.codex');
}

export function getCursorConfigDir(): string {
  return path.join(os.homedir(), '.cursor');
}

export function getAntigravityConfigDir(): string {
  return path.join(os.homedir(), '.gemini');
}

export const TARGET_TOOLS: Record<TargetTool, TargetToolInfo> = {
  'opencode': {
    id: 'opencode', name: 'OpenCode',
    configDir: getOpenCodeConfigDir(), configFile: 'opencode.json',
    installCmd: '', installUrl: 'https://github.com/anomalyco/opencode',
  },
  'claude-code': {
    id: 'claude-code', name: 'Claude Code',
    configDir: getClaudeCodeConfigDir(), configFile: 'settings.json',
    installCmd: 'curl -fsSL https://claude.ai/install.sh | bash',
    installUrl: 'https://claude.ai/code',
  },
  'codex': {
    id: 'codex', name: 'Codex CLI',
    configDir: getCodexConfigDir(), configFile: 'config.toml',
    installCmd: 'curl -fsSL https://chatgpt.com/codex/install.sh | sh',
    installUrl: 'https://developers.openai.com/codex',
  },
  'cursor': {
    id: 'cursor', name: 'Cursor',
    configDir: getCursorConfigDir(), configFile: 'settings.json',
    installCmd: 'curl https://cursor.com/install -fsS | bash',
    installUrl: 'https://cursor.sh',
  },
  'antigravity': {
    id: 'antigravity', name: 'Antigravity',
    configDir: getAntigravityConfigDir(), configFile: 'settings.json',
    installCmd: 'curl -fsSL https://antigravity.google/cli/install.sh | bash',
    installUrl: 'https://antigravity.google',
  },
};

export function getTargetConfigPath(tool: TargetTool): string {
  const info = TARGET_TOOLS[tool];
  return path.join(info.configDir, info.configFile);
}

export function checkToolInstalled(tool: TargetTool): { installed: boolean; cmd?: string } {
  const cmdMap: Partial<Record<TargetTool, string>> = {
    'claude-code': 'claude', 'codex': 'codex', 'cursor': 'cursor-agent', 'antigravity': 'agy',
  };
  const cmd = cmdMap[tool];
  if (!cmd) return { installed: true };
  try { execSync(`which ${cmd}`, { stdio: 'ignore' }); return { installed: true, cmd }; }
  catch { return { installed: false, cmd }; }
}
