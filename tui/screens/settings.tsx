import React, { useState } from 'react';
import { Box, Text, useInput } from 'ink';
import TextInput from 'ink-text-input';
import Divider from '../components/divider.js';
import {
  loadKeys, saveKeys, addKey, removeKey,
  getBridgeConfigDir, setBridgeConfigDir,
  TARGET_TOOLS, getTargetConfigPath, checkToolInstalled,
} from '../lib/config-paths.js';
import Footer from '../components/footer.js';

interface SettingsScreenProps {
  onBack: () => void;
}

type View = 'main' | 'edit-key' | 'add-key' | 'edit-dir';

export default function SettingsScreen({ onBack }: SettingsScreenProps) {
  const [view, setView] = useState<View>('main');
  const [keys, setKeys] = useState(loadKeys());
  const [selectedKey, setSelectedKey] = useState('');
  const [editValue, setEditValue] = useState('');
  const [newKeyName, setNewKeyName] = useState('');
  const [cursor, setCursor] = useState(0);

  const keyNames = Object.keys(keys);
  const allItems = [
    ...keyNames.map(k => ({ type: 'key' as const, label: k, value: k })),
    { type: 'action' as const, label: '[+] Add provider', value: 'add' },
    { type: 'action' as const, label: '[d] Change config directory', value: 'dir' },
    { type: 'action' as const, label: '[esc] Back', value: 'back' },
  ];

  const refreshKeys = () => setKeys(loadKeys());

  useInput((input, key) => {
    if (view === 'main') {
      if (key.escape) {
        onBack();
      } else if (key.upArrow && cursor > 0) {
        setCursor(c => c - 1);
      } else if (key.downArrow && cursor < allItems.length - 1) {
        setCursor(c => c + 1);
      } else if (key.return) {
        const item = allItems[cursor];
        if (item.type === 'key') {
          setSelectedKey(item.value);
          setEditValue(keys[item.value] || '');
          setView('edit-key');
        } else if (item.value === 'add') {
          setNewKeyName('');
          setEditValue('');
          setView('add-key');
        } else if (item.value === 'dir') {
          setEditValue(getBridgeConfigDir());
          setView('edit-dir');
        } else if (item.value === 'back') {
          onBack();
        }
      } else if (input === 'x' && allItems[cursor]?.type === 'key') {
        removeKey(allItems[cursor].value);
        refreshKeys();
        if (cursor >= Object.keys(keys).length) {
          setCursor(Math.max(0, Object.keys(keys).length - 1));
        }
      }
    } else if (view === 'edit-key') {
      if (key.escape) {
        setView('main');
      } else if (key.return) {
        addKey(selectedKey, editValue);
        refreshKeys();
        setView('main');
      }
    } else if (view === 'add-key') {
      if (key.escape) {
        setView('main');
      } else if (key.return && newKeyName.trim()) {
        addKey(newKeyName.trim(), editValue);
        refreshKeys();
        setView('main');
      }
    } else if (view === 'edit-dir') {
      if (key.escape) {
        setView('main');
      } else if (key.return && editValue.trim()) {
        setBridgeConfigDir(editValue.trim());
        refreshKeys();
        setView('main');
      }
    }
  });

  if (view === 'edit-key') {
    return (
      <Box flexDirection="column" padding={1}>
        <Text bold>Edit Key: {selectedKey}</Text>
        <Divider />
        <Box marginTop={1}>
          <Text>Value: </Text>
          <TextInput
            value={editValue}
            onChange={setEditValue}
            mask="*"
            placeholder="enter key value..."
          />
        </Box>
        <Box marginTop={1}>
          <Text dimColor>Enter to save, Esc to cancel</Text>
        </Box>
      </Box>
    );
  }

  if (view === 'add-key') {
    return (
      <Box flexDirection="column" padding={1}>
        <Text bold>Add New Provider</Text>
        <Divider />
        <Box marginTop={1}>
          <Text>Name: </Text>
          <TextInput
            value={newKeyName}
            onChange={setNewKeyName}
            placeholder="e.g. OPENAI, ANTHROPIC..."
          />
        </Box>
        <Box marginTop={1}>
          <Text>Key:  </Text>
          <TextInput
            value={editValue}
            onChange={setEditValue}
            mask="*"
            placeholder="paste API key..."
          />
        </Box>
        <Box marginTop={1}>
          <Text dimColor>Enter to save, Esc to cancel</Text>
        </Box>
      </Box>
    );
  }

  if (view === 'edit-dir') {
    return (
      <Box flexDirection="column" padding={1}>
        <Text bold>Config Directory</Text>
        <Divider />
        <Box marginTop={1}>
          <Text>Path: </Text>
          <TextInput
            value={editValue}
            onChange={setEditValue}
            placeholder="enter directory path..."
          />
        </Box>
        <Box marginTop={1}>
          <Text dimColor>Current: {getBridgeConfigDir()}</Text>
        </Box>
        <Box marginTop={1}>
          <Text dimColor>Enter to save, Esc to cancel</Text>
        </Box>
      </Box>
    );
  }

  return (
    <Box flexDirection="column" padding={1}>
      <Text bold>Settings</Text>
      <Divider title="Config Directory" />

      <Box>
        <Text>{getBridgeConfigDir()}</Text>
      </Box>

      <Divider title="API Keys" />

      <Box flexDirection="column">
        {keyNames.length === 0 ? (
          <Text dimColor>No providers configured. Press [+] to add one.</Text>
        ) : (
          keyNames.map((name, i) => (
            <Box key={name}>
              <Text bold={i === cursor}>{i === cursor ? '›' : ' '}</Text>
              <Text> {name.padEnd(12)}</Text>
              <Text dimColor>{keys[name] ? '●●●●●●●●●●●●●●●●' : '(empty)'}</Text>
              {i === cursor && <Text dimColor> [Enter] edit  [x] remove</Text>}
            </Box>
          ))
        )}
        <Box>
          <Text bold={cursor === keyNames.length}>{cursor === keyNames.length ? '›' : ' '}</Text>
          <Text> [+] Add provider</Text>
        </Box>
        <Box>
          <Text bold={cursor === keyNames.length + 1}>{cursor === keyNames.length + 1 ? '›' : ' '}</Text>
          <Text> [d] Change config directory</Text>
        </Box>
      </Box>

      <Divider title="Config Targets" />

      <Box flexDirection="column">
        {(Object.keys(TARGET_TOOLS) as Array<keyof typeof TARGET_TOOLS>).map(id => {
          const info = TARGET_TOOLS[id];
          const configPath = getTargetConfigPath(id);
          const { installed } = checkToolInstalled(id);
          return (
            <Box key={id}>
              <Text>{info.name.padEnd(16)}</Text>
              <Text dimColor>{configPath}</Text>
              <Text> </Text>
              <Text bold={installed}>{installed ? '✓' : '✗'}</Text>
            </Box>
          );
        })}
      </Box>

      <Divider />
      <Footer hints={[
        { key: '↑↓', label: 'navigate' },
        { key: '↵', label: 'select' },
        { key: 'x', label: 'remove' },
        { key: 'esc', label: 'back' },
      ]} />
    </Box>
  );
}
