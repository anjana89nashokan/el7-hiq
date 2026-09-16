import { useState, useEffect, useRef } from "react";
import { useDispatch, useSelector } from "react-redux";
import { Info, RefreshCw } from "lucide-react";
import { reviewMetadata, updateMetadataField, updateFileAttribute, runExtractMetadata, runJudgeMetadata, resetMetadata, manualUpdateMetadata, runDriverMapping } from "../../state/reducers/extract/extractReducer";
import type { AppDispatch, RootState } from "../../state/store";
import type { FileAttribute } from "../../end-points/extract/extractApi";
import { getCurrentSessionRuntime } from "../../utils/appSessionStorage";
import { Download } from "lucide-react";
import * as XLSX from "xlsx";
import { instructionsData } from "../../config/instructionsConfig";
import { valuesData } from "../../config/valuesConfig";
import { getUserIdentity } from "../../utils/userIdentity";


const ATTRIBUTE_COLUMNS: (keyof FileAttribute)[] = [
  "Attribute Name", "Logical Attribute Name", "Attribute Description",
  "Data Type", "Length", "Precision", "Format",
  "Nullability", "Default Value", "Primary Key", "Foreign Key", "Alternate Key1",
];

const ATTRIBUTE_COL_WIDTH: Partial<Record<keyof FileAttribute, string>> = {
  "Attribute Name": "min-w-[200px] max-w-[220px]",
  "Logical Attribute Name": "min-w-[200px] max-w-[240px]",
  "Attribute Description": "min-w-[300px] max-w-[400px]",
};

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
        <h3 className="text-base font-bold text-brand-darkblue mb-1">Reject Metadata</h3>
        <p className="text-xs text-gray-500 mb-4">Provide feedback so the metadata can be re-extracted.</p>
        <div className="flex flex-col gap-1 mb-5">
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Feedback <span className="text-red-500">*</span>
          </label>
          <textarea
            rows={4}
            value={feedback}
            onChange={(e) => { setFeedback(e.target.value); setTouched(true); }}
            placeholder="e.g. use this name file_layout_Gainwell as Physical File Name"
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

export default function ExtractMetadata({ onGenerateDriver, onNext }: { readonly onGenerateDriver?: () => void; readonly onNext?: () => void }) {
  const dispatch = useDispatch<AppDispatch>();
  const { metadataData, metadataLoading, metadataReviewLoading, metadataReviewStatus, metadataError, brdGcsUri, layoutGcsUri, judgeMetadataLoading, judgeMetadataData, judgeMetadataError } = useSelector(
    (state: RootState) => state.extract
  );

  const [editMode, setEditMode] = useState(false);
  const [showRejectModal, setShowRejectModal] = useState(false);
  const [metaReactivated, setMetaReactivated] = useState(false);
  const [generateDriverLoading, setGenerateDriverLoading] = useState(false);
  const [showKpiInfo, setShowKpiInfo] = useState(false);
  const [kpiExpanded, setKpiExpanded] = useState(false);
  const judgeMetadataDispatched = useRef(false);

  const { userId } = getCurrentSessionRuntime();

  // Auto-trigger on mount if no data has been loaded yet and not already loading
  useEffect(() => {
    if (!metadataData && !metadataLoading && !metadataError) {
      dispatch(
        runExtractMetadata({
          user_id: userId ?? getUserIdentity().userId,
          session_id: sessionStorage.getItem("session_id") ?? "",
          brd_gcs_uri: brdGcsUri ?? "",
          layout_gcs_uri: layoutGcsUri ?? "",
        })
      );
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-call judge-metadata once metadata extraction completes
  useEffect(() => {
    if (!metadataData || judgeMetadataData || judgeMetadataLoading || judgeMetadataDispatched.current) return;
    judgeMetadataDispatched.current = true;
    const sessionId = sessionStorage.getItem("session_id") ?? "";
    dispatch(runJudgeMetadata({
      userId: userId ?? getUserIdentity().userId,
      sessionId: sessionId,
      brd_uri: brdGcsUri ?? "",
      layout_uri: layoutGcsUri ?? "",
      extracted_metadata: {
        extracted_filespecs: metadataData.extracted_filespecs ?? {},
        extracted_file1: metadataData.extracted_file1 ?? {},
      },
      revision_number: 0,
    }));
  }, [metadataData]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleRetry = () => {
    judgeMetadataDispatched.current = false;
    dispatch(resetMetadata());
    dispatch(
      runExtractMetadata({
        user_id: userId ?? getUserIdentity().userId,
        session_id: sessionStorage.getItem("session_id") ?? "",
        brd_gcs_uri: brdGcsUri ?? "",
        layout_gcs_uri: layoutGcsUri ?? "",
      })
    );
  };

  const extracted_filespecs = metadataData?.extracted_filespecs;
  const extracted_file1 = metadataData?.extracted_file1;
  const session_id = metadataData?.session_id ?? "";

  const isApproved = metadataReviewStatus === "approved" && !metaReactivated;
  const showReactivatedNote = metadataReviewStatus === "approved" && metaReactivated;

  const handleApprove = () => {
    setMetaReactivated(false);
    dispatch(reviewMetadata());
  };

  const handleUpdateAndApprove = () => {
    const updated_metadata: Record<string, any> = {
      extracted_filespecs: extracted_filespecs ?? {},
      extracted_file1: extracted_file1 ?? {},
    };
    dispatch(manualUpdateMetadata({ user_id: userId ?? getUserIdentity().userId, session_id, updated_metadata, bq_reference: metadataData?.bq_reference ?? {} }));
    setEditMode(false);
  };

  const handleRejectConfirm = (_feedback: string) => {
    setShowRejectModal(false);
    dispatch(reviewMetadata());
  };

  const exportExcelFile = () => {
    if (!metadataData) return;
    const wb = XLSX.utils.book_new();

    const instructionSheet = XLSX.utils.aoa_to_sheet(instructionsData.map((item) => [item.Guideline]));
    instructionSheet["!merges"] = [{ s: { r: 0, c: 0 }, e: { r: 0, c: 13 } }];
    instructionSheet["!cols"] = [{ wch: 120 }];
    XLSX.utils.book_append_sheet(wb, instructionSheet, "Instructions");

    XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(valuesData), "Values");

    if (extracted_filespecs) {
      const rows = Object.entries(extracted_filespecs).map(([Field, Value]) => ({ Field, Value: Value ?? "" }));
      const fileSpecsWs = XLSX.utils.json_to_sheet(rows);
      fileSpecsWs["!cols"] = [{ wch: 35 }, { wch: 60 }];
      XLSX.utils.book_append_sheet(wb, fileSpecsWs, "FileSpecs");
    }

    if (extracted_file1) {
      const { entity_type, file_type, entity_physical_name, entity_business_name, entity_description, attributes } = extracted_file1;
      const metaRows = [
        ["Entity Type", entity_type ?? ""],
        ["File Type", file_type ?? ""],
        ["Entity Physical Name", entity_physical_name ?? ""],
        ["Entity Business Name", entity_business_name ?? ""],
        ["Entity Description", entity_description ?? ""],
        [],
        ATTRIBUTE_COLUMNS as string[],
        ...(attributes ?? []).map((attr) => ATTRIBUTE_COLUMNS.map((col) => attr[col] ?? "")),
      ];
      const metaWs = XLSX.utils.aoa_to_sheet(metaRows);
      metaWs["!cols"] = [
        { wch: 25 }, { wch: 30 }, { wch: 45 },
        { wch: 15 }, { wch: 10 }, { wch: 10 }, { wch: 15 },
        { wch: 12 }, { wch: 18 }, { wch: 12 }, { wch: 12 }, { wch: 18 },
      ];
      XLSX.utils.book_append_sheet(wb, metaWs, "Metadata");
    }

    XLSX.writeFile(wb, "Extracted_Metadata.xlsx");
  };

  return (
    <div className="mt-4 space-y-6">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-bold text-brand-darkblue">Extract Metadata</h3>
        <div className="flex items-center gap-3">
          {metadataData && !metadataLoading && (
            <button
              type="button"
              onClick={exportExcelFile}
              className="flex items-center gap-1 text-xs text-font-blue hover:text-font-blue font-medium cursor-pointer"
            >
              <Download size={14} />
              Export as Excel
            </button>
          )}
          {session_id && <span className="text-xs text-gray-400">Session: {session_id}</span>}
        </div>
      </div>

      {metadataLoading && (
        <div className="flex items-center justify-center py-10">
          <svg className="animate-spin h-6 w-6 text-font-blue mr-3" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
          </svg>
          <span className="text-sm text-gray-500">Extracting metadata…</span>
        </div>
      )}

      {metadataError && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3">
          <p className="text-red-600 text-xs">{metadataError}</p>
        </div>
      )}

      {/* ── File Specs ── */}
      {!metadataLoading && extracted_filespecs && <section>
        <h4 className="text-sm font-semibold text-gray-700 mb-2">File Specifications</h4>
        <div className="border border-gray-200 rounded-lg overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                <th className="px-4 py-2 text-left font-semibold text-gray-700 w-1/3">Field</th>
                <th className="px-4 py-2 text-left font-semibold text-gray-700">Value</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(extracted_filespecs).map(([key, value], idx) => (
                <tr key={key} className={idx % 2 === 0 ? "bg-white" : "bg-gray-50/50"}>
                  <td className="px-4 py-2 font-medium text-gray-700 border-b border-gray-100">{key}</td>
                  <td className="px-4 py-2 border-b border-gray-100">
                    {editMode ? (
                      <input
                        type="text"
                        value={value ?? ""}
                        onChange={(e) => dispatch(updateMetadataField({ key, value: e.target.value || null }))}
                        className="w-full px-2 py-1 border border-gray-300 rounded text-xs focus:outline-none focus:ring-1 focus:ring-brand-primary"
                      />
                    ) : (
                      <span className={value ? "text-gray-800" : "text-gray-300 italic"}>{value ?? "—"}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>}

      {/* ── File Entity ── */}
      {!metadataLoading && extracted_file1 && <section>
        <h4 className="text-sm font-semibold text-gray-700 mb-2">File Entity</h4>
        <div className="border border-gray-200 rounded-lg p-4 bg-white text-xs space-y-2 mb-3">
          <div className="grid grid-cols-2 gap-x-6 gap-y-2">
            {(["entity_type", "file_type", "entity_physical_name", "entity_business_name"] as const).map((field) => (
              <div key={field} className="flex gap-2">
                <span className="font-bold text-gray-600 capitalize">{field.replace(/_/g, " ")}:</span>
                <span className="text-gray-800">{extracted_file1[field]}</span>
              </div>
            ))}
          </div>
          <div className="flex gap-2 mt-1">
            <span className="font-bold text-gray-600">Description:</span>
            <span className="text-gray-800">{extracted_file1.entity_description}</span>
          </div>
        </div>

        {/* Attributes table */}
        <div className="border border-gray-200 rounded-lg overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                {ATTRIBUTE_COLUMNS.map((col) => (
                  <th key={col} className="px-3 py-2 text-left font-semibold text-gray-700">{col}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(extracted_file1.attributes ?? []).map((attr, idx) => (
                <tr key={idx} className={idx % 2 === 0 ? "bg-white" : "bg-gray-50/50"}>
                  {ATTRIBUTE_COLUMNS.map((col) => (
                    <td key={col} className={`px-3 py-2 border-b border-gray-100 whitespace-normal break-words ${ATTRIBUTE_COL_WIDTH[col] ?? "min-w-[80px] max-w-[140px]"}`}>
                      {editMode ? (
                        col === "Attribute Description" ? (
                          <textarea
                            rows={2}
                            value={attr[col] ?? ""}
                            onChange={(e) => dispatch(updateFileAttribute({ index: idx, field: col, value: e.target.value || null }))}
                            className="w-full min-w-[200px] px-2 py-1 border border-gray-300 rounded text-xs resize-none focus:outline-none focus:ring-1 focus:ring-brand-primary"
                          />
                        ) : (
                          <input
                            type="text"
                            value={attr[col] ?? ""}
                            onChange={(e) => dispatch(updateFileAttribute({ index: idx, field: col, value: e.target.value || null }))}
                            className={`px-2 py-1 border border-gray-300 rounded text-xs focus:outline-none focus:ring-1 focus:ring-brand-primary ${col === "Attribute Name" || col === "Logical Attribute Name"
                                ? "w-full min-w-[180px]"
                                : "w-full min-w-[80px]"
                              }`}
                          />
                        )
                      ) : (
                        <span className={attr[col] ? "text-gray-800" : "text-gray-300 italic"}>{attr[col] ?? "—"}</span>
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>}

      {/* Judge Metadata KPI */}
      {(judgeMetadataLoading || judgeMetadataData || judgeMetadataError) && (
        <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setKpiExpanded(!kpiExpanded)}
                className="flex items-center gap-1 cursor-pointer group"
              >
                <h3 className="text-sm font-semibold text-brand-darkblue">Metadata Quality Evaluation</h3>
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
              disabled={judgeMetadataLoading}
              onClick={() => {
                judgeMetadataDispatched.current = false;
                const sessionId = sessionStorage.getItem("session_id") ?? "";
                if (metadataData) {
                  dispatch(runJudgeMetadata({
                    userId: userId ?? getUserIdentity().userId,
                    sessionId: sessionId,
                    brd_uri: brdGcsUri ?? "",
                    layout_uri: layoutGcsUri ?? "",
                    extracted_metadata: {
                      extracted_filespecs: metadataData.extracted_filespecs ?? {},
                      extracted_file1: metadataData.extracted_file1 ?? {},
                    },
                    revision_number: 0,
                  }));
                }
              }}
              className="text-gray-400 hover:text-brand-darkblue disabled:opacity-40 disabled:cursor-not-allowed transition-colors cursor-pointer"
              title="Retry metadata evaluation"
            >
              <RefreshCw size={14} className={judgeMetadataLoading ? "animate-spin" : ""} />
            </button>
          </div>

          {kpiExpanded && (
            <>
              {showKpiInfo && (
                <div className="bg-brand-surface border border-teal-200 rounded-lg p-3 text-xs space-y-2">
                  <div className="font-semibold text-brand-darkblue mb-2">KPI Definitions</div>
                  {judgeMetadataData ? Object.entries(judgeMetadataData.kpis).map(([name, kpi]) => (
                    kpi.definition ? (
                      <div key={name} className="text-gray-700">
                        {kpi.definition}
                      </div>
                    ) : null
                  )) : <p className="text-gray-400 italic">Run evaluation to see KPI definitions.</p>}
                </div>
              )}

              {judgeMetadataLoading && (
                <div className="flex items-center gap-2 text-gray-400 text-xs">
                  <div className="w-4 h-4 border-2 border-brand-darkblue border-t-transparent rounded-full animate-spin" />
                  Running metadata evaluation…
                </div>
              )}
              {judgeMetadataError && <p className="text-xs text-red-600">{judgeMetadataError}</p>}
              {judgeMetadataData && (
                <div className="grid grid-cols-2 gap-2">
                  {Object.entries(judgeMetadataData.kpis).map(([name, kpi]) => {
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
      <div className="flex items-center gap-3 pt-4 border-t border-gray-200">
        {!editMode && (
          <>
            <button
              type="button"
              disabled={generateDriverLoading}
              onClick={() => { setEditMode(true); setMetaReactivated(true); }}
              className="px-4 py-2 text-sm border border-brand-darkblue text-brand-darkblue rounded-lg hover:bg-brand-surface disabled:opacity-50 disabled:cursor-not-allowed transition-colors cursor-pointer"
            >
              Edit
            </button>
            {showReactivatedNote && (
              <p className="text-xs text-amber-600 italic">Note: Re-approve to apply changes.</p>
            )}
            <button
              type="button"
              disabled={metadataReviewLoading || isApproved || generateDriverLoading}
              onClick={handleApprove}
              className="px-4 py-2 text-sm bg-brand-primary text-white rounded-lg hover:bg-brand-primary-hover disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors cursor-pointer"
            >
              {metadataReviewLoading ? "Approving…" : isApproved ? "Approved ✓" : "Approve"}
            </button>
            <button
              type="button"
              disabled={metadataReviewLoading || generateDriverLoading}
              onClick={() => { setMetaReactivated(true); setShowRejectModal(true); }}
              className="px-4 py-2 text-sm border border-red-500 text-red-600 rounded-lg hover:bg-red-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors cursor-pointer"
            >
              Reject
            </button>
            <button
              type="button"
              disabled={metadataLoading || generateDriverLoading}
              onClick={handleRetry}
              className="px-4 py-2 text-sm border border-gray-400 text-gray-600 rounded-lg hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors cursor-pointer"
            >
              {metadataLoading ? "Retrying…" : "Retry"}
            </button>
          </>
        )}

        {editMode && (
          <>
            <button
              type="button"
              disabled={metadataReviewLoading}
              onClick={handleUpdateAndApprove}
              className="px-4 py-2 text-sm bg-brand-primary text-white rounded-lg hover:bg-brand-primary-hover disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors cursor-pointer"
            >
              {metadataReviewLoading ? "Updating…" : "Update & Approve"}
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

      <div className="flex justify-end pt-2 gap-2 items-center">
        {!isApproved && (
          <p className="text-xs text-gray-400 italic">Note: Approve metadata to enable Generate Driver Mapping.</p>
        )}
        <button
          type="button"
          onClick={() => {
            setGenerateDriverLoading(true);
            const { appName, sessionId, userId } = getCurrentSessionRuntime();
            dispatch(runDriverMapping({
              appName: `projects/677861082546/locations/us-central1/reasoningEngines/${appName ?? ""}`,
              sessionId: sessionId ?? "",
              userId: userId ?? getUserIdentity().userId,
              brd_uri: brdGcsUri ?? "",
              brd: {},
            })).unwrap().then(() => {
              onGenerateDriver?.();
              onNext?.();
            }).catch(() => {}).finally(() => setGenerateDriverLoading(false));
          }}
          disabled={!isApproved || generateDriverLoading}
          className="text-sm bg-brand-darkblue text-white px-4 py-2 rounded-lg hover:bg-brand-darkblue/80 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors cursor-pointer"
        >
          {generateDriverLoading ? "Generating…" : "Generate Driver Mapping"}
        </button>
      </div>

      {showRejectModal && (
        <RejectModal
          loading={metadataReviewLoading}
          onConfirm={handleRejectConfirm}
          onCancel={() => setShowRejectModal(false)}
        />
      )}

    </div>
  );
}
