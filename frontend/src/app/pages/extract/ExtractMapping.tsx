import { useState, useEffect, useRef } from "react";
import * as XLSX from "xlsx";
import { Download, Info, RefreshCw } from "lucide-react";
import { useDispatch, useSelector } from "react-redux";
import {
  runExtractMapping, acceptMapping, runFieldHumanCheckpoint, setMappingData, runJudgeMapping, resetMappingData,
} from "../../state/reducers/extract/extractReducer";
import type { AppDispatch, RootState } from "../../state/store";
import type { MappingRow } from "../../end-points/extract/extractApi";
import { getCurrentAppSessionId, getCurrentSessionRuntime } from "../../utils/appSessionStorage";
import { getUserIdentity } from "../../utils/userIdentity";


// ── Constants ─────────────────────────────────────────────────────────────────

const ROW_COLUMNS: (keyof MappingRow)[] = [
  "order_no", "target_attribute", "logical_attribute_name", "attribute_description",
  "data_type", "length", "precision", "format", "nullable", "default_value",
  "cdc_indicator", "key_columns", "rule_type", "rule_name", "source_entity",
  "source_attribute", "join", "filter", "transformation_rule", "special_consideration",
  "last_updated", "match_level", "match_score", "open_item", "open_item_reason",
];

const COL_LABEL: Partial<Record<keyof MappingRow, string>> = {
  order_no: "Order No", target_attribute: "Target Attribute", logical_attribute_name: "Logical Attribute Name",
  attribute_description: "Attribute Description", data_type: "Data Type", length: "Length",
  precision: "Precision", format: "Format", nullable: "Nullable", default_value: "Default Value",
  cdc_indicator: "CDC Indicator", key_columns: "Key Columns", rule_type: "Rule Type", rule_name: "Rule Name",
  source_entity: "Source Entity", source_attribute: "Source Attribute", join: "Join",
  filter: "Filter", transformation_rule: "Transformation Rule",
  special_consideration: "Special Consideration", last_updated: "Last Updated",
  match_level: "Match Level", match_score: "Match Score", open_item: "Open Item", open_item_reason: "Open Item Reason",
};

const TR_META_FIELDS = ["target_entity", "driver_table_required", "history_data_pull", "common_filter"] as const;

const ROW_COL_WIDTH: Partial<Record<keyof MappingRow, string>> = {
  target_attribute: "min-w-[200px] max-w-[260px]",
  logical_attribute_name: "min-w-[200px] max-w-[260px]",
  attribute_description: "min-w-[350px] max-w-[420px]",
  transformation_rule: "min-w-[300px] max-w-[360px]",
  special_consideration: "min-w-[220px] max-w-[300px]",
  join: "min-w-[200px] max-w-[280px]",
  filter: "min-w-[200px] max-w-[280px]",
  open_item_reason: "min-w-[200px] max-w-[280px]",
  last_updated: "min-w-[160px] max-w-[220px]",
};

// ── Edit Modal ────────────────────────────────────────────────────────────────

function EditModal({
  loading, onConfirm, onCancel,
}: {
  readonly loading: boolean;
  readonly onConfirm: (instruction: string) => void;
  readonly onCancel: () => void;
}) {
  const [instruction, setInstruction] = useState("");
  const [touched, setTouched] = useState(false);
  const hasError = touched && !instruction.trim();

  const handleConfirm = () => {
    setTouched(true);
    if (!instruction.trim()) return;
    onConfirm(instruction.trim());
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md mx-4 p-6">
        <h3 className="text-base font-bold text-brand-darkblue mb-1">Review Row</h3>
        <p className="text-xs text-gray-500 mb-4">Provide instructions for how this row should be updated.</p>
        <div className="flex flex-col gap-1 mb-5">
          <label className="block text-sm font-medium text-gray-700 mb-1">
            STTM Instruction <span className="text-red-500">*</span>
          </label>
          <textarea
            rows={4}
            value={instruction}
            onChange={(e) => { setInstruction(e.target.value); setTouched(true); }}
            placeholder="e.g. change rule type to Lookup and rewrite transformation rule"
            className={`w-full px-3 py-2 border rounded-md bg-white text-xs resize-none focus:outline-none focus:ring-2 ${hasError ? "border-red-400 focus:ring-red-400" : "border-gray-300 focus:ring-brand-primary"
              }`}
          />
          {hasError && <p className="text-xs text-red-600 mt-1">Instruction is required.</p>}
        </div>
        <div className="flex justify-end gap-3">
          <button
            type="button"
            disabled={loading}
            onClick={onCancel}
            className="px-4 py-2 text-sm border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50 transition-colors cursor-pointer"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={loading}
            onClick={handleConfirm}
            className="px-4 py-2 text-sm bg-brand-primary text-white rounded-lg hover:bg-brand-primary-hover disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors cursor-pointer"
          >
            {loading ? "Updating…" : "Confirm Review"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Reject Modal ──────────────────────────────────────────────────────────────

function RejectModal({
  loading, onConfirm, onCancel,
}: {
  readonly loading: boolean;
  readonly onConfirm: (feedback: string) => void;
  readonly onCancel: () => void;
}) {
  const [feedback, setFeedback] = useState("");
  const [touched, setTouched] = useState(false);
  const hasError = touched && !feedback.trim();

  const handleConfirm = () => {
    setTouched(true);
    if (!feedback.trim()) return;
    onConfirm(feedback.trim());
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md mx-4 p-6">
        <h3 className="text-base font-bold text-brand-darkblue mb-1">Reject Mapping</h3>
        <p className="text-xs text-gray-500 mb-4">Provide feedback so the mapping can be re-extracted.</p>
        <div className="flex flex-col gap-1 mb-5">
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Feedback <span className="text-red-500">*</span>
          </label>
          <textarea
            rows={4}
            value={feedback}
            onChange={(e) => { setFeedback(e.target.value); setTouched(true); }}
            placeholder="e.g. target_attribute names should follow snake_case convention"
            className={`w-full px-3 py-2 border rounded-md bg-white text-xs resize-none focus:outline-none focus:ring-2 ${hasError ? "border-red-400 focus:ring-red-400" : "border-gray-300 focus:ring-brand-primary"
              }`}
          />
          {hasError && <p className="text-xs text-red-600 mt-1">Feedback is required.</p>}
        </div>
        <div className="flex justify-end gap-3">
          <button
            type="button"
            disabled={loading}
            onClick={onCancel}
            className="px-4 py-2 text-sm border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50 transition-colors cursor-pointer"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={loading}
            onClick={handleConfirm}
            className="px-4 py-2 text-sm bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors cursor-pointer"
          >
            {loading ? "Rejecting…" : "Confirm Reject"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function ExtractMapping({ onApprove }: { readonly onApprove?: () => void }) {
  const dispatch = useDispatch<AppDispatch>();
  const { mappingData: data, mappingLoading: loading, mappingError: error, mappingApproved, uploadSessionId,
    judgeMappingLoading, judgeMappingData, judgeMappingError,
  } = useSelector(
    (state: RootState) => state.extract
  );

  const [reviewLoading, setReviewLoading] = useState(false);
  const [approveLoading, setApproveLoading] = useState(false);
  const [retryLoading, setRetryLoading] = useState(false);
  const [showRejectModal, setShowRejectModal] = useState(false);
  const [editingRow, setEditingRow] = useState<{ row: MappingRow; idx: number } | null>(null);
  const [editLoading, setEditLoading] = useState(false);
  const [editMode, setEditMode] = useState(false);
  const [showKpiInfo, setShowKpiInfo] = useState(false);
  const [kpiExpanded, setKpiExpanded] = useState(false);
  const fetchedRef = useRef(false);
  const judgeMappingDispatched = useRef(false);

  // Fetch only once — ref guard prevents double-call in React Strict Mode
  useEffect(() => {
    if (data || loading || fetchedRef.current) return;
    fetchedRef.current = true;
    const sessionId = uploadSessionId ?? sessionStorage.getItem("current_app_session_id") ?? "";
    dispatch(runExtractMapping(sessionId));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-call judge-mapping once mapping data is available
  useEffect(() => {
    if (!data || judgeMappingData || judgeMappingLoading || judgeMappingDispatched.current) return;
    judgeMappingDispatched.current = true;
    const { userId } = getCurrentSessionRuntime();
    dispatch(runJudgeMapping({
      userId: userId ?? getUserIdentity().userId,
      sessionId: sessionStorage.getItem("session_id") ?? "",
      brd_uri: sessionStorage.getItem("brd_gcs_uri") ?? "",
      driver_uri: sessionStorage.getItem("driver_gcs_uri") ?? "",
      metadata_uri: sessionStorage.getItem("metadata_gcs_uri") ?? "",
      mapping_result: data as any,
      mapping_uri: "",
      revision_number: 0,
    }));
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps

  // Notify parent when approved via Redux
  useEffect(() => { if (mappingApproved) onApprove?.(); }, [mappingApproved]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Review actions ──────────────────────────────────────────────────────────

  const handleApprove = async () => {
    if (!data) return;
    setApproveLoading(true);
    try {
      await dispatch(acceptMapping({ common_rules: data.common_rules, transformation_rules: data.transformation_rules })).unwrap();
      onApprove?.();
    } catch (e) { console.error("Approve mapping failed:", e); } finally { setApproveLoading(false); }
  };

  const handleUpdateAndApprove = async () => {
    if (!data) return;
    setReviewLoading(true);
    try {
      await dispatch(acceptMapping({ common_rules: data.common_rules, transformation_rules: data.transformation_rules })).unwrap();
      setEditMode(false);
      onApprove?.();
    } catch (e) { console.error("Update and approve failed:", e); } finally { setReviewLoading(false); }
  };

  const handleEditConfirm = async (instruction: string) => {
    if (!editingRow) return;
    const { appName, userId } = getCurrentSessionRuntime();
    const sessionId = getCurrentAppSessionId() ?? "";
    setEditLoading(true);
    try {
      await dispatch(runFieldHumanCheckpoint({
        appName: appName ?? "",
        sessionId,
        user_id: userId ?? "dev-user",
        target_attribute: editingRow.row.target_attribute ?? "",
        current_row: editingRow.row,
        bsa_instruction: instruction,
        idx: editingRow.idx,
      })).unwrap();
    } catch (e) { console.error("Field checkpoint failed:", e); } finally { setEditLoading(false); setEditingRow(null); }
  };

  const handleRejectConfirm = async (feedback: string) => {
    setShowRejectModal(false);
    setReviewLoading(true);
    try {
      console.log("Reject feedback:", feedback);
      await new Promise((r) => setTimeout(r, 500));
    } finally { setReviewLoading(false); }
  };

  const fetchMapping = async () => {
    setRetryLoading(true);
    judgeMappingDispatched.current = false;
    dispatch(resetMappingData());
    try {
      const sessionId = uploadSessionId ?? sessionStorage.getItem("current_app_session_id") ?? "";
      await dispatch(runExtractMapping(sessionId)).unwrap();
    } catch (e) { console.error("Fetch mapping failed:", e); } finally { setRetryLoading(false); }
  };

  // ── Excel export ────────────────────────────────────────────────────────────

  const exportExcel = () => {
    if (!data) return;
    const wb = XLSX.utils.book_new();

    const commonRulesWs = XLSX.utils.json_to_sheet(data.common_rules);
    commonRulesWs["!cols"] = [{ wch: 35 }, { wch: 50 }];
    XLSX.utils.book_append_sheet(wb, commonRulesWs, "Common Rules");

    const tr = data.transformation_rules;
    const rowCount = tr.rows.length;
    const META_LABELS = ["Target Entity", "Driver Table Required", "History Data Pull", "Common Filter"];
    const META_VALUES = [tr.target_entity ?? "", tr.driver_table_required ?? "", tr.history_data_pull ?? "", tr.common_filter ?? ""];
    const headerRow = [...META_LABELS, ...ROW_COLUMNS.map((c) => COL_LABEL[c])];
    const dataRows = tr.rows.map((row, i) => [
      ...(i === 0 ? META_VALUES : ["", "", "", ""]),
      ...ROW_COLUMNS.map((c) => c === "match_score" && row[c] != null ? `${(Number(row[c]) * 100).toFixed(0)}%` : row[c] ?? ""),
    ]);
    const ws = XLSX.utils.aoa_to_sheet([headerRow, ...dataRows]);
    if (rowCount > 1) {
      ws["!merges"] = META_VALUES.map((_, colIdx) => ({ s: { r: 1, c: colIdx }, e: { r: rowCount, c: colIdx } }));
    }
    const colWidths: Record<keyof MappingRow, number> = {
      order_no: 10, target_attribute: 25, logical_attribute_name: 30, attribute_description: 45,
      data_type: 15, length: 10, precision: 10, format: 15, nullable: 10, default_value: 15,
      cdc_indicator: 15, key_columns: 20, rule_type: 15, rule_name: 20, source_entity: 25,
      source_attribute: 25, join: 40, filter: 40, transformation_rule: 50, special_consideration: 45,
      last_updated: 18, match_level: 15, match_score: 12, open_item: 12, open_item_reason: 40,
    };
    ws["!cols"] = [
      { wch: 25 }, { wch: 25 }, { wch: 25 }, { wch: 40 },
      ...ROW_COLUMNS.map((c) => ({ wch: colWidths[c] })),
    ];
    XLSX.utils.book_append_sheet(wb, ws, "Transformation Rules");
    XLSX.writeFile(wb, "Extract_Mapping.xlsx");
  };

  const tr = data?.transformation_rules;

  return (
    <div className="mt-4 space-y-6">
      {/* ── Header ── */}
      <div className="flex items-center justify-between">
        <h3 className="text-base font-bold text-brand-darkblue">Extract Mapping</h3>
        <div className="flex items-center gap-3">
          {data && !loading && (
            <button
              type="button"
              onClick={exportExcel}
              className="flex items-center gap-1 text-xs text-font-blue hover:text-font-blue font-medium cursor-pointer"
            >
              <Download size={14} />
              Export as Excel
            </button>
          )}
          {data && (
            <span className="text-xs text-gray-400">
              Session: {data.session_id}&nbsp;·&nbsp;
            </span>
          )}
        </div>
      </div>

      {/* ── Loading ── */}
      {loading && (
        <div className="flex items-center justify-center py-10 gap-3">
          <div className="w-6 h-6 border-2 border-brand-darkblue border-t-transparent rounded-full animate-spin" />
          <span className="text-sm text-gray-500">Loading mapping…</span>
        </div>
      )}

      {/* ── Error ── */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3">
          <p className="text-red-600 text-xs">{error}</p>
        </div>
      )}

      {/* ── Common Rules ── */}
      {data && (
        <section>
          <h4 className="text-sm font-semibold text-gray-700 mb-2">Common Rules</h4>
          <div className="border border-gray-200 rounded-lg overflow-hidden">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200">
                  <th className="px-4 py-2 text-left font-semibold text-gray-700 w-1/3">Field</th>
                  <th className="px-4 py-2 text-left font-semibold text-gray-700">Value</th>
                </tr>
              </thead>
              <tbody>
                {data.common_rules.map((rule, idx) => (
                  <tr key={rule.Field} className={idx % 2 === 0 ? "bg-white" : "bg-gray-50/50"}>
                    <td className="px-4 py-2 font-medium text-gray-700 border-b border-gray-100">{rule.Field}</td>
                    <td className="px-4 py-2 border-b border-gray-100">
                      <span className={rule.Value ? "text-gray-800" : "text-gray-300 italic"}>{rule.Value || "—"}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* ── Transformation Rules ── */}
      {tr && (
        <section>
          <h4 className="text-sm font-semibold text-gray-700 mb-2">Transformation Rules</h4>

          {/* Meta fields */}
          {(tr.target_entity || tr.driver_table_required || tr.history_data_pull || tr.common_filter) && (
            <div className="border border-gray-200 rounded-lg p-4 bg-white text-xs grid grid-cols-2 gap-x-6 gap-y-2 mb-3">
              {TR_META_FIELDS.map((f) => (
                <div key={f} className={`flex gap-2 items-center ${f === "common_filter" ? "col-span-2" : ""}`}>
                  <span className="font-bold text-gray-600 capitalize shrink-0">{f.replace(/_/g, " ")}</span>
                  {f === "common_filter" && (
                    <span className="relative group">
                      <Info size={13} className="text-gray-400 cursor-pointer hover:text-brand-primary" />
                      <span className="absolute left-5 top-0 z-50 hidden group-hover:block w-64 bg-gray-800 text-white text-xs rounded-lg px-3 py-2 shadow-lg">
                        STTM can edit Common Filters in Requirement Layer
                      </span>
                    </span>
                  )}
                  <span> :</span>
                  {editMode && f !== "common_filter" ? (
                    <input
                      type="text"
                      value={tr[f] ?? ""}
                      onChange={(e) => dispatch(setMappingData({
                        ...data!,
                        transformation_rules: { ...data!.transformation_rules, [f]: e.target.value || null },
                      }))}
                      className="flex-1 px-2 py-1 border border-gray-300 rounded text-xs focus:outline-none focus:ring-1 focus:ring-brand-primary"
                    />
                  ) : (
                    <span className={tr[f] ? "text-gray-800" : "text-gray-300 italic"}>{tr[f] ?? "—"}</span>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Rows table */}
          {tr.rows.length === 0 && !tr.target_entity && !tr.driver_table_required && !tr.history_data_pull && !tr.common_filter ? (
            <div className="border border-gray-200 bg-white rounded-lg py-10 flex items-center justify-center">
              <p className="text-sm text-gray-400 italic">No transformation rules available.</p>
            </div>
          ) : (
            <div className="border border-gray-200 rounded-lg overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="bg-gray-50 border-b border-gray-200">
                    <th className="px-3 py-2 text-left font-semibold text-gray-700">Actions</th>
                    {ROW_COLUMNS.map((col) => (
                      <th key={col} className="px-3 py-2 text-left font-semibold text-gray-700">{COL_LABEL[col]}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {tr.rows.map((row, idx) => (
                    <tr key={idx} className={idx % 2 === 0 ? "bg-white" : "bg-gray-50/50"}>
                      <td className="px-3 py-2 border-b border-gray-100">
                        <button
                          type="button"
                          onClick={() => setEditingRow({ row, idx })}
                          className="px-2 py-1 text-xs bg-brand-primary text-white rounded hover:bg-brand-primary-hover transition-colors cursor-pointer"
                        >
                          Review
                        </button>
                      </td>
                      {ROW_COLUMNS.map((col) => (
                        <td key={col} className={`px-3 py-2 border-b border-gray-100 whitespace-normal break-words ${ROW_COL_WIDTH[col] ?? "min-w-[80px] max-w-[140px]"}`}>
                          {editMode ? (
                            <input
                              type="text"
                              value={String(row[col] ?? "")}
                              onChange={(e) => {
                                const newRows = data!.transformation_rules.rows.map((r, i) =>
                                  i === idx ? { ...r, [col]: e.target.value || null } : r
                                );
                                dispatch(setMappingData({ ...data!, transformation_rules: { ...data!.transformation_rules, rows: newRows } }));
                              }}
                              className="w-full min-w-[80px] px-2 py-1 border border-gray-300 rounded text-xs focus:outline-none focus:ring-1 focus:ring-brand-primary"
                            />
                          ) : (
                            <span className={row[col] ? "text-gray-800" : "text-gray-300 italic"}>
                              {col === "match_score"
                                ? row[col] != null ? `${(Number(row[col]) * 100).toFixed(0)}%` : "—"
                                : row[col] ?? "—"}
                            </span>
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {/* ── Judge Mapping ── */}
      {(judgeMappingLoading || judgeMappingData || judgeMappingError) && (
        <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setKpiExpanded(!kpiExpanded)}
                className="flex items-center gap-1 cursor-pointer group"
              >
                <h3 className="text-sm font-semibold text-brand-darkblue">Mapping Quality Evaluation</h3>
                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={`text-gray-400 group-hover:text-brand-darkblue transition-transform ${kpiExpanded ? "rotate-180" : ""}`}><polyline points="6 9 12 15 18 9" /></svg>
              </button>
              {kpiExpanded && (
                <button
                  type="button"
                  onClick={() => setShowKpiInfo(!showKpiInfo)}
                  className="text-gray-400 hover:text-brand-primary transition-colors cursor-pointer"
                  title="Toggle KPI information"
                >
                  <Info size={14} />
                </button>
              )}
            </div>
            <button
              type="button"
              disabled={judgeMappingLoading}
              onClick={() => {
                judgeMappingDispatched.current = false;
                const { userId } = getCurrentSessionRuntime();
                if (data) dispatch(runJudgeMapping({
                  userId: userId ?? getUserIdentity().userId,
                  sessionId: sessionStorage.getItem("session_id") ?? "",
                  brd_uri: sessionStorage.getItem("brd_gcs_uri") ?? "",
                  driver_uri: sessionStorage.getItem("driver_gcs_uri") ?? "",
                  metadata_uri: sessionStorage.getItem("metadata_gcs_uri") ?? "",
                  mapping_result: data as any,
                  mapping_uri: "",
                  revision_number: 0,
                }));
              }}
              className="text-gray-400 hover:text-brand-darkblue disabled:opacity-40 disabled:cursor-not-allowed transition-colors cursor-pointer"
              title="Retry mapping evaluation"
            >
              <RefreshCw size={14} className={judgeMappingLoading ? "animate-spin" : ""} />
            </button>
          </div>
          {kpiExpanded && (
            <>
              {showKpiInfo && (
                <div className="bg-brand-surface border border-teal-200 rounded-lg p-3 text-xs space-y-2">
                  <div className="font-semibold text-brand-darkblue mb-2">KPI Definitions</div>
                  {judgeMappingData ? Object.entries(judgeMappingData.kpis).map(([name, kpi]) => (
                    kpi.definition ? (
                      <div key={name} className="text-gray-700">
                        {kpi.definition}
                      </div>
                    ) : null
                  )) : <p className="text-gray-400 italic">Run evaluation to see KPI definitions.</p>}
                </div>
              )}
              {judgeMappingLoading && (
                <div className="flex items-center gap-2 text-gray-400 text-xs">
                  <div className="w-4 h-4 border-2 border-brand-darkblue border-t-transparent rounded-full animate-spin" />
                  Running mapping evaluation…
                </div>
              )}
              {judgeMappingError && <p className="text-xs text-red-600">{judgeMappingError}</p>}
              {judgeMappingData && (
                <div className="grid grid-cols-2 gap-2">
                  {Object.entries(judgeMappingData.kpis).map(([name, kpi]) => {
                    const pct = kpi.score * 100;
                    const color = pct >= 80 ? "text-green-600" : pct >= 50 ? "text-amber-500" : "text-red-500";
                    const bar = pct >= 80 ? "bg-green-500" : pct >= 50 ? "bg-amber-400" : "bg-red-400";
                    return (
                      <div key={name} className="flex flex-col gap-1.5 border border-gray-100 rounded-lg p-3 bg-gray-50">
                        <span className="text-xs font-semibold text-gray-500 capitalize">{name.replace(/_/g, " ")}</span>
                        <span className={`text-lg font-bold ${color}`}>{pct.toFixed(1)}%</span>
                        <div className="w-full h-1.5 bg-gray-200 rounded-full overflow-hidden">
                          <div className={`h-full rounded-full ${bar}`} style={{ width: `${pct}%` }} />
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* ── Action bar ── */}
      {data && (
        <div className="flex items-center gap-3 pt-4 border-t border-gray-200">
          {!editMode && (
            <>
              <button
                type="button"
                disabled={approveLoading || mappingApproved}
                onClick={handleApprove}
                className="px-4 py-2 text-sm bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors cursor-pointer"
              >
                {approveLoading ? "Approving…" : mappingApproved ? "Approved ✓" : "Approve"}
              </button>
              <button
                type="button"
                onClick={() => setEditMode(true)}
                className="px-4 py-2 text-sm bg-brand-primary text-white rounded-lg hover:bg-brand-primary-hover transition-colors cursor-pointer"
              >
                Edit
              </button>
              <button
                type="button"
                disabled={retryLoading}
                onClick={fetchMapping}
                className="px-4 py-2 text-sm border border-gray-400 text-gray-600 rounded-lg hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors cursor-pointer"
              >
                {retryLoading ? "Retrying…" : "Retry"}
              </button>
            </>
          )}

          {editMode && (
            <>
              <button
                type="button"
                disabled={reviewLoading}
                onClick={handleUpdateAndApprove}
                className="px-4 py-2 text-sm bg-brand-primary text-white rounded-lg hover:bg-brand-primary-hover disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors cursor-pointer"
              >
                {reviewLoading ? "Updating…" : "Update & Approve"}
              </button>
              <button
                type="button"
                onClick={() => setEditMode(false)}
                className="px-4 py-2 text-sm border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50 transition-colors cursor-pointer"
              >
                Cancel
              </button>
            </>
          )}
        </div>
      )}

      {/* ── Reject Modal ── */}
      {showRejectModal && (
        <RejectModal
          loading={reviewLoading}
          onConfirm={handleRejectConfirm}
          onCancel={() => setShowRejectModal(false)}
        />
      )}

      {/* ── Edit Modal ── */}
      {editingRow && (
        <EditModal
          loading={editLoading}
          onConfirm={handleEditConfirm}
          onCancel={() => setEditingRow(null)}
        />
      )}
    </div>
  );
}
