import { Plus, Check } from "lucide-react";
import { EXTRACT_STEPS } from "../../config/extractConfig";

interface Props {
  readonly currentStep: number;
  readonly completedSteps: Set<number>;
  readonly maxStep: number;
  readonly onStepClick: (stepId: number) => void;
  readonly onNewSession: () => void;
  readonly newSessionLoading?: boolean;
}

export default function ExtractStepSidebar({ currentStep, completedSteps, maxStep, onStepClick, onNewSession, newSessionLoading }: Props) {
  return (
    <div className="w-60 bg-white rounded-l-lg sticky top-0 self-start">
      <div className="p-4">
        <button
          onClick={onNewSession}
          disabled={newSessionLoading}
          className="flex items-center justify-center gap-1 w-full rounded-md bg-brand-darkblue px-2 py-1.5 text-xs text-white hover:bg-brand-darkblue/80 disabled:opacity-60 disabled:cursor-not-allowed transition-colors mb-4 cursor-pointer"
        >
          {newSessionLoading ? <div className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" /> : <Plus size={14} />}
          New Extract
        </button>
        <div className="space-y-6 relative">
          {EXTRACT_STEPS.map((step, index) => {
            const isActive = currentStep === step.id;
            const isCompleted = completedSteps.has(step.id);
            const isClickable = step.id <= maxStep && step.id !== currentStep;

            let circleClass = "bg-white border-gray-300 text-gray-400";
            if (isCompleted) circleClass = "bg-brand-darkblue border-brand-darkblue text-white shadow-md";
            else if (isActive) circleClass = "bg-white border-brand-darkblue text-brand-darkblue shadow-md";

            const stepContent = (
              <>
                {index < EXTRACT_STEPS.length - 1 && (
                  <div className="absolute left-4 top-8 w-px h-full bg-brand-light" />
                )}
                <div className="flex items-start gap-3">
                  <div className="relative z-10 shrink-0">
                    <div className={`w-8 h-8 rounded-full border-2 flex items-center justify-center text-sm font-semibold transition-all duration-200 ${circleClass}`}>
                      {isCompleted ? <Check size={14} strokeWidth={2} /> : step.id}
                    </div>
                  </div>
                  <div className="flex flex-col min-w-0 flex-1">
                    <span className={`text-sm leading-tight ${isActive ? "text-brand-darkblue" : "text-brand-charcoal"}`}>
                      {step.title}
                    </span>
                    <span className="text-[10px] text-brand-charcoal/50 mt-1">
                      {step.description}
                    </span>
                  </div>
                </div>
              </>
            );

            return isClickable ? (
              <button
                key={step.id}
                type="button"
                className="relative w-full text-left cursor-pointer"
                onClick={() => onStepClick(step.id)}
              >
                {stepContent}
              </button>
            ) : (
              <div key={step.id} className="relative">
                {stepContent}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
