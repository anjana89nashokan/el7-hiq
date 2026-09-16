import { type ReactNode } from 'react';
import { XCircle } from 'lucide-react';
import EmptyState from '../EmptyState';
import NavigationButtons from '../NavigationButtons';
import StepWrapper from './StepWrapper';

interface GenericAnalysisStepProps {
  readonly title: string;
  readonly children: ReactNode;
  readonly hasData: boolean;
  readonly isLoading: boolean;
  readonly steps: any[];
  readonly stepIndex: number;
  readonly previousLabel: string;
  readonly nextLabel?: string;
  readonly onPrevious: () => void;
  readonly onNext?: () => void;
  readonly onRetry: () => void;
  readonly showBotIcon?: boolean;
  readonly alwaysShowNext?: boolean;
  readonly nextDisabled?: boolean;
  readonly finishMessage?: string;
  readonly onUseResponse?: (response: any, isModified?: boolean) => void;
  readonly modifiedAnomalyResponse?: any;
  readonly modifiedMetadataResponse?: any;
}

export default function GenericAnalysisStep({
  title,
  children,
  hasData,
  isLoading,
  steps,
  stepIndex,
  previousLabel,
  nextLabel = '',
  onPrevious,
  onNext = () => {},
  showBotIcon = true,
  alwaysShowNext = false,
  nextDisabled = false,
  finishMessage,
  onUseResponse,
  // modifiedAnomalyResponse,
  // modifiedMetadataResponse
}: GenericAnalysisStepProps) {
  return (
    <StepWrapper title={title} showBotIcon={showBotIcon} onUseResponse={onUseResponse}>
      {hasData ? (
        children
      ) : (
        <EmptyState
          icon={XCircle}
          title="No Data Available"
          description={`Cannot perform ${title.toLowerCase()} without uploaded data.`}
          iconColor="text-red-500"
          bgColor="bg-red-50"
          borderColor="border-red-200"
          titleColor="text-red-700"
          descColor="text-red-600"
        />
      )}

      {finishMessage ? (
        <div className="mt-6 rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">
          {finishMessage}
        </div>
      ) : null}

      <NavigationButtons
        onPrevious={onPrevious}
        onNext={onNext}
        /* onRetry={onRetry} */
        previousLabel={previousLabel}
        nextLabel={nextLabel}
        showNext={alwaysShowNext || (!!nextLabel && steps[stepIndex - 1]?.completed)}
        showRetry={hasData}
        disabled={isLoading}
        nextDisabled={nextDisabled}
      />
    </StepWrapper>
  );
}
