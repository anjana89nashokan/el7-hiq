export const getCurrentPhase = (progress: number, isAnalyzingLLM: boolean) => {
  if (progress < 90) {
    return {
      id: 1,
      name: "Tool Execution",
      description: "Running profiling tool - analyzing tables in batches with statistical + LLM analysis",
      color: "blue",
    };
  } else if (progress === 90) {
    return {
      id: 2,
      name: "Tool Complete",
      description: "Profiling tool finished. Preparing for intelligent analysis...",
      color: "green",
    };
  } else if (isAnalyzingLLM && progress < 100) {
    return {
      id: 3,
      name: "AI Analysis",
      description: "Gemini is generating intelligent insights from profiling results",
      color: "purple",
    };
  } else {
    return {
      id: 4,
      name: "Complete",
      description: "Analysis complete! Review your intelligent profiling report below",
      color: "green",
    };
  }
};

export const allPhases = [
  { id: 1, name: "Tool Execution", threshold: 0 },
  { id: 2, name: "Tool Complete", threshold: 90 },
  { id: 3, name: "AI Analysis", threshold: 91 },
  { id: 4, name: "Complete", threshold: 100 },
];

export const getPhaseTextClass = (isActive: boolean, isCompleted: boolean) => {
  if (isActive) return "text-font-blue font-semibold";
  if (isCompleted) return "text-green-600";
  return "text-gray-500";
};

export const getPhaseIndicatorClass = (isCompleted: boolean, isActive: boolean) => {
  if (isCompleted) return "bg-green-500 ring-2 ring-green-200";
  if (isActive) return "bg-brand-primary ring-2 ring-teal-200 animate-pulse";
  return "bg-gray-300";
};

export const getPhaseState = (phase: typeof allPhases[0], profilingProgress: number, isAnalyzing: boolean) => {
  let isCompleted = false;
  let isActive = false;

  if (phase.threshold === 0) {
    isCompleted = profilingProgress >= 90;
    isActive = profilingProgress < 90 && !isCompleted;
  } else if (phase.threshold === 90) {
    isCompleted = profilingProgress > 90;
    isActive = profilingProgress === 90;
  } else if (phase.threshold === 91) {
    isCompleted = profilingProgress === 100;
    isActive = isAnalyzing && profilingProgress >= 91 && profilingProgress < 100;
  } else if (phase.threshold === 100) {
    isCompleted = profilingProgress === 100 && !isAnalyzing;
  }

  return { isCompleted, isActive };
};
