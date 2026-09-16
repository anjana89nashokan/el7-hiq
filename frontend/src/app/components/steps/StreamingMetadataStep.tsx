import { Bot, ChevronRight } from "lucide-react";
import MetadataTemplate from "../MetadataTemplate";
import ChatModal from "../ChatModal";

interface StreamingMetadataStepProps {
  profilingData: any;
  setPrevMetadataResponse: (response: any) => void;
  prevMetadataResponse: any;
  stepApiCallTracker: Set<number>;
  setStepApiCallTracker: (tracker: Set<number>) => void;
  modifiedMetadataResponse: any;
  steps: any[];
  chatOpenStates: { [key: number]: boolean };
  openChatModal: (step: number) => void;
  closeChatModal: (step: number) => void;
  handleMetadataUseResponse: (response: any, isModified?: boolean) => void;
  onPrevious: () => void;
  onNext: () => void;
}

export default function StreamingMetadataStep({
  profilingData,
  setPrevMetadataResponse,
  prevMetadataResponse,
  stepApiCallTracker,
  setStepApiCallTracker,
  modifiedMetadataResponse,
  steps,
  chatOpenStates,
  openChatModal,
  closeChatModal,
  handleMetadataUseResponse,
  onPrevious,
  onNext,
}: StreamingMetadataStepProps) {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xl text-brand-blue">Metadata Template</h2>
        <button
          type="button"
          className="p-2 rounded-full hover:bg-gray-100 transition-colors cursor-pointer flex items-center gap-1"
          onClick={() => openChatModal(6)}
          aria-label="Bot info for Metadata Template"
        >
          <Bot size={20} className="text-gray-500 hover:text-brand-blue" />
          <span>Chat</span>
        </button>
      </div>
      <div className="bg-white rounded-lg border border-gray-300 p-4">
        <MetadataTemplate
          profilingData={profilingData}
          setMetadataTemplateResponse={setPrevMetadataResponse}
          metadataResponse={prevMetadataResponse}
          hasApiBeenCalled={stepApiCallTracker.has(6)}
          markApiCalled={() => setStepApiCallTracker(new Set([...stepApiCallTracker, 6]))}
          modifiedResponse={modifiedMetadataResponse}
        />

        <div className="flex justify-between mt-6">
          <button
            onClick={onPrevious}
            className="bg-gray-500 hover:bg-gray-600 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors"
          >
            Previous: Data Anomaly Analysis
          </button>
          {steps[5].completed && (
            <button
              onClick={onNext}
              className="bg-brand-blue hover:bg-brand-blue/75 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors"
            >
              Next: Detailed Profiling <ChevronRight size={16} />
            </button>
          )}
        </div>
      </div>

      <ChatModal
        isOpen={chatOpenStates[6] || false}
        onClose={() => closeChatModal(6)}
        stepTitle="Metadata Template"
        stepNumber={6}
        onUseResponse={handleMetadataUseResponse}
      />
    </div>
  );
}
