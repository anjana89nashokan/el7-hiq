import StepIndicator from './StepIndicator';

interface Step {
  id: number;
  title: string;
  description: string;
  completed: boolean;
}

interface StepSidebarProps {
  readonly steps: Step[];
  readonly currentStep: number;
  readonly canProceedToStep: (stepId: number) => boolean;
  readonly loadingStates: { [key: number]: boolean };
  readonly onStepClick: (stepId: number) => void;
}

export default function StepSidebar({
  steps,
  currentStep,
  canProceedToStep,
  loadingStates,
  onStepClick
}: StepSidebarProps) {
  return (
    <div className="w-60 bg-white rounded-l-lg sticky top-0 self-start">
      <div className="p-4">
        <div className="space-y-6 relative max-h-fit overflow-y-auto scrollbar-hide">
          {steps.map((step, index) => {
            // Allow clicking on previous steps even during API calls
            const isClickable = canProceedToStep(step.id);
            
            return (
              <div key={`step-${step.id}`} className="relative">
                {index < steps.length - 1 && (
                  <div className="absolute left-4 top-8 w-px h-full bg-brand-light"></div>
                )}
                <StepIndicator
                  step={step}
                  isActive={currentStep === step.id}
                  isClickable={isClickable}
                  inProgress={loadingStates[step.id] || false}
                  onStepClick={onStepClick}
                />
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}