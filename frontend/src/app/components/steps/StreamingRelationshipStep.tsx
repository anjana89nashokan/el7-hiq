import { Bot, ChevronRight } from "lucide-react";
import StreamingRelationshipView from "../StreamingRelationshipView";
import ChatModal from "../ChatModal";

interface StreamingRelationshipStepProps {
  initialMessageData: any[];
  setRelationshipResponse: (response: string) => void;
  setPrevRelationshipResponse: (response: string) => void;
  prevRelationshipResponse: string;
  stepApiCallTracker: Set<number>;
  setStepApiCallTracker: (tracker: Set<number>) => void;
  setActiveStreamingStep: (step: number | null) => void;
  modifiedRelationshipResponse: any;
  steps: any[];
  chatOpenStates: { [key: number]: boolean };
  openChatModal: (step: number) => void;
  closeChatModal: (step: number) => void;
  handleRelationshipUseResponse: (response: any, isModified?: boolean) => void;
  onPrevious: () => void;
  onNext: () => void;
}

export default function StreamingRelationshipStep({
  initialMessageData,
  setRelationshipResponse,
  setPrevRelationshipResponse,
  prevRelationshipResponse,
  stepApiCallTracker,
  setStepApiCallTracker,
  setActiveStreamingStep,
  modifiedRelationshipResponse,
  steps,
  chatOpenStates,
  openChatModal,
  closeChatModal,
  handleRelationshipUseResponse,
  onPrevious,
  onNext,
}: StreamingRelationshipStepProps) {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xl text-brand-blue">Relationship Analysis</h2>
        <button
          type="button"
          className="p-2 rounded-full hover:bg-gray-100 transition-colors cursor-pointer flex items-center gap-1"
          onClick={() => openChatModal(2)}
          aria-label="Bot info for Relationship Analysis"
        >
          <Bot size={20} className="text-gray-500 hover:text-brand-blue" />
          <span>Chat</span>
        </button>
      </div>
      <div className="bg-white rounded-lg border border-gray-300 p-4">
        {initialMessageData && (
          <StreamingRelationshipView
            profilingData={initialMessageData[0]?.tool_response}
            updateRelationshipAnalysisStatus={setRelationshipResponse}
            setRelationshipAnalysisResponse={setPrevRelationshipResponse}
            prevRelationshipAnalysisResponse={prevRelationshipResponse}
            hasApiBeenCalled={stepApiCallTracker.has(2)}
            markApiCalled={() => setStepApiCallTracker(new Set([...stepApiCallTracker, 2]))}
            onStreamingStart={() => setActiveStreamingStep(2)}
            onStreamingEnd={() => setActiveStreamingStep(null)}
            modifiedResponse={modifiedRelationshipResponse}
          />
        )}

        {steps[1].completed && (
          <div className="flex justify-between mt-6">
            <button
              onClick={onPrevious}
              className="bg-gray-500 hover:bg-gray-600 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors"
            >
              Previous: Dataset Overview
            </button>
            <button
              onClick={onNext}
              className="bg-brand-blue hover:bg-brand-blue/75 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors cursor-pointer text"
            >
              Next: Data Dictionary <ChevronRight size={16} />
            </button>
          </div>
        )}
      </div>

      <ChatModal
        isOpen={chatOpenStates[2] || false}
        onClose={() => closeChatModal(2)}
        stepTitle="Relationship Analysis"
        stepNumber={2}
        onUseResponse={handleRelationshipUseResponse}
      />
    </div>
  );
}
