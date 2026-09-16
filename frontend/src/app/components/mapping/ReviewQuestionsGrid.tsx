import { HelpCircle, Loader2 } from "lucide-react";

interface ReviewQuestionsGridProps {
  readonly questions: any[];
  readonly answers: Record<string, string>;
  readonly isFetchingQuestions: boolean;
  readonly onAnswerChange: (questionId: string, value: string) => void;
  readonly unansweredQuestionIds?: Set<string>;
}

export default function ReviewQuestionsGrid({
  questions,
  answers,
  isFetchingQuestions,
  onAnswerChange,
  unansweredQuestionIds
}: ReviewQuestionsGridProps) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 overflow-y-auto pr-2">
      {questions.length > 0 ? (
        questions.map((q: any, index: number) => (
          <div
            key={`${q.question_id}-${index}`}
            className={`bg-white p-6 rounded-xl shadow-sm border flex flex-col gap-4 transition-all ${unansweredQuestionIds?.has(q.question_id) ? 'border-red-200 hover:border-red-300' : 'border-gray-100 hover:border-teal-200'}`}
          >
            <div className="flex items-start gap-3">
              <div className="bg-brand-surface p-2 rounded-lg">
                <HelpCircle size={18} className="text-font-blue" />
              </div>
              <div className="flex-1">
                <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1">
                  {q.target_column.entity_id}.{q.target_column.column_name}
                </p>
                <p className="text-sm font-medium text-gray-800 leading-relaxed">
                  {q.question_text}
                </p>
              </div>
            </div>

            <div className="space-y-2">
              <div className="text-[10px] font-bold text-gray-500 uppercase">Your Answer:</div>
              <textarea
                rows={3}
                className={`w-full px-3 py-2 border rounded-lg text-xs focus:ring-2 outline-none bg-gray-50 hover:bg-white transition-all resize-none shadow-inner ${unansweredQuestionIds?.has(q.question_id) ? 'border-red-300 focus:ring-red-300' : 'border-gray-200 focus:ring-brand-primary'}`}
                placeholder="Provide your answer or clarification..."
                value={answers[q.question_id] || ""}
                onChange={(e) => onAnswerChange(q.question_id, e.target.value)}
              />
              {unansweredQuestionIds?.has(q.question_id) && (
                <div className="text-[10px] text-red-600 font-semibold">Answer required</div>
              )}
            </div>
          </div>
        ))
      ) : (
        <div className="col-span-full flex flex-col items-center justify-center py-20 bg-gray-50/50 rounded-xl border-2 border-dashed border-gray-200">
          {isFetchingQuestions ? (
            <>
              <Loader2 size={48} className="text-teal-300 animate-spin mb-4" />
              <p className="text-gray-500 font-medium">Analyzing mappings and generating review questions...</p>
            </>
          ) : (
            <>
              <HelpCircle size={48} className="text-gray-200 mb-4" />
              <p className="text-gray-500 font-medium">No review questions generated for this mapping.</p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
