import { useEffect, useState, useRef, useMemo, useCallback } from "react";
import type { KeyboardEvent } from "react";
import {
  FileSpreadsheet,
  ChevronRight,
  Check,
  ChevronDown,
  ChevronUp,
  Bot,
  Download,
  Send,
  Loader2,
  X,
} from "lucide-react";
import { useLocation, useNavigate } from "react-router-dom";
import { API_BASE_URL } from "../config/env";
import { useDispatch, useSelector } from "react-redux";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../components/Tabs";
import { useSSEStream } from "../hooks/useSSEStream";
import Markdown from "react-markdown";
import StreamingRelationshipView from "../components/StreamingRelationshipView";
import StreamingDataDictionaryView from "../components/StreamingDataDictionaryView";
import StreamingDataAnomalyView from "../components/StreamingDataAnomalyView";
import StreamingSimilarityView from "../components/StreamingSimilarityView";
import remarkGfm from "remark-gfm";
import TableWithExport from "../components/TableWithExport";
import MetadataTemplate from "../components/MetadataTemplate";
import DataAnomalyAnalyzer from "../components/DataAnomalyAnalyzer";
import TableItemDisplay from "../components/TableItemDisplay";
import StreamingTableProfilingDisplay from "../components/StreamingTableProfilingDisplay";
import ChatModal from "../components/ChatModal";
import DartSuggestion from "./DartSuggestion";
import { setStep3Response } from "../state/reducers/dartReducer";
import { getAppSessionDetail, saveProfilingResumeState } from "../end-points/appSessionsApi";
import type { AppSessionDetail } from "../end-points/appSessionsApi";
import { sendStreamingProfilingChatHITLMessage } from "../end-points/chatApi";
import { getCurrentAppSessionId, onSessionChanged } from "../utils/appSessionStorage";
import { resolveDataDictionaryPayload } from "../utils/profilingUtils";
import { resolveDataDictionaryTableId } from "../utils/dataDictionaryUtils";
import { hasProfilingToolResult } from "../end-points/profilingResultApi";
import { showDownloadSelectionModal, downloadFilesIndividually, showLoadingModal } from "../utils/fileDownloadUtils";
import { sttmNav } from "../utils/sttmRoutes";

interface StepIndicatorProps {
  step: Step;
  isActive: boolean;
  isClickable: boolean;
  onStepClick: (stepId: number) => void;
}

interface PhaseNameWordProps {
  word: string;
}

interface TableWithExportWrapperProps {
  stepTitle?: string;
  [key: string]: any;
}

const PhaseNameWord = ({ word }: PhaseNameWordProps) => (
  <span className="block">{word}</span>
);

const TableWithExportWrapper = ({ stepTitle, ...props }: TableWithExportWrapperProps) => (
  <TableWithExport {...props} stepTitle={stepTitle} />
);

const StepIndicator = ({
  step,
  isActive,
  isClickable,
  onStepClick,
}: StepIndicatorProps) => {
  const getStepIndicatorClass = () => {
    if (step.completed) return "bg-brand-blue border-brand-blue text-white shadow-md";
    if (isActive) return "bg-white border-brand-blue text-brand-blue shadow-md";
    return "bg-white border-gray-300 text-gray-400";
  };

  const getSpanTextClass = () => {
    if (isActive) return "text-brand-blue";
    if (isDisabled) return "text-gray-400";
    return "text-brand-charcoal";
  };

  const stepIndicatorClass = getStepIndicatorClass();
  const isDisabled = !isClickable;

  return (
    <button
      className={`flex items-start gap-3 cursor-pointer transition-all duration-200 bg-transparent border-none p-0 text-left w-full ${isClickable ? "hover:opacity-80" : "cursor-not-allowed opacity-60"
        } ${isDisabled ? "pointer-events-none" : ""}`}
      onClick={() => isClickable && onStepClick(step.id)}
      disabled={!isClickable}
      aria-label={`Step ${step.id}: ${step.title}`}
    >
      <div className="relative z-10 flex-shrink-0">
        <div
          className={`w-8 h-8 rounded-full border-2 flex items-center justify-center text-sm font-semibold transition-all duration-200 ${stepIndicatorClass} ${isDisabled ? "opacity-50" : ""}`}
        >
          {step.completed ? <Check size={14} strokeWidth={2} /> : step.id}
        </div>
      </div>

      <div className="flex flex-col min-w-0 flex-1">
        <span
          className={`text-sm leading-tight ${getSpanTextClass()}`}
        >
          {step.title}
        </span>
        <span
          className={`text-[10px] mt-1 ${isDisabled ? "text-gray-300" : "text-brand-charcoal/50"}`}
        >
          {step.description}
        </span>
      </div>
    </button>
  );
};

interface FileUpload {
  sessionID: string;
  user: string;
  createdDate: string;
  lastUpdateDate: string;
  file_id: string;
  filename: string;
  table_name: string;
  dataset_id: string;
  project_id: string;
  rows_uploaded: number;
  upload_timestamp: string;
  initial_profiling_report: string;
  profiling_report_url?: string;
}

interface ApiResponse {
  total_files: number;
  successful_uploads: FileUpload[];
  failed_uploads: { filename: string; error: string }[];
  summary: {
    successful: number;
    failed: number;
    total_rows_uploaded: number;
  };
}

interface Step {
  id: number;
  title: string;
  description: string;
  completed: boolean;
}

interface ProfilingChatMessage {
  text: string;
  role: "user" | "assistant";
  mode?: string;
  response?: any;
}

export default function StreamingProfilingResult() {
  const location = useLocation();
  const routeState = (location as any).state;
  const dispatch = useDispatch();
  const { dartSuggestionResponse } = useSelector((state: any) => state.dart);
  const hydrationCompleteRef = useRef(false);
  const [restoredProfilingData, setRestoredProfilingData] = useState<ApiResponse | null>(null);

  const [showDropdown, setShowDropdown] = useState(false);
  const [sessionDetail, setSessionDetail] = useState<AppSessionDetail | null>(null);
  const [isDownloadingFiles, setIsDownloadingFiles] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const metadataExportRef = useRef<(() => void) | null>(null);
  const detailedProfilingExportRef = useRef<(() => void) | null>(null);

  // Add debugging for export refs
  useEffect(() => {
    console.log('[StreamingProfilingResult] Export refs updated:', {
      metadataExportRef: metadataExportRef.current,
      detailedProfilingExportRef: detailedProfilingExportRef.current
    });
  }, [metadataExportRef.current, detailedProfilingExportRef.current]);

  const [activeTab, setActiveTab] = useState<string>("");
  const [currentStep, setCurrentStep] = useState(1);
  const [hasReachedStep8, setHasReachedStep8] = useState(false);
  const [workflowFinished, setWorkflowFinished] = useState(false);
  const navigate = useNavigate();
  const [profileSummaryCollapsed, setProfileSummaryCollapsed] = useState(false);
  const [open, setOpen] = useState(false);

  const [initialMessageData, setInitialMessageData] = useState<any>();
  const [relationshipResponse, setRelationshipResponse] = useState<string>("");
  const [prevRelationshipResponse, setPrevRelationshipResponse] =
    useState<string>("");
  const [dataDictionaryJson, setDataDictionaryJson] = useState();
  const [dataDictionaryResponse, setDataDictionaryResponse] =
    useState<string>("");
  const [dataDictionaryTableId, setDataDictionaryTableId] = useState<string>("");
  const [prevMetadataResponse, setPrevMetadataResponse] = useState<any>();
  const [anomalyData, setAnomalyData] = useState<any>();
  const [modifiedRelationshipResponse, setModifiedRelationshipResponse] = useState<any>(null);
  const [modifiedAnomalyResponse, setModifiedAnomalyResponse] = useState<any>(null);
  const [modifiedMetadataResponse, setModifiedMetadataResponse] = useState<any>(null);
  const [chatOpenStates, setChatOpenStates] = useState<{ [key: number]: boolean }>({});
  const [isProfilingChatOpen, setIsProfilingChatOpen] = useState(false);
  const [profilingChatInput, setProfilingChatInput] = useState("");
  const [profilingChatMessages, setProfilingChatMessages] = useState<ProfilingChatMessage[]>([]);
  const [isSubmittingProfilingChat, setIsSubmittingProfilingChat] = useState(false);
  const [profilingChatError, setProfilingChatError] = useState("");
  const [isScrolled, setIsScrolled] = useState(false);

  // Streaming-specific state
  const [accordionStates, setAccordionStates] = useState<{
    [key: string]: boolean;
  }>({});
  const hasSentRef = useRef(false);
  const [stepApiCallTracker, setStepApiCallTracker] = useState<Set<number>>(
    new Set(),
  );
  const [activeStreamingStep, setActiveStreamingStep] = useState<number | null>(
    null,
  );

  const toggleAccordion = (key: string) => {
    setAccordionStates((prev) => ({
      ...prev,
      [key]: !prev[key],
    }));
  };

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setShowDropdown(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Add useEffect to fetch session detail
  useEffect(() => {
    const fetchSessionDetail = async () => {
      const appSessionId = getCurrentAppSessionId();
      if (!appSessionId) {
        console.log("[StreamingProfilingResult] No appSessionId found");
        return;
      }
      
      console.log("[StreamingProfilingResult] Fetching session detail for:", appSessionId);
      
      try {
        const detail = await getAppSessionDetail(appSessionId);
        console.log("[StreamingProfilingResult] Session detail fetched:", detail);
        setSessionDetail(detail);
      } catch (error) {
        console.error("[StreamingProfilingResult] Failed to fetch session detail:", error);
      }
    };
    
    fetchSessionDetail();
  }, []);


  const baseUrl = API_BASE_URL;
  const isStreamingAnomalyEnabled =
    (process.env.REACT_APP_STREAMING_ANOMALY ?? "true")
      .toString()
      .toLowerCase() !== "false";

  // Profiling data passed via location.state
  const [steps, setSteps] = useState<Step[]>([
    {
      id: 1,
      title: "Dataset Overview",
      description: "Review dataset information and profiling report",
      completed: false,
    },
    {
      id: 2,
      title: "Relationship Analysis",
      description: "Analyze relationships between datasets",
      completed: false,
    },
    {
      id: 3,
      title: "Data Dictionary",
      description: "Generate comprehensive data dictionary",
      completed: false,
    },
    {
      id: 4,
      title: "Similarity Check",
      description: "Check similarity with Reference tables",
      completed: false,
    },
    {
      id: 5,
      title: "Reference Suggestion",
      description: "Get Reference table suggestions",
      completed: false,
    },
    {
      id: 6,
      title: "Data Anomaly Analysis",
      description: "Generate comprehensive data anomaly analysis",
      completed: false,
    },
    {
      id: 7,
      title: "Metadata Template",
      description: "Generate metadata template",
      completed: false,
    },
    {
      id: 8,
      title: "Detailed Profiling",
      description: "View detailed table profiling information",
      completed: false,
    },
  ]);

  const profilingData = useMemo(
    () => (routeState?.data as ApiResponse) || restoredProfilingData,
    [routeState?.data, restoredProfilingData],
  );

  const profilingSession = useMemo(
    () => ({
      sessionId: sessionStorage.getItem("session_id"),
      userId: sessionStorage.getItem("user_id"),
      appName: sessionStorage.getItem("app_name"),
    }),
    [],
  );

  const requiredStepsComplete = useMemo(
    () => [1, 2, 3, 6, 7].every((id) => steps.find((s) => s.id === id)?.completed),
    [steps],
  );

  const handleContinueToMapping = useCallback(() => {
    navigate(sttmNav("/mapping"), {
      state: {
        fromProfilingComplete: true,
        profilingMode: "streaming",
      },
    });
  }, [navigate]);

  // Update the condition for showing buttons
  const shouldShowButtons = useMemo(() => {
    // Required steps: 1, 2, 3, 6, 7 (steps 4 and 5 are optional)
    const requiredSteps = steps.filter((s) => [1, 2, 3, 6, 7].includes(s.id));
    const requiredCompleted = requiredSteps.every((s) => s.completed);
    
    // Once user reaches step 8 or all required steps are completed, consider it as allStepsCompleted
    const completed = hasReachedStep8 || requiredCompleted || workflowFinished;
    
    const profilingRunStatus = sessionDetail?.profiling_run?.status;
    const uploadResponseStatus = sessionDetail?.profiling_run?.resume_state?.uploadResponse?.status;
    
    // Once all required steps are completed or user reached step 8, always show buttons regardless of current step
    const shouldShow = completed || profilingRunStatus === "COMPLETED" || uploadResponseStatus === "completed" || currentStep === 8;
    
    console.log("[StreamingProfilingResult] Button visibility debug:", {
      allStepsCompleted: completed,
      profilingRunStatus: profilingRunStatus,
      uploadResponseStatus: uploadResponseStatus,
      sessionDetail: sessionDetail,
      currentStep: currentStep,
      hasReachedStep8: hasReachedStep8,
      requiredCompleted: requiredCompleted,
      shouldShow: shouldShow
    });
    
    return shouldShow;
  }, [steps, sessionDetail, currentStep, hasReachedStep8, workflowFinished]);

  useEffect(() => {
    const handleScroll = () => {
      setIsScrolled(window.scrollY > 100);
    };
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  const handleDownloadUploadedFiles = useCallback(async () => {
    console.log('[StreamingProfilingResult] handleDownloadUploadedFiles called');
    
    try {
      setIsDownloadingFiles(true);
      const sessionId = getCurrentAppSessionId();
      if (!sessionId) {
        alert('No session ID found');
        return;
      }

      // Show loading modal while fetching data
      const loadingModal = showLoadingModal();
      
      try {
        // Call the API endpoint to get download data
        const baseUrl = API_BASE_URL;
        const response = await fetch(`${baseUrl}/files/download/${sessionId}`, {
          method: 'GET',
          headers: {
            'accept': 'application/json'
          }
        });

        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const downloadData = await response.json();
        console.log('[StreamingProfilingResult] Download data from API:', downloadData);
        
        // Close loading modal
        loadingModal.close();
        
        if (!downloadData.files || downloadData.files.length === 0) {
          alert('No files available for download');
          return;
        }

        const selectedUrls = await showDownloadSelectionModal(downloadData);
        console.log('[StreamingProfilingResult] User selected URLs:', selectedUrls);
        
        if (selectedUrls.length > 0) {
          await downloadFilesIndividually(selectedUrls, 'uploaded-files');
        } else {
          console.log('[StreamingProfilingResult] No URLs selected by user');
        }
      } catch (apiError) {
        // Close loading modal on API error
        loadingModal.close();
        throw apiError;
      }
    } catch (error) {
      console.error('[StreamingProfilingResult] Failed to download uploaded files:', error);
      alert('Failed to download files. Please try again.');
    } finally {
      setIsDownloadingFiles(false);
    }
  }, []);



  const handleDownloadDataDictionaryCSV = useCallback(() => {
    console.log('[StreamingProfilingResult] handleDownloadDataDictionaryCSV called');
    console.log('[StreamingProfilingResult] dataDictionaryJson:', dataDictionaryJson);
    
    // Check if we have data dictionary data from step 3
    let dataToExport = null;
    
    if (dataDictionaryJson && Array.isArray(dataDictionaryJson)) {
      dataToExport = dataDictionaryJson;
    } else if (dataDictionaryJson && typeof dataDictionaryJson === 'object') {
      // Handle case where it's a single object
      dataToExport = [dataDictionaryJson];
    } else {
      console.log('[StreamingProfilingResult] No data dictionary data available for export');
      alert('No Data Dictionary data available for download. Please complete Step 3 first.');
      return;
    }
    
    console.log('[StreamingProfilingResult] Exporting data:', dataToExport);
    
    const headers = ["File Name", "Field Name", "Field Business Name", "Data Type", "Length", "Format", "Nullable", "Most Occurrences", "Primary Key", "Foreign Key", "Field Description"];
    const rows = dataToExport.map((item: any) => {
      const mostOcc = item.most_occurrences ?? item["Most Occurrences"] ?? "";
      const mostOccStr = Array.isArray(mostOcc) ? mostOcc.join(", ") : String(mostOcc);
      return [
        item.file_name || item["File Name"] || "",
        item.field_name || item["Field Name"] || item["Attribute Name"] || "",
        item.business_name || item["Field Business Name"] || item["Logical Attribute Name"] || "",
        item.data_type || item["Data Type"] || "",
        item.length || item["Length"] || "",
        item.format || item["Format"] || "",
        item.nullable || item["Nullable"] || item["Nullability"] || "",
        mostOccStr,
        item.primary_key || item["Primary Key"] || "",
        item.foreign_key || item["Foreign Key"] || "",
        item.field_description || item["Field Description"] || item["Attribute Description"] || "",
      ].map((f) => `"${String(f).replace(/"/g, '""')}"`).join(",");
    });
    
    if (rows.length === 0) {
      alert('No data available to export. Please complete Step 3: Data Dictionary first.');
      return;
    }
    
    const csv = [headers.join(","), ...rows].join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `data-dictionary-${new Date().toISOString().split("T")[0]}.csv`;
    console.log('[StreamingProfilingResult] CSV download initiated:', a.download);
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }, [dataDictionaryJson]);

  const getStoredSession = () => ({
    sessionId: sessionStorage.getItem("session_id"),
    appName: sessionStorage.getItem("app_name"),
    userId: sessionStorage.getItem("user_id"),
  });

  // Use SSE streaming hook for profiling with new LLM analysis states
  const {
    isStreaming: isLoadingResponse,
    progress: profilingProgress,
    statusMessage: profilingStatus,
    result: profilingResult,
    llmAnalysis, // NEW: Streaming LLM text
    isAnalyzing, // NEW: LLM analysis phase indicator
    startStream: startProfilingStream,
  } = useSSEStream({
    onComplete: (finalResult) => {
      console.log("Profiling complete:", finalResult);
      setInitialMessageData([finalResult]);
      setActiveStreamingStep(null); // Clear streaming state when profiling completes
    },
    onError: (errorMsg) => {
      console.error("Profiling error:", errorMsg);
      setActiveStreamingStep(null); // Clear streaming state on error
    },
  });

  useEffect(() => {
    if (!profilingResult) return;
    const current = Array.isArray(initialMessageData) ? initialMessageData[0] : undefined;
    if (hasProfilingToolResult(profilingResult) && !hasProfilingToolResult(current)) {
      setInitialMessageData([profilingResult]);
    }
  }, [profilingResult, initialMessageData]);

  useEffect(() => {
    if (profilingData && hasSentRef.current === false) {
      if (profilingData.successful_uploads?.length > 0 && !activeTab) {
        setActiveTab(profilingData.successful_uploads[0].file_id);
      }
      if (!initialMessageData || initialMessageData.length === 0) {
        hasSentRef.current = true;
        initializeStreamingChat();
      }
    }
  }, [activeTab, initialMessageData, profilingData]);

  useEffect(() => {
    const restoreSession = async () => {
      const appSessionId = getCurrentAppSessionId();
      if (!appSessionId) {
        hydrationCompleteRef.current = true;
        return;
      }
      try {
        const detail = await getAppSessionDetail(appSessionId);
        const profilingRun = detail.profiling_run;
        const resumeState = profilingRun?.resume_state || {};
        if (resumeState.uploadResponse && !routeState?.data) {
          setRestoredProfilingData(resumeState.uploadResponse as ApiResponse);
        }
        if (Object.keys(resumeState).length > 0) {
          if (resumeState.activeTab !== undefined) setActiveTab(resumeState.activeTab);
          if (resumeState.currentStep !== undefined) setCurrentStep(resumeState.currentStep);
          if (resumeState.profileSummaryCollapsed !== undefined) setProfileSummaryCollapsed(resumeState.profileSummaryCollapsed);
          if (resumeState.initialMessageData !== undefined) {
            setInitialMessageData(resumeState.initialMessageData);
            if (Array.isArray(resumeState.initialMessageData) && resumeState.initialMessageData.length > 0) {
              hasSentRef.current = true;
            }
          }
          if (resumeState.relationshipResponse !== undefined) setRelationshipResponse(resumeState.relationshipResponse);
          if (resumeState.prevRelationshipResponse !== undefined) setPrevRelationshipResponse(resumeState.prevRelationshipResponse);
          if (resumeState.dataDictionaryJson !== undefined) setDataDictionaryJson(resumeState.dataDictionaryJson);
          if (resumeState.dataDictionaryResponse !== undefined) setDataDictionaryResponse(resumeState.dataDictionaryResponse);
          if (resumeState.dataDictionaryTableId !== undefined) setDataDictionaryTableId(resumeState.dataDictionaryTableId);
          if (resumeState.prevMetadataResponse !== undefined) setPrevMetadataResponse(resumeState.prevMetadataResponse);
          if (resumeState.anomalyData !== undefined) setAnomalyData(resumeState.anomalyData);
          if (resumeState.modifiedRelationshipResponse !== undefined) setModifiedRelationshipResponse(resumeState.modifiedRelationshipResponse);
          if (resumeState.modifiedAnomalyResponse !== undefined) setModifiedAnomalyResponse(resumeState.modifiedAnomalyResponse);
          if (resumeState.modifiedMetadataResponse !== undefined) setModifiedMetadataResponse(resumeState.modifiedMetadataResponse);
          if (resumeState.similarityResponse !== undefined) setSimilarityResponse(resumeState.similarityResponse);
          if (resumeState.hasReachedStep8 !== undefined) setHasReachedStep8(resumeState.hasReachedStep8);
          if (resumeState.workflowFinished !== undefined) setWorkflowFinished(resumeState.workflowFinished);
          if (resumeState.steps !== undefined) setSteps(resumeState.steps);
        }
      } catch (error) {
        console.error("Failed to restore streaming profiling session:", error);
      } finally {
        hydrationCompleteRef.current = true;
      }
    };

    void restoreSession();
    return onSessionChanged(() => {
      void restoreSession();
    });
  }, []);

  // Similarity check state
  const [similarityResponse, setSimilarityResponse] = useState<string>("");

  // Effect: Populate Redux store with data dictionary data for Reference Suggestion
  useEffect(() => {
    if (dataDictionaryJson) {
      const formattedData = Array.isArray(dataDictionaryJson) ? dataDictionaryJson : [dataDictionaryJson];
      dispatch(setStep3Response({ tool_response: formattedData }));
    }
  }, [dataDictionaryJson, dispatch]);

  // Keep the metadata fill API wired to the latest data dictionary table id.
  useEffect(() => {
    const resolvedId =
      dataDictionaryTableId ||
      resolveDataDictionaryTableId(dataDictionaryJson) ||
      resolveDataDictionaryTableId(dataDictionaryResponse);
    if (resolvedId && resolvedId !== dataDictionaryTableId) {
      setDataDictionaryTableId(resolvedId);
    }
  }, [dataDictionaryJson, dataDictionaryResponse, dataDictionaryTableId]);

  // Update step completion based on data availability
  useEffect(() => {
    const newSteps = steps.map((step) => {
      switch (step.id) {
        case 1:
          return { ...step, completed: !!profilingResult };
        case 2:
          return { ...step, completed: !!relationshipResponse };
        case 3:
          return { ...step, completed: !!dataDictionaryJson };
        case 4:
          return { ...step, completed: !!similarityResponse };
        case 5:
          return { ...step, completed: !!dartSuggestionResponse };
        case 6:
          return { ...step, completed: !!anomalyData };
        case 7:
          return { ...step, completed: !!prevMetadataResponse };
        case 8:
          return { ...step, completed: workflowFinished };
        default:
          return step;
      }
    });
    
    console.log("[StreamingProfilingResult] Step completion update:", {
      profilingResult: !!profilingResult,
      relationshipResponse: !!relationshipResponse,
      dataDictionaryJson: !!dataDictionaryJson,
      similarityResponse: !!similarityResponse,
      dartSuggestionResponse: !!dartSuggestionResponse,
      anomalyData: !!anomalyData,
      prevMetadataResponse: !!prevMetadataResponse,
      currentStep: currentStep,
      newSteps: newSteps.map(s => ({ id: s.id, title: s.title, completed: s.completed }))
    });
    
    setSteps(newSteps);
  }, [
    profilingResult,
    relationshipResponse,
    dataDictionaryJson,
    anomalyData,
    similarityResponse,
    dartSuggestionResponse,
    prevMetadataResponse,
    workflowFinished,
  ]);

  const createMessageRequest = (initialText: string) => ({
    appName: getStoredSession().appName,
    sessionId: getStoredSession().sessionId,
    userId: getStoredSession().userId,
    newMessage: {
      parts: [{ text: initialText }],
      role: "user",
    },
    streaming: true,
    stateDelta: {},
  });

  const addFilesToFormData = (formData: FormData, uploadData: any) => {
    if (Array.isArray(uploadData.dataDictionaryFiles) && uploadData.dataDictionaryFiles.length > 0) {
      uploadData.dataDictionaryFiles.forEach((file: File) => {
        formData.append("data_dict_files", file, file.name);
      });
    } else if (uploadData.dataDictionaryFile) {
      formData.append("data_dict_file", uploadData.dataDictionaryFile);
    }
    if (uploadData.brdFile) formData.append("brd_file", uploadData.brdFile);
    if (uploadData.erwinModelFile) formData.append("file_spec_file", uploadData.erwinModelFile);
  };

  const addFormFieldsToFormData = (formData: FormData, uploadData: any) => {
    const fields = [
      ["project_name", uploadData.projectName],
      ["vendor_name", uploadData.vendorName],
      ["vendor_contact_person", uploadData.contactPerson],
      ["vendor_contact_name", uploadData.contactName],
      ["vendor_phone_number", uploadData.phoneNumber],
      ["vendor_server_name", uploadData.serverName],
      ["file_delivery_frequency", uploadData.deliveryFrequency],
      ["frequency_mode", uploadData.frequencyMode],
      ["transfer_method", uploadData.transferMethod],
      ["file_compression_type", uploadData.compressionType],
      ["file_population_type", uploadData.populationType],
      ["header_record_number", uploadData.headerRecordNumber],
      ["trailer_record_number", uploadData.trailerRecordNumber],
      ["quote_indicator", uploadData.quoteIndicator],
      ["date_timestamp_format", uploadData.dateTimestampFormat],
      ["receive_file_when_no_data", uploadData.receiveFileWhenNoData],
      ["email_notification_dl", uploadData.emailNotificationDl],
      ["assumptions", uploadData.assumptions],
      ["dependencies", uploadData.dependencies],
      ["brd_description", uploadData.brdDescription],
      ["spec_description", uploadData.erwinModelDescription],
      ["database_name", uploadData.databaseName],
      ["metadata_path", uploadData.metadataPath],
    ];

    for (const [key, value] of fields) {
      if (value) formData.append(key, value);
    }
  };

  const initializeStreamingChat = async () => {
    if (!profilingData) return;

    // Set streaming state for initial profiling
    setActiveStreamingStep(1);

    const tableNames = profilingData.successful_uploads.map((f) => {
      const parts = f.table_name.split(".");
      return parts.at(-1) || "";
    });

    const initialText = `Do profiling for the following files : ${tableNames.join(", ")}`;
    const initialUploadData = location.state?.initialUploadData;

    if (initialUploadData) {
      console.log("Found initial upload data, preparing FormData request...");
      const formData = new FormData();

      formData.append(
        "request",
        JSON.stringify(createMessageRequest(initialText)),
      );
      addFilesToFormData(formData, initialUploadData);
      addFormFieldsToFormData(formData, initialUploadData);

      await startProfilingStream(formData);
    } else {
      await startProfilingStream(createMessageRequest(initialText));
    }
  };

  /**
   * Handles retry action for dataset overview step
   * Resets streaming state and reinitializes the profiling process
   */
  const handleDatasetOverviewRetry = () => {
    // Reset streaming state
    setActiveStreamingStep(null);
    
    // Reset initial message data
    setInitialMessageData(undefined);
    
    // Reset step completion
    setSteps((prev) => prev.map((step) => ({ ...step, completed: false })));
    
    // Reset API call tracker
    setStepApiCallTracker(new Set());
    
    // Reset ref to allow re-initialization
    hasSentRef.current = false;
    
    // Reinitialize streaming chat
    setTimeout(() => {
      initializeStreamingChat();
    }, 100);
  };



  // Helper function to get current phase info based on progress (NEW PHASES)
  const getCurrentPhase = (progress: number, isAnalyzingLLM: boolean) => {
    if (progress < 90) {
      return {
        id: 1,
        name: "Tool Execution",
        description:
          "Running profiling tool - analyzing tables in batches with statistical + LLM analysis",
        color: "blue",
      };
    } else if (progress === 90) {
      return {
        id: 2,
        name: "Tool Complete",
        description:
          "Profiling tool finished. Preparing for intelligent analysis...",
        color: "green",
      };
    } else if (isAnalyzingLLM && progress < 100) {
      return {
        id: 3,
        name: "AI Analysis",
        description:
          "Gemini is generating intelligent insights from profiling results",
        color: "purple",
      };
    } else {
      return {
        id: 4,
        name: "Complete",
        description:
          "Analysis complete! Review your intelligent profiling report below",
        color: "green",
      };
    }
  };

  // Helper to get all phases for visual indicators (NEW PHASES)
  const allPhases = [
    { id: 1, name: "Tool Execution", threshold: 0 },
    { id: 2, name: "Tool Complete", threshold: 90 },
    { id: 3, name: "AI Analysis", threshold: 91 },
    { id: 4, name: "Complete", threshold: 100 },
  ];

  const getPhaseTextClass = (isActive: boolean, isCompleted: boolean) => {
    if (isActive) return "text-font-blue font-semibold";
    if (isCompleted) return "text-green-600";
    return "text-gray-500";
  };

  const getPhaseIndicatorClass = (isCompleted: boolean, isActive: boolean) => {
    if (isCompleted) {
      return "bg-green-500 ring-2 ring-green-200";
    }
    if (isActive) {
      return "bg-brand-primary ring-2 ring-teal-200 animate-pulse";
    }
    return "bg-gray-300";
  };

  const handleStepNavigation = (stepId: number) => {
    // Track when user reaches step 8
    if (stepId === 8) {
      setHasReachedStep8(true);
    }
    
    // Allow navigation to completed steps even during streaming
    const targetStep = steps.find((s) => s.id === stepId);
    if (targetStep?.completed) {
      setCurrentStep(stepId);
      return;
    }

    // Only block navigation to non-completed steps if streaming is active
    if (activeStreamingStep && activeStreamingStep !== stepId) {
      console.log(
        `[Navigation] Blocked navigation to step ${stepId} - step ${activeStreamingStep} is streaming`,
      );
      return;
    }
    setCurrentStep(stepId);
  };

  const handleRelationshipUseResponse = (response: any, isModified?: boolean) => {
    console.log("Original response:", response);
    if (isModified) {
      const modifiedResponse = [response];
      console.log("modifiedResponse:", modifiedResponse);
      setModifiedRelationshipResponse(modifiedResponse);
    } else {
      const responseText = response.text_response || response.message || JSON.stringify(response);
      setRelationshipResponse(responseText);
      setPrevRelationshipResponse(responseText);
    }
  };

  const handleAnomalyUseResponse = (response: any, isModified?: boolean) => {
    console.log("handleAnomalyUseResponse called:", { response, isModified });
    if (isModified) {
      const modifiedResponse = [response];
      console.log("Setting modifiedAnomalyResponse:", modifiedResponse);
      setModifiedAnomalyResponse(modifiedResponse);
      const dataToSet = response.tool_response || response;
      console.log("Setting anomalyData:", dataToSet);
      setAnomalyData(dataToSet);
    } else {
      console.log("Setting anomalyData directly:", response);
      setAnomalyData(response);
    }
  };

  const handleMetadataUseResponse = (response: any, isModified?: boolean) => {
    console.log('[DEBUG] handleMetadataUseResponse called:', { response, isModified });
    if (isModified) {
      const modifiedResponse = [response];
      console.log('[DEBUG] Setting modifiedMetadataResponse as array:', modifiedResponse);
      setModifiedMetadataResponse(modifiedResponse);
      setPrevMetadataResponse(JSON.stringify(modifiedResponse));
    } else {
      setPrevMetadataResponse(response);
    }
  };

  const handleStreamingProfilingChatSend = async () => {
    const trimmedInput = profilingChatInput.trim();
    if (!trimmedInput || isSubmittingProfilingChat) return;

    const { sessionId, appName, userId } = getStoredSession();
    setProfilingChatMessages((prev) => [...prev, { role: "user", text: trimmedInput }]);
    setProfilingChatInput("");
    setProfilingChatError("");
    setIsSubmittingProfilingChat(true);

    try {
      const response = await sendStreamingProfilingChatHITLMessage({
        user_id: userId || "",
        session_id: sessionId || "",
        app_name: appName || "",
        user_message: trimmedInput,
      });

      setProfilingChatMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text: response?.text_response || response?.message || "Dataset overview response updated.",
          mode: response?.mode,
          response,
        },
      ]);
    } catch (error) {
      setProfilingChatError(error instanceof Error ? error.message : "Failed to process dataset overview chat.");
    } finally {
      setIsSubmittingProfilingChat(false);
    }
  };

  const handleStreamingProfilingChatKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleStreamingProfilingChatSend();
    }
  };

  const applyStreamingProfilingChatResponse = (response: any) => {
    if (!response) return;
    setInitialMessageData([
      {
        text_response: response.text_response,
        tool_response: response.tool_response,
        should_update: false,
      },
    ]);
    setIsProfilingChatOpen(false);
  };

  const openChatModal = (stepNumber: number) => {
    setChatOpenStates(prev => ({ ...prev, [stepNumber]: true }));
  };

  const closeChatModal = (stepNumber: number) => {
    setChatOpenStates(prev => ({ ...prev, [stepNumber]: false }));
  };

  const handleFinishWorkflow = useCallback(async () => {
    if (workflowFinished) {
      window.scrollTo({ top: 0, behavior: "smooth" });
      setShowDropdown(true);
      return;
    }

    setHasReachedStep8(true);
    setWorkflowFinished(true);
    setSteps((prev) =>
      prev.map((step) => (step.id === 8 ? { ...step, completed: true } : step)),
    );
    window.scrollTo({ top: 0, behavior: "smooth" });
    globalThis.setTimeout(() => setShowDropdown(true), 400);

    const appSessionId = getCurrentAppSessionId();
    if (!appSessionId || !profilingData) return;

    try {
      await saveProfilingResumeState(appSessionId, {
        status: "COMPLETED",
        current_step: "step_8",
        resume_state: {
          profilingMode: "streaming",
          uploadResponse: profilingData,
          activeTab,
          currentStep: 8,
          profileSummaryCollapsed,
          initialMessageData,
          relationshipResponse,
          prevRelationshipResponse,
          dataDictionaryJson,
          dataDictionaryResponse,
          dataDictionaryTableId,
          similarityResponse,
          hasReachedStep8: true,
          workflowFinished: true,
          anomalyData,
          prevMetadataResponse,
          modifiedRelationshipResponse,
          modifiedAnomalyResponse,
          modifiedMetadataResponse,
          steps: steps.map((step) =>
            step.id === 8 ? { ...step, completed: true } : step,
          ),
        },
      });
    } catch (error) {
      console.error("Failed to save completed streaming profiling state:", error);
    }
  }, [
    workflowFinished,
    profilingData,
    activeTab,
    profileSummaryCollapsed,
    initialMessageData,
    relationshipResponse,
    prevRelationshipResponse,
    dataDictionaryJson,
    dataDictionaryResponse,
    dataDictionaryTableId,
    similarityResponse,
    anomalyData,
    prevMetadataResponse,
    modifiedRelationshipResponse,
    modifiedAnomalyResponse,
    modifiedMetadataResponse,
    steps,
  ]);

  const canProceedToStep = (stepId: number) => {
    // Allow navigation to any completed step, regardless of current streaming state
    const targetStep = steps.find((s) => s.id === stepId);
    if (targetStep?.completed) {
      return true;
    }

    // Original logic for non-completed steps
    switch (stepId) {
      case 1:
        return true;
      case 2:
        return steps[0].completed;
      case 3:
        return steps[1].completed;
      case 4:
        return steps[2].completed;
      case 5:
        return steps[3].completed;
      case 6:
        return steps[4].completed;
      case 7:
        return steps[5].completed;
      case 8:
        return steps[0].completed; // Can access Detailed Profiling after Dataset Overview completes
      default:
        return false;
    }
  };

  useEffect(() => {
    if (!hydrationCompleteRef.current || !profilingData) return;
    const appSessionId = getCurrentAppSessionId();
    if (!appSessionId) return;

    const timeout = globalThis.setTimeout(() => {
      void saveProfilingResumeState(appSessionId, {
        status: workflowFinished || currentStep >= 8 ? "COMPLETED" : "READY",
        current_step: `step_${currentStep}`,
        resume_state: {
          profilingMode: "streaming",
          uploadResponse: profilingData,
          activeTab,
          currentStep,
          profileSummaryCollapsed,
          initialMessageData,
          relationshipResponse,
          prevRelationshipResponse,
          dataDictionaryJson,
          dataDictionaryResponse,
          dataDictionaryTableId,
          similarityResponse,
          hasReachedStep8,
          workflowFinished,
          anomalyData,
          prevMetadataResponse,
          modifiedRelationshipResponse,
          modifiedAnomalyResponse,
          modifiedMetadataResponse,
          steps,
        },
      }).catch((error) => {
        console.error("Failed to save streaming profiling resume state:", error);
      });
    }, 800);

    return () => globalThis.clearTimeout(timeout);
  }, [
    profilingData,
    activeTab,
    currentStep,
    profileSummaryCollapsed,
    initialMessageData,
    relationshipResponse,
    prevRelationshipResponse,
    dataDictionaryJson,
    dataDictionaryResponse,
    dataDictionaryTableId,
    similarityResponse,
    hasReachedStep8,
    workflowFinished,
    anomalyData,
    prevMetadataResponse,
    modifiedRelationshipResponse,
    modifiedAnomalyResponse,
    modifiedMetadataResponse,
    steps,
  ]);

  if (!profilingData) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-brand-primary"></div>
        <div className="text-center text-font-blue mt-4">
          Loading profiling results...
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="w-full mb-6">
        <div className="flex items-center justify-between mb-3">
          <h1 className="text-xl text-brand-blue mb-2 mt-2">
            Data Profiling Pipeline (Streaming)
          </h1>
          <div className="flex items-center gap-3">
            {workflowFinished ? (
              <button
                type="button"
                onClick={handleContinueToMapping}
                className="bg-brand-blue hover:bg-brand-blue/75 text-white px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2 transition-colors"
              >
                Continue to Mapping <ChevronRight size={16} />
              </button>
            ) : null}
            {shouldShowButtons && (
            <div className="relative" ref={dropdownRef}>
              <button
                className="flex items-center gap-1.5 px-3 py-1.5 bg-brand-darkblue hover:bg-brand-blue text-white rounded-lg text-xs font-medium transition-colors"
                onClick={() => setShowDropdown(!showDropdown)}
              >
                <Download size={13} />
                All Downloads
                <svg className={`w-3 h-3 transition-transform ${showDropdown ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </button>
              {showDropdown && (
                <div className="absolute right-0 mt-1 w-56 bg-white border border-gray-200 rounded-lg shadow-lg z-10">
                  <button
                    onClick={() => {
                      console.log('[StreamingProfilingResult] Uploaded Files button clicked');
                      handleDownloadUploadedFiles();
                      setShowDropdown(false);
                    }}
                    disabled={isDownloadingFiles}
                    className="w-full text-left px-4 py-2 text-xs hover:bg-gray-50 flex items-center gap-2 border-b border-gray-100 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {isDownloadingFiles ? (
                      <div className="animate-spin rounded-full h-3 w-3 border-b-2 border-brand-darkblue"></div>
                    ) : (
                      <Download size={12} className="text-brand-darkblue" />
                    )}
                    {isDownloadingFiles ? 'Loading...' : 'Uploaded Files'}
                  </button>
                  <button
                    onClick={() => {
                      console.log('[StreamingProfilingResult] Data Dictionary CSV button clicked');
                      handleDownloadDataDictionaryCSV();
                      setShowDropdown(false);
                    }}
                    className="w-full text-left px-4 py-2 text-xs hover:bg-gray-50 flex items-center gap-2 border-b border-gray-100"
                  >
                    <Download size={12} className="text-font-blue" />
                    Data Dictionary (CSV)
                  </button>
                  <button
                    onClick={() => {
                      console.log('[StreamingProfilingResult] Metadata Template Excel button clicked');
                      console.log('[StreamingProfilingResult] metadataExportRef.current:', metadataExportRef.current);
                      if (metadataExportRef.current) {
                        try {
                          metadataExportRef.current();
                        } catch (error) {
                          console.error('[StreamingProfilingResult] Error calling metadata export:', error);
                          alert('Failed to export Metadata Template. Please try again.');
                        }
                      } else {
                        console.log('[StreamingProfilingResult] No metadata export function available');
                        alert('Metadata Template export not available. Please complete Step 7: Metadata Template first.');
                      }
                      setShowDropdown(false);
                    }}
                    className="w-full text-left px-4 py-2 text-xs hover:bg-gray-50 flex items-center gap-2 border-b border-gray-100"
                  >
                    <Download size={12} className="text-font-blue" />
                    Metadata Template (Excel)
                  </button>

                  <button
                    onClick={() => {
                      console.log('[StreamingProfilingResult] Detailed Profiling Excel button clicked');
                      console.log('[StreamingProfilingResult] detailedProfilingExportRef.current:', detailedProfilingExportRef.current);
                      if (detailedProfilingExportRef.current) {
                        try {
                          detailedProfilingExportRef.current();
                        } catch (error) {
                          console.error('[StreamingProfilingResult] Error calling detailed profiling export:', error);
                          alert('Failed to export Detailed Profiling. Please try again.');
                        }
                      } else {
                        console.log('[StreamingProfilingResult] No detailed profiling export function available');
                        alert('Detailed Profiling export not available. Please complete Step 8: Detailed Profiling first.');
                      }
                      setShowDropdown(false);
                    }}
                    className="w-full text-left px-4 py-2 text-xs hover:bg-gray-50 flex items-center gap-2"
                  >
                    <Download size={12} className="text-green-600" />
                    Detailed Profiling (Excel)
                  </button>
                </div>
              )}
            </div>
            )}
          </div>
        </div>

        {workflowFinished ? (
          <div className="mb-4 rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">
            Profiling is complete. Continue to the Mapping module to build source-to-target mappings.
          </div>
        ) : requiredStepsComplete && !hasReachedStep8 ? (
          <div className="mb-4 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-900">
            Core profiling steps are complete. Open Step 8 (Detailed Profiling), then click{" "}
            <strong>Finish Profiling</strong> to unlock Mapping.
          </div>
        ) : null}

        <div className="flex bg-white rounded-lg border-font-dark/25 border">
          {/* Sidebar with Stepper */}
          <div className="w-60 bg-white overflow-y-auto rounded-l-lg">
            <div className="p-4">
              <p className="text-xs text-font-dark mb-6">
                {profilingData.summary.successful} files •{" "}
                {profilingData.summary.total_rows_uploaded.toLocaleString()}{" "}
                rows
              </p>

              {/* Streaming Status Banner */}
              {activeStreamingStep && (
                <div className="mb-4 p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
                  <div className="flex items-center gap-2 mb-1">
                    <div className="w-2 h-2 bg-yellow-500 rounded-full animate-pulse"></div>
                    <span className="text-xs font-medium text-yellow-800">
                      Step {activeStreamingStep} Processing
                    </span>
                  </div>
                  <p className="text-xs text-yellow-700">
                    Navigation limited while streaming
                  </p>
                </div>
              )}

              <div className="space-y-6 relative">
                {steps.map((step, index) => (
                  <div key={step.id} className="relative">
                    {index < steps.length - 1 && (
                      <div className="absolute left-4 top-8 w-px h-full bg-brand-light"></div>
                    )}

                    <StepIndicator
                      step={step}
                      isActive={currentStep === step.id}
                      isClickable={
                        canProceedToStep(step.id) &&
                        (!activeStreamingStep ||
                          activeStreamingStep === step.id ||
                          step.completed)
                      }
                      onStepClick={handleStepNavigation}
                    />
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Main Content */}
          <div className="flex-1 overflow-hidden bg-brand-light">
            <div className="h-full p-6 overflow-y-auto">
              {/* Step 1: Dataset Overview */}
              {currentStep === 1 && (
                <div className="space-y-6">
                  <div className="flex items-center justify-between">
                    <h2 className="text-xl text-brand-blue">Dataset Overview</h2>
                    <button
                      type="button"
                      className="p-2 rounded-full hover:bg-gray-100 transition-colors cursor-pointer flex items-center gap-1 disabled:cursor-not-allowed disabled:opacity-50"
                      onClick={() => setIsProfilingChatOpen(true)}
                      disabled={!initialMessageData || isLoadingResponse}
                      aria-label="Chat about Dataset Overview"
                    >
                      <Bot size={20} className="text-gray-500 hover:text-brand-blue" />
                      <span>Chat</span>
                    </button>
                  </div>

                  {/* Dataset Tabs */}
                  <Tabs
                    value={activeTab}
                    onValueChange={(val) => setActiveTab(val)}
                    className="mb-6"
                  >
                    <TabsList className="gap-2 overflow-x-auto">
                      {profilingData.successful_uploads.map((file) => (
                        <TabsTrigger
                          key={file.file_id}
                          value={file.file_id}
                          className={`flex items-center text-sm transition-colors cursor-pointer ${activeTab === file.file_id
                              ? "text-brand-blue"
                              : "hover:bg-gray-100"
                            }`}
                        >
                          <FileSpreadsheet size={16} /> {file.filename}
                        </TabsTrigger>
                      ))}
                    </TabsList>

                    {profilingData.successful_uploads.map((file) => (
                      <TabsContent
                        key={file.file_id}
                        value={file.file_id}
                        className="flex-col flex-1 border border-gray-300 overflow-hidden bg-white rounded-b-lg"
                      >
                        <div className="text-sm text-brand-charcoal p-4 pb-0">
                          <p>
                            <strong>Table:</strong> {file.table_name}
                          </p>
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm text-brand-charcoal p-4 pt-2">

                          <div className="space-y-2">
                            <p>
                              <strong>Session ID:</strong> {file.sessionID}
                            </p>
                            <p>
                              <strong>Dataset:</strong> {file.dataset_id}
                            </p>

                            {/* <p>
                              <strong>Created:</strong>{" "}
                              {formatDate(file.createdDate)}
                            </p>
                            <p>
                              <strong>Last Updated:</strong>{" "}
                              {formatDate(file.lastUpdateDate)}
                            </p> */}
                          </div>
                          <div className="space-y-2">
                            <p>
                              <strong>User:</strong> {file.user}
                            </p>

                            <p>
                              <strong>Project:</strong> {file.project_id}
                            </p>
                            {/* <p>
                              <strong>Rows Uploaded:</strong>{" "}
                              {file.rows_uploaded.toLocaleString()}
                            </p> */}
                          </div>
                        </div>
                      </TabsContent>
                    ))}
                  </Tabs>

                  {/* Profiling Summary - Collapsible */}
                  <div className="bg-brand-blue/5 border border-brand-blue/50 rounded-lg mb-6 p-4">
                    <button
                      className="w-full cursor-pointer flex justify-between items-center bg-transparent border-none p-0 text-left cursor-pointer"
                      onClick={() =>
                        setProfileSummaryCollapsed(!profileSummaryCollapsed)
                      }
                    >
                      <h3 className="text-md text-brand-blue">
                        Profiling Summary
                      </h3>
                      {profileSummaryCollapsed ? (
                        <ChevronDown size={20} />
                      ) : (
                        <ChevronUp size={20} />
                      )}
                    </button>

                    {!profileSummaryCollapsed && (
                      <div className="profile-summary pt-2">
                        {isLoadingResponse ? (
                          <div className="space-y-5">
                            {/* Current Phase Header */}
                            <div className="bg-gradient-to-r from-brand-surface to-teal-50 border border-teal-200 rounded-lg p-4">
                              <div className="flex items-start gap-3">
                                <div className="flex-1">
                                  <div className="flex items-center justify-between mb-1">
                                    <h4 className="text-sm font-semibold text-gray-900">
                                      Phase{" "}
                                      {
                                        getCurrentPhase(
                                          profilingProgress,
                                          isAnalyzing,
                                        ).id
                                      }
                                      :{" "}
                                      {
                                        getCurrentPhase(
                                          profilingProgress,
                                          isAnalyzing,
                                        ).name
                                      }
                                    </h4>
                                    <span className="text-xs font-medium text-font-blue bg-teal-100 px-2 py-1 rounded">
                                      {profilingProgress.toFixed(1)}%
                                    </span>
                                  </div>
                                  <p className="text-xs text-gray-600 mb-3">
                                    {
                                      getCurrentPhase(
                                        profilingProgress,
                                        isAnalyzing,
                                      ).description
                                    }
                                  </p>

                                  {/* Status message */}
                                  {profilingStatus && (
                                    <div className="bg-white/80 rounded px-2 py-1.5 border border-gray-200">
                                      <p className="text-xs text-gray-700">
                                        {profilingStatus}
                                      </p>
                                    </div>
                                  )}
                                </div>
                              </div>
                            </div>

                            {/* NEW: LLM Token Streaming Display */}
                            {isAnalyzing && llmAnalysis && (
                              <div className="bg-brand-surface border border-teal-200 rounded-lg p-4">
                                <div className="flex items-center gap-2 mb-2">
                                  <h5 className="text-sm font-semibold text-brand-darkblue">
                                    AI Analysis Preview
                                  </h5>
                                  <div className="flex-1 flex justify-end">
                                    <div className="animate-pulse flex gap-1">
                                      <div className="w-2 h-2 bg-brand-primary rounded-full"></div>
                                      <div className="w-2 h-2 bg-brand-primary rounded-full animation-delay-200"></div>
                                      <div className="w-2 h-2 bg-brand-primary rounded-full animation-delay-400"></div>
                                    </div>
                                  </div>
                                </div>
                                <div className="bg-white rounded px-3 py-2 max-h-48 overflow-y-auto">
                                  <div className="text-xs text-gray-800 prose max-w-none">
                                    <Markdown remarkPlugins={[remarkGfm]}>
                                      {llmAnalysis}
                                    </Markdown>
                                  </div>
                                </div>
                                <p className="text-xs text-font-blue mt-2">
                                  ✨ Streaming tokens in real-time from
                                  Gemini...
                                </p>
                              </div>
                            )}

                            {/* Progress Bar */}
                            <div className="space-y-2">
                              <div className="w-full bg-gray-200 rounded-full h-3 overflow-hidden">
                                <div
                                  className="bg-gradient-to-r from-brand-primary to-brand-primary-hover h-3 rounded-full transition-all duration-500 ease-out relative"
                                  style={{ width: `${profilingProgress}%` }}
                                >
                                  <div className="absolute inset-0 bg-white/20 animate-pulse"></div>
                                </div>
                              </div>
                            </div>

                            {/* Phase Indicators - NEW 4-PHASE SYSTEM */}
                            <div className="flex items-center justify-between gap-2 px-1">
                              {allPhases.map((phase, index) => {
                                // Determine phase state based on new thresholds
                                let isCompleted = false;
                                let isActive = false;

                                if (phase.threshold === 0) {
                                  // Tool Execution (0-90%)
                                  isCompleted = profilingProgress >= 90;
                                  isActive =
                                    profilingProgress < 90 && !isCompleted;
                                } else if (phase.threshold === 90) {
                                  // Tool Complete (90%)
                                  isCompleted = profilingProgress > 90;
                                  isActive = profilingProgress === 90;
                                } else if (phase.threshold === 91) {
                                  // AI Analysis (91-100%)
                                  isCompleted = profilingProgress === 100;
                                  isActive =
                                    isAnalyzing &&
                                    profilingProgress >= 91 &&
                                    profilingProgress < 100;
                                } else if (phase.threshold === 100) {
                                  // Complete (100%)
                                  isCompleted =
                                    profilingProgress === 100 && !isAnalyzing;
                                }

                                return (
                                  <div
                                    key={phase.id}
                                    className="flex flex-col items-center flex-1"
                                  >
                                    <div className="flex items-center w-full mb-2">
                                      {index > 0 && (
                                        <div
                                          className={`flex-1 h-0.5 ${isCompleted || isActive ? "bg-brand-primary" : "bg-gray-300"} transition-colors duration-300`}
                                        ></div>
                                      )}
                                      <div
                                        className={`w-4 h-4 rounded-full flex items-center justify-center transition-all duration-300 ${getPhaseIndicatorClass(isCompleted, isActive)}`}
                                        title={phase.name}
                                      ></div>
                                      {index < allPhases.length - 1 && (
                                        <div
                                          className={`flex-1 h-0.5 ${isCompleted ? "bg-brand-primary" : "bg-gray-300"} transition-colors duration-300`}
                                        ></div>
                                      )}
                                    </div>
                                    <span
                                      className={`text-[10px] text-center leading-tight ${getPhaseTextClass(isActive, isCompleted)}`}
                                    >
                                      {phase.name.split(" ").map((word, i) => (
                                        <PhaseNameWord key={i} word={word} />
                                      ))}
                                    </span>
                                  </div>
                                );
                              })}
                            </div>

                            {/* Processing Info */}
                            <div className="flex items-center justify-center gap-2 text-xs text-gray-500">
                              <div className="animate-spin rounded-full h-3 w-3 border-b-2 border-brand-primary"></div>
                              <span>Processing in progress...</span>
                            </div>
                          </div>
                        ) : (
                          <div className="text-sm text-gray-700 prose max-w-none">
                            {initialMessageData &&
                              initialMessageData.length > 0 ? (
                              (() => {
                                // Backend sends: { tool_response: { all_tables: [...] }, text_response: "..." }
                                const profilingToolData =
                                  initialMessageData[0]?.tool_response
                                    ?.all_tables || [];
                                const textResponse =
                                  initialMessageData[0]?.text_response;

                                // Display tool response tables if available
                                const hasToolData =
                                  profilingToolData &&
                                  Array.isArray(profilingToolData) &&
                                  profilingToolData.length > 0;
                                const hasTextResponse =
                                  textResponse &&
                                  typeof textResponse === "string" &&
                                  textResponse.trim().length > 0;

                                if (!hasToolData && !hasTextResponse) {
                                  return (
                                    <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-6 text-center">
                                      <h3 className="text-lg text-yellow-700 mb-2">
                                        No Profiling Data
                                      </h3>
                                      <p className="text-yellow-600">
                                        No profiling data available to display.
                                      </p>
                                    </div>
                                  );
                                }

                                return (
                                  <div className="space-y-6">
                                    {/* Tool response tables (if available) */}
                                    {hasToolData &&
                                      profilingToolData.map(
                                        (tableItem: any, index: number) => (
                                          <TableItemDisplay
                                            key={`table-${tableItem.table_reference || tableItem.table_name || index}`}
                                            tableItem={tableItem}
                                            index={index}
                                            accordionStates={accordionStates}
                                            toggleAccordion={toggleAccordion}
                                          />
                                        ),
                                      )}

                                    {/* LLM text response (if available) */}
                                    {hasTextResponse && (
                                      <div className="prose-headings:text-brand-blue prose-h1:text-lg prose-h2:text-base prose-h3:text-sm prose-p:text-gray-700 prose-strong:text-gray-900 prose-ul:list-disc prose-ol:list-decimal prose-li:text-gray-700 prose-code:bg-gray-100 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-pre:bg-gray-50 prose-pre:border prose-pre:p-3 prose-pre:rounded-lg prose-blockquote:border-l-4 prose-blockquote:border-brand-blue prose-blockquote:pl-4 prose-blockquote:italic markdown-content">
                                        <Markdown
                                          remarkPlugins={[remarkGfm]}
                                          components={{
                                            table: (props) => (
                                              <TableWithExportWrapper
                                                {...props}
                                                stepTitle={
                                                  steps.find(
                                                    (s) => s.id === currentStep,
                                                  )?.title
                                                }
                                              />
                                            ),
                                          }}
                                        >
                                          {textResponse}
                                        </Markdown>
                                      </div>
                                    )}
                                  </div>
                                );
                              })()
                            ) : (
                              <div className="bg-gray-50 border border-gray-200 rounded-lg p-6 text-center">
                                <h3 className="text-lg text-gray-600 mb-2">
                                  No Analysis Data
                                </h3>
                                <p className="text-gray-500">
                                  No analysis data available to display.
                                </p>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </div>

                  {steps[0].completed && (
                    <div className="flex justify-end mt-6">
                      <button
                        onClick={() => setCurrentStep(2)}
                        className="bg-brand-blue hover:bg-brand-blue/75 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors cursor-pointer text"
                      >
                        Next: Relationship Analysis <ChevronRight size={16} />
                      </button>
                    </div>
                  )}

                  {/* Navigation Buttons */}
                  {/* Floating Retry Button */}
                  {isScrolled && initialMessageData && !isLoadingResponse && (
                    <button
                      onClick={handleDatasetOverviewRetry}
                      className="fixed bottom-6 right-6 bg-brand-blue hover:bg-brand-blue/75 text-white px-3 py-1 rounded text-sm font-medium transition-colors cursor-pointer shadow-lg z-50"
                    >
                      Retry
                    </button>
                  )}
                </div>
              )}

              {/* Step 2: Relationship Analysis */}
              {currentStep === 2 && (
                <div className="space-y-6">
                  <div className="flex items-center justify-between">
                    <h2 className="text-xl text-brand-blue">
                      Relationship Analysis
                    </h2>
                    <button
                      type="button"
                      className="p-2 rounded-full hover:bg-gray-100 transition-colors cursor-pointer flex items-center gap-1"
                      onClick={() => openChatModal(2)}
                      aria-label="Bot info for Relationship Analysis"
                    >
                      <Bot size={20} className="text-gray-500 hover:text-brand-blue" />
                      <span>Chat</span>
                    </button>
                  </div>
                  <div className="bg-white rounded-lg border border-gray-300 p-4">
                    {initialMessageData && (
                      <StreamingRelationshipView
                        profilingData={initialMessageData[0]?.tool_response}
                        updateRelationshipAnalysisStatus={
                          setRelationshipResponse
                        }
                        setRelationshipAnalysisResponse={
                          setPrevRelationshipResponse
                        }
                        prevRelationshipAnalysisResponse={
                          prevRelationshipResponse
                        }
                        hasApiBeenCalled={stepApiCallTracker.has(2)}
                        markApiCalled={() =>
                          setStepApiCallTracker((prev) => new Set([...prev, 2]))
                        }
                        onStreamingStart={() => setActiveStreamingStep(2)}
                        onStreamingEnd={() => setActiveStreamingStep(null)}
                        modifiedResponse={modifiedRelationshipResponse}
                      />
                    )}

                    {steps[1].completed && (
                      <div className="flex justify-between mt-6">
                        <button
                          onClick={() => setCurrentStep(1)}
                          className="bg-gray-500 hover:bg-gray-600 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors"
                        >
                          Previous: Dataset Overview
                        </button>
                        <button
                          onClick={() => setCurrentStep(3)}
                          className="bg-brand-blue hover:bg-brand-blue/75 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors cursor-pointer text"
                        >
                          Next: Data Dictionary <ChevronRight size={16} />
                        </button>
                      </div>
                    )}
                  </div>

                  <ChatModal
                    isOpen={chatOpenStates[2] || false}
                    onClose={() => closeChatModal(2)}
                    stepTitle="Relationship Analysis"
                    stepNumber={2}
                    onUseResponse={handleRelationshipUseResponse}
                  />
                </div>
              )}

              {/* Step 3: Data Dictionary */}
              {currentStep === 3 && (
                <div className="space-y-6">
                  <h2 className="text-xl text-brand-blue">Data Dictionary</h2>
                  <div className="bg-white rounded-lg border border-gray-300 p-4">
                    {initialMessageData && relationshipResponse && (
                      <StreamingDataDictionaryView
                        profilingData={initialMessageData[0]?.tool_response}
                        relationshipAnalysis={relationshipResponse}
                        setDataDictionaryJson={setDataDictionaryJson}
                        setDataDictionaryResponse={setDataDictionaryResponse}
                        onDataDictionaryTableId={setDataDictionaryTableId}
                        dataDictionaryTableId={dataDictionaryTableId}
                        prevResponse={dataDictionaryResponse}
                        hasApiBeenCalled={stepApiCallTracker.has(3)}
                        markApiCalled={() =>
                          setStepApiCallTracker((prev) => new Set([...prev, 3]))
                        }
                        onStreamingStart={() => setActiveStreamingStep(3)}
                        onStreamingEnd={() => setActiveStreamingStep(null)}
                      />
                    )}

                    {steps[2].completed && (
                      <div className="flex justify-between mt-6">
                        <button
                          onClick={() => setCurrentStep(2)}
                          className="bg-gray-500 hover:bg-gray-600 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors"
                        >
                          Previous: Relationship Analysis
                        </button>
                        <button
                          onClick={() => {
                            // Call the export function from Data Dictionary
                            if (dataDictionaryJson) {
                              alert(
                                "Data Dictionary added successfully to profiling table",
                              );
                            }
                            setCurrentStep(4);
                          }}
                          className="bg-brand-blue hover:bg-brand-blue/75 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors cursor-pointer text"
                        >
                          Next: Similarity Check <ChevronRight size={16} />
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Step 4: Similarity Check */}
              {currentStep === 4 && (
                <div className="space-y-6">
                  <h2 className="text-xl text-brand-blue">Similarity Check</h2>
                  <div className="bg-white rounded-lg border border-gray-300 p-4">
                    {initialMessageData && (() => {
                      const databaseName = profilingData.successful_uploads[0]?.dataset_id || "";
                      console.log('[StreamingProfilingResult] propDatabaseName being passed:', databaseName);
                      return (
                        <StreamingSimilarityView
                          sourceTables={profilingData.successful_uploads
                            .map((f) => f.table_name.split(".").pop() || "")
                            .join(", ")}
                          databaseName={databaseName}
                          onComplete={(response) =>
                            setSimilarityResponse(response)
                          }
                        />
                      );
                    })()}

                    {/* Navigation - Always visible */}
                    <div className="flex justify-between mt-6">
                      <button
                        onClick={() => setCurrentStep(3)}
                        className="bg-gray-500 hover:bg-gray-600 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors"
                      >
                        Previous: Data Dictionary
                      </button>

                      <div className="flex gap-3">
                        {/* Skip button - only show if not completed */}
                        {!steps[3].completed && (
                          <button
                            onClick={() => {
                              setSimilarityResponse("skipped");
                              setCurrentStep(5);
                            }}
                            className="bg-yellow-600 hover:bg-yellow-700 text-white px-6 py-2 rounded-lg transition-colors"
                          >
                            Skip Similarity Check
                          </button>
                        )}

                        {/* Next button - only show if completed */}
                        {steps[3].completed && (
                          <button
                            onClick={() => setCurrentStep(5)}
                            className="bg-brand-blue hover:bg-brand-blue/75 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors cursor-pointer text"
                          >
                            Next: Reference Suggestion <ChevronRight size={16} />
                          </button>
                        )}
                      </div>
                    </div>
                  </div>


                </div>
              )}

              {/* Step 5: Reference Suggestion */}
              {currentStep === 5 && (
                <div className="space-y-6">
                  <h2 className="text-xl text-brand-blue">Reference Suggestion</h2>
                  <div className="bg-white rounded-lg border border-gray-300 p-4">
                    <DartSuggestion
                      onPrevious={() => setCurrentStep(4)}
                      onNext={() => setCurrentStep(6)}
                      onRetry={() => { }}
                    />
                  </div>
                </div>
              )}

              {/* Step 6: Data Anomaly Analysis */}
              {currentStep === 6 && (
                <div className="space-y-6">
                  <div className="flex items-center justify-between">
                    <h2 className="text-xl text-brand-blue">
                      Data Anomaly Analysis
                    </h2>
                    <button
                      type="button"
                      className="p-2 rounded-full hover:bg-gray-100 transition-colors cursor-pointer flex items-center gap-1"
                      onClick={() => openChatModal(6)}
                      aria-label="Bot info for Data Anomaly Analysis"
                    >
                      <Bot size={20} className="text-gray-500 hover:text-brand-blue" />
                      <span>Chat</span>
                    </button>
                  </div>
                  <div className="bg-white rounded-lg border border-gray-300 p-4">
                    {isStreamingAnomalyEnabled ? (
                      <StreamingDataAnomalyView
                        setAnomalyData={setAnomalyData}
                        anomalyData={anomalyData}
                        prevResponse={anomalyData?.text_response}
                        isStreamingEnabled={isStreamingAnomalyEnabled}
                        sourceTables={profilingData.successful_uploads
                          .map((f) => f.table_name.split(".").pop() || "")
                          .filter(Boolean)
                          .join(", ")}
                        hasApiBeenCalled={stepApiCallTracker.has(6)}
                        markApiCalled={() =>
                          setStepApiCallTracker((prev) => new Set([...prev, 6]))
                        }
                        onStreamingStart={() => setActiveStreamingStep(6)}
                        onStreamingEnd={() => setActiveStreamingStep(null)}
                        modifiedResponse={modifiedAnomalyResponse}
                      />
                    ) : (
                      <DataAnomalyAnalyzer
                        setAnomalyData={setAnomalyData}
                        anomalyData={anomalyData}
                      />
                    )}

                    {steps[5].completed && (
                      <div className="flex justify-between mt-6">
                        <button
                          onClick={() => setCurrentStep(5)}
                          className="bg-gray-500 hover:bg-gray-600 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors"
                        >
                          Previous: Reference Suggestion
                        </button>
                        <button
                          onClick={() => setCurrentStep(7)}
                          className="bg-brand-blue hover:bg-brand-blue/75 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors cursor-pointer text"
                        >
                          Next: Metadata Template <ChevronRight size={16} />
                        </button>
                      </div>
                    )}
                  </div>

                  <ChatModal
                    isOpen={chatOpenStates[6] || false}
                    onClose={() => closeChatModal(6)}
                    stepTitle="Data Anomaly Analysis"
                    stepNumber={6}
                    onUseResponse={handleAnomalyUseResponse}
                  />
                </div>
              )}

              {/* Step 7: Metadata Template */}
              {currentStep === 7 && (
                <div className="space-y-6">
                  <div className="flex items-center justify-between">
                    <h2 className="text-xl text-brand-blue">Metadata Template</h2>
                    <button
                      type="button"
                      className="p-2 rounded-full hover:bg-gray-100 transition-colors cursor-pointer flex items-center gap-1"
                      onClick={() => openChatModal(7)}
                      aria-label="Bot info for Metadata Template"
                    >
                      <Bot size={20} className="text-gray-500 hover:text-brand-blue" />
                      <span>Chat</span>
                    </button>
                  </div>
                  <div className="bg-white rounded-lg border border-gray-300 p-4">
                      <MetadataTemplate
                        profilingData={profilingData}
                        setMetadataTemplateResponse={(r) =>
                          setPrevMetadataResponse(r)
                        }
                        metadataResponse={prevMetadataResponse}
                        dataDictionaryTableId={dataDictionaryTableId}
                        dataDictionaryJson={dataDictionaryJson}
                        hasApiBeenCalled={stepApiCallTracker.has(7)}
                        markApiCalled={() =>
                          setStepApiCallTracker((prev) => new Set([...prev, 7]))
                        }
                        modifiedResponse={modifiedMetadataResponse}
                        exportRef={metadataExportRef}
                      />

                    <div className="flex justify-between mt-6">
                      <button
                        onClick={() => setCurrentStep(6)}
                        className="bg-gray-500 hover:bg-gray-600 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors"
                      >
                        Previous: Data Anomaly Analysis
                      </button>
                      {(steps[7].completed || !!prevMetadataResponse) && (
                        <button
                          onClick={() => {
                            setHasReachedStep8(true);
                            setCurrentStep(8);
                          }}
                          className="bg-brand-blue hover:bg-brand-blue/75 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors"
                        >
                          Next: Detailed Profiling <ChevronRight size={16} />
                        </button>
                      )}
                    </div>
                  </div>

                  <ChatModal
                    isOpen={chatOpenStates[7] || false}
                    onClose={() => closeChatModal(7)}
                    stepTitle="Metadata Template"
                    stepNumber={7}
                    onUseResponse={handleMetadataUseResponse}
                  />
                </div>
              )}

              {/* Step 8: Detailed Profiling */}
              {currentStep === 8 && (
                <div className="space-y-6">
                  <div className="flex items-center justify-between">
                    <h2 className="text-xl text-brand-blue">
                      Detailed Table Profiling
                    </h2>
                    <button
                      type="button"
                      className="p-2 rounded-full hover:bg-gray-100 transition-colors cursor-pointer flex items-center gap-1"
                      onClick={() => openChatModal(8)}
                      aria-label="Bot info for Detailed Table Profiling"
                    >
                      <Bot size={20} className="text-gray-500 hover:text-brand-blue" />
                      <span>Chat</span>
                    </button>
                  </div>
                  <div className="bg-white rounded-lg border border-gray-300 p-4">
                    {(() => {
                      const hasInitialData =
                        initialMessageData && initialMessageData.length > 0;
                      const resolvedDataDictionary = resolveDataDictionaryPayload(
                        dataDictionaryJson,
                        {
                          resultData: dataDictionaryJson,
                          json: dataDictionaryJson,
                        },
                      );
                      const hasDataDictionary = !!resolvedDataDictionary;

                      if (hasInitialData && hasDataDictionary) {
                        return (
                          <StreamingTableProfilingDisplay
                            profilingData={initialMessageData}
                            uploadData={profilingData}
                            dataDictionary={resolvedDataDictionary}
                            anomalyData={anomalyData}
                            similarityData={similarityResponse}
                            relationshipData={relationshipResponse}
                            profilingSession={profilingSession}
                            isStep4Skipped={false}
                            exportRef={detailedProfilingExportRef}
                          />
                        );
                      }

                      if (!hasInitialData) {
                        return (
                          <div className="bg-red-50 border border-red-200 rounded-lg p-6 text-center">
                            <h3 className="text-lg text-red-700 mb-2">
                              Missing Profiling Data
                            </h3>
                            <p className="text-red-600 mb-4">
                              Cannot display detailed profiling without initial
                              data.
                            </p>
                            <button
                              onClick={() => setCurrentStep(1)}
                              className="bg-red-600 hover:bg-red-700 text-white px-4 py-2 rounded-lg transition-colors"
                            >
                              Return to Dataset Overview
                            </button>
                          </div>
                        );
                      }

                      if (!hasDataDictionary) {
                        return (
                          <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-6 text-center">
                            <h3 className="text-lg text-yellow-700 mb-2">
                              Data Dictionary Required
                            </h3>
                            <p className="text-yellow-600 mb-4">
                              Please complete the Data Dictionary step first.
                            </p>
                            <button
                              onClick={() => setCurrentStep(3)}
                              className="bg-yellow-600 hover:bg-yellow-700 text-white px-4 py-2 rounded-lg transition-colors"
                            >
                              Go to Data Dictionary
                            </button>
                          </div>
                        );
                      }

                      return null;
                    })()}

                    <div className="flex flex-wrap items-center justify-between gap-3 mt-6">
                      <button
                        onClick={() => setCurrentStep(7)}
                        className="bg-gray-500 hover:bg-gray-600 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors"
                      >
                        Previous: Metadata Template
                      </button>

                      <div className="flex flex-wrap items-center gap-3">
                        {workflowFinished ? (
                          <>
                            <div className="rounded-lg border border-green-200 bg-green-50 px-4 py-2 text-sm text-green-800">
                              Profiling workflow complete. Use All Downloads above to export your results.
                            </div>
                            <button
                              onClick={handleContinueToMapping}
                              className="bg-brand-blue hover:bg-brand-blue/75 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors"
                            >
                              Continue to Mapping <ChevronRight size={16} />
                            </button>
                          </>
                        ) : (
                          <button
                            onClick={() => void handleFinishWorkflow()}
                            className="bg-brand-darkblue hover:bg-brand-blue text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors"
                          >
                            Finish Profiling <Check size={16} />
                          </button>
                        )}
                      </div>
                    </div>
                  </div>

                  <ChatModal
                    isOpen={chatOpenStates[8] || false}
                    onClose={() => closeChatModal(8)}
                    stepTitle="Detailed Table Profiling"
                    stepNumber={8}
                    onUseResponse={undefined}
                  />
                </div>
              )}
            </div>
          </div>

          {/* Modals */}
          {open && (
            <div className="fixed inset-0 bg-black bg-opacity-70 flex items-center justify-center z-50">
              <div className="relative w-full h-full flex justify-center items-center">
                <button
                  onClick={() => setOpen(false)}
                  className="absolute top-4 right-4 bg-white text-black rounded-full px-3 py-1 shadow-lg hover:bg-gray-200"
                >
                  ✕
                </button>
                <iframe
                  src={`${baseUrl}${profilingData.successful_uploads.find(
                    (f) => f.file_id === activeTab,
                  )?.profiling_report_url || ""}`}
                  className="w-11/12 h-5/6 border rounded-lg bg-white"
                  title="Profiling Report"
                />
              </div>
            </div>
          )}

          {isProfilingChatOpen && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4">
              <div className="flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-lg bg-white shadow-xl">
                <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
                  <div>
                    <h3 className="text-base font-semibold text-brand-blue">Chat: Dataset Overview</h3>
                    <p className="text-xs text-gray-500">Ask a question or request a precise edit, then apply the updated response.</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setIsProfilingChatOpen(false)}
                    className="rounded-md p-1 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-800"
                    aria-label="Close dataset overview chat"
                  >
                    <X size={20} />
                  </button>
                </div>

                <div className="flex-1 space-y-3 overflow-y-auto p-4">
                  {profilingChatMessages.length === 0 && (
                    <div className="rounded-md border border-dashed border-gray-300 bg-gray-50 p-4 text-sm text-gray-600">
                      Describe the dataset overview change you want to make, or ask a question about the profiling result.
                    </div>
                  )}

                  {profilingChatMessages.map((message, index) => (
                    <div key={`${message.role}-${index}`} className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}>
                      <div className={`max-w-[85%] rounded-lg px-4 py-3 text-sm ${message.role === "user" ? "bg-brand-blue text-white" : "bg-gray-100 text-gray-800"}`}>
                        <div className="whitespace-pre-wrap">{message.text}</div>
                        {message.role === "assistant" && message.mode === "UPDATE" && (
                          <button
                            type="button"
                            onClick={() => applyStreamingProfilingChatResponse(message.response)}
                            className="mt-3 rounded-md bg-green-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-green-700"
                          >
                            Apply changes
                          </button>
                        )}
                      </div>
                    </div>
                  ))}

                  {isSubmittingProfilingChat && (
                    <div className="flex justify-start">
                      <div className="rounded-lg bg-gray-100 px-4 py-3">
                        <Loader2 size={18} className="animate-spin text-gray-600" />
                      </div>
                    </div>
                  )}

                  {profilingChatError && (
                    <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                      {profilingChatError}
                    </div>
                  )}
                </div>

                <div className="border-t border-gray-200 p-4">
                  <div className="flex items-end gap-2">
                    <textarea
                      value={profilingChatInput}
                      onChange={(event) => setProfilingChatInput(event.target.value)}
                      onKeyDown={handleStreamingProfilingChatKeyDown}
                      placeholder="Ask about the dataset overview or describe an edit..."
                      disabled={isSubmittingProfilingChat}
                      rows={3}
                      className="min-h-[84px] flex-1 resize-none rounded-md border border-gray-300 px-3 py-2 text-sm outline-none transition-colors focus:border-brand-blue focus:ring-1 focus:ring-brand-blue disabled:bg-gray-50"
                    />
                    <button
                      type="button"
                      onClick={() => void handleStreamingProfilingChatSend()}
                      disabled={!profilingChatInput.trim() || isSubmittingProfilingChat}
                      className="flex h-10 w-10 items-center justify-center rounded-md bg-brand-blue text-white transition-colors hover:bg-brand-blue/80 disabled:cursor-not-allowed disabled:opacity-60"
                      aria-label="Send dataset overview chat message"
                    >
                      {isSubmittingProfilingChat ? <Loader2 size={18} className="animate-spin" /> : <Send size={18} />}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
