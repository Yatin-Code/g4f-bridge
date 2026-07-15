import React, { useState, useEffect, useRef } from 'react';
import { Box, Text, Static, useInput } from 'ink';
import Divider from '../components/divider.js';
import type { HealthStatus } from '../lib/types.js';
import { bridge } from '../lib/bridge-process.js';
import Footer from '../components/footer.js';

interface DashboardScreenProps {
  health: HealthStatus;
  onStop: () => void;
  onExit: () => void;
  onSettings: () => void;
  onModelPicker: () => void;
}

interface LogEntry {
  id: number;
  timestamp: string;
  message: string;
  level: 'info' | 'error' | 'warn';
}

export default function DashboardScreen({ health, onStop, onExit, onSettings, onModelPicker }: DashboardScreenProps) {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const logIdRef = useRef(0);

  useEffect(() => {
    const handleStdout = (line: string) => {
      const now = new Date();
      const ts = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}:${now.getSeconds().toString().padStart(2, '0')}`;
      const level: LogEntry['level'] = line.includes('ERROR') || line.includes('error') ? 'error'
        : line.includes('WARN') || line.includes('warn') ? 'warn'
        : 'info';
      setLogs(prev => [...prev.slice(-200), { id: logIdRef.current++, timestamp: ts, message: line, level }]);
    };

    const handleStderr = (line: string) => {
      const now = new Date();
      const ts = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}:${now.getSeconds().toString().padStart(2, '0')}`;
      setLogs(prev => [...prev.slice(-200), { id: logIdRef.current++, timestamp: ts, message: line, level: 'error' }]);
    };

    bridge.on('stdout', handleStdout);
    bridge.on('stderr', handleStderr);
    return () => {
      bridge.off('stdout', handleStdout);
      bridge.off('stderr', handleStderr);
    };
  }, []);

  useInput((input, key) => {
    if (input === 'q') {
      onExit();
    } else if (key.escape) {
      onStop();
    } else if (input === 'r') {
      bridge.restart();
    } else if (input === 'c') {
      onSettings();
    } else if (input === 'm') {
      onModelPicker();
    }
  });

  const formatUptime = (seconds: number): string => {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}m ${s}s`;
  };

  return (
    <Box flexDirection="column" height="100%">
      <Box justifyContent="space-between" alignItems="center" marginBottom={0}>
        <Box>
          <Text bold>⚡ BRIDGE RUNNING</Text>
          <Text dimColor>  ──── Port {health.port}</Text>
        </Box>
        <Box>
          <Text dimColor>Uptime {formatUptime(health.uptime)}</Text>
          <Text dimColor>  ──── </Text>
          <Text>{logs.length} logs</Text>
        </Box>
      </Box>

      <Divider />

      <Box flexGrow={1}>
        <Box width="35%" flexDirection="column" borderStyle="single" paddingX={1}>
          <Text bold>MODELS</Text>
          <Box marginTop={0}>
            <Text dimColor>G4F models configured</Text>
          </Box>
          <Box marginTop={1}>
            <Text dimColor>EAON models configured</Text>
          </Box>
          <Divider />
          <Box>
            <Text dimColor>Requests: </Text>
            <Text>{health.requestCount}</Text>
          </Box>
        </Box>

        <Box width="65%" flexDirection="column" paddingX={1}>
          <Text bold>LIVE LOGS</Text>
          <Box marginTop={0} flexGrow={1} flexDirection="column">
            <Static items={logs}>
              {(log) => (
                <Box key={log.id}>
                  <Text dimColor>{log.timestamp} </Text>
                  <Text bold={log.level === 'error'}>
                    {log.message}
                  </Text>
                </Box>
              )}
            </Static>
          </Box>
        </Box>
      </Box>

      <Divider />

      <Footer hints={[
        { key: 'q', label: 'quit' },
        { key: 'esc', label: 'stop' },
        { key: 'r', label: 'restart' },
        { key: 'c', label: 'config' },
        { key: 'm', label: 'models' },
      ]} />
    </Box>
  );
}
