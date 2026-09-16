import { useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronUp, ListChecks } from "lucide-react";

interface IssuesGridProps {
  readonly step4Data: any;
  readonly onSelectRowId?: (rowId: string) => void;
  readonly step3Questions?: any[];
  readonly answers?: Record<string, string>;
  readonly feedbacks?: Record<string, string>;
}

function statusStyle(status: string) {
  const s = (status || "").toUpperCase();
  if (s === "RESOLVED") return { cls: "border-green-200 hover:border-green-300", badge: "bg-green-100 text-green-700", Icon: CheckCircle2, label: "Resolved" };
  if (s === "PARTIALLY_RESOLVED")
    return { cls: "border-amber-200 hover:border-amber-300", badge: "bg-amber-100 text-amber-800", Icon: AlertTriangle, label: "Partial" };
  return { cls: "border-red-200 hover:border-red-300", badge: "bg-red-100 text-red-700", Icon: AlertTriangle, label: "Unresolved" };
}

export default function IssuesGrid({ step4Data, onSelectRowId, step3Questions, answers, feedbacks }: IssuesGridProps) {
  const [expandedCardId, setExpandedCardId] = useState<string | null>(null);

  const rowsById = useMemo(() => {
    const out = new Map<string, any>();
    (step4Data?.column_mappings || []).forEach((r: any) => {
      if (r?.row_id) out.set(r.row_id, r);
    });
    return out;
  }, [step4Data]);

  const issues = Array.isArray(step4Data?.issue_resolutions) ? step4Data.issue_resolutions : [];
  const changeLog = Array.isArray(step4Data?.change_log) ? step4Data.change_log : [];

  const questionsById = useMemo(() => {
    const out = new Map<string, any>();
    (step3Questions || []).forEach((q: any) => {
      if (q?.question_id) out.set(q.question_id, q);
    });
    return out;
  }, [step3Questions]);

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 overflow-y-auto pr-2">
      {issues.length > 0 ? (
        issues.map((issue: any, idx: number) => {
          const issueId = issue.issue_id || `UNKNOWN_ISSUE_${idx}`;
          const cardId = `${issueId}__${idx}`;
          const isExpanded = expandedCardId === cardId;
          const firstRowId = (issue.affected_row_ids || [])[0];
          const row = firstRowId ? rowsById.get(firstRowId) : null;
          const { cls, badge, Icon, label } = statusStyle(issue.status);

          const relatedChanges = firstRowId ? changeLog.filter((c: any) => c.row_id === firstRowId) : [];
          const relatedQuestionIds: string[] =
            row && Array.isArray(step3Questions)
              ? (step3Questions || [])
                  .filter((q: any) => (q?.row_ids || []).includes(row.row_id))
                  .map((q: any) => q?.question_id)
                  .filter(Boolean)
              : [];

          return (
            <div
              key={issueId}
              className={`bg-white p-5 rounded-xl shadow-sm border flex flex-col gap-3 transition-all ${cls}`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold ${badge}`}>
                      <Icon size={12} />
                      {label}
                    </span>
                    <span className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">Issue</span>
                  </div>
                  <div className="mt-1 text-[11px] font-mono text-gray-600 truncate">{issueId}</div>
                  {row && (
                    <div className="mt-2 text-[11px] font-bold text-gray-500 uppercase tracking-wider">
                      {row.target_table?.entity_id}.{row.target_column_name}
                    </div>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => setExpandedCardId((prev) => (prev === cardId ? null : cardId))}
                  className="shrink-0 p-2 rounded-lg border border-gray-200 bg-white text-gray-600 hover:text-gray-900 hover:border-gray-300"
                  title="Expand"
                >
                  {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                </button>
              </div>

              <div className="text-xs text-gray-700 line-clamp-3">
                {issue.reason_summary || "No reason summary provided."}
              </div>

              {Array.isArray(issue.manual_actions) && issue.manual_actions.length > 0 && (
                <div className="text-[11px] text-amber-900 bg-amber-50 border border-amber-100 rounded px-3 py-2">
                  Manual actions: {issue.manual_actions.length}
                </div>
              )}

              {row && (
                <button
                  type="button"
                  onClick={() => onSelectRowId?.(row.row_id)}
                  className="text-xs text-font-blue font-semibold hover:underline text-left"
                >
                  Jump to row
                </button>
              )}

              {isExpanded && (
                <div className="pt-2 border-t border-gray-100 space-y-3">
                  {row && (
                    <div className="text-[11px] text-gray-700">
                      <div className="font-bold text-gray-500 uppercase text-[10px]">Current Mapping</div>
                      <div className="mt-1">
                        <span className="text-gray-500">Rule:</span> {row.rule_type}
                      </div>
                      <div>
                        <span className="text-gray-500">Source:</span>{" "}
                        {row.source_entity?.entity_id || "-"}
                        {Array.isArray(row.source_field_names) && row.source_field_names.length > 0
                          ? `.${row.source_field_names.join(", ")}`
                          : ""}
                      </div>
                      <div>
                        <span className="text-gray-500">Join:</span> {row.join_condition?.join_text || "-"}
                      </div>
                    </div>
                  )}

                  {row && feedbacks?.[row.row_id] && (
                    <div className="text-[11px] text-gray-800 bg-brand-surface border border-teal-100 rounded p-3">
                      <div className="font-bold text-brand-darkblue uppercase text-[10px]">STTM Feedback</div>
                      <div className="mt-2 whitespace-pre-wrap">{feedbacks[row.row_id]}</div>
                    </div>
                  )}

                  {row && relatedQuestionIds.length > 0 && (
                    <div className="text-[11px] text-gray-800 bg-gray-50 border border-gray-100 rounded p-3">
                      <div className="font-bold text-gray-500 uppercase text-[10px]">Questions & Answers (row)</div>
                      <div className="mt-2 space-y-3">
                        {relatedQuestionIds.slice(0, 3).map((qid: string) => {
                          const q = questionsById.get(qid);
                          return (
                            <div key={qid} className="border-l-2 border-gray-200 pl-3">
                              <div className="text-[10px] font-bold text-gray-500 uppercase">{qid}</div>
                              {q?.question_text && <div className="mt-1 font-semibold text-gray-800">{q.question_text}</div>}
                              {q?.context_summary && (
                                <div className="mt-1 text-gray-600">
                                  <span className="text-gray-500 font-semibold">Context:</span> {q.context_summary}
                                </div>
                              )}
                              <div className="mt-1 text-gray-700 whitespace-pre-wrap">
                                <span className="text-gray-500 font-semibold">Answer:</span> {answers?.[qid] || "-"}
                              </div>
                            </div>
                          );
                        })}
                        {relatedQuestionIds.length > 3 && (
                          <div className="text-gray-500">Showing first 3 questions for this row.</div>
                        )}
                      </div>
                    </div>
                  )}

                  {Array.isArray(issue.manual_actions) && issue.manual_actions.length > 0 && (
                    <div className="text-[11px] text-gray-800 bg-gray-50 border border-gray-100 rounded p-3">
                      <div className="font-bold text-gray-500 uppercase text-[10px]">Manual Actions</div>
                      <ul className="mt-2 space-y-2">
                        {issue.manual_actions.map((a: any, idx: number) => (
                          <li key={idx}>
                            <div className="font-semibold">{a.action_title}</div>
                            <div className="text-gray-700">{a.action_details}</div>
                            {a.suggested_location && (
                              <div className="text-gray-500">Where: {a.suggested_location}</div>
                            )}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {relatedChanges.length > 0 && (
                    <div className="text-[11px] text-gray-800 bg-gray-50 border border-gray-100 rounded p-3">
                      <div className="font-bold text-gray-500 uppercase text-[10px]">Change Log (row)</div>
                      <div className="mt-2 space-y-2">
                        {relatedChanges.slice(0, 5).map((c: any) => (
                          <div key={c.change_id || Math.random()}>
                            <div className="flex items-center justify-between gap-2">
                              <div className="font-semibold">{c.field_name}</div>
                              <div className="text-gray-500 font-bold">{c.source}</div>
                            </div>
                            <div className="text-gray-600">Before: {String(c.before_value ?? "-")}</div>
                            <div className="text-gray-600">After: {String(c.after_value ?? "-")}</div>
                          </div>
                        ))}
                        {relatedChanges.length > 5 && <div className="text-gray-500">Showing first 5 changes.</div>}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })
      ) : (
        <div className="col-span-full flex flex-col items-center justify-center py-20 bg-gray-50/50 rounded-xl border-2 border-dashed border-gray-200">
          <ListChecks size={48} className="text-gray-200 mb-4" />
          <p className="text-gray-500 font-medium">No issues found in Step 4 output.</p>
        </div>
      )}
    </div>
  );
}
