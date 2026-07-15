import React from 'react';
import { Box, Text, useInput, useApp } from 'ink';
import SelectInput from 'ink-select-input';
import type { HealthStatus } from '../lib/types.js';
import AnimatedLogo from '../components/animated-logo.js';
import StatusBadge from '../components/status-badge.js';
import Footer from '../components/footer.js';

interface WelcomeScreenProps {
  health: HealthStatus;
  onStartBridge: () => void;
  onSetup: () => void;
  onModelPicker: () => void;
  onSettings: () => void;
  onExit: () => void;
}

export default function WelcomeScreen({
  health,
  onStartBridge,
  onSetup,
  onModelPicker,
  onSettings,
  onExit,
}: WelcomeScreenProps) {
  const { exit } = useApp();

  useInput((input, key) => {
    if (input === 'q' || input === 'Q' || key.escape) {
      onExit();
    }
  });

  const bridgeStatus = health.bridge === 'running' ? 'online' as const
    : health.bridge === 'starting' ? 'loading' as const
    : 'offline' as const;

  const items = [
    { label: 'Start Bridge & Dashboard', value: 'start' },
    { label: 'Run Setup Wizard', value: 'setup' },
    { label: 'Configure Models', value: 'models' },
    { label: 'Settings', value: 'settings' },
    { label: 'Quit', value: 'quit' },
  ];

  const handleSelect = (item: { label: string; value: string }) => {
    switch (item.value) {
      case 'start': onStartBridge(); break;
      case 'setup': onSetup(); break;
      case 'models': onModelPicker(); break;
      case 'settings': onSettings(); break;
      case 'quit': onExit(); break;
    }
  };

  return (
    <Box flexDirection="column" padding={1}>
      <AnimatedLogo />

      <Box flexDirection="column" borderStyle="round" paddingX={1} marginBottom={1} marginTop={1}>
        <StatusBadge
          label="Bridge"
          status={bridgeStatus}
          value={health.bridge.toUpperCase()}
        />
        {Object.entries(health.keys).map(([name, isSet]) => (
          <StatusBadge
            key={name}
            label="API Key"
            status={isSet ? 'set' : 'not-set'}
            value={`${name} ${isSet ? '✓ set' : '✗ not set'}`}
          />
        ))}
        {Object.keys(health.keys).length === 0 && (
          <StatusBadge
            label="API Keys"
            status="not-set"
            value="no providers configured"
          />
        )}
        <StatusBadge
          label="Port 1337"
          status={health.portFree ? 'online' : 'offline'}
          value={health.portFree ? '● free' : '● in use'}
        />
      </Box>

      <Box marginBottom={1}>
        <SelectInput items={items} onSelect={handleSelect} />
      </Box>

      <Footer hints={[
        { key: '↑↓', label: 'navigate' },
        { key: '↵', label: 'select' },
        { key: 'q', label: 'quit' },
      ]} />
    </Box>
  );
}
