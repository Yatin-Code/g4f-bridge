export type BackendKey = 'G4F' | 'EAON';

export interface BackendConfig {
  url: string;
  key: string;
}

export type Backends = Partial<Record<BackendKey, BackendConfig>>;

export interface ModelEntry {
  id: string;
  label: string;
  model: string;
  requests: number;
  backend: BackendKey;
  tier?: string;
}

export type BridgeState = 'stopped' | 'starting' | 'running' | 'error' | 'restarting';

export type Screen = 'welcome' | 'onboarding' | 'dashboard' | 'model-picker' | 'settings';

export interface LogEntry {
  timestamp: string;
  level: 'info' | 'warn' | 'error' | 'debug';
  message: string;
}

export type TargetTool = 'opencode' | 'claude-code' | 'codex' | 'cursor' | 'antigravity';

export const ALL_TARGETS: TargetTool[] = ['opencode', 'claude-code', 'codex', 'cursor', 'antigravity'];

export interface TargetToolInfo {
  id: TargetTool;
  name: string;
  configDir: string;
  configFile: string;
  installCmd: string;
  installUrl: string;
}

export interface HealthStatus {
  bridge: BridgeState;
  keys: Record<string, boolean>;
  port: number;
  portFree: boolean;
  modelCount: number;
  uptime: number;
  requestCount: number;
}
