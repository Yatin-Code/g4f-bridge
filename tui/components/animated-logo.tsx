import React from 'react';
import BigText from 'ink-big-text';
import Divider from './divider.js';
import { Box, Text } from 'ink';

export default function AnimatedLogo() {
  return (
    <Box flexDirection="column" alignItems="center">
      <BigText text="G4F·BRIDGE" font="chrome" colors={['white']} />
      <Box marginTop={0}>
        <Text dimColor>Multi-tool API bridge for G4F &amp; EAON proxy networks</Text>
      </Box>
      <Divider />
    </Box>
  );
}
