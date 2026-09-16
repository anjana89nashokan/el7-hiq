import React from 'react';
import { XCircle, BookOpenText } from 'lucide-react';
import DataDictionaryView from '../DataDictionaryView';
import EmptyState from '../EmptyState';
import NavigationButtons from '../NavigationButtons';
import StepWrapper from './StepWrapper';

interface DataDictionaryStepProps {
  initialMessageData: any[];
  relationshipResponse: string;
  dataDictionaryJson: any;
  dataDictionaryResponse: string;
  dataDictionaryState: any;
  setDataDictionaryJson: (json: any) => void;
  setDataDictionaryResponse: (response: string) => void;
  setDataDictionaryState: (state: any) => void;
  setIsLoadingDataDictionary: (loading: boolean) => void;
  dataDictionaryRetryRef: React.RefObject<(() => void) | null>;
  clearDataDictionaryState: () => void;
  isLoadingDataDictionary: boolean;
  steps: any[];
  onPrevious: () => void;
  onNext: () => void;
  onRetry?: () => void;
  hasApiBeenCalled?: boolean;
  markApiCalled?: () => void;
  onUseResponse?: (response: any, isModified?: boolean) => void;
  modifiedDataDictionaryResponse?: any;
}

export default function DataDictionaryStep({
  initialMessageData,
  relationshipResponse,
  dataDictionaryJson,
  dataDictionaryResponse,
  dataDictionaryState,
  setDataDictionaryJson,
  setDataDictionaryResponse,
  setDataDictionaryState,
  setIsLoadingDataDictionary,
  dataDictionaryRetryRef,
  isLoadingDataDictionary,
  steps,
  onPrevious,
  onNext,
  hasApiBeenCalled,
  markApiCalled,
  onUseResponse,
  modifiedDataDictionaryResponse
}: Readonly<DataDictionaryStepProps>) {
  const hasInitialData = initialMessageData?.length > 0;
  const hasRelationshipResponse = relationshipResponse;
  const hasExistingDataDictionary = dataDictionaryJson || dataDictionaryResponse || dataDictionaryState.isCompleted;
  const exportFunctionRef = React.useRef<(() => Promise<void>) | null>(null);

  // Handle modified response from ChatModal
  React.useEffect(() => {
    console.log('DataDictionaryStep - modifiedDataDictionaryResponse changed:', modifiedDataDictionaryResponse);
    if (modifiedDataDictionaryResponse) {
      console.log('DataDictionaryStep - Setting dataDictionaryJson to:', modifiedDataDictionaryResponse);
      setDataDictionaryJson(modifiedDataDictionaryResponse);
      setDataDictionaryState((prev: any) => ({ ...prev, isCompleted: true }));
    }
  }, [modifiedDataDictionaryResponse, setDataDictionaryJson, setDataDictionaryState]);

  const handleNext = async () => {
    if (exportFunctionRef.current) {
      await exportFunctionRef.current();
    }
    onNext();
  };

  const renderContent = () => {
    console.log('DataDictionaryStep - renderContent - dataDictionaryJson:', dataDictionaryJson);
    console.log('DataDictionaryStep - renderContent - modifiedDataDictionaryResponse:', modifiedDataDictionaryResponse);
    
    if (!hasInitialData) {
      return (
        <EmptyState
          icon={XCircle}
          title="Missing Profiling Data"
          description="Cannot generate data dictionary without initial profiling data."
          iconColor="text-red-500"
          bgColor="bg-red-50"
          borderColor="border-red-200"
          titleColor="text-red-700"
          descColor="text-red-600"
          action={
            <button
              onClick={() => onPrevious()}
              className="bg-red-600 hover:bg-red-700 text-white px-4 py-2 rounded-lg transition-colors"
            >
              Return to Dataset Overview
            </button>
          }
        />
      );
    }

    if (hasRelationshipResponse || hasExistingDataDictionary) {
      return (
        <DataDictionaryView
          profilingData={initialMessageData}
          relationshipAnalysis={relationshipResponse}
          setDataDictionaryJson={setDataDictionaryJson}
          setDataDictionaryResponse={setDataDictionaryResponse}
          prevResponse={dataDictionaryResponse}
          onLoadingChange={setIsLoadingDataDictionary}
          onRetry={dataDictionaryRetryRef}
          dataDictionaryState={dataDictionaryState}
          setDataDictionaryState={setDataDictionaryState}
          exportFunctionRef={exportFunctionRef}
          hasApiBeenCalled={hasApiBeenCalled}
          markApiCalled={markApiCalled}
          isLoadingDataDictionary={isLoadingDataDictionary}
          modifiedResponse={modifiedDataDictionaryResponse}
        />
      );
    }

    return null;
  };

  return (
    <StepWrapper title="Data Dictionary" showBotIcon={true} onUseResponse={onUseResponse}>
      {renderContent()}

      {!relationshipResponse && (
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
          <div className="flex items-center gap-2 text-yellow-700">
            <BookOpenText size={20} />
            <p className="font-medium">Waiting for relationship analysis to complete...</p>
          </div>
          <p className="text-sm text-yellow-600 mt-1">
            Please complete Step 2 (Relationship Analysis) to generate the data dictionary.
          </p>
        </div>
      )}

      <NavigationButtons
        onPrevious={onPrevious}
        onNext={handleNext}
        /* onRetry={() => {
          if (dataDictionaryRetryRef.current) {
            dataDictionaryRetryRef.current();
          } else if (onRetry) {
            onRetry();
          }
        }} */
        previousLabel="Previous: Relationship Analysis"
        nextLabel="Next: Similarity Check"
        showNext={steps[2].completed || dataDictionaryState.isCompleted}
        showRetry={true}
        disabled={isLoadingDataDictionary}
      />
    </StepWrapper>
  );
}
