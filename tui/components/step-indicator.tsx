import React from 'react';
import { Box, Text } from 'ink';

interface StepIndicatorProps {
  currentStep: number;
  totalSteps: number;
  labels?: string[];
}

export default function StepIndicator({ currentStep, totalSteps, labels }: StepIndicatorProps) {
  return (
    <Box justifyContent="center" gap={1}>
      {Array.from({ length: totalSteps }, (_, i) => {
        const isActive = i === currentStep;
        const isComplete = i < currentStep;
        const icon = isComplete ? '●' : isActive ? '◉' : '○';
        const label = labels?.[i] ? ` ${labels[i]}` : '';

        return (
          <Box key={i}>
            <Text bold={isActive || isComplete}>{icon}</Text>
            <Text dimColor>{label}</Text>
          </Box>
        );
      })}
    </Box>
  );
}
