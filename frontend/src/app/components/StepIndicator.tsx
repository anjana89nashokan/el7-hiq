import React from "react";
import { Check } from "lucide-react";

interface Step {
  id: number;
  title: string;
  description: string;
  completed: boolean;
}

interface StepIndicatorProps {
  step: Step;
  isActive: boolean;
  isClickable: boolean;
  inProgress?: boolean;
  onStepClick: (stepId: number) => void;
}

const StepIndicator: React.FC<StepIndicatorProps> = ({
  step,
  isActive,
  isClickable,
  inProgress = false,
  onStepClick
}) => {
  const displayStepId = Number.isInteger(step.id) ? step.id : Math.floor(step.id);
  let stepCircleClasses;
  if (step.completed) {
    stepCircleClasses = "bg-brand-darkblue border-brand-blue text-white shadow-md";
  } else if (isActive) {
    stepCircleClasses = "bg-white border-brand-blue text-brand-darkblue shadow-md";
  } else {
    stepCircleClasses = "bg-white border-gray-300 text-gray-400";
  }

  const buttonClasses = isClickable ? "cursor-pointer hover:opacity-80" : "cursor-not-allowed opacity-60";
  const titleTextColor = isActive ? "text-brand-darkblue" : "text-brand-charcoal";

  return (
  <button
    type="button"
    className={`flex items-start gap-3 transition-all duration-200 bg-transparent border-none p-0 text-left ${buttonClasses}`}
    onClick={() => isClickable && onStepClick(step.id)}
    disabled={!isClickable}
    aria-label={`Step ${displayStepId}: ${step.title}`}
  >
    <div className="relative z-10 flex-shrink-0">
      <div
        className={`w-8 h-8 rounded-full border-2 flex items-center justify-center text-sm font-semibold transition-all duration-200 relative
        ${stepCircleClasses} ${
          inProgress ? "animate-pulse" : ""
        }`}
      >
        {inProgress && (
          <div className="absolute inset-0 rounded-full border-2 border-transparent border-t-brand-blue animate-spin"></div>
        )}
        {step.completed ? <Check size={14} strokeWidth={2} /> : displayStepId}
      </div>
    </div>

    <div className="flex flex-col min-w-0 flex-1">
      <span
        className={`text-sm leading-tight ${titleTextColor}`}
      >
        {step.title}
      </span>
      <span className="text-[10px] text-brand-charcoal/50 mt-1">
        {step.description}
      </span>
    </div>
  </button>
  );
};

export default StepIndicator;
