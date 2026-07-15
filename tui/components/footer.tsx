import React from 'react';
import { Box, Text } from 'ink';

interface FooterHint {
  key: string;
  label: string;
}

interface FooterProps {
  hints: FooterHint[];
}

export default function Footer({ hints }: FooterProps) {
  return (
    <Box marginTop={1} justifyContent="flex-start" flexWrap="wrap">
      {hints.map((hint, i) => (
        <Box key={i} marginRight={2}>
          <Text>[{hint.key}]</Text>
          <Text dimColor> {hint.label}</Text>
        </Box>
      ))}
    </Box>
  );
}
