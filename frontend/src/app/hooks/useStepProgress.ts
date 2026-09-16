import { useMemo } from 'react';

interface Step {
  id: number;
  completed: boolean;
}

export function useStepProgress(
  steps: Step[], 
  currentStep: number, 
  skippedSteps: Set<number>,
  maxVisitedStep: number
) {
  const canProceedToStep = useMemo(() => (stepId: number): boolean => {
    // Always allow navigation to step 1
    if (stepId === 1) return true;
    
    // Allow navigation to any previously visited step
    if (stepId <= maxVisitedStep) return true;
    
    // Allow staying on current step
    if (stepId === currentStep) return true;
    
    // Forward navigation to new steps requires previous step completion
    switch (stepId) {
      case 2: return steps[0]?.completed || false;
      case 3: return steps[1]?.completed || false;
      case 4: return steps[2]?.completed || false;
      case 5: return steps[3]?.completed || skippedSteps.has(4);
      case 6: return steps[4]?.completed || false;
      case 7: return steps[5]?.completed || false;
      case 8: return steps[0]?.completed || false;
      default: return false;
    }
  }, [steps, skippedSteps, currentStep, maxVisitedStep]);

  const getStepInProgress = useMemo(() => (
    stepId: number,
    loadingStates: { [key: number]: boolean }
  ): boolean => {
    return loadingStates[stepId] || false;
  }, []);

  return { canProceedToStep, getStepInProgress };
}
