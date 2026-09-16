import { useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Info, ListChecks, ChevronDown, ChevronUp } from "lucide-react";

interface IssuesPanelProps {
  readonly step4Data: any;
  readonly selectedMappingIndex: number | null;
  readonly step3Questions?: any[];
  readonly answers?: Record<string, string>;
  readonly feedbacks?: Record<string, string>;
}

function statusBadge(status: string) {
  const s = (status || "").toUpperCase();
  if (s === "RESOLVED") return { cls: "bg-green-100 text-green-700", Icon: CheckCircle2, label: "Resolved" };
  if (s === "PARTIALLY_RESOLVED")
    return { cls: "bg-amber-100 text-amber-800", Icon: AlertTriangle, label: "Partial" };
  return { cls: "bg-red-100 text-red-700", Icon: AlertTriangle, label: "Unresolved" };
}

export default function IssuesPanel({ step4Data, selectedMappingIndex, step3Questions, answers, feedbacks }: IssuesPanelProps) {
  const [expandedIssueIds, setExpandedIssueIds] = useState<Set<string>>(new Set());

  const selectedRow = useMemo(() => {
    if (!step4Data?.column_mappings || selectedMappingIndex === null) return null;
    return step4Data.column_mappings[selectedMappingIndex] || null;
  }, [step4Data, selectedMappingIndex]);

  const rowId = selectedRow?.row_id;

  const { issues, warnings, changes } = useMemo(() => {
    const issueRes = Array.isArray(step4Data?.issue_resolutions) ? step4Data.issue_resolutions : [];
    const warns = Array.isArray(step4Data?.warnings) ? step4Data.warnings : [];
    const chg = Array.isArray(step4Data?.change_log) ? step4Data.change_log : [];

    const issuesForRow = rowId ? issueRes.filter((i: any) => (i.affected_row_ids || []).includes(rowId)) : [];
    const warningsForRow = rowId ? warns.filter((w: any) => w.row_id === rowId) : [];
    const changesForRow = rowId ? chg.filter((c: any) => c.row_id === rowId) : [];

    return { issues: issuesForRow, warnings: warningsForRow, changes: changesForRow };
  }, [step4Data, rowId]);

  const questionsForRow = useMemo(() => {
    if (!rowId || !Array.isArray(step3Questions)) return [];
    return (step3Questions || []).filter((q: any) => (q?.row_ids || []).includes(rowId));
  }, [rowId, step3Questions]);

  const toggleExpanded = (issueId: string) => {
    setExpandedIssueIds((prev) => {
      const next = new Set(prev);
      if (next.has(issueId)) next.delete(issueId);
      else next.add(issueId);
      return next;
    });
  };

  return (
    <div className="w-96 bg-white rounded-xl shadow-sm border border-gray-200 flex flex-col">
      <div className="p-4 border-b border-gray-100 bg-gray-50/50 flex items-center gap-2">
        <ListChecks size={18} className="text-font-blue" />
        <h3 className="text-sm font-bold text-brand-darkblue">Issues</h3>
      </div>

      <div className="p-5 flex-1 overflow-y-auto">
        {selectedRow ? (
          <div className="space-y-5">
            <div className="pb-3 border-b border-gray-100">
              <p className="text-[10px] text-gray-400 uppercase font-bold tracking-wider mb-1">Target</p>
              <p className="text-sm font-bold text-brand-darkblue">
                {selectedRow?.target_table?.entity_id}.{selectedRow?.target_column_name}
              </p>
              {selectedRow?.needs_review && (
                <div className="mt-2 inline-flex items-center gap-1 text-[11px] font-semibold text-amber-800 bg-amber-100 px-2 py-1 rounded">
                  <AlertTriangle size={12} />
                  needs_review = true
                </div>
              )}
            </div>

            {rowId && feedbacks?.[rowId] && (
              <div className="bg-brand-surface border border-teal-100 rounded p-4">
                <div className="flex items-center gap-2 text-[10px] font-bold text-brand-darkblue uppercase">
                  <Info size={12} />
                  STTM Feedback
                </div>
                <div className="mt-2 text-xs text-gray-800 whitespace-pre-wrap">{feedbacks[rowId]}</div>
              </div>
            )}

            {questionsForRow.length > 0 && (
              <div className="bg-gray-50 border border-gray-100 rounded p-4">
                <div className="flex items-center gap-2 text-[10px] font-bold text-gray-600 uppercase">
                  <Info size={12} />
                  Questions & Answers
                </div>
                <div className="mt-3 space-y-3">
                  {questionsForRow.slice(0, 3).map((q: any) => (
                    <div key={q.question_id} className="border-l-2 border-gray-200 pl-3">
                      <div className="text-[10px] font-bold text-gray-500 uppercase">{q.question_id}</div>
                      <div className="mt-1 text-xs font-semibold text-gray-800">{q.question_text}</div>
                      {q.context_summary && (
                        <div className="mt-1 text-[11px] text-gray-600">
                          <span className="text-gray-500 font-semibold">Context:</span> {q.context_summary}
                        </div>
                      )}
                      <div className="mt-1 text-[11px] text-gray-700 whitespace-pre-wrap">
                        <span className="text-gray-500 font-semibold">Answer:</span> {answers?.[q.question_id] || "-"}
                      </div>
                    </div>
                  ))}
                  {questionsForRow.length > 3 && (
                    <div className="text-[11px] text-gray-500">Showing first 3 questions for this row.</div>
                  )}
                </div>
              </div>
            )}

            {issues.length > 0 ? (
              <div className="space-y-3">
                {issues.map((issue: any) => {
                  const issueId = issue.issue_id || "UNKNOWN_ISSUE";
                  const isExpanded = expandedIssueIds.has(issueId);
                  const { cls, Icon, label } = statusBadge(issue.status);
                  return (
                    <div key={issueId} className="border border-gray-100 rounded-lg bg-white shadow-sm">
                      <button
                        type="button"
                        onClick={() => toggleExpanded(issueId)}
                        className="w-full text-left px-4 py-3 flex items-start justify-between gap-3 hover:bg-gray-50 rounded-lg"
                      >
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold ${cls}`}>
                              <Icon size={12} />
                              {label}
                            </span>
                            <span className="text-[10px] font-bold text-gray-500">Issue</span>
                            <span className="text-[10px] font-mono text-gray-600 truncate">{issueId}</span>
                          </div>
                          <div className="mt-1 text-xs text-gray-700 line-clamp-2">
                            {issue.reason_summary || "No reason summary provided."}
                          </div>
                        </div>
                        <div className="pt-1 text-gray-400">{isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}</div>
                      </button>

                      {isExpanded && (
                        <div className="px-4 pb-4 space-y-3">
                          {Array.isArray(issue.manual_actions) && issue.manual_actions.length > 0 && (
                            <div className="bg-amber-50 border border-amber-100 rounded p-3">
                              <div className="flex items-center gap-2 text-[10px] font-bold text-amber-800 uppercase">
                                <Info size={12} />
                                Manual Actions
                              </div>
                              <ul className="mt-2 space-y-2">
                                {issue.manual_actions.map((a: any, idx: number) => (
                                  <li key={idx} className="text-xs text-amber-900">
                                    <div className="font-semibold">{a.action_title}</div>
                                    <div className="text-amber-900/80">{a.action_details}</div>
                                    {a.suggested_location && (
                                      <div className="text-[11px] text-amber-900/70">Where: {a.suggested_location}</div>
                                    )}
                                  </li>
                                ))}
                              </ul>
                            </div>
                          )}

                          <div className="grid grid-cols-2 gap-3">
                            <div className="text-[11px] text-gray-600">
                              <div className="font-bold text-gray-500 uppercase text-[10px]">Affected Rows</div>
                              <div className="mt-1 text-gray-800">{(issue.affected_row_ids || []).length}</div>
                            </div>
                            <div className="text-[11px] text-gray-600">
                              <div className="font-bold text-gray-500 uppercase text-[10px]">Updated At</div>
                              <div className="mt-1 text-gray-800">{issue.updated_at ? String(issue.updated_at) : "-"}</div>
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="text-center py-10 bg-gray-50/30 rounded-lg border-2 border-dashed border-gray-100">
                <CheckCircle2 size={32} className="text-gray-200 mx-auto mb-3" />
                <p className="text-sm text-gray-400 font-medium">No issues linked to this row.</p>
              </div>
            )}

            {warnings.length > 0 && (
              <div className="space-y-2">
                <div className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Warnings</div>
                {warnings.map((w: any) => (
                  <div key={w.warning_id || Math.random()} className="text-xs bg-red-50 border border-red-100 text-red-800 rounded p-3">
                    <div className="font-semibold">{w.warning_type || "WARNING"}</div>
                    <div className="mt-1">{w.message}</div>
                  </div>
                ))}
              </div>
            )}

            {changes.length > 0 && (
              <div className="space-y-2">
                <div className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Change Log</div>
                <div className="space-y-2">
                  {changes.slice(0, 20).map((c: any) => (
                    <div key={c.change_id || Math.random()} className="bg-gray-50 border border-gray-100 rounded p-3">
                      <div className="flex items-center justify-between gap-2">
                        <div className="text-xs font-semibold text-gray-800">{c.field_name}</div>
                        <div className="text-[10px] font-bold text-gray-500">{c.source}</div>
                      </div>
                      <div className="mt-1 text-[11px] text-gray-700">
                        <span className="text-gray-500">Before:</span> {String(c.before_value ?? "-")}
                      </div>
                      <div className="text-[11px] text-gray-700">
                        <span className="text-gray-500">After:</span> {String(c.after_value ?? "-")}
                      </div>
                      {c.rationale && <div className="mt-1 text-[11px] text-gray-600">{c.rationale}</div>}
                    </div>
                  ))}
                  {changes.length > 20 && (
                    <div className="text-[11px] text-gray-500">Showing first 20 changes for this row.</div>
                  )}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center p-6 bg-gray-50/30 rounded-lg border-2 border-dashed border-gray-100">
            <ListChecks size={32} className="text-gray-200 mb-3" />
            <p className="text-sm text-gray-400 font-medium">Select a row to see its issue status</p>
          </div>
        )}
      </div>
    </div>
  );
}
