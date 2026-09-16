import React, { useEffect, useMemo, useState } from "react";
import { CloudUpload, Loader2 } from "lucide-react";
import Toast from "../Toast";
import { getDefaultDatabase } from "../../end-points/databaseApi";
import { mappingService } from "../../end-points/mappingService";
import type { SubjectAreaStatus } from "../../end-points/mappingService";

interface ConfigurationFormProps {
  readonly onSubmit: (data: {
    interfaceCode: string;
    instructionsText: string;
    subjectAreas: string[];
    targetLayout: "UPLOAD_FILES" | "INDEMAP";
    indemapPairs: Array<{ databaseName: string; tableName: string }>;
    sourceFiles: FileList | null;
    targetFiles: FileList | null;
  }) => void;
  readonly onBuildSubjectArea: (data: {
    subjectArea: string;
    tablesAndColumnsFile: File;
    tablesAndIndexesFile: File;
  }) => Promise<void>;
  readonly isSubmitting: boolean;
  readonly isDrafting: boolean;
  readonly validationErrors: string[];
  readonly isBuildingSubjectArea: boolean;
  readonly subjectAreaStatuses: SubjectAreaStatus[];
  readonly isLoadingSubjectAreas: boolean;
}

export default function ConfigurationForm({
  onSubmit,
  onBuildSubjectArea,
  isSubmitting,
  isDrafting,
  validationErrors,
  isBuildingSubjectArea,
  subjectAreaStatuses,
  isLoadingSubjectAreas,
}: ConfigurationFormProps) {
  const [interfaceCode, setInterfaceCode] = useState("");
  const [instructionsText, setInstructionsText] = useState("");
  const [subjectAreas, setSubjectAreas] = useState<string[]>([]);
  const [targetLayout, setTargetLayout] = useState<"UPLOAD_FILES" | "INDEMAP">("UPLOAD_FILES");
  const [indemapGroups, setIndemapGroups] = useState<Array<{ databaseName: string; tableNamesText: string }>>([
    { databaseName: "", tableNamesText: "" },
  ]);
  const [sourceFiles, setSourceFiles] = useState<FileList | null>(null);
  const [targetFiles, setTargetFiles] = useState<FileList | null>(null);
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [toastVariant, setToastVariant] = useState<"error" | "success">("error");

  interface ValidationResult {
    valid: boolean;
    message: string;
    db_check: { valid: boolean; db_name: string; message: string } | null;
    table_check: any;
  }
  const [groupValidations, setGroupValidations] = useState<Record<number, { loading: boolean; result: ValidationResult | null }>>({});

  const handleValidateGroup = async (index: number) => {
    const group = indemapGroups[index];
    const db = group.databaseName.trim();
    const tables = parseListInput(group.tableNamesText);
    if (!db) return;
    setGroupValidations((prev) => ({ ...prev, [index]: { loading: true, result: null } }));
    try {
      const result = await mappingService.validateDbAndTables(db, tables);
      setGroupValidations((prev) => ({ ...prev, [index]: { loading: false, result } }));
    } catch {
      setGroupValidations((prev) => ({ ...prev, [index]: { loading: false, result: null } }));
    }
  };

  const [buildModalOpen, setBuildModalOpen] = useState(false);
  const [buildSubjectArea, setBuildSubjectArea] = useState("");
  const [tablesAndColumnsFile, setTablesAndColumnsFile] = useState<File | null>(null);
  const [tablesAndIndexesFile, setTablesAndIndexesFile] = useState<File | null>(null);

  useEffect(() => {
    const loadDefaultDatabase = async () => {
      try {
        const defaultDb = await getDefaultDatabase();
        if (defaultDb && indemapGroups[0].databaseName === "") {
          updateIndemapGroup(0, { databaseName: defaultDb });
        }
      } catch (error) {
        console.error("Failed to load default database:", error);
      }
    };
    loadDefaultDatabase();
  }, []);

  const enabledSubjectAreas = useMemo(
    () => new Set(subjectAreaStatuses.filter((x) => x.enabled).map((x) => x.subject_area)),
    [subjectAreaStatuses]
  );

  useEffect(() => {
    setSubjectAreas((prev) => prev.filter((subjectArea) => enabledSubjectAreas.has(subjectArea)));
  }, [enabledSubjectAreas]);

  const parseListInput = (raw: string): string[] =>
    raw
      .split(/\r?\n|,/g)
      .map((x) => x.trim())
      .filter((x) => x.length > 0);

  const buildIndemapPairs = (): Array<{ databaseName: string; tableName: string }> => {
    const out: Array<{ databaseName: string; tableName: string }> = [];
    const seen = new Set<string>();
    for (const group of indemapGroups) {
      const db = (group.databaseName || "").trim();
      if (!db) continue;
      const tables = parseListInput(group.tableNamesText || "");
      for (const table of tables) {
        const key = `${db}::${table}`;
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({ databaseName: db, tableName: table });
      }
    }
    return out;
  };

  const updateIndemapGroup = (index: number, patch: Partial<{ databaseName: string; tableNamesText: string }>) => {
    setIndemapGroups((prev) => prev.map((g, i) => (i === index ? { ...g, ...patch } : g)));
  };

  const addIndemapGroup = () => {
    setIndemapGroups((prev) => [...prev, { databaseName: "", tableNamesText: "" }]);
  };

  const removeIndemapGroup = (index: number) => {
    setIndemapGroups((prev) => prev.filter((_, i) => i !== index));
  };

  const openBuildModal = (targetSubjectArea: string) => {
    setBuildSubjectArea(targetSubjectArea);
    setTablesAndColumnsFile(null);
    setTablesAndIndexesFile(null);
    setBuildModalOpen(true);
  };

  const closeBuildModal = () => {
    if (isBuildingSubjectArea) return;
    setBuildModalOpen(false);
  };

  const handleBuildSubjectArea = async () => {
    if (!buildSubjectArea || !tablesAndColumnsFile || !tablesAndIndexesFile) {
      setToastVariant("error");
      setToastMessage("Please upload both ERwin files before building.");
      return;
    }
    try {
      await onBuildSubjectArea({
        subjectArea: buildSubjectArea,
        tablesAndColumnsFile,
        tablesAndIndexesFile,
      });
      setSubjectAreas((prev) => (prev.includes(buildSubjectArea) ? prev : [...prev, buildSubjectArea]));
      setBuildModalOpen(false);
      setToastVariant("success");
      setToastMessage(`Subject area diagram ready: ${buildSubjectArea}`);
    } catch (error: any) {
      setToastVariant("error");
      setToastMessage(error.message || "Failed to build subject area.");
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({
      interfaceCode,
      instructionsText,
      subjectAreas,
      targetLayout,
      indemapPairs: buildIndemapPairs(),
      sourceFiles,
      targetFiles,
    });
  };

  const toggleSubjectArea = (subjectArea: string) => {
    setSubjectAreas((prev) =>
      prev.includes(subjectArea)
        ? prev.filter((value) => value !== subjectArea)
        : [...prev, subjectArea]
    );
  };

  const renderSelectedFiles = (files: FileList | null) =>
    files
      ? Array.from(files).map(f => f.name).join(", ")
      : null;

  return (
    <div className="w-full mb-6">
      <h2 className="text-base font-bold text-brand-darkblue mb-4">Data Mapping Configuration</h2>
      <form onSubmit={handleSubmit} className="space-y-6">
        <div className="p-6 border border-gray-200 rounded-lg bg-white shadow-sm">
          <div className="block text-sm font-medium text-gray-700 mb-2">Subject Area *</div>
          <div className="text-xs text-gray-500 mb-3">
            Choose one or more available subject areas. Upload files for subject areas that are not yet ready.
          </div>
          {isLoadingSubjectAreas ? (
            <div className="flex items-center gap-2 text-xs text-gray-500">
              <Loader2 size={14} className="animate-spin" />
              Loading subject areas...
            </div>
          ) : (
            <div className="space-y-2">
              {subjectAreaStatuses.map((sa) => {
                const selected = subjectAreas.includes(sa.subject_area);
                const lastUploadTitle = sa.last_uploaded_at
                  ? `Last uploaded: ${new Date(sa.last_uploaded_at).toLocaleString()}`
                  : "";
                return (
                  <div
                    key={sa.subject_area}
                    className={`flex items-center justify-between rounded-md border px-3 py-2 ${selected ? "border-brand-primary bg-brand-surface" : "border-gray-200 bg-white"
                      }`}
                  >
                    <label
                      className={`flex items-center gap-2 text-left ${sa.enabled ? "cursor-pointer" : "cursor-not-allowed opacity-60"}`}
                    >
                      <input
                        type="checkbox"
                        disabled={!sa.enabled}
                        checked={selected}
                        onChange={() => toggleSubjectArea(sa.subject_area)}
                        className="h-3.5 w-3.5 rounded border-gray-300 text-font-blue focus:ring-brand-primary"
                      />
                      <span className="text-xs font-medium text-gray-800" title={lastUploadTitle}>
                        {sa.subject_area}
                      </span>
                    </label>
                    <div className="flex items-center gap-2">
                      <span
                        className={`px-2 py-0.5 rounded-full text-[10px] font-semibold ${sa.enabled ? "bg-emerald-100 text-emerald-700" : "bg-gray-100 text-gray-600"
                          }`}
                      >
                        {sa.enabled ? "Available" : "Not uploaded"}
                      </span>
                      {sa.enabled && <span className="text-[10px] text-gray-500">Outdated diagram?</span>}
                      <button
                        type="button"
                        onClick={() => openBuildModal(sa.subject_area)}
                        className="text-[11px] bg-brand-primary text-white px-2.5 py-1 rounded-md hover:bg-brand-primary-hover transition-all"
                      >
                        {sa.enabled ? "Update" : "Upload files"}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="p-6 border border-gray-200 rounded-lg bg-white shadow-sm">
          <div className="grid grid-cols-2 gap-8 mb-4">
            <div>
              <div className="block text-sm font-medium text-gray-700 mb-2">Interface Code *</div>
              <textarea
                value={interfaceCode}
                onChange={(e) => setInterfaceCode(e.target.value)}
                required
                className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs focus:ring-2 focus:ring-brand-primary focus:border-transparent outline-none transition-all"
                placeholder="Enter your interface code here..."
                rows={6}
              />
            </div>

            <div>
              <div className="block text-sm font-medium text-gray-700 mb-2">Instructions Text</div>
              <textarea
                value={instructionsText}
                onChange={(e) => setInstructionsText(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs focus:ring-2 focus:ring-brand-primary focus:border-transparent outline-none transition-all"
                placeholder="Enter additional instructions (optional)..."
                rows={6}
              />
            </div>
          </div>
          <div className="mb-4">
            <div className="block text-sm font-medium text-gray-700 mb-2">Source Files Metadata *</div>
            <div className="bg-white rounded-lg">
              <input
                type="file"
                multiple
                required
                onChange={(e) => setSourceFiles(e.target.files)}
                className="hidden"
                id="source-files-upload"
              />
              <label
                htmlFor="source-files-upload"
                className="bg-white cursor-pointer border-2 border-dashed border-gray-300 rounded-lg p-3 flex items-center gap-2 mb-3"
              >
                <CloudUpload size={20} className="text-gray-400" />
                <span className="text-xs text-gray-600">
                  {sourceFiles && sourceFiles.length > 0
                    ? renderSelectedFiles(sourceFiles)
                    : "Upload Source Files Metadata"}
                </span>
              </label>
            </div>
          </div>

          <div className="mb-4">
            <div className="block text-sm font-medium text-gray-700 mb-4">Target Metadata Layout *</div>
            <select
              value={targetLayout}
              onChange={(e) => setTargetLayout(e.target.value as "UPLOAD_FILES" | "INDEMAP")}
              className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs focus:ring-2 focus:ring-brand-primary focus:border-transparent outline-none transition-all mb-3"
            >
              <option value="UPLOAD_FILES">Upload Files</option>
              <option value="INDEMAP">IndeMap (Database + Table Names)</option>
            </select>

            {targetLayout === "UPLOAD_FILES" ? (
              <div className="bg-white rounded-lg">
                <input
                  type="file"
                  multiple
                  required={targetLayout === "UPLOAD_FILES"}
                  onChange={(e) => setTargetFiles(e.target.files)}
                  className="hidden"
                  id="target-files-upload"
                />
                <label
                  htmlFor="target-files-upload"
                  className="bg-white cursor-pointer border-2 border-dashed border-gray-300 rounded-lg p-3 flex items-center gap-2 mb-3"
                >
                  <CloudUpload size={20} className="text-gray-400" />
                  <span className="text-xs text-gray-600">
                    {targetFiles && targetFiles.length > 0
                      ? renderSelectedFiles(targetFiles)
                    : "Upload Target Files Metadata"}
                  </span>
                </label>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="p-3 rounded-md border border-amber-200 bg-amber-50 text-amber-800 text-xs">
                  Database and table names are case-sensitive. Enter exact values from IndeMap.
                </div>
                {indemapGroups.map((group, index) => (
                  <div key={index} className="border border-gray-200 rounded-md p-3 space-y-2 bg-white">
                    <div className="flex items-center justify-between">
                      <div className="text-xs font-semibold text-gray-700">Pair Group {index + 1}</div>
                      <div className="flex items-center gap-2">
                        {indemapGroups.length > 1 && (
                          <button
                            type="button"
                            onClick={() => removeIndemapGroup(index)}
                            className="text-xs text-red-600 hover:text-red-700"
                          >
                            Remove
                          </button>
                        )}
                      </div>
                    </div>
                    {(() => {
                      const validationResult = groupValidations[index]?.result;
                      if (!validationResult) return null;
                      return (
                      <div className={`text-xs rounded-md px-3 py-2 mt-1 ${validationResult.valid
                          ? "bg-emerald-50 border border-emerald-200 text-emerald-800"
                          : "bg-red-50 border border-red-200 text-red-800"
                        }`}>
                        <div>{validationResult.message}</div>
                        {validationResult.table_check && (
                          <div className="mt-1 text-[11px] opacity-80">{JSON.stringify(validationResult.table_check)}</div>
                        )}
                      </div>
                      );
                    })()}
                    <div>
                      <div className="text-xs font-medium text-gray-700 mb-1">Database Name *</div>
                      <input
                        value={group.databaseName}
                        onChange={(e) => updateIndemapGroup(index, { databaseName: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs focus:ring-2 focus:ring-brand-primary focus:border-transparent outline-none transition-all"
                        placeholder="e.g., DB_AEDWP1V"
                      />
                    </div>
                    <div>
                      <div className="text-xs font-medium text-gray-700 mb-1">Table Names *</div>
                      <textarea
                        value={group.tableNamesText}
                        onChange={(e) => updateIndemapGroup(index, { tableNamesText: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs focus:ring-2 focus:ring-brand-primary focus:border-transparent outline-none transition-all"
                        rows={3}
                        placeholder="One per line or comma-separated (e.g., PRV_DATA, PRV_MAP)"
                      />
                    </div>
                    <button
                      type="button"
                      onClick={() => handleValidateGroup(index)}
                      disabled={!indemapGroups[index].databaseName.trim() || groupValidations[index]?.loading}
                      className="text-xs bg-emerald-50 text-emerald-700 border border-emerald-200 px-2.5 py-1 rounded-md hover:bg-emerald-100 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1 transition-all"
                    >
                      {groupValidations[index]?.loading ? <Loader2 size={11} className="animate-spin" /> : null}
                      Validate
                    </button>
                  </div>
                ))}
                <button
                  type="button"
                  onClick={addIndemapGroup}
                  className="text-xs bg-brand-surface text-font-blue border border-teal-200 px-3 py-1.5 rounded-md hover:bg-teal-100 transition-all"
                >
                  Add Another Database Group
                </button>
              </div>
            )}
          </div>

          {targetLayout === "UPLOAD_FILES" && (
            <div className="grid grid-cols-2 gap-8 mt-4">
              <div className="text-[11px] text-gray-500">
                Use this mode for current Excel-based target metadata ingestion.
              </div>
            </div>
          )}

          {validationErrors.length > 0 && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-4">
              <ul className="text-red-600 text-sm list-disc list-inside space-y-1">
                {validationErrors.map((error) => (
                  <li key={error}>{error}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="pt-2">
            <button
              type="submit"
              disabled={isSubmitting}
              className="w-full bg-brand-primary text-white py-3 rounded-lg hover:bg-brand-primary-hover disabled:bg-gray-400 disabled:cursor-not-allowed flex items-center justify-center gap-2 transition-all font-semibold shadow-sm"
            >
              {isSubmitting ? (
                <>
                  <Loader2 size={20} className="animate-spin" />
                  {isDrafting ? "Generating Draft..." : "Ingesting Metadata..."}
                </>
              ) : (
                "Generate Mapping"
              )}
            </button>
          </div>
        </div>
      </form>

      {buildModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-lg rounded-lg bg-white shadow-xl border border-gray-200 p-4">
            <div className="flex items-center justify-between mb-3">
              <div>
                <div className="text-sm font-semibold text-gray-800">
                  {enabledSubjectAreas.has(buildSubjectArea) ? "Update Diagram" : "Upload Diagram Files"}
                </div>
                <div className="text-xs text-gray-500">{buildSubjectArea}</div>
              </div>
              <button
                type="button"
                onClick={closeBuildModal}
                className="text-xs px-2 py-1 rounded border border-gray-300 hover:bg-gray-50"
              >
                Close
              </button>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <div className="text-xs font-medium text-gray-700 mb-1">Tables and Columns</div>
                <input
                  type="file"
                  onChange={(e) => setTablesAndColumnsFile(e.target.files?.[0] || null)}
                  className="w-full text-xs border border-gray-300 rounded p-2"
                />
              </div>
              <div>
                <div className="text-xs font-medium text-gray-700 mb-1">Tables and Indexes</div>
                <input
                  type="file"
                  onChange={(e) => setTablesAndIndexesFile(e.target.files?.[0] || null)}
                  className="w-full text-xs border border-gray-300 rounded p-2"
                />
              </div>
            </div>
            <div className="mt-4 flex justify-end">
              <button
                type="button"
                onClick={handleBuildSubjectArea}
                disabled={isBuildingSubjectArea || !tablesAndColumnsFile || !tablesAndIndexesFile}
                className="bg-brand-primary text-white px-4 py-2 rounded-lg hover:bg-brand-primary-hover disabled:bg-gray-400 disabled:cursor-not-allowed flex items-center gap-2 transition-all font-semibold shadow-sm text-sm"
              >
                {isBuildingSubjectArea ? (
                  <>
                    <Loader2 size={16} className="animate-spin" />
                    Building...
                  </>
                ) : (
                  "Build"
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {toastMessage && (
        <Toast
          message={toastMessage}
          variant={toastVariant}
          onClose={() => setToastMessage(null)}
        />
      )}
    </div>
  );
}
