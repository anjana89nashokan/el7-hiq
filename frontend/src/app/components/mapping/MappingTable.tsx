import { useEffect, useRef, useState } from "react";
import { Maximize2 } from "lucide-react";

interface MappingTableProps {
  readonly mappingData: any;
  readonly selectedMappingIndex: number | null;
  readonly editingCell: { rowIdx: number; colName: string } | null;
  readonly feedbacks: Record<string, string>;
  readonly rowsMissingFeedback?: Set<string>;
  readonly readOnly?: boolean;
  readonly showFeedback?: boolean;
  readonly onRowSelect: (index: number) => void;
  readonly onCellEdit: (cell: { rowIdx: number; colName: string } | null) => void;
  readonly onFieldUpdate: (rowIdx: number, field: string, value: any) => void;
  readonly onFeedbackChange: (rowId: string, value: string) => void;
}

const RULE_OPTIONS = ["DIRECT", "LOOKUP", "SK", "TECHNICAL", "DEFAULT", "HARDCODE", "SUBSTRING", "CASE", "IF_ELSE", "UNKNOWN"];
//const JOIN_OPTIONS = ["DIRECT", "T1.ACCOUNT_ID = T2.ID", "T1.PROVIDER_ID = T2.SRC_ID", "MAP_TO_FIRST"];

export default function MappingTable({
  mappingData,
  selectedMappingIndex,
  editingCell,
  feedbacks,
  rowsMissingFeedback,
  readOnly,
  showFeedback,
  onRowSelect,
  onCellEdit,
  onFieldUpdate,
  onFeedbackChange
}: MappingTableProps) {
  console.log('mappingData:', mappingData);

  const allowEdit = !readOnly;
  const shouldShowFeedback = showFeedback !== false;

  const [feedbackModal, setFeedbackModal] = useState<null | { rowId: string; initialValue: string; draft: string }>(null);
  const feedbackTextareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!feedbackModal) return;
    const t = setTimeout(() => feedbackTextareaRef.current?.focus(), 0);
    return () => clearTimeout(t);
  }, [feedbackModal]);

  useEffect(() => {
    if (!feedbackModal) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setFeedbackModal(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [feedbackModal]);
  
  const getRuleTypeClass = (ruleType: string) => {
    if (ruleType === 'DIRECT') return 'bg-green-100 text-green-700';
    if (ruleType === 'SK') return 'bg-teal-100 text-font-blue';
    if (ruleType === 'LOOKUP') return 'bg-teal-100 text-font-blue';
    return 'bg-gray-100 text-gray-700';
  };
  return (
    <div className="flex-1 bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden flex flex-col">
      <div className="overflow-x-auto flex-1">
        <table className="w-full text-left border-collapse">
          <thead className="sticky top-0 z-10 bg-gray-50 shadow-sm">
            <tr className="border-b border-gray-200">
              <th className="px-4 py-3 text-[10px] font-bold text-gray-600 uppercase tracking-wider">Database</th>
              <th className="px-4 py-3 text-[10px] font-bold text-gray-600 uppercase tracking-wider">Target Name</th>
              <th className="px-4 py-3 text-[10px] font-bold text-gray-600 uppercase tracking-wider">Target Attribute Name</th>
              <th className="px-4 py-3 text-[10px] font-bold text-gray-600 uppercase tracking-wider">Logical Attribute Name</th>
              <th className="px-4 py-3 text-[10px] font-bold text-gray-600 uppercase tracking-wider">Attribute Business Description</th>
              <th className="px-4 py-3 text-[10px] font-bold text-gray-600 uppercase tracking-wider">Data Type</th>
              <th className="px-4 py-3 text-[10px] font-bold text-gray-600 uppercase tracking-wider">Default</th>
              <th className="px-4 py-3 text-[10px] font-bold text-gray-600 uppercase tracking-wider">Nullability</th>
              <th className="px-4 py-3 text-[10px] font-bold text-gray-600 uppercase tracking-wider">Key (P/F/A)</th>
              <th className="px-4 py-3 text-[10px] font-bold text-gray-600 uppercase tracking-wider">Source Table</th>
              <th className="px-4 py-3 text-[10px] font-bold text-gray-600 uppercase tracking-wider">Source Column</th>
              <th className="px-4 py-3 text-[10px] font-bold text-gray-600 uppercase tracking-wider">Rule</th>
              <th className="px-4 py-3 text-[10px] font-bold text-gray-600 uppercase tracking-wider">Join Condition</th>
              <th className="px-4 py-3 text-[10px] font-bold text-gray-600 uppercase tracking-wider">Transformation Rules</th>
              <th className="px-4 py-3 text-[10px] font-bold text-gray-600 uppercase tracking-wider">Special Considerations</th>
              <th className="px-4 py-3 text-[10px] font-bold text-gray-600 uppercase tracking-wider">Filters</th>
              {shouldShowFeedback && (
                <th className="px-4 py-3 text-[10px] font-bold text-gray-600 uppercase tracking-wider">Feedback</th>
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 overflow-y-auto">
            {mappingData.column_mappings.map((mapping: any, idx: number) => {
              const rowId = mapping.row_id || `mapping-${idx}`;
              const needsFeedback = Boolean(rowsMissingFeedback?.has(rowId));
              const nullabilityDisplay =
                mapping.target_nullability === true
                  ? "Y"
                  : mapping.target_nullability === false
                    ? "N"
                    : "-";

              return (
                <tr
                  key={rowId}
                  className={`transition-colors cursor-pointer ${selectedMappingIndex === idx ? 'bg-brand-surface border-l-4 border-l-sky-500' : 'hover:bg-gray-50'} ${needsFeedback ? 'ring-1 ring-red-200' : ''}`}
                  onClick={() => onRowSelect(idx)}
                >
                <td className="px-4 py-4 text-[11px] text-gray-700">{mapping.target_database || "-"}</td>
                <td className="px-4 py-4 text-[11px] font-medium text-gray-900">{mapping.target_table?.entity_id || "-"}</td>
                <td className="px-4 py-4 text-[11px] text-brand-darkblue font-semibold">{mapping.target_column_name}</td>
                <td className="px-4 py-4 text-[11px] text-gray-700">{mapping.target_logical_attribute_name || "-"}</td>
                <td className="px-4 py-4 text-[10px] text-gray-600">
                  <span className="block truncate max-w-[240px]">{mapping.target_attribute_business_description || "-"}</span>
                </td>
                <td className="px-4 py-4 text-[11px] text-gray-700">{mapping.target_data_type || "-"}</td>
                <td className="px-4 py-4 text-[11px] text-gray-700">
                  <span className="block truncate max-w-[140px]">{mapping.target_default || "-"}</span>
                </td>
                <td className="px-4 py-4 text-[11px] text-gray-700">
                  {nullabilityDisplay}
                </td>
                <td className="px-4 py-4 text-[11px] text-gray-700">{mapping.target_key || "-"}</td>
                <td className="px-4 py-4 text-[11px] text-gray-600">{mapping.source_entity?.entity_id || "-"}</td>

                {/* Source Column Editing */}
                <td
                  className="px-4 py-4 text-[11px] text-gray-600 relative"
                  onClick={(e) => {
                    if (!allowEdit) return;
                    e.stopPropagation();
                    onCellEdit({ rowIdx: idx, colName: "source_column" });
                  }}
                >
                  {allowEdit && editingCell?.rowIdx === idx && editingCell.colName === "source_column" ? (
                    <select
                      autoFocus
                      className="absolute inset-x-2 inset-y-2 text-[10px] border border-brand-primary rounded outline-none z-20"
                      onChange={(e) => {
                        const candidate = mapping.candidate_sources_topk.find((c: any) => c.source_column_name === e.target.value);
                        onFieldUpdate(idx, "source_column", candidate);
                      }}
                      onBlur={() => onCellEdit(null)}
                      defaultValue={mapping.source_field_names?.[0]}
                    >
                      {mapping.candidate_sources_topk?.map((c: any) => (
                        <option key={c.source_column_name} value={c.source_column_name}>
                          {c.source_column_name} ({c.score})
                        </option>
                      ))}
                    </select>
                  ) : (
                    <span className="border-b border-dashed border-gray-300 hover:text-font-blue">
                      {mapping.source_field_names?.join(", ") || "-"}
                    </span>
                  )}
                </td>

                {/* Rule Editing */}
                <td
                  className="px-4 py-4 relative"
                  onClick={(e) => {
                    if (!allowEdit) return;
                    e.stopPropagation();
                    onCellEdit({ rowIdx: idx, colName: "rule_type" });
                  }}
                >
                  {allowEdit && editingCell?.rowIdx === idx && editingCell.colName === "rule_type" ? (
                    <select
                      autoFocus
                      className="absolute inset-x-2 inset-y-2 text-[10px] border border-brand-primary rounded outline-none z-20"
                      onChange={(e) => onFieldUpdate(idx, "rule_type", e.target.value)}
                      onBlur={() => onCellEdit(null)}
                      defaultValue={mapping.rule_type}
                    >
                      {RULE_OPTIONS.map(opt => (
                        <option key={opt} value={opt}>{opt}</option>
                      ))}
                    </select>
                  ) : (
                    <span className={`px-2 py-1 rounded-full text-[9px] font-bold uppercase transition-transform hover:scale-105 ${getRuleTypeClass(mapping.rule_type)}`}>
                      {mapping.rule_type}
                    </span>
                  )}
                </td>

                {/* Join Editing */}
                <td
                  className="px-4 py-4 text-[10px] text-gray-500 font-mono italic relative"
                  onClick={(e) => {
                    if (!allowEdit) return;
                    e.stopPropagation();
                    onCellEdit({ rowIdx: idx, colName: "join_condition" });
                  }}
                >
                  {allowEdit && editingCell?.rowIdx === idx && editingCell.colName === "join_condition" ? (
                    <input
                      autoFocus
                      type="text"
                      className="absolute inset-x-2 inset-y-2 text-[9px] border border-brand-primary rounded outline-none z-20 px-2"
                      onChange={(e) => onFieldUpdate(idx, "join_condition", e.target.value)}
                      onBlur={() => onCellEdit(null)}
                      defaultValue={mapping.join_condition?.join_text || ""}
                    />
                  ) : (
                    <span className="border-b border-dashed border-gray-300 hover:text-font-blue block truncate max-w-[100px]">
                      {mapping.join_condition?.join_text || "-"}
                    </span>
                  )}
                </td>

                {/* Transformation Rules Editing */}
                <td
                  className="px-4 py-4 text-[10px] text-gray-500 relative"
                  onClick={(e) => {
                    if (!allowEdit) return;
                    e.stopPropagation();
                    onCellEdit({ rowIdx: idx, colName: "transformation_rules_text" });
                  }}
                >
                  {allowEdit && editingCell?.rowIdx === idx && editingCell.colName === "transformation_rules_text" ? (
                    <textarea
                      autoFocus
                      className="absolute inset-x-2 inset-y-2 text-[10px] border border-brand-primary rounded outline-none z-20 p-2 min-h-[60px]"
                      onChange={(e) => onFieldUpdate(idx, "transformation_rules_text", e.target.value)}
                      onBlur={() => onCellEdit(null)}
                      defaultValue={mapping.transformation_rules_text || ""}
                    />
                  ) : (
                    <span className="border-b border-dashed border-gray-300 hover:text-font-blue block truncate max-w-[120px]">
                      {mapping.transformation_rules_text || "-"}
                    </span>
                  )}
                </td>

                {/* Special Considerations Editing */}
                <td
                  className="px-4 py-4 text-[10px] text-gray-500 relative"
                  onClick={(e) => {
                    if (!allowEdit) return;
                    e.stopPropagation();
                    onCellEdit({ rowIdx: idx, colName: "special_considerations_text" });
                  }}
                >
                  {allowEdit && editingCell?.rowIdx === idx && editingCell.colName === "special_considerations_text" ? (
                    <textarea
                      autoFocus
                      className="absolute inset-x-2 inset-y-2 text-[10px] border border-brand-primary rounded outline-none z-20 p-2 min-h-[60px]"
                      onChange={(e) => onFieldUpdate(idx, "special_considerations_text", e.target.value)}
                      onBlur={() => onCellEdit(null)}
                      defaultValue={mapping.special_considerations_text || ""}
                    />
                  ) : (
                    <span className="border-b border-dashed border-gray-300 hover:text-font-blue block truncate max-w-[120px]">
                      {mapping.special_considerations_text || "-"}
                    </span>
                  )}
                </td>

                {/* Filters Editing */}
                <td
                  className="px-4 py-4 text-[10px] text-gray-500 relative"
                  onClick={(e) => {
                    if (!allowEdit) return;
                    e.stopPropagation();
                    onCellEdit({ rowIdx: idx, colName: "row_filter_text" });
                  }}
                >
                  {allowEdit && editingCell?.rowIdx === idx && editingCell.colName === "row_filter_text" ? (
                    <textarea
                      autoFocus
                      className="absolute inset-x-2 inset-y-2 text-[10px] border border-brand-primary rounded outline-none z-20 p-2 min-h-[60px]"
                      onChange={(e) => onFieldUpdate(idx, "row_filter_text", e.target.value)}
                      onBlur={() => onCellEdit(null)}
                      defaultValue={mapping.row_filter_text || ""}
                    />
                  ) : (
                    <span className="border-b border-dashed border-gray-300 hover:text-font-blue block truncate max-w-[100px]">
                      {mapping.row_filter_text || "-"}
                    </span>
                  )}
                </td>

                  {shouldShowFeedback && (
                    <td className="px-4 py-4" onClick={(e) => e.stopPropagation()}>
                      {allowEdit ? (
                        <>
                          <div className="flex items-start gap-1">
                            <input
                              type="text"
                              placeholder="Add feedback..."
                              className={`flex-1 px-2 py-1 border rounded text-[10px] focus:ring-1 outline-none bg-white ${needsFeedback ? 'border-red-400 focus:ring-red-400' : 'border-gray-200 focus:ring-brand-primary'}`}
                              value={feedbacks[rowId] || ""}
                              onChange={(e) => onFeedbackChange(rowId, e.target.value)}
                            />
                            <button
                              type="button"
                              title="Edit feedback"
                              className="shrink-0 p-1 rounded border border-gray-200 bg-white text-gray-600 hover:text-gray-900 hover:border-gray-300"
                              onClick={(e) => {
                                e.stopPropagation();
                                const current = feedbacks[rowId] || "";
                                setFeedbackModal({ rowId, initialValue: current, draft: current });
                              }}
                            >
                              <Maximize2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                          {needsFeedback && (
                            <div className="text-[10px] text-red-600 mt-1 font-semibold">Feedback required</div>
                          )}
                        </>
                      ) : (
                        <span className="text-[10px] text-gray-500">-</span>
                      )}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {allowEdit && shouldShowFeedback && feedbackModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div
            role="dialog"
            aria-modal="true"
            className="w-full max-w-2xl rounded-lg bg-white shadow-xl border border-gray-200"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
              <div className="text-sm font-semibold text-gray-900">Edit feedback</div>
              <div className="text-[11px] text-gray-500 truncate max-w-[70%]">Row: {feedbackModal.rowId}</div>
            </div>

            <div className="p-4">
              <textarea
                ref={feedbackTextareaRef}
                className="w-full min-h-[240px] p-3 text-sm border border-gray-300 rounded outline-none focus:ring-2 focus:ring-brand-primary focus:border-brand-primary"
                value={feedbackModal.draft}
                onChange={(e) => setFeedbackModal({ ...feedbackModal, draft: e.target.value })}
                placeholder="Write detailed feedback, reasoning, or instructions for Step 4..."
              />
              <div className="mt-2 text-[11px] text-gray-500">
                Cancel discards changes. Save updates the feedback for this row.
              </div>
            </div>

            <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-gray-200 bg-gray-50">
              <button
                type="button"
                className="px-3 py-1.5 text-sm rounded border border-gray-300 bg-white hover:bg-gray-100"
                onClick={() => setFeedbackModal(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="px-3 py-1.5 text-sm rounded bg-brand-primary text-white hover:bg-brand-primary-hover"
                onClick={() => {
                  onFeedbackChange(feedbackModal.rowId, feedbackModal.draft);
                  setFeedbackModal(null);
                }}
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
