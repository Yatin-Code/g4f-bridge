import React, { useState, useCallback } from 'react';
import { Box, Text, useInput } from 'ink';
import TextInput from 'ink-text-input';
import { execSync } from 'node:child_process';
import AnimatedLogo from '../components/animated-logo.js';
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
  onExit: () => void;
}

const STEP_LABELS = ['Keys', 'IDEs', 'Done'];

export default function OnboardingScreen({ onComplete, onBack, onExit }: OnboardingScreenProps) {
  const [step, setStep] = useState(0);
  const existingKeys = loadKeys();
  const prevState = loadOnboardingState();

  const [g4fKey, setG4fKey] = useState(existingKeys.G4F || '');
  const [eaonKey, setEaonKey] = useState(existingKeys.EAON || '');
  const [selectedTargets, setSelectedTargets] = useState<Set<TargetTool>>(
    new Set(prevState.selectedTargets.length > 0 ? prevState.selectedTargets : ['opencode'])
  );
  const [focusField, setFocusField] = useState<'g4f' | 'eaon'>('g4f');
  const [ideCursor, setIdeCursor] = useState(0);
  const [installPrompt, setInstallPrompt] = useState<TargetTool | null>(null);
  const [installStatus, setInstallStatus] = useState<'prompting' | 'installing' | 'error' | null>(null);
  const targetKeys = Object.keys(TARGET_TOOLS) as TargetTool[];

  const saveTargets = useCallback((targets: Set<TargetTool>) => {
    saveOnboardingState({
      completed: false,
      selectedTargets: Array.from(targets),
      selectedModels: prevState.selectedModels,
    });
  }, [prevState.selectedModels]);

  const doInstall = useCallback((target: TargetTool) => {
    const info = TARGET_TOOLS[target];
    if (!info.installCmd) {
      setInstallStatus(null);
      setInstallPrompt(null);
      return;
    }
    setInstallStatus('installing');
    try {
      execSync(info.installCmd, { stdio: 'inherit', timeout: 120000 });
      setInstallStatus(null);
      setInstallPrompt(null);
      const next = new Set(selectedTargets);
      next.add(target);
      setSelectedTargets(next);
      saveTargets(next);
    } catch {
      setInstallStatus('error');
    }
  }, [selectedTargets, saveTargets]);

  const handleNext = useCallback(() => {
    if (step === 0) {
      saveKeys({ ...existingKeys, G4F: g4fKey, EAON: eaonKey });
    }
    if (step < 2) {
      setStep(s => s + 1);
      setIdeCursor(0);
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
    if (installPrompt) { setInstallPrompt(null); setInstallStatus(null); return; }
    if (step === 0) onExit();
    else setStep(s => s - 1);
  }, [step, onExit, installPrompt]);

  useInput((input, key) => {
    if (key.escape) { handleBack(); return; }
    if (key.rightArrow) {
      if (!installPrompt) { handleNext(); return; }
    }
    if (key.tab) { setFocusField(f => f === 'g4f' ? 'eaon' : 'g4f'); return; }

    if (installPrompt) {
      if (installStatus === 'error' && (input === 'e' || key.return || key.rightArrow)) {
        setInstallPrompt(null);
        setInstallStatus(null);
        return;
      }
      if (input === 'y' || input === 'Y') {
        doInstall(installPrompt);
        return;
      }
      if (input === 'n' || input === 'N') {
        const target = installPrompt;
        setInstallPrompt(null);
        setInstallStatus(null);
        const next = new Set(selectedTargets);
        next.add(target);
        setSelectedTargets(next);
        saveTargets(next);
        return;
      }
      return;
    }

    if (step === 1) {
      if (key.upArrow) {
        setIdeCursor(i => (i > 0 ? i - 1 : targetKeys.length - 1));
        return;
      }
      if (key.downArrow) {
        setIdeCursor(i => (i < targetKeys.length - 1 ? i + 1 : 0));
        return;
      }
      if (key.return || input === ' ') {
        const target = targetKeys[ideCursor];
        const { installed } = checkToolInstalled(target);
        if (!installed && TARGET_TOOLS[target].installCmd) {
          setInstallPrompt(target);
          setInstallStatus('prompting');
          return;
        }
        const next = new Set(selectedTargets);
        if (next.has(target)) next.delete(target);
        else next.add(target);
        setSelectedTargets(next);
        saveTargets(next);
        return;
      }
    }
  });

  return (
    <Box flexDirection="column" padding={1}>
      <AnimatedLogo />
      <Box marginBottom={1} justifyContent="center">
        <StepIndicator currentStep={step} totalSteps={3} labels={STEP_LABELS} />
      </Box>

      <Divider title={`Step ${step + 1}/3 — ${STEP_LABELS[step]}`} />

      <Box marginTop={1} flexDirection="column">
        {step === 0 && (
          <StepKeys g4fKey={g4fKey} eaonKey={eaonKey} onG4fChange={setG4fKey} onEaonChange={setEaonKey} focusField={focusField} />
        )}
        {step === 1 && !installPrompt && (
          <StepTools targetKeys={targetKeys} selected={selectedTargets} cursor={ideCursor} />
        )}
        {step === 1 && installPrompt && (
          <InstallPrompt target={installPrompt} status={installStatus} />
        )}
        {step === 2 && (
          <StepSummary g4fKey={g4fKey} eaonKey={eaonKey} targets={selectedTargets} />
        )}
      </Box>

      <Footer hints={
        installPrompt
          ? [
              { key: 'y', label: 'install' },
              { key: 'n', label: 'skip' },
              { key: 'esc', label: 'cancel' },
            ]
          : step === 1
          ? [
              { key: '↑↓', label: 'navigate' },
              { key: 'Enter', label: 'toggle' },
              { key: '→', label: 'next' },
              { key: 'esc', label: 'prev' },
            ]
          : [
              { key: '→', label: step < 2 ? 'next' : 'launch' },
              { key: 'esc', label: step === 0 ? 'quit' : 'prev' },
              { key: 'Tab', label: 'switch' },
            ]
      } />
    </Box>
  );
}

function InstallPrompt({ target, status }: { target: TargetTool; status: 'prompting' | 'installing' | 'error' | null }) {
  const info = TARGET_TOOLS[target];
  if (status === 'installing') {
    return (
      <Box flexDirection="column" padding={1} borderStyle="round">
        <Text bold>Installing {info.name}...</Text>
      </Box>
    );
  }
  if (status === 'error') {
    return (
      <Box flexDirection="column" padding={1} borderStyle="round">
        <Text bold>Installation failed</Text>
        <Text>Run manually: {info.installCmd}</Text>
        <Text dimColor>Press [e] or [Enter] or [→] to dismiss</Text>
      </Box>
    );
  }
  return (
    <Box flexDirection="column" padding={1} borderStyle="round">
      <Text bold>{info.name} is not installed</Text>
      <Box marginTop={1}>
        <Text>Install with:</Text>
        <Text bold>  {info.installCmd}</Text>
      </Box>
      <Box marginTop={1}>
        <Text>Install now?</Text>
        <Text>  y (yes) / n (no, just enable) / esc (cancel)</Text>
      </Box>
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

function StepTools({ targetKeys, selected, cursor }: {
  targetKeys: TargetTool[]; selected: Set<TargetTool>; cursor: number;
}) {
  return (
    <Box flexDirection="column">
      {targetKeys.map((target, i) => {
        const info = TARGET_TOOLS[target];
        const isSelected = selected.has(target);
        const isCursor = i === cursor;
        const { installed } = checkToolInstalled(target);
        return (
          <Box key={target}>
            <Text bold={isCursor}>{isCursor ? '▸' : ' '}</Text>
            <Text bold={isSelected}>{isSelected ? ' ◉' : ' ○'}</Text>
            <Text bold={isCursor}> {info.name}</Text>
            <Text dimColor>  {installed ? '✓ installed' : 'not found'}</Text>
            {isSelected && <Text dimColor>  → {info.configFile}</Text>}
          </Box>
        );
      })}
      <Box marginTop={1}>
        <Text dimColor>Enter to toggle  ·  → when done</Text>
      </Box>
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
