import { HelpCircle } from "lucide-react";

interface QuestionsPanelProps {
  readonly mappingData: any;
  readonly selectedMappingIndex: number | null;
  readonly answers: Record<string, string>;
  readonly onAnswerChange: (questionId: string, value: string) => void;
  readonly step3Questions: any[];
  readonly unansweredQuestionIds?: Set<string>;
}

export default function QuestionsPanel({
  mappingData,
  selectedMappingIndex,
  answers,
  onAnswerChange,
  step3Questions,
  unansweredQuestionIds
}: QuestionsPanelProps) {
  const getQuestionsForSelectedRow = () => {
    if (selectedMappingIndex === null || !mappingData) return [];
    const selectedRow = mappingData.column_mappings[selectedMappingIndex];
    if (!selectedRow) return [];

    console.log('Selected row:', selectedRow);
    console.log('Step3 questions:', step3Questions);

    return step3Questions?.filter((q: any) =>
      q.row_ids?.includes(selectedRow.row_id)
    ) || [];
  };

  return (
    <div className="w-80 bg-white rounded-xl shadow-sm border border-gray-200 flex flex-col">
      <div className="p-4 border-b border-gray-100 bg-gray-50/50 flex items-center gap-2">
        <HelpCircle size={18} className="text-font-blue" />
        <h3 className="text-sm font-bold text-brand-darkblue">Identifying Questions</h3>
      </div>
      <div className="p-5 flex-1 overflow-y-auto">
        {selectedMappingIndex !== null && mappingData.column_mappings[selectedMappingIndex] ? (
          <div className="space-y-6">
            <div className="pb-3 border-b border-gray-100">
              <p className="text-[10px] text-gray-400 uppercase font-bold tracking-wider mb-1">Target Column</p>
              <p className="text-sm font-bold text-brand-darkblue">{mappingData.column_mappings[selectedMappingIndex].target_column_name}</p>
            </div>

            <div className="space-y-5">
              {getQuestionsForSelectedRow().length > 0 ? (
                getQuestionsForSelectedRow().map((sq: any) => (
                  <div key={sq.question_id} className="bg-gray-50 p-4 rounded-lg border border-gray-100 space-y-3">
                    <div className="flex flex-col gap-1">
                      <span className="text-[10px] font-bold text-gray-500 uppercase">Question:</span>
                      <p className="text-xs font-bold text-gray-800 leading-relaxed">{sq.question_text}</p>
                    </div>
                    <div className="flex flex-col gap-1">
                      <span className="text-[10px] font-bold text-gray-500 uppercase">Context Summary:</span>
                      <p className="text-xs font-nedium text-gray-800 leading-relaxed">{sq.context_summary}</p>
                    </div>
                    <div className="flex flex-col gap-1">
                      <span className="text-[10px] font-bold text-gray-500 uppercase">Answer:</span>
                      <textarea
                        key={`${selectedMappingIndex}-${sq.question_id}`}
                        rows={2}
                        placeholder="Type your answer here..."
                        className={`w-full px-3 py-2 border rounded-md text-xs focus:ring-1 outline-none bg-white resize-none shadow-sm ${unansweredQuestionIds?.has(sq.question_id) ? 'border-red-400 focus:ring-red-400' : 'border-gray-200 focus:ring-brand-primary'}`}
                        value={answers[`${selectedMappingIndex}-${sq.question_id}`] || ""}
                        onChange={(e) => onAnswerChange(`${selectedMappingIndex}-${sq.question_id}`, e.target.value)}
                      />
                      {unansweredQuestionIds?.has(sq.question_id) && (
                        <div className="text-[10px] text-red-600 font-semibold">Answer required</div>
                      )}
                    </div>
                  </div>
                ))
              ) : (
                <div className="text-center py-10">
                  <p className="text-xs text-gray-400 italic">No suggested questions for this mapping.</p>
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center p-6 bg-gray-50/30 rounded-lg border-2 border-dashed border-gray-100">
            <HelpCircle size={32} className="text-gray-200 mb-3" />
            <p className="text-sm text-gray-400 font-medium">Select a row to see identifying questions</p>
          </div>
        )}
      </div>
    </div>
  );
}
