import React from 'react';
import { Box, Text } from 'ink';

interface DividerProps {
  title?: string;
  color?: string;
}

export default function Divider({ title, color = 'gray' }: DividerProps) {
  const lineChar = '─';
  const lineWidth = 40;

  if (!title) {
    return (
      <Box>
        <Text dimColor color={color}>{lineChar.repeat(lineWidth)}</Text>
      </Box>
    );
  }

  const pad = Math.max(0, Math.floor((lineWidth - title.length - 4) / 2));
  const left = lineChar.repeat(pad);
  const right = lineChar.repeat(pad);

  return (
    <Box>
      <Text dimColor color={color}>{left} </Text>
      <Text bold color={color}>{title}</Text>
      <Text dimColor color={color}> {right}</Text>
    </Box>
  );
}
