import { ChevronRight } from "lucide-react";
import StreamingDataDictionaryView from "../StreamingDataDictionaryView";

interface StreamingDataDictionaryStepProps {
  initialMessageData: any[];
  relationshipResponse: string;
  setDataDictionaryJson: (json: any) => void;
  setDataDictionaryResponse: (response: string) => void;
  dataDictionaryResponse: string;
  stepApiCallTracker: Set<number>;
  setStepApiCallTracker: (tracker: Set<number>) => void;
  setActiveStreamingStep: (step: number | null) => void;
  steps: any[];
  onPrevious: () => void;
  onNext: () => void;
}

export default function StreamingDataDictionaryStep({
  initialMessageData,
  relationshipResponse,
  setDataDictionaryJson,
  setDataDictionaryResponse,
  dataDictionaryResponse,
  stepApiCallTracker,
  setStepApiCallTracker,
  setActiveStreamingStep,
  steps,
  onPrevious,
  onNext,
}: StreamingDataDictionaryStepProps) {
  return (
    <div className="space-y-6">
      <h2 className="text-xl text-brand-blue">Data Dictionary</h2>
      <div className="bg-white rounded-lg border border-gray-300 p-4">
        {initialMessageData && relationshipResponse && (
          <StreamingDataDictionaryView
            profilingData={initialMessageData[0]?.tool_response}
            relationshipAnalysis={relationshipResponse}
            setDataDictionaryJson={setDataDictionaryJson}
            setDataDictionaryResponse={setDataDictionaryResponse}
            prevResponse={dataDictionaryResponse}
            hasApiBeenCalled={stepApiCallTracker.has(3)}
            markApiCalled={() => setStepApiCallTracker(new Set([...stepApiCallTracker, 3]))}
            onStreamingStart={() => setActiveStreamingStep(3)}
            onStreamingEnd={() => setActiveStreamingStep(null)}
          />
        )}

        {steps[2].completed && (
          <div className="flex justify-between mt-6">
            <button
              onClick={onPrevious}
              className="bg-gray-500 hover:bg-gray-600 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors"
            >
              Previous: Relationship Analysis
            </button>
            <button
              onClick={onNext}
              className="bg-brand-blue hover:bg-brand-blue/75 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors cursor-pointer text"
            >
              Next: Similarity Check <ChevronRight size={16} />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
