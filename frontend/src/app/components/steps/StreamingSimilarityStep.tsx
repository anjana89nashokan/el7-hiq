import { ChevronRight } from "lucide-react";
import StreamingSimilarityView from "../StreamingSimilarityView";

interface StreamingSimilarityStepProps {
  profilingData: any;
  setSimilarityResponse: (response: string) => void;
  setCurrentStep: (step: number) => void;
  steps: any[];
}

export default function StreamingSimilarityStep({
  profilingData,
  setSimilarityResponse,
  setCurrentStep,
  steps,
}: StreamingSimilarityStepProps) {
  const databaseName = profilingData.successful_uploads[0]?.dataset_id || "";

  return (
    <div className="space-y-6">
      <h2 className="text-xl text-brand-blue">Similarity Check</h2>
      <div className="bg-white rounded-lg border border-gray-300 p-4">
        <StreamingSimilarityView
          sourceTables={profilingData.successful_uploads.map((f: any) => f.table_name.split(".").pop() || "").join(", ")}
          databaseName={databaseName}
          onComplete={(response) => setSimilarityResponse(response)}
        />

        <div className="flex justify-between mt-6">
          <button
            onClick={() => setCurrentStep(3)}
            className="bg-gray-500 hover:bg-gray-600 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors"
          >
            Previous: Data Dictionary
          </button>

          <div className="flex gap-3">
            {!steps[3].completed && (
              <button
                onClick={() => {
                  setSimilarityResponse("skipped");
                  setCurrentStep(5);
                }}
                className="bg-yellow-600 hover:bg-yellow-700 text-white px-6 py-2 rounded-lg transition-colors"
              >
                Skip Similarity Check
              </button>
            )}

            {steps[3].completed && (
              <button
                onClick={() => setCurrentStep(5)}
                className="bg-brand-blue hover:bg-brand-blue/75 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors cursor-pointer text"
              >
                Next: Data Anomaly Analysis <ChevronRight size={16} />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
