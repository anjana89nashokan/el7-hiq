import { Bot, ChevronRight } from "lucide-react";
import StreamingDataAnomalyView from "../StreamingDataAnomalyView";
import DataAnomalyAnalyzer from "../DataAnomalyAnalyzer";
import ChatModal from "../ChatModal";

interface StreamingAnomalyStepProps {
  isStreamingAnomalyEnabled: boolean;
  setAnomalyData: (data: any) => void;
  anomalyData: any;
  stepApiCallTracker: Set<number>;
  setStepApiCallTracker: (tracker: Set<number>) => void;
  setActiveStreamingStep: (step: number | null) => void;
  modifiedAnomalyResponse: any;
  steps: any[];
  chatOpenStates: { [key: number]: boolean };
  openChatModal: (step: number) => void;
  closeChatModal: (step: number) => void;
  handleAnomalyUseResponse: (response: any, isModified?: boolean) => void;
  onPrevious: () => void;
  onNext: () => void;
}

export default function StreamingAnomalyStep({
  isStreamingAnomalyEnabled,
  setAnomalyData,
  anomalyData,
  stepApiCallTracker,
  setStepApiCallTracker,
  setActiveStreamingStep,
  modifiedAnomalyResponse,
  steps,
  chatOpenStates,
  openChatModal,
  closeChatModal,
  handleAnomalyUseResponse,
  onPrevious,
  onNext,
}: StreamingAnomalyStepProps) {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xl text-brand-blue">Data Anomaly Analysis</h2>
        <button
          type="button"
          className="p-2 rounded-full hover:bg-gray-100 transition-colors cursor-pointer flex items-center gap-1"
          onClick={() => openChatModal(5)}
          aria-label="Bot info for Data Anomaly Analysis"
        >
          <Bot size={20} className="text-gray-500 hover:text-brand-blue" />
          <span>Chat</span>
        </button>
      </div>
      <div className="bg-white rounded-lg border border-gray-300 p-4">
        {isStreamingAnomalyEnabled ? (
          <StreamingDataAnomalyView
            setAnomalyData={setAnomalyData}
            anomalyData={anomalyData}
            prevResponse={anomalyData?.text_response}
            isStreamingEnabled={isStreamingAnomalyEnabled}
            hasApiBeenCalled={stepApiCallTracker.has(5)}
            markApiCalled={() => setStepApiCallTracker(new Set([...stepApiCallTracker, 5]))}
            onStreamingStart={() => setActiveStreamingStep(5)}
            onStreamingEnd={() => setActiveStreamingStep(null)}
            modifiedResponse={modifiedAnomalyResponse}
          />
        ) : (
          <DataAnomalyAnalyzer setAnomalyData={setAnomalyData} anomalyData={anomalyData} />
        )}

        {steps[4].completed && (
          <div className="flex justify-between mt-6">
            <button
              onClick={onPrevious}
              className="bg-gray-500 hover:bg-gray-600 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors"
            >
              Previous: Similarity Check
            </button>
            <button
              onClick={onNext}
              className="bg-brand-blue hover:bg-brand-blue/75 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors cursor-pointer text"
            >
              Next: Metadata Template <ChevronRight size={16} />
            </button>
          </div>
        )}
      </div>

      <ChatModal
        isOpen={chatOpenStates[5] || false}
        onClose={() => closeChatModal(5)}
        stepTitle="Data Anomaly Analysis"
        stepNumber={5}
        onUseResponse={handleAnomalyUseResponse}
      />
    </div>
  );
}
