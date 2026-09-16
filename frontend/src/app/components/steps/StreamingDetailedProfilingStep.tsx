import { Bot } from "lucide-react";
import StreamingTableProfilingDisplay from "../StreamingTableProfilingDisplay";
import ChatModal from "../ChatModal";

interface StreamingDetailedProfilingStepProps {
  initialMessageData: any[];
  dataDictionaryJson: any;
  anomalyData: any;
  similarityResponse: string;
  setCurrentStep: (step: number) => void;
  chatOpenStates: { [key: number]: boolean };
  openChatModal: (step: number) => void;
  closeChatModal: (step: number) => void;
}

export default function StreamingDetailedProfilingStep({
  initialMessageData,
  dataDictionaryJson,
  anomalyData,
  similarityResponse,
  setCurrentStep,
  chatOpenStates,
  openChatModal,
  closeChatModal,
}: StreamingDetailedProfilingStepProps) {
  const hasInitialData = initialMessageData && initialMessageData.length > 0;
  const hasDataDictionary = dataDictionaryJson;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xl text-brand-blue">Detailed Table Profiling</h2>
        <button
          type="button"
          className="p-2 rounded-full hover:bg-gray-100 transition-colors cursor-pointer flex items-center gap-1"
          onClick={() => openChatModal(7)}
          aria-label="Bot info for Detailed Table Profiling"
        >
          <Bot size={20} className="text-gray-500 hover:text-brand-blue" />
          <span>Chat</span>
        </button>
      </div>
      <div className="bg-white rounded-lg border border-gray-300 p-4">
        {hasInitialData && hasDataDictionary ? (
          <StreamingTableProfilingDisplay
            profilingData={initialMessageData}
            dataDictionary={dataDictionaryJson}
            anomalyData={anomalyData}
            similarityData={similarityResponse}
            isStep4Skipped={false}
          />
        ) : !hasInitialData ? (
          <div className="bg-red-50 border border-red-200 rounded-lg p-6 text-center">
            <h3 className="text-lg text-red-700 mb-2">Missing Profiling Data</h3>
            <p className="text-red-600 mb-4">Cannot display detailed profiling without initial data.</p>
            <button
              onClick={() => setCurrentStep(1)}
              className="bg-red-600 hover:bg-red-700 text-white px-4 py-2 rounded-lg transition-colors"
            >
              Return to Dataset Overview
            </button>
          </div>
        ) : (
          <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-6 text-center">
            <h3 className="text-lg text-yellow-700 mb-2">Data Dictionary Required</h3>
            <p className="text-yellow-600 mb-4">Please complete the Data Dictionary step first.</p>
            <button
              onClick={() => setCurrentStep(3)}
              className="bg-yellow-600 hover:bg-yellow-700 text-white px-4 py-2 rounded-lg transition-colors"
            >
              Go to Data Dictionary
            </button>
          </div>
        )}

        <div className="flex justify-start mt-6">
          <button
            onClick={() => setCurrentStep(6)}
            className="bg-gray-500 hover:bg-gray-600 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors"
          >
            Previous: Metadata Template
          </button>
        </div>
      </div>

      <ChatModal
        isOpen={chatOpenStates[7] || false}
        onClose={() => closeChatModal(7)}
        stepTitle="Detailed Table Profiling"
        stepNumber={7}
        onUseResponse={undefined}
      />
    </div>
  );
}
