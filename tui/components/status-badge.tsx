import React from 'react';
import { Box, Text } from 'ink';

export type BadgeStatus = 'online' | 'offline' | 'warning' | 'loading' | 'set' | 'not-set';

interface StatusBadgeProps {
  label: string;
  status: BadgeStatus;
  value?: string;
}

const STATUS_CONFIG: Record<BadgeStatus, { bold: boolean; icon: string }> = {
  online: { bold: true, icon: '●' },
  offline: { bold: false, icon: '○' },
  warning: { bold: true, icon: '◐' },
  loading: { bold: true, icon: '◐' },
  set: { bold: true, icon: '✓' },
  'not-set': { bold: false, icon: '✗' },
};

export default function StatusBadge({ label, status, value }: StatusBadgeProps) {
  const { bold, icon } = STATUS_CONFIG[status];

  return (
    <Box>
      <Text>{label.padEnd(14)}</Text>
      <Text bold={bold}>{icon} {value || status}</Text>
    </Box>
  );
}
