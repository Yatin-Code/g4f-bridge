import { defineConfig } from 'tsup';

export default defineConfig({
  entry: ['index.tsx'],
  format: ['esm'],
  target: 'node22',
  clean: true,
  banner: {
    js: '#!/usr/bin/env node',
  },
  external: [
    'react',
    'react/jsx-runtime',
    'react-dom',
    'ink',
    '@inkjs/ui',
    'ink-gradient',
    'ink-big-text',
    'ink-spinner',
    'ink-text-input',
    'ink-select-input',
    'ink-table',

  ],
});
