import React, { useState, useMemo } from 'react';
import { Box, Text, useInput } from 'ink';
import TextInput from 'ink-text-input';
import Divider from '../components/divider.js';
import type { ModelEntry, BackendKey } from '../lib/types.js';
import { loadKeys } from '../lib/config-paths.js';
import { fetchG4FModels, fetchEAONCatalog, fetchEAONMonitor, testModelLive } from '../lib/api.js';
import Footer from '../components/footer.js';
import Spinner from 'ink-spinner';

interface ModelPickerScreenProps {
  onBack: () => void;
}

type BackendFilter = 'all' | 'G4F' | 'EAON';

export default function ModelPickerScreen({ onBack }: ModelPickerScreenProps) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<BackendFilter>('all');
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ model: string; passed: boolean; detail: string } | null>(null);

  const loadModels = async () => {
    setLoading(true);
    const keys = loadKeys();
    const all: ModelEntry[] = [];
    for (const [name, key] of Object.entries(keys)) {
      if (!key) continue;
      if (name === 'G4F') {
        const g4f = await fetchG4FModels('https://g4f.space/v1', key);
        all.push(...g4f);
      } else if (name === 'EAON') {
        const eaon = await fetchEAONCatalog('https://api.eaon.dev/v1', key);
        const operational = await fetchEAONMonitor('https://api.eaon.dev/v1', key);
        for (const m of eaon) {
          m.tier = operational.has(m.id) ? 'operational' : 'offline';
        }
        all.push(...eaon);
      }
    }
    setModels(all);
    setLoading(false);
  };

  const filteredModels = useMemo(() => {
    return models.filter(m => {
      const matchesFilter = filter === 'all' || m.backend === filter;
      const matchesQuery = !query || m.label.toLowerCase().includes(query.toLowerCase());
      return matchesFilter && matchesQuery;
    });
  }, [models, filter, query]);

  const toggleModel = (label: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  };

  const handleTest = async (model: ModelEntry) => {
    setTesting(model.label);
    setTestResult(null);
    const keys = loadKeys();
    const url = model.backend === 'G4F' ? 'https://g4f.space/v1' : 'https://api.eaon.dev/v1';
    const key = model.backend === 'G4F' ? keys.G4F : keys.EAON;
    const result = await testModelLive(model.backend, url, key, model.id);
    setTestResult({ model: model.label, ...result });
    setTesting(null);
  };

  useInput((input, key) => {
    if (key.escape || input === 'q') {
      onBack();
    } else if (input === 'l') {
      loadModels();
    } else if (input === 'a') {
      const all = filteredModels.map(m => m.label);
      setSelected(new Set(all));
    } else if (input === 'n') {
      setSelected(new Set());
    }
  });

  const g4fCount = models.filter(m => m.backend === 'G4F').length;
  const eaonCount = models.filter(m => m.backend === 'EAON').length;

  return (
    <Box flexDirection="column" padding={1}>
      <Text bold>Configure Models</Text>
      <Divider />

      <Box marginBottom={1}>
        <TextInput
          value={query}
          onChange={setQuery}
          placeholder="search models..."
        />
        <Text dimColor>  ({filteredModels.length} found)</Text>
      </Box>

      <Box gap={2} marginBottom={1}>
        <Text bold={filter === 'all'}>
          {filter === 'all' ? '◉' : '○'} All ({models.length})
        </Text>
        <Text bold={filter === 'G4F'}>
          {filter === 'G4F' ? '◉' : '○'} G4F ({g4fCount})
        </Text>
        <Text bold={filter === 'EAON'}>
          {filter === 'EAON' ? '◉' : '○'} EAON ({eaonCount})
        </Text>
      </Box>

      {loading ? (
        <Box>
          <Text><Spinner type="dots" /></Text>
          <Text> Loading models...</Text>
        </Box>
      ) : models.length === 0 ? (
        <Box flexDirection="column">
          <Text dimColor>No models loaded.</Text>
          <Text dimColor>Press [l] to load models from configured backends.</Text>
        </Box>
      ) : (
        <Box flexDirection="column" flexGrow={1}>
          {filteredModels.slice(0, 30).map(m => {
            const isSelected = selected.has(m.label);
            const isTesting = testing === m.label;
            return (
              <Box key={m.label}>
                <Text bold={isSelected}>
                  {isSelected ? '☑' : '☐'}
                </Text>
                <Text> {m.label}</Text>
                {m.requests > 0 && <Text dimColor>  {m.requests.toLocaleString()} reqs</Text>}
                {m.tier && (
                  <Text bold={m.tier === 'operational'}>
                    {' '}  {m.tier}
                  </Text>
                )}
                {isTesting && (
                  <Text>  <Spinner type="dots" /> testing...</Text>
                )}
              </Box>
            );
          })}
        </Box>
      )}

      {testResult && (
        <Box marginTop={1}>
          <Text bold={testResult.passed}>
            {testResult.passed ? '✓' : '✗'} {testResult.model}: {testResult.detail}
          </Text>
        </Box>
      )}

      <Divider />
      <Footer hints={[
        { key: 'l', label: 'load' },
        { key: 'a/n', label: 'all/none' },
        { key: 'Space', label: 'toggle' },
        { key: 'q', label: 'back' },
      ]} />
    </Box>
  );
}
