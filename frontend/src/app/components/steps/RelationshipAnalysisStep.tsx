import { type RefObject, type Dispatch, type SetStateAction } from 'react';
import { XCircle } from 'lucide-react';
import RelationshipViewComponent from '../RelationshipViewComponent';
import EmptyState from '../EmptyState';
import NavigationButtons from '../NavigationButtons';
import StepWrapper from './StepWrapper';

interface RelationshipAnalysisStepProps {
  readonly initialMessageData: any[];
  readonly setRelationshipResponse: Dispatch<SetStateAction<string>>;
  readonly setPrevRelationshipResponse: Dispatch<SetStateAction<string>>;
  readonly prevRelationshipResponse: string;
  readonly setIsLoadingRelationship: (loading: boolean) => void;
  readonly relationshipRetryRef: RefObject<(() => void) | null>;
  readonly isLoadingRelationship: boolean;
  readonly steps: any[];
  readonly onPrevious: () => void;
  readonly onNext: () => void;
  readonly onRetry?: () => void;
  readonly hasApiBeenCalled: boolean;
  readonly markApiCalled: () => void;
  readonly onUseResponse?: (response: any, isModified?: boolean) => void;
  readonly modifiedRelationshipResponse?: any;
}

export default function RelationshipAnalysisStep({
  initialMessageData,
  setRelationshipResponse,
  setPrevRelationshipResponse,
  prevRelationshipResponse,
  setIsLoadingRelationship,
  relationshipRetryRef,
  isLoadingRelationship,
  steps,
  onPrevious,
  onNext,
  hasApiBeenCalled,
  markApiCalled,
  onUseResponse,
  modifiedRelationshipResponse
}: RelationshipAnalysisStepProps) {
  return (
    <StepWrapper title="Relationship Analysis" onUseResponse={onUseResponse}>
      {initialMessageData?.length > 0 && initialMessageData[0]?.tool_response ? (
        <RelationshipViewComponent
          profilingData={initialMessageData[0].tool_response}
          updateRelationshipAnalysisStatus={setRelationshipResponse}
          setRelationshipAnalysisResponse={setPrevRelationshipResponse}
          prevRelationshipAnalysisResponse={prevRelationshipResponse}
          onLoadingChange={setIsLoadingRelationship}
          onRetry={relationshipRetryRef}
          hasApiBeenCalled={hasApiBeenCalled}
          markApiCalled={markApiCalled}
          modifiedResponse={modifiedRelationshipResponse}
        />
      ) : (
        <EmptyState
          icon={XCircle}
          title="Missing Profiling Data"
          description="Cannot perform relationship analysis without initial profiling data."
          iconColor="text-red-500"
          bgColor="bg-red-50"
          borderColor="border-red-200"
          titleColor="text-red-700"
          descColor="text-red-600"
          action={
            <button
              onClick={onPrevious}
              className="bg-red-600 hover:bg-red-700 text-white px-4 py-2 rounded-lg transition-colors"
            >
              Return to Dataset Overview
            </button>
          }
        />
      )}

      <NavigationButtons
        onPrevious={onPrevious}
        onNext={onNext}
        /* onRetry={() => {
          if (relationshipRetryRef.current) {
            relationshipRetryRef.current();
          } else if (onRetry) {
            onRetry();
          }
        }} */
        previousLabel="Previous: Dataset Overview"
        nextLabel="Next: Data Dictionary"
        showNext={steps[1].completed}
        showRetry={true}
        disabled={isLoadingRelationship}
      />
    </StepWrapper>
  );
}
