import React, { useState, useCallback } from 'react';
import { Box, Text, useInput } from 'ink';
import TextInput from 'ink-text-input';
import Divider from '../components/divider.js';
import {
  loadKeys, saveKeys, loadOnboardingState, saveOnboardingState,
  TARGET_TOOLS, checkToolInstalled,
} from '../lib/config-paths.js';
import type { TargetTool } from '../lib/types.js';
import StepIndicator from '../components/step-indicator.js';
import Footer from '../components/footer.js';

interface OnboardingScreenProps {
  onComplete: () => void;
  onBack: () => void;
}

const STEP_LABELS = ['Keys', 'IDEs', 'Done'];

export default function OnboardingScreen({ onComplete, onBack }: OnboardingScreenProps) {
  const [step, setStep] = useState(0);
  const existingKeys = loadKeys();
  const prevState = loadOnboardingState();

  const [g4fKey, setG4fKey] = useState(existingKeys.G4F || '');
  const [eaonKey, setEaonKey] = useState(existingKeys.EAON || '');
  const [selectedTargets, setSelectedTargets] = useState<Set<TargetTool>>(
    new Set(prevState.selectedTargets.length > 0 ? prevState.selectedTargets : ['opencode'])
  );
  const [focusField, setFocusField] = useState<'g4f' | 'eaon'>('g4f');

  const handleNext = useCallback(() => {
    if (step === 0) {
      saveKeys({ ...existingKeys, G4F: g4fKey, EAON: eaonKey });
    }
    if (step < 2) {
      setStep(s => s + 1);
    } else {
      saveOnboardingState({
        completed: true,
        selectedTargets: Array.from(selectedTargets),
        selectedModels: prevState.selectedModels,
      });
      onComplete();
    }
  }, [step, g4fKey, eaonKey, existingKeys, selectedTargets, prevState, onComplete]);

  const handleBack = useCallback(() => {
    if (step === 0) onBack();
    else setStep(s => s - 1);
  }, [step, onBack]);

  const toggleTarget = useCallback((target: TargetTool) => {
    setSelectedTargets(prev => {
      const next = new Set(prev);
      if (next.has(target)) next.delete(target);
      else next.add(target);
      return next;
    });
  }, []);

  useInput((input, key) => {
    if (key.escape) { handleBack(); return; }
    if (key.return) { handleNext(); return; }
    if (key.tab) { setFocusField(f => f === 'g4f' ? 'eaon' : 'g4f'); return; }
  });

  return (
    <Box flexDirection="column" padding={1}>
      <Box marginBottom={1} justifyContent="center">
        <StepIndicator currentStep={step} totalSteps={3} labels={STEP_LABELS} />
      </Box>

      <Divider title={`Step ${step + 1}/3 — ${STEP_LABELS[step]}`} />

      <Box marginTop={1} flexDirection="column">
        {step === 0 && (
          <StepKeys g4fKey={g4fKey} eaonKey={eaonKey} onG4fChange={setG4fKey} onEaonChange={setEaonKey} focusField={focusField} />
        )}
        {step === 1 && (
          <StepTools selected={selectedTargets} onToggle={toggleTarget} />
        )}
        {step === 2 && (
          <StepSummary g4fKey={g4fKey} eaonKey={eaonKey} targets={selectedTargets} />
        )}
      </Box>

      <Footer hints={[
        { key: 'Enter', label: step < 2 ? 'next' : 'launch' },
        { key: 'esc', label: step === 0 ? 'back' : 'prev' },
        { key: 'Tab', label: 'switch' },
      ]} />
    </Box>
  );
}

function StepKeys({ g4fKey, eaonKey, onG4fChange, onEaonChange, focusField }: {
  g4fKey: string; eaonKey: string;
  onG4fChange: (v: string) => void; onEaonChange: (v: string) => void;
  focusField: 'g4f' | 'eaon';
}) {
  return (
    <Box flexDirection="column">
      <Box flexDirection="column" marginBottom={1}>
        <Text bold>G4F API Key</Text>
        <TextInput value={g4fKey} onChange={onG4fChange} mask="*" placeholder="paste key..." focus={focusField === 'g4f'} />
        {g4fKey && <Text dimColor>  ✓ set</Text>}
      </Box>
      <Box flexDirection="column">
        <Text bold>EAON API Key (optional)</Text>
        <TextInput value={eaonKey} onChange={onEaonChange} mask="*" placeholder="paste key..." focus={focusField === 'eaon'} />
        {eaonKey && <Text dimColor>  ✓ set</Text>}
      </Box>
    </Box>
  );
}

function StepTools({ selected, onToggle }: {
  selected: Set<TargetTool>; onToggle: (t: TargetTool) => void;
}) {
  return (
    <Box flexDirection="column">
      {Object.entries(TARGET_TOOLS).map(([id, info]) => {
        const target = id as TargetTool;
        const isSelected = selected.has(target);
        const { installed } = checkToolInstalled(target);
        return (
          <Box key={id}>
            <Text bold={isSelected}>{isSelected ? '◉' : '○'}</Text>
            <Text> {info.name}</Text>
            <Text dimColor>  {installed ? '✓ installed' : 'not found'}</Text>
            {isSelected && <Text dimColor>  → {info.configFile}</Text>}
          </Box>
        );
      })}
    </Box>
  );
}

function StepSummary({ g4fKey, eaonKey, targets }: {
  g4fKey: string; eaonKey: string; targets: Set<TargetTool>;
}) {
  const targetList = Array.from(targets);
  return (
    <Box flexDirection="column">
      <Box flexDirection="column" borderStyle="round" paddingX={1}>
        <Text>Keys:     G4F {g4fKey ? '✓' : '✗'}  EAON {eaonKey ? '✓' : '✗'}</Text>
        <Text>Targets:  {targetList.length > 0 ? targetList.join(', ') : 'none'}</Text>
      </Box>
      <Box marginTop={1} flexDirection="column">
        <Text bold>Configs to write:</Text>
        {targetList.map(t => {
          const info = TARGET_TOOLS[t];
          return <Text key={t}>  ✓ ~/{info.configDir.replace(/^\/home\/[^/]+/, '~')}/{info.configFile}</Text>;
        })}
      </Box>
    </Box>
  );
}
