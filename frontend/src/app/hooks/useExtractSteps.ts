import { useState, useEffect } from "react";

interface UseExtractStepsReturn {
  currentStep: number;
  maxStep: number;
  completedSteps: Set<number>;
  setCurrentStep: (step: number) => void;
  reset: () => void;
}

export function useExtractSteps(
  showMetadata: boolean,
  showDriverMapping: boolean,
  showExtractMapping: boolean,
  extractMappingApproved?: boolean,
): UseExtractStepsReturn {
  const [currentStep, setCurrentStep] = useState(1);
  const [maxVisitedStep, setMaxVisitedStep] = useState(1);

  let maxUnlockedStep = 1;
  if (showExtractMapping) maxUnlockedStep = 4;
  else if (showDriverMapping) maxUnlockedStep = 3;
  else if (showMetadata) maxUnlockedStep = 2;

  const maxStep = Math.max(maxUnlockedStep, maxVisitedStep);

  useEffect(() => {
    setCurrentStep((prev) => Math.max(prev, maxUnlockedStep));
    setMaxVisitedStep((prev) => Math.max(prev, maxUnlockedStep));
  }, [maxUnlockedStep]);

  const completedSteps = new Set<number>([
    ...(showMetadata ? [1] : []),
    ...(showDriverMapping ? [1, 2] : []),
    ...(showExtractMapping ? [1, 2, 3] : []),
    ...(extractMappingApproved ? [1, 2, 3, 4] : []),
  ]);

  const reset = () => {
    setCurrentStep(1);
    setMaxVisitedStep(1);
  };

  return { currentStep, maxStep, completedSteps, setCurrentStep, reset };
}
