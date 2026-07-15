import React, { useState, useCallback, useEffect } from 'react';
import { Box, Text, useInput, useApp } from 'ink';
import type { Screen, HealthStatus } from './lib/types.js';
import { loadKeys, isFirstRun, loadOnboardingState } from './lib/config-paths.js';
import { healthCheck as checkBridgeHealth } from './lib/api.js';
import { bridge } from './lib/bridge-process.js';
import WelcomeScreen from './screens/welcome.js';
import OnboardingScreen from './screens/onboarding.js';
import DashboardScreen from './screens/dashboard.js';
import ModelPickerScreen from './screens/model-picker.js';
import SettingsScreen from './screens/settings.js';

interface AppProps {
  initialScreen?: Screen;
}

export default function App({ initialScreen }: AppProps) {
  const { exit } = useApp();

  const [screen, setScreen] = useState<Screen>(() => {
    if (initialScreen) return initialScreen;
    if (isFirstRun()) return 'onboarding';
    return 'dashboard';
  });
  const [health, setHealth] = useState<HealthStatus>(() => {
    const keys = loadKeys();
    const keyStatus: Record<string, boolean> = {};
    for (const [name, value] of Object.entries(keys)) {
      keyStatus[name] = !!value;
    }
    return {
      bridge: 'stopped',
      keys: keyStatus,
      port: 1337,
      portFree: true,
      modelCount: 0,
      uptime: 0,
      requestCount: 0,
    };
  });

  const checkHealth = useCallback(async () => {
    const running = await checkBridgeHealth();
    setHealth(prev => ({ ...prev, bridge: running ? 'running' : 'stopped' }));
  }, []);

  useEffect(() => {
    if (!isFirstRun() && screen === 'dashboard') {
      const state = loadOnboardingState();
      const args = ['-b'];
      if (state.selectedTargets.length > 0) {
        args.push('--target', ...state.selectedTargets);
      }
      setHealth(prev => ({ ...prev, bridge: 'starting' }));
      bridge.start(args);
    }
  }, []);

  useEffect(() => {
    const interval = setInterval(checkHealth, 3000);
    return () => clearInterval(interval);
  }, [checkHealth]);

  useInput((input, key) => {
    if (key.escape) {
      if (screen === 'welcome' || screen === 'dashboard') {
        bridge.destroy();
        exit();
      } else {
        setScreen('welcome');
      }
    }
  });

  const getBridgeArgs = useCallback((): string[] => {
    const state = loadOnboardingState();
    const args = ['-b'];
    if (state.selectedTargets.length > 0) {
      args.push('--target', ...state.selectedTargets);
    }
    return args;
  }, []);

  const startBridge = useCallback(() => {
    setHealth(prev => ({ ...prev, bridge: 'starting' }));
    bridge.start(getBridgeArgs());
    setHealth(prev => ({ ...prev, bridge: 'running' }));
    setScreen('dashboard');
  }, [getBridgeArgs]);

  switch (screen) {
    case 'welcome':
      return (
        <WelcomeScreen
          health={health}
          onStartBridge={startBridge}
          onSetup={() => setScreen('onboarding')}
          onModelPicker={() => setScreen('model-picker')}
          onSettings={() => setScreen('settings')}
          onExit={() => { bridge.destroy(); exit(); }}
        />
      );
    case 'onboarding':
      return (
        <OnboardingScreen
          onComplete={() => {
            setHealth(prev => ({ ...prev, bridge: 'starting' }));
            bridge.start(getBridgeArgs());
            setScreen('dashboard');
          }}
          onBack={() => setScreen('welcome')}
          onExit={() => { bridge.destroy(); exit(); }}
        />
      );
    case 'model-picker':
      return <ModelPickerScreen onBack={() => setScreen('welcome')} />;
    case 'settings':
      return <SettingsScreen onBack={() => setScreen('welcome')} />;
    case 'dashboard':
      return (
        <DashboardScreen
          health={health}
          onStop={() => { bridge.stop(); setScreen('welcome'); }}
          onSettings={() => setScreen('settings')}
          onModelPicker={() => setScreen('model-picker')}
        />
      );
    default:
      return (
        <WelcomeScreen
          health={health}
          onStartBridge={startBridge}
          onSetup={() => setScreen('onboarding')}
          onModelPicker={() => setScreen('model-picker')}
          onSettings={() => setScreen('settings')}
          onExit={() => { bridge.destroy(); exit(); }}
        />
      );
  }
}
