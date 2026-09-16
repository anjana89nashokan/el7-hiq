import { useEffect, useRef, useState } from "react";
import { useDispatch, useSelector } from "react-redux";
import { Info, RefreshCw } from "lucide-react";
import {
  runDriverLogic,
  runDriverValidate,
  runDriverMapping,
  runDriverApprove,
  runDriverCheckpoint,
  runDriverSave,
  runJudgeDriver,
  resetDriverState,
} from "../../state/reducers/extract/extractReducer";
import type { AppDispatch, RootState } from "../../state/store";
import { getCurrentSessionRuntime } from "../../utils/appSessionStorage";
import type { FilterCandidate, DriverLogicFilter, BsaQuestion } from "../../end-points/extract/extractApi";
import LoadingSpinner from "../../components/LoadingSpinner";
import { getUserIdentity } from "../../utils/userIdentity";


// ── Shared helpers ────────────────────────────────────────────────────────────

function SectionHeader({ title }: { readonly title: string }) {
  return <h3 className="text-sm font-semibold text-brand-darkblue mb-2">{title}</h3>;
}

function StepLoader({ message }: { readonly message: string }) {
  return <LoadingSpinner message={message} size="md" />;
}

function StepError({ message }: { readonly message: string }) {
  return <p className="text-sm text-red-600">{message}</p>;
}

// ── Driver Mapping panel ──────────────────────────────────────────────────────

function DriverMappingPanel({ data }: { readonly data: { filter_candidates: FilterCandidate[]; unmapped_concepts: string[]; ibc_aha_context: string } }) {
  const filterCandidates = data?.filter_candidates ?? [];
  const unmappedConcepts = data?.unmapped_concepts ?? [];
  return (
    <div className="space-y-4">
      {filterCandidates.length > 0 && (
        <div>
          <SectionHeader title="Filter Candidates" />
          <div className="overflow-x-auto">
            <table className="w-full text-xs border border-gray-200 rounded-lg">
              <thead className="bg-brand-darkblue text-white">
                <tr>
                  {["BRD Concept", "BRD Source", "Category", "Reference Field", "Reference Table", "Reference Layer", "Filter Type", "Suggested Values", "Confidence", "Scope"].map((h) => (
                    <th key={h} className="px-3 py-2 text-left whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filterCandidates.map((fc: FilterCandidate, i: number) => (
                  <tr key={i} className={i % 2 === 0 ? "bg-white" : "bg-gray-50"}>
                    <td className="px-3 py-2">{fc.brd_concept}</td>
                    <td className="px-3 py-2">{fc.brd_source}</td>
                    <td className="px-3 py-2">{fc.filter_category}</td>
                    <td className="px-3 py-2 font-mono">{fc.dart_field}</td>
                    <td className="px-3 py-2 font-mono">{fc.dart_table}</td>
                    <td className="px-3 py-2 font-mono">{fc.dart_layer}</td>
                    <td className="px-3 py-2">{fc.filter_type}</td>
                    <td className="px-3 py-2">{fc.suggested_values.join(", ")}</td>
                    <td className="px-3 py-2">{(fc.confidence * 100).toFixed(0)}%</td>
                    <td className="px-3 py-2">{fc.filter_scope}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {unmappedConcepts.length > 0 && (
        <div>
          <SectionHeader title="Unmapped Concepts" />
          <ul className="list-disc list-inside text-xs text-gray-700 space-y-0.5">
            {unmappedConcepts.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

// ── Driver Logic panel ────────────────────────────────────────────────────────

function FilterRow({ filter }: { readonly filter: DriverLogicFilter }) {
  return (
    <tr className="bg-white hover:bg-gray-50">
      <td className="px-3 py-2 font-mono">{filter.filter_id}</td>
      <td className="px-3 py-2">{filter.filter_category}</td>
      <td className="px-3 py-2">{filter.filter_scope}</td>
      <td className="px-3 py-2">{filter.file_name}</td>
      <td className="px-3 py-2 font-mono">{filter.dart_field}</td>
      <td className="px-3 py-2 font-mono">{filter.dart_table}</td>
      <td className="px-3 py-2 font-mono">{filter.dart_layer}</td>
      <td className="px-3 py-2">{filter.filter_type}</td>
      <td className="px-3 py-2">{(filter.filter_values ?? []).join(", ")}</td>
      <td className="px-3 py-2 font-mono max-w-[200px]" title={filter.sql_clause ?? ""}>{filter.sql_clause ?? "—"}</td>
      <td className="px-3 py-2">{(filter.confidence * 100).toFixed(0)}%</td>
      <td className="px-3 py-2">{filter.open_item ? <span className="text-amber-600 font-medium">Yes</span> : "No"}</td>
      <td className="px-3 py-2 font-mono max-w-[200px]" title={filter.notes}>
        <span className="block truncate max-w-[200px]">{filter.notes}</span>
      </td>
    </tr>
  );
}

function DriverLogicPanel({ data, bsaQuestions }: {
  readonly data: { common_filters: DriverLogicFilter[]; sql_where_clause: string; global_filter_count?: number; file_level_filter_count?: number; open_item_count?: number; ibc_aha_context?: string };
  readonly bsaQuestions: BsaQuestion[];
}) {
  const commonFilters = data?.common_filters ?? [];
  return (
    <div className="space-y-4">
      {commonFilters.length > 0 && (
        <div>
          <SectionHeader title="Common Filters" />
          <div className="overflow-x-auto">
            <table className="w-full text-xs border border-gray-200 rounded-lg">
              <thead className="bg-brand-darkblue text-white">
                <tr>
                  {["ID", "Category", "Scope", "File Name", "Reference Field", "Reference Table", "Dart Layer", "Filter Type", "Values", "SQL Clause", "Confidence", "Open Item", "Notes"].map((h) => (
                    <th key={h} className="px-3 py-2 text-left whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {commonFilters.map((f: DriverLogicFilter) => (
                  <FilterRow key={f.filter_id} filter={f} />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <div>
        <SectionHeader title="SQL WHERE Clause" />
        <pre className="bg-gray-50 border border-gray-200 rounded-lg p-3 text-xs font-mono whitespace-pre-wrap break-words text-gray-800">
          {data.sql_where_clause || "—"}
        </pre>
      </div>
      {bsaQuestions.length > 0 && (
        <div>
          <SectionHeader title="STTM Questions" />
          <div className="space-y-2">
            {bsaQuestions.map((q, i) => (
              <div key={i} className="border border-amber-200 bg-amber-50 rounded-lg p-3">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-[10px] font-semibold font-mono text-amber-700 bg-amber-100 border border-amber-300 px-2 py-0.5 rounded">{q.filter_id}</span>
                  <span className="text-xs font-mono text-gray-500">{q.dart_field}</span>
                </div>
                <p className="text-xs text-gray-700">{q.bsa_question}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Driver Validation panel ───────────────────────────────────────────────────

type ValidationIssue = {
  issue_type: string;
  severity: string;
  filter_id: string;
  description: string;
  recommended_action: string;
};

type DriverValidationData = {
  status?: string;
  req_id?: string;
  elapsed_sec?: number;
  events?: number;
  summary?: {
    can_proceed: boolean;
    total_high: number;
    total_medium: number;
    standards_compliant: boolean;
    no_transformation_logic: boolean;
    all_brd_requirements_traced: boolean;
  };
  issues?: ValidationIssue[];
  driver_validation?: {
    issues: ValidationIssue[];
    total_high: number;
    total_medium: number;
    all_brd_requirements_traced: boolean;
    no_transformation_logic: boolean;
    standards_compliant: boolean;
    can_proceed: boolean;
  };
};

function SeverityBadge({ severity }: { readonly severity: string }) {
  const cls = severity === "high"
    ? "bg-red-100 text-red-700 border border-red-300"
    : severity === "medium"
      ? "bg-amber-100 text-amber-700 border border-amber-300"
      : "bg-gray-100 text-gray-600 border border-gray-300";
  return <span className={`px-2 py-0.5 rounded text-[10px] font-semibold uppercase ${cls}`}>{severity}</span>;
}

function StatusPill({ value, trueLabel = "Yes", falseLabel = "No" }: { readonly value: boolean; readonly trueLabel?: string; readonly falseLabel?: string }) {
  return value
    ? <span className="text-green-600 font-semibold">{trueLabel}</span>
    : <span className="text-red-600 font-semibold">{falseLabel}</span>;
}

function DriverValidationPanel({ data }: { readonly data: DriverValidationData }) {
  const summary = data.summary ?? data.driver_validation;
  const issues = data.issues ?? data.driver_validation?.issues ?? [];

  return (
    <div className="space-y-4">
      {summary && (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          {([
            ["Can Proceed", <StatusPill value={summary.can_proceed} />],
            ["Standards Compliant", <StatusPill value={summary.standards_compliant} />],
            ["No Transform Logic", <StatusPill value={summary.no_transformation_logic} />],
            ["BRD Requirements Traced", <StatusPill value={summary.all_brd_requirements_traced} />],
            ["High Issues", <span className={`font-semibold ${summary.total_high > 0 ? "text-red-600" : "text-green-600"}`}>{summary.total_high}</span>],
            ["Medium Issues", <span className={`font-semibold ${summary.total_medium > 0 ? "text-amber-600" : "text-green-600"}`}>{summary.total_medium}</span>],
          ] as [string, React.ReactNode][]).map(([label, val]) => (
            <div key={label} className="bg-gray-50 border border-gray-200 rounded-lg px-3 py-2">
              <p className="text-[10px] text-gray-500 mb-0.5">{label}</p>
              <div className="text-xs">{val}</div>
            </div>
          ))}
        </div>
      )}

      <div>
        <SectionHeader title="Issues" />
        {issues.length === 0 ? (
          <p className="text-xs text-green-600 font-medium">No validation issues found.</p>
        ) : (
          <div className="space-y-2">
            {issues.map((issue, i) => (
              <div key={i} className="border border-gray-200 rounded-lg p-3 bg-white">
                <div className="flex items-center gap-2 mb-1">
                  <SeverityBadge severity={issue.severity} />
                  <span className="text-xs font-mono text-gray-500">{issue.filter_id}</span>
                  <span className="text-xs text-gray-600 font-medium">{issue.issue_type.replace(/_/g, " ")}</span>
                </div>
                <p className="text-xs text-gray-700 mb-1">{issue.description}</p>
                <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1">
                  <span className="font-semibold">Action: </span>{issue.recommended_action}
                </p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Checkpoint (Reject) Modal ─────────────────────────────────────────────────

function CheckpointModal({
  loading, onConfirm, onCancel,
}: {
  readonly loading: boolean;
  readonly onConfirm: (instruction: string) => void;
  readonly onCancel: () => void;
}) {
  const [instruction, setInstruction] = useState("");
  const [touched, setTouched] = useState(false);
  const hasError = touched && !instruction.trim();

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md mx-4 p-6">
        <h3 className="text-base font-bold text-brand-darkblue mb-1">Reject</h3>
        <p className="text-xs text-gray-500 mb-4">Provide instructions so the driver logic can be revised.</p>
        <div className="flex flex-col gap-1 mb-5">
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Instruction <span className="text-red-500">*</span>
          </label>
          <textarea
            rows={4}
            value={instruction}
            onChange={(e) => { setInstruction(e.target.value); setTouched(true); }}
            placeholder="e.g. Remove the Active enrollment filter — it is not needed for this extract."
            className={`w-full px-3 py-2 border rounded-md bg-white text-xs resize-none focus:outline-none focus:ring-2 ${hasError ? "border-red-400 focus:ring-red-400" : "border-gray-300 focus:ring-brand-primary"
              }`}
          />
          {hasError && <p className="text-xs text-red-600 mt-1">Instruction is required.</p>}
        </div>
        <div className="flex justify-end gap-3">
          <button type="button" disabled={loading} onClick={onCancel}
            className="px-4 py-2 text-sm border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50 transition-colors cursor-pointer">
            Cancel
          </button>
          <button type="button" disabled={loading}
            onClick={() => { setTouched(true); if (instruction.trim()) onConfirm(instruction.trim()); }}
            className="px-4 py-2 text-sm bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors cursor-pointer">
            {loading ? "Submitting…" : "Submit"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Edit Driver Logic (inline) ────────────────────────────────────────────────

function EditDriverLogicView({
  data, loading, error, onUpdate, onCancel,
}: {
  readonly data: { common_filters: DriverLogicFilter[]; sql_where_clause: string };
  readonly loading: boolean;
  readonly error: string | null;
  readonly onUpdate: (updated: { common_filters: DriverLogicFilter[]; sql_where_clause: string }) => void;
  readonly onCancel: () => void;
}) {
  const [filters, setFilters] = useState<DriverLogicFilter[]>((data.common_filters ?? []).map(f => ({ ...f, filter_values: [...(f.filter_values ?? [])] })));
  const [sqlClause, setSqlClause] = useState(data.sql_where_clause);

  const updateFilter = (idx: number, field: keyof DriverLogicFilter, value: string) => {
    setFilters(prev => prev.map((f, i) => i === idx ? { ...f, [field]: field === "filter_values" ? value.split(",").map(v => v.trim()) : value } as DriverLogicFilter : f));
  };

  return (
    <div className="space-y-4">
      <div>
        <SectionHeader title="Common Filters" />
        <div className="overflow-x-auto">
          <table className="w-full text-xs border border-gray-200 rounded-lg">
            <thead className="bg-brand-darkblue text-white">
              <tr>
                {["ID", "Category", "Scope", "Reference Field", "Reference Table", "Filter Type", "Values", "SQL Clause"].map(h => (
                  <th key={h} className="px-3 py-2 text-left whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filters.map((f, idx) => (
                <tr key={f.filter_id} className={idx % 2 === 0 ? "bg-white" : "bg-gray-50"}>
                  <td className="px-3 py-2 font-mono">{f.filter_id}</td>
                  {(["filter_category", "filter_scope", "dart_field", "dart_table", "filter_type"] as (keyof DriverLogicFilter)[]).map(field => (
                    <td key={field} className="px-2 py-1">
                      <input
                        className="w-full min-w-[140px] border border-gray-300 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-brand-primary"
                        value={(f[field] as string) ?? ""}
                        onChange={e => updateFilter(idx, field, e.target.value)}
                      />
                    </td>
                  ))}
                  <td className="px-2 py-1">
                    <input
                      className="w-full min-w-[160px] border border-gray-300 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-brand-primary"
                      value={(f.filter_values ?? []).join(", ")}
                      onChange={e => updateFilter(idx, "filter_values", e.target.value)}
                    />
                  </td>
                  <td className="px-2 py-1">
                    <input
                      className="w-full min-w-[200px] border border-gray-300 rounded px-2 py-1 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-brand-primary"
                      value={f.sql_clause ?? ""}
                      onChange={e => updateFilter(idx, "sql_clause", e.target.value)}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <SectionHeader title="SQL WHERE Clause" />
        <textarea
          rows={4}
          value={sqlClause}
          onChange={e => setSqlClause(e.target.value)}
          className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs font-mono resize-y focus:outline-none focus:ring-2 focus:ring-brand-primary"
        />
      </div>

      {error && <p className="text-xs text-red-600">{error}</p>}

      <div className="flex gap-3">
        <button type="button" disabled={loading} onClick={onCancel}
          className="px-4 py-2 text-sm border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50 transition-colors cursor-pointer">
          Cancel
        </button>
        <button type="button" disabled={loading}
          onClick={() => onUpdate({ common_filters: filters, sql_where_clause: sqlClause })}
          className="px-4 py-2 text-sm bg-brand-darkblue text-white rounded-lg hover:bg-brand-darkblue/80 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors cursor-pointer">
          {loading ? "Updating…" : "Update"}
        </button>
      </div>
    </div>
  );
}

// ── Step wrapper ──────────────────────────────────────────────────────────────

function StepCard({ step, title, children }: { readonly step: number; readonly title: string; readonly children: React.ReactNode }) {
  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-3 bg-brand-darkblue/5 border-b border-gray-200">
        <span className="w-6 h-6 rounded-full bg-brand-darkblue text-white text-xs flex items-center justify-center font-semibold shrink-0">{step}</span>
        <h3 className="text-sm font-semibold text-brand-darkblue">{title}</h3>
      </div>
      <div className="p-4 bg-white">{children}</div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function GenerateDriverMapping({ onPrevious, onNext, onExtractMapping }: { readonly onPrevious?: () => void; readonly onNext?: () => void; readonly onExtractMapping?: () => void }) {
  const dispatch = useDispatch<AppDispatch>();
  const {
    brdGcsUri,
    driverMappingLoading, driverMappingData, driverMappingError,
    driverLogicLoading, driverLogicData, driverLogicError, driverLogicBsaQuestions,
    driverValidateLoading, driverValidateData, driverValidateError,
    driverApproveLoading, driverApproveError, driverReviewStatus,
    driverCheckpointLoading, driverCheckpointError,
    mappingLoading,
    driverSaveLoading, driverSaveError,
    judgeDriverLoading, judgeDriverData, judgeDriverError,
  } = useSelector((state: RootState) => state.extract);

  const [showCheckpointModal, setShowCheckpointModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [driverReactivated, setDriverReactivated] = useState(false);
  const [showKpiInfo, setShowKpiInfo] = useState(false);
  const [kpiExpanded, setKpiExpanded] = useState(false);
  const driverLogicDispatched = useRef(false);
  const judgeDriverDispatched = useRef(false);

  const buildPayload = () => {
    const { appName, sessionId, userId } = getCurrentSessionRuntime();
    return {
      appName: `projects/677861082546/locations/us-central1/reasoningEngines/${appName ?? ""}`,
      sessionId: sessionId ?? "",
      userId: userId ?? getUserIdentity().userId,
      brd_uri: brdGcsUri ?? "",
      brd: {},
    };
  };

  const buildApprovePayload = () => {
    const { appName, sessionId, userId } = getCurrentSessionRuntime();
    return {
      appName: `projects/677861082546/locations/us-central1/reasoningEngines/${appName ?? ""}`,
      sessionId: sessionId ?? "",
      userId: userId ?? getUserIdentity().userId,
    };
  };

  // Auto-call driver logic once driver mapping arrives
  useEffect(() => {
    if (!driverMappingData || driverLogicData || driverLogicLoading || driverLogicDispatched.current) return;
    driverLogicDispatched.current = true;
    dispatch(runDriverLogic(buildPayload()));
  }, [driverMappingData]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-call driver validate once driver logic arrives
  useEffect(() => {
    if (!driverLogicData || driverValidateData || driverValidateLoading) return;
    dispatch(runDriverValidate(buildPayload()));
  }, [driverLogicData]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-call judge-driver once all three responses are available
  useEffect(() => {
    if (!driverMappingData || !driverLogicData || !driverValidateData || judgeDriverData || judgeDriverLoading || judgeDriverDispatched.current) return;
    judgeDriverDispatched.current = true;
    const { sessionId, userId } = getCurrentSessionRuntime();
    dispatch(runJudgeDriver({
      userId: userId ?? getUserIdentity().userId,
      sessionId: sessionId ?? "",
      brd_uri: brdGcsUri ?? "",
      driver_mapping: driverMappingData,
      driver_logic: driverLogicData,
      driver_validation: driverValidateData,
      revision_number: 0,
    }));
  }, [driverMappingData, driverLogicData, driverValidateData]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleRetry = () => {
    const p = buildPayload();
    setDriverReactivated(false);
    driverLogicDispatched.current = false;
    judgeDriverDispatched.current = false;
    dispatch(resetDriverState());
    dispatch(runDriverMapping(p)).catch(() => { });
  };

  const handleApprove = () => {
    dispatch(runDriverApprove({ ...buildApprovePayload(), bsa_notes: "" }));
  };

  const handleCheckpointConfirm = (instruction: string) => {
    const { appName, sessionId, userId } = getCurrentSessionRuntime();
    setShowCheckpointModal(false);
    dispatch(runDriverCheckpoint({
      appName: `projects/677861082546/locations/us-central1/reasoningEngines/${appName ?? ""}`,
      sessionId: sessionId ?? "",
      userId: userId ?? getUserIdentity().userId,
      brd_uri: brdGcsUri ?? "",
      instruction,
    }));
  };

  const handleExtractMapping = () => {
    onExtractMapping?.();
    onNext?.();
  };

  const handleDriverSave = (updated: { common_filters: DriverLogicFilter[]; sql_where_clause: string }) => {
    if (!driverLogicData) return;
    const { appName, sessionId, userId } = getCurrentSessionRuntime();
    dispatch(runDriverSave({
      appName: `projects/677861082546/locations/us-central1/reasoningEngines/${appName ?? ""}`,
      sessionId: sessionId ?? "",
      userId: userId ?? getUserIdentity().userId,
      driver_logic: { ...driverLogicData, ...updated },
    })).unwrap().then(() => setShowEditModal(false)).catch(() => { });
  };

  const isDriverApproved = driverReviewStatus === "approved" && !driverReactivated;
  const showReactivatedNote = driverReviewStatus === "approved" && driverReactivated;

  return (
    <div className="space-y-4">
      <h2 className="text-base font-bold text-brand-darkblue">Generate Driver</h2>
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <div className="space-y-4">

          {/* Step 1 — Driver Mapping */}
          <StepCard step={1} title="Business Mapping">
            {driverMappingLoading && <StepLoader message="Generating Driver..." />}
            {!driverMappingLoading && driverMappingError && (
              <div className="flex items-center gap-3">
                <StepError message={driverMappingError} />
              </div>
            )}
            {!driverMappingLoading && driverMappingData && <DriverMappingPanel data={driverMappingData} />}
          </StepCard>

          {/* Step 2 — Driver Logic (shown once mapping data is received) */}
          {driverMappingData && (
            <StepCard step={2} title="Driver Logic">
              {driverLogicLoading && <StepLoader message="Generating driver logic…" />}
              {!driverLogicLoading && driverLogicError && (
                <div className="flex items-center gap-3">
                  <StepError message={driverLogicError} />
                </div>
              )}
              {!driverLogicLoading && driverLogicData && (
                showEditModal
                  ? <EditDriverLogicView
                    data={driverLogicData}
                    loading={driverSaveLoading}
                    error={driverSaveError}
                    onUpdate={handleDriverSave}
                    onCancel={() => setShowEditModal(false)}
                  />
                  : <DriverLogicPanel data={driverLogicData} bsaQuestions={driverLogicBsaQuestions ?? []} />
              )}
            </StepCard>
          )}

          {/* Step 3 — Driver Validation (shown once logic data is received) */}
          {driverLogicData && (
            <StepCard step={3} title="Driver Validation">
              {driverValidateLoading && <StepLoader message="Validating driver…" />}
              {!driverValidateLoading && driverValidateError && (
                <div className="flex items-center gap-3">
                  <StepError message={driverValidateError} />
                </div>
              )}
              {!driverValidateLoading && driverValidateData && (
                <DriverValidationPanel data={driverValidateData as DriverValidationData} />
              )}
            </StepCard>
          )}

          {/* Judge Driver KPI */}
          {(judgeDriverLoading || judgeDriverData || judgeDriverError) && (
            <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setKpiExpanded(!kpiExpanded)}
                    className="flex items-center gap-1 cursor-pointer group"
                  >
                    <h3 className="text-sm font-semibold text-brand-darkblue">Driver Quality Evaluation</h3>
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
                  disabled={judgeDriverLoading}
                  onClick={() => {
                    judgeDriverDispatched.current = false;
                    const { sessionId, userId } = getCurrentSessionRuntime();
                    if (driverMappingData && driverLogicData && driverValidateData) {
                      dispatch(runJudgeDriver({
                        userId: userId ?? getUserIdentity().userId,
                        sessionId: sessionId ?? "",
                        brd_uri: brdGcsUri ?? "",
                        driver_mapping: driverMappingData,
                        driver_logic: driverLogicData,
                        driver_validation: driverValidateData,
                        revision_number: 0,
                      }));
                    }
                  }}
                  className="text-gray-400 hover:text-brand-darkblue disabled:opacity-40 disabled:cursor-not-allowed transition-colors cursor-pointer"
                  title="Retry driver evaluation"
                >
                  <RefreshCw size={14} className={judgeDriverLoading ? "animate-spin" : ""} />
                </button>
              </div>

              {kpiExpanded && (
                <>
                  {showKpiInfo && (
                    <div className="bg-brand-surface border border-teal-200 rounded-lg p-3 text-xs space-y-2">
                      <div className="font-semibold text-brand-darkblue mb-2">KPI Definitions</div>
                      {judgeDriverData ? Object.entries(judgeDriverData.kpis).map(([name, kpi]) => (
                        kpi.definition ? (
                          <div key={name} className="text-gray-700">
                            {kpi.definition}
                          </div>
                        ) : null
                      )) : <p className="text-gray-400 italic">Run evaluation to see KPI definitions.</p>}
                    </div>
                  )}

                  {judgeDriverLoading && (
                    <div className="flex items-center gap-2 text-gray-400 text-xs">
                      <div className="w-4 h-4 border-2 border-brand-darkblue border-t-transparent rounded-full animate-spin" />
                      Running driver evaluation…
                    </div>
                  )}
                  {judgeDriverError && <p className="text-xs text-red-600">{judgeDriverError}</p>}
                  {judgeDriverData && (
                    <div className="grid grid-cols-2 gap-2">
                      {Object.entries(judgeDriverData.kpis).map(([name, kpi]) => {
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

        </div>

        <div className="flex items-center gap-3 mt-4 pt-4 border-t border-gray-200">
          {driverApproveError && <p className="text-xs text-red-600">{driverApproveError}</p>}
          {driverCheckpointError && <p className="text-xs text-red-600">{driverCheckpointError}</p>}
          {onPrevious && (
            <button type="button" onClick={onPrevious}
              className="text-sm border border-gray-300 text-gray-600 px-4 py-2 rounded-lg hover:bg-gray-50 transition-colors cursor-pointer">
              ← Previous
            </button>
          )}
          {driverValidateData && (
            <>
              {driverLogicData && (
                <button type="button" onClick={() => { setShowEditModal(true); setDriverReactivated(true); }}
                  disabled={mappingLoading}
                  className="text-sm border border-brand-darkblue text-brand-darkblue px-4 py-2 rounded-lg hover:bg-brand-surface disabled:opacity-50 disabled:cursor-not-allowed transition-colors cursor-pointer">
                  Edit
                </button>
              )}
              {showReactivatedNote && (
                <p className="text-xs text-amber-600 italic">Note: Re-approve to apply changes.</p>
              )}
              <button type="button" disabled={driverApproveLoading || isDriverApproved || mappingLoading} onClick={() => { setDriverReactivated(false); handleApprove(); }}
                className="text-sm bg-brand-darkblue text-white px-4 py-2 rounded-lg hover:bg-brand-darkblue/80 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors cursor-pointer">
                {driverApproveLoading ? "Approving…" : isDriverApproved ? "Approved ✓" : "Approve"}
              </button>
              <button type="button" disabled={driverCheckpointLoading || mappingLoading} onClick={() => { setDriverReactivated(true); setShowCheckpointModal(true); }}
                className="text-sm border border-red-500 text-red-600 px-4 py-2 rounded-lg hover:bg-red-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors cursor-pointer">
                {driverCheckpointLoading ? "Submitting…" : "Reject"}
              </button>
            </>
          )}
        </div>
      </div>

      <div className="flex justify-end pt-2 gap-2 items-center">
        {!isDriverApproved && (
          <p className="text-xs text-gray-400 italic">Note: Approve driver logic to enable Extract Mapping.</p>
        )}
        <button type="button" onClick={handleExtractMapping}
          disabled={!isDriverApproved || mappingLoading}
          className="text-sm bg-brand-darkblue text-white px-4 py-2 rounded-lg hover:bg-brand-darkblue/80 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors cursor-pointer">
          {mappingLoading ? "Extracting Mapping…" : "Extract Mapping"}
        </button>
        <button type="button" disabled={driverMappingLoading || driverLogicLoading || driverValidateLoading || mappingLoading} onClick={handleRetry}
          className="text-sm border border-gray-400 text-gray-600 px-4 py-2 rounded-lg hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors cursor-pointer">
          {driverMappingLoading || driverLogicLoading || driverValidateLoading || mappingLoading ? "Retrying…" : "Retry"}
        </button>
      </div>

      {showCheckpointModal && (
        <CheckpointModal
          loading={driverCheckpointLoading}
          onConfirm={handleCheckpointConfirm}
          onCancel={() => setShowCheckpointModal(false)}
        />
      )}
    </div>
  );
}
