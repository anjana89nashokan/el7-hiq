import { useState, useCallback, useEffect, useReducer, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { sttmNav } from "../utils/sttmRoutes";
import { CloudUpload, Loader2 } from "lucide-react";

import FileUploader from "../components/FileUploader";
import Toast from "../components/Toast";
import DDSelectionModal from "../components/DDSelectionModal";
import { TOOLTIP_CONTENT } from "../constants/tooltipContent";

import { uploadIdentifierReducer, initialState } from "../state/reducers/uploadIdentifierReducers";
import { uploadFilesApi, saveSelectedDD, processFiles } from "../end-points/uploadIdentifier";
import { isHL7File, uploadHL7Files } from "../end-points/hl7Api";
import { fetchSessions } from "../end-points/sessionsApi";
import {
  canResumeProfilingRunFromSummary,
  createAppSession,
  getAppSessionSummary,
  renameAppSession,
  type AppSessionSummaryDetail,
} from "../end-points/appSessionsApi";
import {
  getCurrentAppSessionId,
  onSessionChanged,
  emitSessionChanged,
  onNewSessionLoading,
} from "../utils/appSessionStorage";
import InfoTooltip from "./InfoTooltip";

interface SessionResponse {
  name: string;
  createTime: string;
  updateTime: string;
}

const POLL_INTERVAL_MS = 10000;
const MAX_FILE_SIZE_MB = 2;
const MAX_FILE_SIZE = MAX_FILE_SIZE_MB * 1024 * 1024;

const FILE_RULES = {
  DEFAULT_EXTENSIONS: new Set([".pdf", ".docx", ".txt"]),
  DATA_DICT_EXTENSIONS: new Set([".csv", ".xlsx", ".txt", ".docx"]),
  MIME_TYPES: new Set([
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
  ]),
};

export default function UploadIdentifier() {
  const navigate = useNavigate();
  const [uploadState, dispatch] = useReducer(uploadIdentifierReducer, initialState);

  // === File States ===
  const [files, setFiles] = useState<File[]>([]);
  const [brdFile, setBrdFile] = useState<File | null>(null);
  const [erwinModelFile, setErwinModelFile] = useState<File | null>(null);
  const [dataDictionaryFiles, setDataDictionaryFiles] = useState<File[]>([]);

  // === UI States ===
  const [hl7Busy, setHl7Busy] = useState(false);
  const [showAdditionalInput, setShowAdditionalInput] = useState(false);
  const [showBrdSection, setShowBrdSection] = useState(false);
  const [showErwinModelSection, setShowErwinModelSection] = useState(false);
  const [showDataDictionarySection, setShowDataDictionarySection] = useState(false);

  // === Form States ===
  const [projectName, setProjectName] = useState("");
  const [vendorName, setVendorName] = useState("");
  const [contactPerson, setContactPerson] = useState("");
  const [contactName, setContactName] = useState("");
  const [phoneNumber, setPhoneNumber] = useState("");
  const [serverName, setServerName] = useState("");
  const [deliveryFrequency, setDeliveryFrequency] = useState("");
  const [frequencyMode, setFrequencyMode] = useState("");
  const [transferMethod, setTransferMethod] = useState("");
  const [compressionType, setCompressionType] = useState("");
  const [populationType, setPopulationType] = useState("");
  const [headerRecordNumber, setHeaderRecordNumber] = useState("");
  const [trailerRecordNumber, setTrailerRecordNumber] = useState("");
  const [quoteIndicator, setQuoteIndicator] = useState("");
  const [dateTimestampFormat, setDateTimestampFormat] = useState("");
  const [receiveFileWhenNoData, setReceiveFileWhenNoData] = useState("0");
  const [emailNotificationDl, setEmailNotificationDl] = useState("");
  const [assumptions, setAssumptions] = useState("");
  const [dependencies, setDependencies] = useState("");
  const [brdDescription, setBrdDescription] = useState("");
  const [erwinModelDescription, setErwinModelDescription] = useState("");

  // === Error and Session States ===
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [sessionDetail, setSessionDetail] = useState<AppSessionSummaryDetail | null>(null);
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [pendingNavigation, setPendingNavigation] = useState<any>(null);
  const isRefreshingSessionRef = useRef(false);

  // === DD Selection States ===
  const [showDDSelection, setShowDDSelection] = useState(false);
  const [ddCandidates, setDDCandidates] = useState<any[]>([]);
  const [ddSelectionLoading, setDDSelectionLoading] = useState(false);
  const [newSessionLoading, setNewSessionLoading] = useState(false);

  // === Initialize Session ===
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
      } catch (error) {
        console.warn("Failed to load current app session", error);
        setSession(null);
        setSessionDetail(null);
      }
    };

    void loadCurrentSession();
    const cleanup = onSessionChanged(() => {
      void loadCurrentSession();
    });
    const cleanupLoading = onNewSessionLoading((loading) => setNewSessionLoading(loading));

    return () => {
      cleanup();
      cleanupLoading();
      sessionStorage.removeItem("project_details");
    };
  }, []);

  useEffect(() => {
    if (!sessionDetail?.profiling_run || sessionDetail.profiling_run.status !== "RUNNING") {
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
      } catch (error) {
        console.warn("Failed to refresh profiling session status", error);
      } finally {
        isRefreshingSessionRef.current = false;
      }
    }, POLL_INTERVAL_MS);

    return () => globalThis.clearInterval(intervalId);
  }, [sessionDetail?.profiling_run?.id, sessionDetail?.profiling_run?.status]);

  const handleOpenProfiling = useCallback(() => {
    navigate(sttmNav("/profiling"));
  }, [navigate]);

  const activeProfilingRun = sessionDetail?.profiling_run;
  const profilingIsRunning = activeProfilingRun?.status === "RUNNING";
  const profilingCanResume = sessionDetail ? canResumeProfilingRunFromSummary(sessionDetail) : false;

  // === File Validation ===
  const validateFile = useCallback((file: File, label: string): string | null => {
      const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
      const validExtensions =
        label === "Data Dictionary"
          ? FILE_RULES.DATA_DICT_EXTENSIONS
          : FILE_RULES.DEFAULT_EXTENSIONS;

      if (!validExtensions.has(ext)) {
        return `${label}: Invalid file extension. Allowed: ${Array.from(validExtensions).join(", ")}`;
      }

    if (label !== "Data Dictionary" && file.type && !FILE_RULES.MIME_TYPES.has(file.type)) {
        return `${label}: Invalid file type.`;
      }

      if (file.size > MAX_FILE_SIZE) {
        return `${label}: File size exceeds ${MAX_FILE_SIZE_MB} MB.`;
      }

      return null;
  }, []);

  // === Generic File Change Handler ===
  const handleFileChange = useCallback(
    (
      e: React.ChangeEvent<HTMLInputElement>,
      label: string,
      setFile: React.Dispatch<React.SetStateAction<File | null>>
    ) => {
      const file = e.target.files?.[0];
      if (!file) return;

      const error = validateFile(file, label);
      if (error) {
        setValidationErrors((prev) => [...new Set([...prev, error])]);
        setFile(null);
      } else {
        setFile(file);
        setValidationErrors((prev) => prev.filter((err) => !err.includes(label)));
      }
    },
    [validateFile]
  );

  // === Form Validation ===
  const validateForm = useCallback((): boolean => {
    const errors: string[] = [];
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

    if (!projectName.trim()) errors.push("Project Name is required.");
    if (!vendorName.trim()) errors.push("Vendor Name is required.");
    if (!deliveryFrequency) errors.push("File Delivery Frequency is required.");
    if (!populationType) errors.push("File Population Type is required.");
    if (contactPerson && !emailRegex.test(contactPerson)) {
      errors.push("Contact Person email must be a valid email address.");
    }

    setValidationErrors(errors);
    return errors.length === 0;
  }, [projectName, vendorName, deliveryFrequency, populationType]);

  // === Supporting Handlers ===
  const handleUpload = useCallback((newFiles: File[]) => {
    setFiles((prev) => [...prev, ...newFiles]);
  }, []);

  const handleRemove = useCallback((index?: number) => {
    setFiles((prev) => {
      const newFiles = index === undefined ? [] : prev.filter((_, i) => i !== index);
      // Hide additional input section and clear all fields if no files remain
      if (newFiles.length === 0) {
        setShowAdditionalInput(false);
        // Clear all form fields
        setProjectName("");
        setVendorName("");
        setContactPerson("");
        setContactName("");
        setPhoneNumber("");
        setServerName("");
        setDeliveryFrequency("");
        setFrequencyMode("");
        setTransferMethod("");
        setCompressionType("");
        setPopulationType("");
        setHeaderRecordNumber("");
        setTrailerRecordNumber("");
        setQuoteIndicator("");
        setDateTimestampFormat("");
        setReceiveFileWhenNoData("0");
        setEmailNotificationDl("");
        setAssumptions("");
        setDependencies("");
        // Clear BRD section
        setShowBrdSection(false);
        setBrdFile(null);
        setBrdDescription("");
        // Clear Erwin Model section
        setShowErwinModelSection(false);
        setErwinModelFile(null);
        setErwinModelDescription("");
        // Clear Data Dictionary section
        setShowDataDictionarySection(false);
        setDataDictionaryFiles([]);
        // Clear validation errors
        setValidationErrors([]);
      }
      return newFiles;
    });
  }, []);

  // === HL7 branch ===
  // HL7 messages are trees, not rows, so they bypass the tabular pipeline
  // entirely: no DataFrame, no warehouse table, no column profiling.
  const startHL7Ingestion = useCallback(async () => {
    setHl7Busy(true);
    setValidationErrors([]);
    try {
      let appSessionId = getCurrentAppSessionId();
      if (!appSessionId) {
        const created = await createAppSession(undefined, "sess");
        appSessionId = created.session.id;
        emitSessionChanged();
      }
      const result = await uploadHL7Files(files, appSessionId);
      emitSessionChanged();
      navigate(sttmNav(`/hl7/${result.hl7_session_id}`), { state: { result } });
    } catch (error: unknown) {
      let message = "HL7 ingestion failed";
      if (error instanceof Error && error.message) {
        message = error.message;
      } else if (
        typeof error === "object" &&
        error !== null &&
        "response" in error &&
        typeof (error as { response?: { data?: { detail?: string } } }).response?.data?.detail ===
          "string"
      ) {
        message = (error as { response: { data: { detail: string } } }).response.data.detail;
      } else if (!navigator.onLine) {
        message = "Network error — is the backend running on port 8001?";
      }
      setValidationErrors([`HL7 ingestion failed: ${message}`]);
    } finally {
      setHl7Busy(false);
    }
  }, [files, navigate]);

  const handleUploadFiles = useCallback(() => {
    if (files.length === 0) {
      setValidationErrors(["Please upload at least one file before proceeding."]);
      return;
    }

    const hl7Count = files.filter(isHL7File).length;
    if (hl7Count === files.length) {
      void startHL7Ingestion();
      return;
    }
    if (hl7Count > 0) {
      setValidationErrors([
        "HL7 files use a separate pipeline and cannot be mixed with tabular files. Upload them on their own.",
      ]);
      return;
    }

    setShowAdditionalInput(true);
  }, [files, startHL7Ingestion]);

  const handleDataDictionaryFilesChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const selectedFiles = Array.from(e.target.files ?? []);
      if (selectedFiles.length === 0) return;

      const errors: string[] = [];
      const validFiles: File[] = [];

      selectedFiles.forEach((file) => {
        const error = validateFile(file, "Data Dictionary");
        if (error) {
          errors.push(error);
        } else {
          validFiles.push(file);
        }
      });

      if (errors.length > 0) {
        setValidationErrors((prev) => [...new Set([...prev, ...errors])]);
      } else {
        setValidationErrors((prev) =>
          prev.filter((err) => !err.includes("Data Dictionary"))
        );
      }

      if (validFiles.length > 0) {
        setDataDictionaryFiles(validFiles);
      }
    },
    [validateFile]
  );

  // === Main Upload Handler ===
  const handleStartProfiling = useCallback(async (): Promise<void> => {
    // If DD selection modal was closed without selection, show it again
    if (ddCandidates.length > 0 && !showDDSelection) {
      setShowDDSelection(true);
      return;
    }

    if (files.length === 0) {
      setValidationErrors(["Please upload at least one file before starting profiling."]);
      return;
    }
    setValidationErrors([]);

    const isFormValid = validateForm();
    if (!isFormValid) return;

    if (sessionStorage.getItem("name")) {
      sessionStorage.setItem("project_details", JSON.stringify({
        projectName,
        vendorName,
        contactPerson,
        deliveryFrequency
      }));
    }

    dispatch({ type: "UPLOAD_START" });
    dispatch({ type: "START_PROFILING" });

    try {
      const payload: Record<string, unknown> = {
        project_context: { project_name: projectName.trim() },
        vendor_details: {
          name: vendorName.trim(),
          contact_person: contactPerson.trim(),
          contact_name: contactName.trim(),
          phone_number: phoneNumber.trim(),
          server_name: serverName.trim(),
          file_delivery_frequency: deliveryFrequency,
          frequency_mode: frequencyMode,
          transfer_method: transferMethod,
        },
        file_details: {
          compression_type: compressionType,
          population_type: populationType,
          header_record_number: headerRecordNumber,
          trailer_record_number: trailerRecordNumber,
          quote_indicator: quoteIndicator,
          date_timestamp_format: dateTimestampFormat,
          receive_file_when_no_data: receiveFileWhenNoData,
        },
        notification: {
          email_dl: emailNotificationDl,
        },
        additional_info: {
          assumptions: assumptions.trim(),
          dependencies: dependencies.trim(),
        },
      };

      if (showBrdSection && brdFile) {
        payload.brd_file = brdFile;
        payload.brd_details = {
          filename: brdFile.name,
          description: brdDescription.trim() || "BRD document",
        };
      }

      if (showErwinModelSection && erwinModelFile) {
        payload.erwin_model = {
          filename: erwinModelFile.name,
          description: erwinModelDescription.trim() || "Erwin model document",
        };
      }

      if (showDataDictionarySection && dataDictionaryFiles.length > 0) {
        payload.data_dictionary = {
          type: "upload",
          filenames: dataDictionaryFiles.map((file) => file.name),
        };
        payload.data_dictionary_files = dataDictionaryFiles;
      }

      const sessionId = getCurrentAppSessionId();
      const [results] = await Promise.all([
        uploadFilesApi(files, payload),
        sessionId && projectName.trim()
          ? renameAppSession(sessionId, projectName.trim())
          : Promise.resolve(null),
      ]);
      await fetchSessions();
      emitSessionChanged();

      if (results.status === "awaiting_dd_selection" && results.dd_candidates) {
        dispatch({ type: "UPLOAD_SUCCESS", payload: results });
        setDDCandidates(results.dd_candidates);
        setShowDDSelection(true);
        return;
      }

      if (results.status === "ready_for_processing") {
        if (sessionId) {
          const processResults = await processFiles(files, results.metadata_path || "", sessionId);
          dispatch({ type: "UPLOAD_SUCCESS", payload: processResults }); // End loading after processing
          navigate(sttmNav("/profiling"), { state: { data: processResults } });
        }
        return;
      }

      // Handle legacy BRD extraction responses (fallback)
      if (results.brd_extraction_status?.extraction_success && results.brd_extraction_status?.dd_candidates) {
        dispatch({ type: "UPLOAD_SUCCESS", payload: results }); // End loading for DD selection
        setDDCandidates(results.brd_extraction_status.dd_candidates);
        setShowDDSelection(true);
        return;
      }

      // Handle BRD extraction error
      if (results.brd_extraction_status?.extraction_success === false && results.brd_extraction_status?.error_description) {
        setToastMessage(results.brd_extraction_status.error_description);
        if (sessionId) {
          const processResults = await processFiles(files, "", sessionId);
          dispatch({ type: "UPLOAD_SUCCESS", payload: processResults }); // End loading after processing
          setPendingNavigation({ path: sttmNav("/profiling"), state: { data: processResults } });
        }
        return;
      }

      if (sessionId) {
        const processResults = await processFiles(files, "", sessionId);
        dispatch({ type: "UPLOAD_SUCCESS", payload: processResults });
        navigate(sttmNav("/profiling"), { state: { data: processResults } });
      }
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : "Upload failed";
      dispatch({ type: "UPLOAD_ERROR", payload: message });
      dispatch({ type: "STOP_PROFILING" });
      setValidationErrors([message]);
    }
  }, [
    files,
    validateForm,
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
    showBrdSection,
    brdFile,
    brdDescription,
    showErwinModelSection,
    erwinModelFile,
    erwinModelDescription,
    showDataDictionarySection,
    dataDictionaryFiles,
    navigate,
    ddCandidates,
    showDDSelection,
  ]);

  // === DD Selection Handler ===
  const handleDDSelection = useCallback(async (selectedPaths: string[], shouldMerge: boolean = false, columnMappings?: any[], targetSchema?: any[]) => {
      setDDSelectionLoading(true);
      dispatch({ type: "UPLOAD_START" });

      try {
        const sessionId = getCurrentAppSessionId();
        if (!sessionId) throw new Error("No session ID found");

        let metadataPath = "";

        if (selectedPaths.length > 0) {
        // Save the selection with merge option and column mappings
        const saveResult = await saveSelectedDD(sessionId, selectedPaths, shouldMerge, columnMappings, targetSchema);
        
          if (shouldMerge && saveResult.info?.merged) {
            metadataPath = saveResult.path;
          } else {
            const selectedCandidate = ddCandidates.find((candidate) =>
              selectedPaths.includes(candidate.file_path),
            );
            if (selectedCandidate) {
              metadataPath = selectedCandidate.file_path;
            }
          }
        } else {
          metadataPath = "";
        }

        setShowDDSelection(false);
      setDDCandidates([]); // Clear candidates after selection/skip
      
      // Keep the main loading state active during file processing
      // Don't set setDDSelectionLoading(false) yet - let the main flow handle it
      
      // Now process the files with selected metadata
      const processResults = await processFiles(files, metadataPath, sessionId);
      
      // End loading state and navigate
        dispatch({ type: "UPLOAD_SUCCESS", payload: processResults });
        navigate(sttmNav("/profiling"), { state: { data: processResults } });
      } catch (error) {
        console.error("Failed to save DD selection or process files:", error);
      setToastMessage("Failed to save data dictionary selection or process files. Please try again.");
        dispatch({ type: "UPLOAD_ERROR", payload: "Failed to process files" });
      } finally {
        setDDSelectionLoading(false);
      }
  }, [files, navigate, ddCandidates, dispatch]);

  return (
    <div className="flex flex-col gap-8">
      {toastMessage && (
        <Toast
          message={toastMessage}
          duration={0}
          onClose={() => {
            setToastMessage(null);
            if (pendingNavigation) {
              navigate(pendingNavigation.path, {
                state: pendingNavigation.state,
              });
              setPendingNavigation(null);
            }
          }}
        />
      )}

      {newSessionLoading ? (
        <div className="flex flex-col items-center justify-center py-16 gap-3 text-gray-400">
          <div className="w-6 h-6 border-2 border-brand-darkblue border-t-transparent rounded-full animate-spin" />
          <span className="text-xs">Creating new session…</span>
        </div>
      ) : (
        <>
          {session && (
            <div className="bg-green-50 border border-green-200 rounded-lg p-4">
              <p className="text-sm text-green-700 font-semibold">
                Active Session:
              </p>
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
                profilingIsRunning
                  ? "bg-amber-50 border-amber-200"
                  : "bg-brand-surface border-teal-200",
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
                {profilingIsRunning ? "Profiling is running in this session" : "Profiling is ready in this session"}
                  </p>
                  <p className="text-xs text-gray-600 mt-1">
                    Status: {activeProfilingRun.status}
                {activeProfilingRun.current_step ? ` | Step: ${activeProfilingRun.current_step}` : ""}
                  </p>
                  {activeProfilingRun.started_at && (
                    <p className="text-xs text-gray-600">
                      Started:{" "}
                      {new Date(activeProfilingRun.started_at).toLocaleString()}
                    </p>
                  )}
                  {profilingIsRunning && (
                    <p className="text-xs text-amber-700 mt-2">
                      You can leave this page. The profiling job continues in
                      the backend.
                    </p>
                  )}
                  {!profilingIsRunning && profilingCanResume && (
                    <p className="text-xs text-font-blue mt-2">
                      The profiling pipeline finished. Open it to review the
                      saved steps.
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  {profilingIsRunning && (
                    <Loader2
                      className="text-amber-600 animate-spin"
                      size={18}
                    />
                  )}
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
            <div className="flex-col w-full mr-8">
              <div className="w-full mb-6">
                <h2 className="text-base font-bold text-brand-darkblue mb-2">
                  Upload Identifier File(s)
                </h2>

                <FileUploader
                  onUpload={handleUpload}
                  onRemove={handleRemove}
                  onUploadFiles={handleUploadFiles}
                  multiple
                  files={files}
                  allowedTypes={[
                    ".csv",
                    ".tsv",
                    ".ced",
                    ".json",
                    ".xml",
                    ".xlsx",
                    ".xls",
                    ".psv",
                    ".txt",
                    ".zip",
                    ".dat",
                    ".fwf",
                    ".asc",
                    ".prn",
                    ".out",
                    ".log",
                    ".data",
                    ".hl7",
                  ]}
                  maxSize={100 * 1024 * 1024}
                  maxFiles={25}
                  profilingInProgress={uploadState.profilingInProgress || hl7Busy}
                  uploadButtonLabel={hl7Busy ? "Processing HL7…" : undefined}
                />

                {validationErrors.length > 0 && (
                  <div className="bg-red-50 border border-red-200 rounded-lg p-3 mt-4">
                    <ul className="text-red-600 text-sm list-disc list-inside">
                      {[...new Set(validationErrors)].map((error) => (
                        <li key={error}>{error}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* === Data Dictionary Section === */}
                {showAdditionalInput && (
                  <div className="mt-6 p-4 border border-gray-200 rounded-lg bg-gray-50">
                    <div className="flex gap-10">
                      <h4 className="text-sm text-brand-darkblue flex items-center">
                        Upload Data Dictionary
                        <InfoTooltip text={TOOLTIP_CONTENT.dataDictionary} />
                      </h4>
                      <div className="flex gap-4">
                        <label className="flex items-center text-sm">
                          <input
                            type="radio"
                            name="dataDictionary"
                            value="yes"
                            checked={showDataDictionarySection}
                            onChange={() => setShowDataDictionarySection(true)}
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
                              setShowDataDictionarySection(false);
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
                          accept=".csv,.tsv,.ced,.xlsx,.txt"
                          multiple
                          onChange={handleDataDictionaryFilesChange}
                          className="hidden"
                          id="data-dictionary-upload"
                          disabled={uploadState.profilingInProgress}
                        />
                        <label
                          htmlFor="data-dictionary-upload"
                          className={`border-2 border-dashed rounded-lg p-3 flex items-center gap-2 ${uploadState.profilingInProgress
                              ? "border-gray-300 bg-gray-100 cursor-not-allowed"
                              : "border-gray-300 cursor-pointer"
                            }`}
                        >
                          <CloudUpload size={20} className="text-gray-400" />
                          <span
                            className={`text-xs ${uploadState.profilingInProgress
                                ? "text-gray-500"
                                : "text-gray-600"
                              }`}
                          >
                            {(() => {
                              if (uploadState.profilingInProgress) {
                                return "Upload disabled during profiling";
                              }
                              if (dataDictionaryFiles.length > 0) {
                                return `${dataDictionaryFiles.length} file(s): ${dataDictionaryFiles
                                  .map((file) => file.name)
                                  .join(", ")}`;
                              }
                              return `Upload Data Dictionary (CSV, XLSX, TXT - Max ${MAX_FILE_SIZE_MB}MB)`;
                            })()}
                          </span>
                        </label>
                        {dataDictionaryFiles.length > 0 && (
                          <div className="mt-2 space-y-1">
                            {dataDictionaryFiles.map((file, index) => (
                              <div
                                key={index}
                                className="flex items-center justify-between bg-gray-50 p-2 rounded"
                              >
                                <span className="text-xs text-gray-700">
                                  {file.name}
                                </span>
                                <button
                                  onClick={() =>
                                    setDataDictionaryFiles((prev) =>
                                      prev.filter((_, i) => i !== index),
                                    )
                                  }
                                  className="text-red-500 hover:text-red-700 text-xs"
                                >
                                  Remove
                                </button>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}

                {/* === Additional Input Section === */}
                {showAdditionalInput && (
                  <div className="mt-6 p-4 border border-gray-200 rounded-lg bg-gray-50">
                    <h3 className="text-base font-bold text-brand-darkblue mb-4">
                      Additional Input
                    </h3>

                    {/* BRD Section */}
                    <div className="mb-6">
                      <div className="flex gap-10">
                        <div className="block text-sm font-medium text-gray-700 flex items-center">
                          Upload BRD Document?
                          <InfoTooltip text={TOOLTIP_CONTENT.brdFile} />
                        </div>
                        <div className="flex gap-4 mb-3">
                          <label className="flex items-center">
                            <input
                              type="radio"
                              name="brd"
                              value="yes"
                              checked={showBrdSection}
                              onChange={() => setShowBrdSection(true)}
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
                                setShowBrdSection(false);
                                setBrdFile(null);
                                setBrdDescription("");
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
                              handleFileChange(e, "BRD File", setBrdFile)
                            }
                            className="hidden"
                            id="brd-upload"
                            disabled={uploadState.profilingInProgress}
                          />
                          <label
                            htmlFor="brd-upload"
                            className={`bg-white border-2 border-dashed rounded-lg p-3 flex items-center gap-2 mb-3 ${uploadState.profilingInProgress
                                ? "border-gray-300 bg-gray-100 cursor-not-allowed"
                                : "border-gray-300 cursor-pointer"
                              }`}
                          >
                            <CloudUpload size={20} className="text-gray-400" />
                            <span
                              className={`text-xs ${uploadState.profilingInProgress
                                  ? "text-gray-500"
                                  : "text-gray-600"
                                }`}
                            >
                              {(() => {
                                if (uploadState.profilingInProgress) {
                                  return "Upload disabled during profiling";
                                }
                                if (brdFile) {
                                  return brdFile.name;
                                }
                                return `Upload BRD Document (Max ${MAX_FILE_SIZE_MB}MB)`;
                              })()}
                            </span>
                          </label>
                          <textarea
                            value={brdDescription}
                            onChange={(e) => setBrdDescription(e.target.value)}
                            className="w-full px-3 py-2 bg-white border border-gray-300 rounded-md text-xs"
                            placeholder="Enter BRD description"
                            rows={2}
                          />
                        </div>
                      )}
                    </div>

                    {/* === Project Info === */}
                    <div className="space-y-6">
                      <div className="grid grid-cols-2 gap-8">
                        <div>
                          <div className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                            Project Name *
                            <InfoTooltip text={TOOLTIP_CONTENT.projectName} />
                          </div>
                          <input
                            type="text"
                            value={projectName}
                            onChange={(e) => setProjectName(e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                          />
                        </div>
                        <div>
                          <div className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                            File Delivery Frequency *
                            <InfoTooltip
                              text={TOOLTIP_CONTENT.deliveryFrequency}
                            />
                          </div>
                          <select
                            value={deliveryFrequency}
                            onChange={(e) =>
                              setDeliveryFrequency(e.target.value)
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
                          <div className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                            Vendor Name *
                            <InfoTooltip text={TOOLTIP_CONTENT.vendorName} />
                          </div>
                          <input
                            type="text"
                            value={vendorName}
                            onChange={(e) => setVendorName(e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                          />
                        </div>
                        <div>
                          <div className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                            Contact Person Email
                            <InfoTooltip
                              text={TOOLTIP_CONTENT.contactPersonEmail}
                            />
                          </div>
                          <input
                            type="email"
                            value={contactPerson}
                            onChange={(e) => setContactPerson(e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                          />
                        </div>
                      </div>

                      <div className="grid grid-cols-2 gap-8">
                        <div>
                          <div className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                            Contact Name
                            <InfoTooltip text={TOOLTIP_CONTENT.contactName} />
                          </div>
                          <input
                            type="text"
                            value={contactName}
                            onChange={(e) => setContactName(e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                          />
                        </div>
                        <div>
                          <div className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                            Phone Number
                            <InfoTooltip text={TOOLTIP_CONTENT.phoneNumber} />
                          </div>
                          <input
                            type="text"
                            value={phoneNumber}
                            onChange={(e) => setPhoneNumber(e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                          />
                        </div>
                      </div>

                      <div className="grid grid-cols-2 gap-8">
                        <div>
                          <div className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                            Server Name
                            <InfoTooltip text={TOOLTIP_CONTENT.serverName} />
                          </div>
                          <input
                            type="text"
                            value={serverName}
                            onChange={(e) => setServerName(e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                          />
                        </div>
                        <div>
                          <div className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                            Transfer Method
                            <InfoTooltip
                              text={TOOLTIP_CONTENT.transferMethod}
                            />
                          </div>
                          <input
                            type="text"
                            value={transferMethod}
                            onChange={(e) => setTransferMethod(e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                          />
                        </div>
                      </div>

                      <div className="grid grid-cols-2 gap-8">
                        <div>
                          <div className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                            Frequency Mode
                            <InfoTooltip text={TOOLTIP_CONTENT.frequencyMode} />
                          </div>
                          <input
                            type="text"
                            value={frequencyMode}
                            onChange={(e) => setFrequencyMode(e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                          />
                        </div>
                        <div>
                          <div className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                            Compression Type
                            <InfoTooltip
                              text={TOOLTIP_CONTENT.compressionType}
                            />
                          </div>
                          <input
                            type="text"
                            value={compressionType}
                            onChange={(e) => setCompressionType(e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                          />
                        </div>
                      </div>

                      <div className="grid grid-cols-2 gap-8">
                        <div>
                          <div className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                            File Population Type *
                            <InfoTooltip
                              text={TOOLTIP_CONTENT.populationType}
                            />
                          </div>
                          <input
                            type="text"
                            value={populationType}
                            onChange={(e) => setPopulationType(e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                          />
                        </div>
                        <div>
                          <div className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                            Header Record Number
                            <InfoTooltip
                              text={TOOLTIP_CONTENT.headerRecordNumber}
                            />
                          </div>
                          <input
                            type="text"
                            value={headerRecordNumber}
                            onChange={(e) =>
                              setHeaderRecordNumber(e.target.value)
                            }
                            className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                          />
                        </div>
                      </div>

                      <div className="grid grid-cols-2 gap-8">
                        <div>
                          <div className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                            Trailer Record Number
                            <InfoTooltip
                              text={TOOLTIP_CONTENT.trailerRecordNumber}
                            />
                          </div>
                          <input
                            type="text"
                            value={trailerRecordNumber}
                            onChange={(e) =>
                              setTrailerRecordNumber(e.target.value)
                            }
                            className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                          />
                        </div>
                        <div>
                          <div className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                            Quote Indicator
                            <InfoTooltip
                              text={TOOLTIP_CONTENT.quoteIndicator}
                            />
                          </div>
                          <input
                            type="text"
                            value={quoteIndicator}
                            onChange={(e) => setQuoteIndicator(e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                          />
                        </div>
                      </div>

                      <div className="grid grid-cols-2 gap-8">
                        <div>
                          <div className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                            Date Timestamp Format
                            <InfoTooltip
                              text={TOOLTIP_CONTENT.dateTimestampFormat}
                            />
                          </div>
                          <input
                            type="text"
                            value={dateTimestampFormat}
                            onChange={(e) =>
                              setDateTimestampFormat(e.target.value)
                            }
                            className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                          />
                        </div>
                        <div>
                          <div className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                            Email Notification DL
                            <InfoTooltip
                              text={TOOLTIP_CONTENT.emailNotificationDl}
                            />
                          </div>
                          <input
                            type="email"
                            value={emailNotificationDl}
                            onChange={(e) =>
                              setEmailNotificationDl(e.target.value)
                            }
                            className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                          />
                        </div>
                      </div>

                      <div className="grid grid-cols-2 gap-8">
                        <div>
                          <div className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                            Receive File When No Data
                            <InfoTooltip
                              text={TOOLTIP_CONTENT.receiveFileWhenNoData}
                            />
                          </div>
                          <select
                            value={receiveFileWhenNoData}
                            onChange={(e) =>
                              setReceiveFileWhenNoData(e.target.value)
                            }
                            className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                          >
                            <option value="0">No</option>
                            <option value="1">Yes</option>
                          </select>
                        </div>
                      </div>

                      <div>
                        <div className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                          Assumptions
                          <InfoTooltip text={TOOLTIP_CONTENT.assumptions} />
                        </div>
                        <textarea
                          value={assumptions}
                          onChange={(e) => setAssumptions(e.target.value)}
                          className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                          rows={3}
                        />
                      </div>

                      <div>
                        <div className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                          Dependencies
                          <InfoTooltip text={TOOLTIP_CONTENT.dependencies} />
                        </div>
                        <textarea
                          value={dependencies}
                          onChange={(e) => setDependencies(e.target.value)}
                          className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs"
                          rows={3}
                        />
                      </div>
                    </div>

                    {/* === Validation Errors === */}
                    {validationErrors.length > 0 && (
                      <div className="bg-red-50 border border-red-200 rounded-lg p-3 mt-2">
                        <ul className="text-red-600 text-sm list-disc list-inside">
                          {[...new Set(validationErrors)].map((error) => (
                            <li key={error}>{error}</li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {/* === Submit Button === */}
                    <button
                      onClick={handleStartProfiling}
                      disabled={
                        uploadState.loading ||
                        !projectName ||
                        !vendorName ||
                        !deliveryFrequency ||
                        !populationType
                      }
                      className="mt-4 w-full bg-brand-primary text-white py-2 rounded-lg hover:bg-brand-primary-hover disabled:bg-gray-400 disabled:cursor-not-allowed flex items-center justify-center gap-2 transition-colors cursor-pointer"
                    >
                      {uploadState.loading
                        ? "Processing Files..."
                        : ddCandidates.length > 0 && !showDDSelection
                          ? "View Data Dictionaries"
                          : "Start Profiling"}
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>
        </>
      )}
      {/* DD Selection Modal */}
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
