/**
 * StreamingUploadIdentifier - Direct Table Profiling
 * Full-width layout matching the normal upload page for consistency
 */

import { useEffect, useReducer, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Database, CloudUpload, Loader2, Plus, X } from "lucide-react";
import axios from "axios";
import Toast from "../components/Toast";
import DDSelectionModal from "../components/DDSelectionModal";
import {
  validateStreamingFile,
  validateTableName,
  startStreamingProfiling,
  uploadStreamingBatch,
  saveSelectedStreamingDD,
} from "../end-points/upload/streamingUploadApi";
import type { StreamingUploadData } from "../end-points/upload/streamingUploadApi";
import {
  streamingUploadReducer,
  initialStreamingUploadState,
} from "../state/reducers/upload/streamingUploadReducers";
import axiosInstance from "../utils/axios-interceptor";
import { sttmNav } from "../utils/sttmRoutes";
import {
  canResumeProfilingRunFromSummary,
  getAppSessionSummary,
  renameAppSession,
  type AppSessionSummaryDetail,
} from "../end-points/appSessionsApi";
import { getCurrentAppSessionId, onSessionChanged, emitSessionChanged } from "../utils/appSessionStorage";

const MAX_FILE_SIZE_MB = 10;
const POLL_INTERVAL_MS = 10000;

interface SessionResponse {
  name: string;
  createTime: string;
  updateTime: string;
}

export default function StreamingUploadIdentifier() {
  const navigate = useNavigate();
  const [state, dispatch] = useReducer(
    streamingUploadReducer,
    initialStreamingUploadState,
  );

  const {
    tableNames,
    currentTableName,
    tableNameError,
    databaseName,
    defaultDatabaseName,
    brdFile,
    erwinModelFile,
    showDataDictionarySection,
    showBrdSection,
    showFilesDetailsSection,
    projectName,
    vendorName,
    contactPerson,
    contactName,
    phoneNumber,
    serverName,
    deliveryFrequency,
    frequencyMode,
    transferMethod,
    compressionType,
    populationType,
    headerRecordNumber,
    trailerRecordNumber,
    quoteIndicator,
    dateTimestampFormat,
    receiveFileWhenNoData,
    emailNotificationDl,
    assumptions,
    dependencies,
    brdDescription,
    erwinModelDescription,
    isLoading,
    error,
    validationErrors,
  } = state;

  const [dataDictionaryFiles, setDataDictionaryFiles] = useState<File[]>([]);
  const [showDDSelection, setShowDDSelection] = useState(false);
  const [ddCandidates, setDDCandidates] = useState<any[]>([]);
  const [ddSelectionLoading, setDDSelectionLoading] = useState(false);

  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [pendingNavigation, setPendingNavigation] = useState<any>(null);
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [sessionDetail, setSessionDetail] = useState<AppSessionSummaryDetail | null>(null);
  const isRefreshingSessionRef = useRef(false);

  // === Cleanup on unmount ===
  useEffect(() => {
    const loadCurrentSession = async (): Promise<void> => {
      const currentSessionId = getCurrentAppSessionId();
      if (!currentSessionId) {
        setSession(null);
        setSessionDetail(null);
        return;
      }
      try {
        const detail = await getAppSessionSummary(currentSessionId);
        setSessionDetail(detail);
        setSession({
          name: detail.session.title,
          createTime: detail.session.created_at || new Date().toISOString(),
          updateTime: detail.session.updated_at || new Date().toISOString(),
        });
      } catch (loadError) {
        console.warn("Failed to load current app session", loadError);
        setSession(null);
        setSessionDetail(null);
      }
    };

    void loadCurrentSession();
    const cleanup = onSessionChanged(() => {
      void loadCurrentSession();
    });

    return () => {
      cleanup();
      if (!sessionStorage.getItem("session_id")) {
        sessionStorage.removeItem("project_details");
      }
    };
  }, []);

  useEffect(() => {
    const activeProfilingRun = sessionDetail?.profiling_run;
    if (!activeProfilingRun || activeProfilingRun.profiling_mode !== "streaming" || activeProfilingRun.status !== "RUNNING") {
      return;
    }

    const intervalId = globalThis.setInterval(async () => {
      const currentSessionId = getCurrentAppSessionId();
      if (!currentSessionId || isRefreshingSessionRef.current) {
        return;
      }
      try {
        isRefreshingSessionRef.current = true;
        const detail = await getAppSessionSummary(currentSessionId);
        setSessionDetail(detail);
        setSession({
          name: detail.session.title,
          createTime: detail.session.created_at || new Date().toISOString(),
          updateTime: detail.session.updated_at || new Date().toISOString(),
        });
      } catch (refreshError) {
        console.warn("Failed to refresh streaming profiling session status", refreshError);
      } finally {
        isRefreshingSessionRef.current = false;
      }
    }, POLL_INTERVAL_MS);

    return () => globalThis.clearInterval(intervalId);
  }, [sessionDetail?.profiling_run?.id, sessionDetail?.profiling_run?.status, sessionDetail?.profiling_run?.profiling_mode]);

  const activeProfilingRun = sessionDetail?.profiling_run?.profiling_mode === "streaming"
    ? sessionDetail.profiling_run
    : null;
  const profilingIsRunning = activeProfilingRun?.status === "RUNNING";
  const profilingCanResume = sessionDetail && activeProfilingRun
    ? canResumeProfilingRunFromSummary(sessionDetail)
    : false;

  const handleOpenProfiling = () => {
    navigate(sttmNav("/streaming-profiling"));
  };

  // === File Change Handler ===
  const handleFileChange = (
    e: React.ChangeEvent<HTMLInputElement>,
    label: string,
    field: string,
  ) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const error = validateStreamingFile(file, label);
    if (error) {
      dispatch({
        type: "SET_VALIDATION_ERRORS",
        payload: [...new Set([...validationErrors, error])],
      });
      dispatch({ type: "SET_FILE", payload: { field, file: null } });
    } else {
      dispatch({ type: "SET_FILE", payload: { field, file } });
      dispatch({
        type: "SET_VALIDATION_ERRORS",
        payload: validationErrors.filter((err) => !err.includes(label)),
      });
    }
  };

  const handleDataDictionaryFilesChange = (
    e: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const selectedFiles = Array.from(e.target.files ?? []);
    if (selectedFiles.length === 0) return;

    const errors: string[] = [];
    const validFiles: File[] = [];

    selectedFiles.forEach((file) => {
      const error = validateStreamingFile(file, "Data Dictionary");
      if (error) {
        errors.push(error);
      } else {
        validFiles.push(file);
      }
    });

    if (errors.length > 0) {
      dispatch({
        type: "SET_VALIDATION_ERRORS",
        payload: [...new Set([...validationErrors, ...errors])],
      });
    } else {
      dispatch({
        type: "SET_VALIDATION_ERRORS",
        payload: validationErrors.filter(
          (err) => !err.includes("Data Dictionary"),
        ),
      });
    }

    if (validFiles.length > 0) {
      setDataDictionaryFiles(validFiles);
    }
  };

  // === Table Name Management ===
  const handleAddTableName = () => {
    const trimmedName = currentTableName.trim();
    const error = validateTableName(trimmedName, tableNames);

    if (error) {
      dispatch({ type: "SET_TABLE_NAME_ERROR", payload: error });
    } else {
      dispatch({
        type: "SET_TABLE_NAMES",
        payload: [...tableNames, trimmedName],
      });
      dispatch({ type: "SET_CURRENT_TABLE_NAME", payload: "" });
      dispatch({ type: "SET_TABLE_NAME_ERROR", payload: "" });
    }
  };

  const handleRemoveTableName = (index: number) => {
    dispatch({
      type: "SET_TABLE_NAMES",
      payload: tableNames.filter((_, i) => i !== index),
    });
  };

  const handleKeyPress = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleAddTableName();
    }
  };

  const getDefaultDatabase = async () => {
    const response = await axiosInstance.get("/data/default-dataset");
    const data = response.data;
    dispatch({ type: "SET_DATABASE_NAME", payload: data.dataset_id });
    dispatch({ type: "SET_DEFAULT_DATABASE_NAME", payload: data.dataset_id });
  };

  useEffect(() => {
    getDefaultDatabase();
  }, []);

  // === Database Validation handler ===
  const handleValidateDatabase = async () => { // NOSONAR - Error handling requires multiple conditional checks
    if (!databaseName && !defaultDatabaseName) {
      dispatch({ type: "SET_ERROR", payload: "Database name is required" });
      return;
    }
    if (tableNames.length === 0) {
      dispatch({ type: "SET_ERROR", payload: "At least one table name is required" });
      return;
    }

    dispatch({ type: "SET_LOADING", payload: true });
    dispatch({ type: "SET_ERROR", payload: "" });

    try {
      const response = await axiosInstance.post("/messages-strm/validate-dataset-tables", {
        dataset_id: databaseName || defaultDatabaseName,
        table_ids: tableNames
      });

      // Check if response phase is complete
      if (response.data.phase === 'complete') {
        // Show files-details section
        dispatch({ 
          type: "SET_TOGGLE", 
          payload: { field: "showFilesDetailsSection", value: true } 
        });
        
        // Save dataset value from response
        if (response.data.details?.dataset) {
          dispatch({ 
            type: "SET_DATASET_VALUE", 
            payload: response.data.details.dataset 
          });
        }
      }

      // Handle success response
      dispatch({ type: "SET_ERROR", payload: "Database and tables validated successfully!" });
    } catch (err) {
      console.error("Validation error:", err);
      let errorMessage = "Validation failed.";
      if (axios.isAxiosError(err)) {
        if (err.response?.data?.message) {
          errorMessage = err.response.data.message;
        } else if (err.response?.data?.detail) {
          errorMessage = err.response.data.detail;
        } else if (err.response) {
          errorMessage = `Server error: ${err.response.status}`;
        } else if (err.request) {
          errorMessage = "No response from server. Please check your network connection.";
        } else {
          errorMessage = err.message;
        }
      } else if (err instanceof Error) {
        errorMessage = err.message;
      }
      dispatch({ type: "SET_ERROR", payload: errorMessage });
    } finally {
      dispatch({ type: "SET_LOADING", payload: false });
    }
  };

  // === STREAMING handler ===
  const handleStartProfilingStreaming = async () => {
    if (ddCandidates.length > 0 && !showDDSelection) {
      setShowDDSelection(true);
      return;
    }

    dispatch({ type: "SET_ERROR", payload: "" });

    const uploadData: StreamingUploadData = {
      tableNames,
      databaseName: databaseName || defaultDatabaseName,
      projectName,
      vendorName,
      contactPerson,
      contactName,
      phoneNumber,
      serverName,
      deliveryFrequency,
      frequencyMode,
      transferMethod,
      compressionType,
      populationType,
      headerRecordNumber,
      trailerRecordNumber,
      quoteIndicator,
      dateTimestampFormat,
      receiveFileWhenNoData,
      emailNotificationDl,
      assumptions,
      dependencies,
      brdDescription,
      erwinModelDescription,
      dataDictionaryFiles,
      brdFile,
      erwinModelFile,
    };

    dispatch({ type: "SET_LOADING", payload: true });

    try {
      const sessionId = getCurrentAppSessionId();
      const [batchResult] = await Promise.all([
        uploadStreamingBatch(uploadData),
        sessionId && projectName.trim()
          ? renameAppSession(sessionId, projectName.trim())
          : Promise.resolve(null),
      ]);
      emitSessionChanged();

      if (batchResult?.status === "awaiting_dd_selection" && batchResult?.dd_candidates) {
        setDDCandidates(batchResult.dd_candidates);
        setShowDDSelection(true);
        dispatch({ type: "SET_LOADING", payload: false });
        return;
      }

      if (batchResult?.brd_extraction_status?.extraction_success === false && batchResult?.brd_extraction_status?.error_description) {
        setToastMessage(batchResult.brd_extraction_status.error_description);
      }

      const profilingPayload: StreamingUploadData = {
        ...uploadData,
        dataDictionaryFile: undefined,
        dataDictionaryFiles: undefined,
        brdFile: undefined,
        erwinModelFile: undefined,
      };

      const mockApiResponse = await startStreamingProfiling(profilingPayload);

      dispatch({ type: "SET_LOADING", payload: false });
      dispatch({ type: "SET_DATABASE_NAME", payload: defaultDatabaseName });

      navigate(sttmNav("/streaming-profiling"), {
        state: {
          data: mockApiResponse,
          streamingMode: true,
          directProfiling: true,
          initialUploadData: profilingPayload,
        },
      });
    } catch (err) {
      console.error("Error starting streaming profiling:", err);
      let errorMessage = "An unknown error occurred.";
      if (axios.isAxiosError(err)) {
        if (err.response) {
          errorMessage =
            err.response.data.detail || `Server error: ${err.response.status}`;
        } else if (err.request) {
          errorMessage =
            "No response from server. Please check your network connection.";
        } else {
          errorMessage = err.message;
        }
      } else if (err instanceof Error) {
        errorMessage = err.message;
      }
      dispatch({
        type: "SET_ERROR",
        payload: `Failed to start profiling: ${errorMessage}`,
      });
    } finally {
      dispatch({ type: "SET_LOADING", payload: false });
    }
  };

  const handleDDSelection = async (
    selectedPaths: string[],
    shouldMerge: boolean = false,
    columnMappings?: any[],
    targetSchema?: any[],
  ) => {
    setDDSelectionLoading(true);
    dispatch({ type: "SET_LOADING", payload: true });

    try {
      const sessionId = sessionStorage.getItem("session_id");
      if (!sessionId) {
        throw new Error("No session ID found");
      }

      if (selectedPaths.length > 0) {
        await saveSelectedStreamingDD(
          sessionId,
          selectedPaths,
          shouldMerge,
          columnMappings,
          targetSchema,
        );
      }

      setShowDDSelection(false);
      setDDCandidates([]);

      const profilingPayload: StreamingUploadData = {
        tableNames,
        databaseName: databaseName || defaultDatabaseName,
        projectName,
        vendorName,
        contactPerson,
        deliveryFrequency,
        brdDescription,
        erwinModelDescription,
      };

      const mockApiResponse = await startStreamingProfiling(profilingPayload);

      dispatch({ type: "SET_LOADING", payload: false });
      dispatch({ type: "SET_DATABASE_NAME", payload: defaultDatabaseName });

      navigate(sttmNav("/streaming-profiling"), {
        state: {
          data: mockApiResponse,
          streamingMode: true,
          directProfiling: true,
          initialUploadData: profilingPayload,
        },
      });
    } catch (error) {
      console.error("Failed to save DD selection or start streaming:", error);
      setToastMessage("Failed to save data dictionary selection. Please try again.");
      dispatch({ type: "SET_ERROR", payload: "Failed to start streaming profiling" });
    } finally {
      setDDSelectionLoading(false);
    }
  };

  return (
    <div className="flex flex-col gap-8">
      {toastMessage && <Toast message={toastMessage} duration={0} onClose={() => {
        setToastMessage(null);
        if (pendingNavigation) {
          navigate(pendingNavigation.path, { state: pendingNavigation.state });
          setPendingNavigation(null);
        }
      }} />}
      {session && (
        <div className="bg-green-50 border border-green-200 rounded-lg p-4">
          <p className="text-sm text-green-700 font-semibold">Active Session:</p>
          <p className="text-xs text-gray-600">Name: {session.name}</p>
          <p className="text-xs text-gray-600">
            Created: {new Date(session.createTime).toLocaleString()}
          </p>
        </div>
      )}
      {activeProfilingRun && (
        <div
          className={[
            "rounded-lg border p-4",
            profilingIsRunning ? "bg-amber-50 border-amber-200" : "bg-brand-surface border-teal-200",
          ].join(" ")}
        >
          <div className="flex items-start justify-between gap-4">
            <div>
              <p
                className={[
                  "text-sm font-semibold",
                  profilingIsRunning ? "text-amber-700" : "text-font-blue",
                ].join(" ")}
              >
                {profilingIsRunning ? "Large-data profiling is running in this session" : "Large-data profiling is ready in this session"}
              </p>
              <p className="text-xs text-gray-600 mt-1">
                Status: {activeProfilingRun.status}
                {activeProfilingRun.current_step ? ` | Step: ${activeProfilingRun.current_step}` : ""}
              </p>
              {activeProfilingRun.started_at && (
                <p className="text-xs text-gray-600">
                  Started: {new Date(activeProfilingRun.started_at).toLocaleString()}
                </p>
              )}
              {profilingIsRunning && (
                <p className="text-xs text-amber-700 mt-2">
                  You can leave this page. The large-data profiling job continues in the backend.
                </p>
              )}
              {!profilingIsRunning && profilingCanResume && (
                <p className="text-xs text-font-blue mt-2">
                  The large-data profiling pipeline finished. Open it to review the saved steps.
                </p>
              )}
            </div>
            <div className="flex items-center gap-2">
              {profilingIsRunning && <Loader2 className="text-amber-600 animate-spin" size={18} />}
              {profilingCanResume && (
                <button
                  onClick={handleOpenProfiling}
                  className="rounded-md bg-brand-darkblue px-3 py-2 text-xs text-white hover:bg-brand-darkblue/90 transition-colors"
                >
                  Open Profiling
                </button>
              )}
            </div>
          </div>
        </div>
      )}
      <div className="flex w-full">
        <div className="flex-col w-full">
          <div className="w-full mb-6">
            <h1 className="text-xl text-brand-blue mb-4">
              Direct Table Profiling
            </h1>

            {/* Database and Table Names Input */}
            <div className="mb-6 p-4 border border-gray-200 rounded-lg bg-gray-50">
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Database Names <span className="text-red-500">*</span>
              </label>

              {/* Input with Add Button */}
              <div className="flex gap-2 mb-3">
                <input
                  type="text"
                  value={databaseName}
                  onChange={(e) =>
                    dispatch({
                      type: "SET_DATABASE_NAME",
                      payload: e.target.value,
                    })
                  }
                  className="flex-1 px-3 py-2 border border-gray-300 rounded-md bg-white text-xs focus:outline-none focus:ring-2 focus:ring-brand-blue font-mono"
                  placeholder="Enter Database Name"
                />
              </div>

              <p className="text-xs text-gray-500 mt-2 mb-2">
                Default Database Name will be considered when Database Name is
                not specified
              </p>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                BigQuery Table Names <span className="text-red-500">*</span>
              </label>

              {/* Input with Add Button */}
              <div className="flex gap-2 mb-3">
                <input
                  type="text"
                  value={currentTableName}
                  onChange={(e) =>
                    dispatch({
                      type: "SET_CURRENT_TABLE_NAME",
                      payload: e.target.value,
                    })
                  }
                  onKeyPress={handleKeyPress}
                  className="flex-1 px-3 py-2 border border-gray-300 rounded-md bg-white text-xs focus:outline-none focus:ring-2 focus:ring-brand-blue font-mono"
                  placeholder="Enter table name (e.g., table1)"
                />
                <button
                  onClick={handleAddTableName}
                  className="px-4 py-2 bg-brand-blue text-white rounded-md hover:bg-brand-darkblue transition-colors flex items-center gap-1 text-xs font-medium"
                  type="button"
                >
                  <Plus size={16} />
                  Add
                </button>
              </div>

              {/* Error Message */}
              {tableNameError && (
                <p className="text-xs text-red-600 mb-2">{tableNameError}</p>
              )}

              {/* Added Tables List - Scrollable */}
              {tableNames.length > 0 && (
                <div className="max-h-48 overflow-y-auto border border-gray-300 rounded-md bg-white p-2">
                  <div className="flex flex-wrap gap-2">
                    {tableNames.map((name, index) => (
                      <div
                        key={index}
                        className="flex items-center gap-2 bg-brand-surface border border-brand-blue rounded-md px-3 py-1.5 text-xs font-mono"
                      >
                        <span className="text-gray-800">{name}</span>
                        <button
                          onClick={() => handleRemoveTableName(index)}
                          className="text-red-600 hover:text-red-800 transition-colors"
                          type="button"
                        >
                          <X size={14} />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <p className="text-xs text-gray-500 mt-2">
                Add table names one by one. Press Enter or click Add button. No
                spaces allowed.
                {tableNames.length > 0 && (
                  <span className="font-semibold ml-1">
                    ({tableNames.length} table
                    {tableNames.length !== 1 ? "s" : ""} added)
                  </span>
                )}
              </p>

              {/* Validation Button */}
              <div className="mt-4">
                <button
                  onClick={handleValidateDatabase}
                  disabled={isLoading || (!databaseName && !defaultDatabaseName) || tableNames.length === 0}
                  className="px-4 py-2 bg-green-600 text-white rounded-md hover:bg-green-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors flex items-center gap-2 text-xs font-medium"
                  type="button"
                >
                  {isLoading ? (
                    <>
                      <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white"></div>
                      Validating...
                    </>
                  ) : (
                    <>
                      <Database size={16} />
                      Validate Database & Tables
                    </>
                  )}
                </button>

                {/* Validation Error/Success Message */}
                {error && (
                  <div className={`mt-3 p-3 rounded-lg text-xs ${error.includes('successfully')
                      ? 'bg-green-50 border border-green-200 text-green-700'
                      : 'bg-red-50 border border-red-200 text-red-700'
                    }`}>
                    {error}
                  </div>
                )}
              </div>
            </div>
            
            {showFilesDetailsSection && (
              <div className="mt-6 files-details">
              {/* Data Dictionary Section */}
              <div className="p-4 border border-gray-200 rounded-lg bg-gray-50">
                <div className="flex gap-10">
                  <h4 className="text-sm text-brand-blue">
                    Upload Data Dictionary
                  </h4>
                  <div className="flex gap-4">
                    <label className="flex items-center text-sm">
                      <input
                        type="radio"
                        name="dataDictionary"
                        value="yes"
                        checked={showDataDictionarySection}
                        onChange={() =>
                          dispatch({
                            type: "SET_TOGGLE",
                            payload: {
                              field: "showDataDictionarySection",
                              value: true,
                            },
                          })
                        }
                        className="mr-2"
                      />
                      <span className="text-xs">Yes</span>
                    </label>
                    <label className="flex items-center text-sm">
                      <input
                        type="radio"
                        name="dataDictionary"
                        value="no"
                        checked={!showDataDictionarySection}
                        onChange={() => {
                          dispatch({
                            type: "SET_TOGGLE",
                            payload: {
                              field: "showDataDictionarySection",
                              value: false,
                            },
                          });
                          setDataDictionaryFiles([]);
                        }}
                        className="mr-2"
                      />
                      <span className="text-xs">No</span>
                    </label>
                  </div>
                </div>

                {showDataDictionarySection && (
                  <div className="bg-white rounded-lg mt-4">
                    <input
                      type="file"
                      accept=".csv,.xlsx,.txt"
                      multiple
                      onChange={handleDataDictionaryFilesChange}
                      className="hidden"
                      id="data-dictionary-upload"
                    />
                    <label
                      htmlFor="data-dictionary-upload"
                      className="cursor-pointer border-2 border-dashed border-gray-300 rounded-lg p-3 flex items-center gap-2"
                    >
                      <CloudUpload size={20} className="text-gray-400" />
                      <span className="text-xs text-gray-600">
                        {dataDictionaryFiles.length > 0
                          ? `${dataDictionaryFiles.length} file(s) selected`
                          : `Upload Data Dictionary (CSV, XLSX, TXT - Max ${MAX_FILE_SIZE_MB}MB)`}
                      </span>
                    </label>
                  </div>
                )}
              </div>

              {/* Additional Input Section */}
              <div className="mt-6 p-4 border border-gray-200 rounded-lg bg-gray-50">
                <h3 className="text-lg font-semibold text-brand-blue mb-4">
                  Additional Input
                </h3>

              {/* BRD Section */}
              <div className="mb-6">
                <div className="flex gap-10">
                  <span className="block text-sm font-medium text-gray-700">
                    Upload BRD Document?
                  </span>
                  <div className="flex gap-4 mb-3">
                    <label className="flex items-center">
                      <input
                        type="radio"
                        name="brd"
                        value="yes"
                        checked={showBrdSection}
                        onChange={() =>
                          dispatch({
                            type: "SET_TOGGLE",
                            payload: { field: "showBrdSection", value: true },
                          })
                        }
                        className="mr-2"
                      />
                      <span className="text-xs">Yes</span>
                    </label>
                    <label className="flex items-center">
                      <input
                        type="radio"
                        name="brd"
                        value="no"
                        checked={!showBrdSection}
                        onChange={() => {
                          dispatch({
                            type: "SET_TOGGLE",
                            payload: { field: "showBrdSection", value: false },
                          });
                          dispatch({
                            type: "SET_FILE",
                            payload: { field: "brdFile", file: null },
                          });
                          dispatch({
                            type: "SET_FORM_FIELD",
                            payload: { field: "brdDescription", value: "" },
                          });
                        }}
                        className="mr-2"
                      />
                      <span className="text-xs">No</span>
                    </label>
                  </div>
                </div>

                  {showBrdSection && (
                    <div className="rounded-lg">
                      <input
                        type="file"
                        accept=".pdf,.docx,.txt"
                        onChange={(e) =>
                          handleFileChange(e, "BRD File", "brdFile")
                        }
                        className="hidden"
                        id="brd-upload"
                      />
                      <label
                        htmlFor="brd-upload"
                        className="bg-white cursor-pointer border-2 border-dashed border-gray-300 rounded-lg p-3 flex items-center gap-2 mb-3"
                      >
                        <CloudUpload size={20} className="text-gray-400" />
                        <span className="text-xs text-gray-600">
                          {brdFile
                            ? brdFile.name
                            : `Upload BRD Document (Max ${MAX_FILE_SIZE_MB}MB)`}
                        </span>
                      </label>
                      <textarea
                        value={brdDescription}
                        onChange={(e) =>
                          dispatch({
                            type: "SET_FORM_FIELD",
                            payload: {
                              field: "brdDescription",
                              value: e.target.value,
                            },
                          })
                        }
                        className="w-full px-3 py-2 bg-white border border-gray-300 rounded-md text-xs"
                        placeholder="Enter BRD description"
                        rows={2}
                      />
                    </div>
                  )}
                </div>

                {/* Project Info */}
                <div className="space-y-6">
                  <div className="grid grid-cols-2 gap-8">
                    <div>
                      <div className="block text-sm font-medium text-gray-700 mb-2">
                        Project Name *
                      </div>
                      <input
                        type="text"
                        value={projectName}
                        onChange={(e) =>
                          dispatch({
                            type: "SET_FORM_FIELD",
                            payload: {
                              field: "projectName",
                              value: e.target.value,
                            },
                          })
                        }
                        className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                      />
                    </div>
                    <div>
                      <div className="block text-sm font-medium text-gray-700 mb-2">
                        File Delivery Frequency *
                      </div>
                      <select
                        value={deliveryFrequency}
                        onChange={(e) =>
                          dispatch({
                            type: "SET_FORM_FIELD",
                            payload: {
                              field: "deliveryFrequency",
                              value: e.target.value,
                            },
                          })
                        }
                        className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                      >
                        <option value="">Select frequency</option>
                        <option value="daily">Daily</option>
                        <option value="weekly">Weekly</option>
                        <option value="monthly">Monthly</option>
                        <option value="quarterly">Quarterly</option>
                        <option value="annually">Annually</option>
                        <option value="ad-hoc">Ad-hoc</option>
                      </select>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-8">
                    <div>
                      <div className="block text-sm font-medium text-gray-700 mb-2">
                        Vendor Name *
                      </div>
                      <input
                        type="text"
                        value={vendorName}
                        onChange={(e) =>
                          dispatch({
                            type: "SET_FORM_FIELD",
                            payload: {
                              field: "vendorName",
                              value: e.target.value,
                            },
                          })
                        }
                        className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                      />
                    </div>
                    <div>
                      <div className="block text-sm font-medium text-gray-700 mb-2">
                        Contact Person Email
                      </div>
                      <input
                        type="email"
                        value={contactPerson}
                        onChange={(e) =>
                          dispatch({
                            type: "SET_FORM_FIELD",
                            payload: {
                              field: "contactPerson",
                              value: e.target.value,
                            },
                          })
                        }
                        className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-8">
                    <div>
                      <div className="block text-sm font-medium text-gray-700 mb-2">
                        Contact Name
                      </div>
                      <input
                        type="text"
                        value={contactName}
                        onChange={(e) =>
                          dispatch({
                            type: "SET_FORM_FIELD",
                            payload: {
                              field: "contactName",
                              value: e.target.value,
                            },
                          })
                        }
                        className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                      />
                    </div>
                    <div>
                      <div className="block text-sm font-medium text-gray-700 mb-2">
                        Phone Number
                      </div>
                      <input
                        type="text"
                        value={phoneNumber}
                        onChange={(e) =>
                          dispatch({
                            type: "SET_FORM_FIELD",
                            payload: {
                              field: "phoneNumber",
                              value: e.target.value,
                            },
                          })
                        }
                        className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-8">
                    <div>
                      <div className="block text-sm font-medium text-gray-700 mb-2">
                        Server Name
                      </div>
                      <input
                        type="text"
                        value={serverName}
                        onChange={(e) =>
                          dispatch({
                            type: "SET_FORM_FIELD",
                            payload: {
                              field: "serverName",
                              value: e.target.value,
                            },
                          })
                        }
                        className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                      />
                    </div>
                    <div>
                      <div className="block text-sm font-medium text-gray-700 mb-2">
                        Transfer Method
                      </div>
                      <input
                        type="text"
                        value={transferMethod}
                        onChange={(e) =>
                          dispatch({
                            type: "SET_FORM_FIELD",
                            payload: {
                              field: "transferMethod",
                              value: e.target.value,
                            },
                          })
                        }
                        className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-8">
                    <div>
                      <div className="block text-sm font-medium text-gray-700 mb-2">
                        Frequency Mode
                      </div>
                      <input
                        type="text"
                        value={frequencyMode}
                        onChange={(e) =>
                          dispatch({
                            type: "SET_FORM_FIELD",
                            payload: {
                              field: "frequencyMode",
                              value: e.target.value,
                            },
                          })
                        }
                        className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                      />
                    </div>
                    <div>
                      <div className="block text-sm font-medium text-gray-700 mb-2">
                        Compression Type
                      </div>
                      <input
                        type="text"
                        value={compressionType}
                        onChange={(e) =>
                          dispatch({
                            type: "SET_FORM_FIELD",
                            payload: {
                              field: "compressionType",
                              value: e.target.value,
                            },
                          })
                        }
                        className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-8">
                    <div>
                      <div className="block text-sm font-medium text-gray-700 mb-2">
                        File Population Type *
                      </div>
                      <input
                        type="text"
                        value={populationType}
                        onChange={(e) =>
                          dispatch({
                            type: "SET_FORM_FIELD",
                            payload: {
                              field: "populationType",
                              value: e.target.value,
                            },
                          })
                        }
                        className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                      />
                    </div>
                    <div>
                      <div className="block text-sm font-medium text-gray-700 mb-2">
                        Header Record Number
                      </div>
                      <input
                        type="text"
                        value={headerRecordNumber}
                        onChange={(e) =>
                          dispatch({
                            type: "SET_FORM_FIELD",
                            payload: {
                              field: "headerRecordNumber",
                              value: e.target.value,
                            },
                          })
                        }
                        className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-8">
                    <div>
                      <div className="block text-sm font-medium text-gray-700 mb-2">
                        Trailer Record Number
                      </div>
                      <input
                        type="text"
                        value={trailerRecordNumber}
                        onChange={(e) =>
                          dispatch({
                            type: "SET_FORM_FIELD",
                            payload: {
                              field: "trailerRecordNumber",
                              value: e.target.value,
                            },
                          })
                        }
                        className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                      />
                    </div>
                    <div>
                      <div className="block text-sm font-medium text-gray-700 mb-2">
                        Quote Indicator
                      </div>
                      <input
                        type="text"
                        value={quoteIndicator}
                        onChange={(e) =>
                          dispatch({
                            type: "SET_FORM_FIELD",
                            payload: {
                              field: "quoteIndicator",
                              value: e.target.value,
                            },
                          })
                        }
                        className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-8">
                    <div>
                      <div className="block text-sm font-medium text-gray-700 mb-2">
                        Date Timestamp Format
                      </div>
                      <input
                        type="text"
                        value={dateTimestampFormat}
                        onChange={(e) =>
                          dispatch({
                            type: "SET_FORM_FIELD",
                            payload: {
                              field: "dateTimestampFormat",
                              value: e.target.value,
                            },
                          })
                        }
                        className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                      />
                    </div>
                    <div>
                      <div className="block text-sm font-medium text-gray-700 mb-2">
                        Email Notification DL
                      </div>
                      <input
                        type="email"
                        value={emailNotificationDl}
                        onChange={(e) =>
                          dispatch({
                            type: "SET_FORM_FIELD",
                            payload: {
                              field: "emailNotificationDl",
                              value: e.target.value,
                            },
                          })
                        }
                        className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-8">
                    <div>
                      <div className="block text-sm font-medium text-gray-700 mb-2">
                        Receive File When No Data
                      </div>
                      <select
                        value={receiveFileWhenNoData}
                        onChange={(e) =>
                          dispatch({
                            type: "SET_FORM_FIELD",
                            payload: {
                              field: "receiveFileWhenNoData",
                              value: e.target.value,
                            },
                          })
                        }
                        className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                      >
                        <option value="0">No</option>
                        <option value="1">Yes</option>
                      </select>
                    </div>
                  </div>

                  <div>
                    <div className="block text-sm font-medium text-gray-700 mb-2">
                      Assumptions
                    </div>
                    <textarea
                      value={assumptions}
                      onChange={(e) =>
                        dispatch({
                          type: "SET_FORM_FIELD",
                          payload: {
                            field: "assumptions",
                            value: e.target.value,
                          },
                        })
                      }
                      className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                      rows={3}
                    />
                  </div>

                  <div>
                    <div className="block text-sm font-medium text-gray-700 mb-2">
                      Dependencies
                    </div>
                    <textarea
                      value={dependencies}
                      onChange={(e) =>
                        dispatch({
                          type: "SET_FORM_FIELD",
                          payload: {
                            field: "dependencies",
                            value: e.target.value,
                          },
                        })
                      }
                      className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                      rows={3}
                    />
                  </div>
                </div>

                {/* Validation Errors */}
                {validationErrors.length > 0 && (
                  <div className="bg-red-50 border border-red-200 rounded-lg p-3 mt-2">
                    <ul className="text-red-600 text-sm list-disc list-inside">
                      {[...new Set(validationErrors)].map((error) => (
                        <li key={error}>{error}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Submit Button */}
                <button
                  onClick={handleStartProfilingStreaming}
                  disabled={
                    isLoading ||
                    !projectName ||
                    !vendorName ||
                    !deliveryFrequency ||
                    !populationType ||
                    tableNames.length === 0
                  }
                  className="mt-4 w-full bg-brand-primary text-white py-2 rounded-lg hover:bg-brand-primary-hover disabled:bg-gray-400 disabled:cursor-not-allowed flex items-center justify-center gap-2 transition-colors cursor-pointer"
                >
                  {isLoading ? (
                    <>
                      <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white"></div>
                      Starting Streaming Profiling...
                    </>
                  ) : (
                    <>
                      <Database className="w-4 h-4" />
                      {ddCandidates.length > 0 && !showDDSelection
                        ? "View Data Dictionaries"
                        : "Start Streaming Profiling"}
                    </>
                  )}
                </button>
              </div>
            </div>
            )}
          </div>
        </div>
      </div>

      <DDSelectionModal
        isOpen={showDDSelection}
        onClose={() => setShowDDSelection(false)}
        candidates={ddCandidates}
        onConfirm={handleDDSelection}
        isLoading={ddSelectionLoading}
      />
    </div>
  );
}
