import React, { useState, useEffect } from 'react';
import { Box, Text } from 'ink';

export type ToastType = 'success' | 'error' | 'warning' | 'info';

interface ToastProps {
  message: string;
  type?: ToastType;
  duration?: number;
  onDismiss?: () => void;
}

const TOAST_CONFIG: Record<ToastType, { bold: boolean; icon: string }> = {
  success: { bold: true, icon: '✓' },
  error: { bold: true, icon: '✗' },
  warning: { bold: true, icon: '!' },
  info: { bold: false, icon: 'i' },
};

export default function Toast({ message, type = 'info', duration = 3000, onDismiss }: ToastProps) {
  const [visible, setVisible] = useState(true);
  const config = TOAST_CONFIG[type];

  useEffect(() => {
    const timer = setTimeout(() => {
      setVisible(false);
      onDismiss?.();
    }, duration);
    return () => clearTimeout(timer);
  }, [duration, onDismiss]);

  if (!visible) return null;

  return (
    <Box borderStyle="round" paddingX={1}>
      <Text bold={config.bold}>{config.icon}</Text>
      <Text> {message}</Text>
    </Box>
  );
}
