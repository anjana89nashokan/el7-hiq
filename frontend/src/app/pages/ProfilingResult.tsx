/**
 * ProfilingResult Component - Refactored
 *
 * Main component for the data profiling pipeline that manages a multi-step workflow
 * for analyzing uploaded datasets. Uses custom hooks for state management and business logic.
 *
 * Architecture:
 * - useProfilingState: Manages all component state
 * - useChatManagement: Handles chat initialization and messaging
 * - useProfilingHandlers: Contains business logic for similarity checks and validation
 *
 * Steps:
 * 1. Dataset Overview - Initial profiling data display
 * 2. Relationship Analysis - Analyze relationships between datasets
 * 3. Data Dictionary - Generate comprehensive data dictionary
 * 4. Similarity Check - Check similarity with Reference tables
 * 5. Data Anomaly Analysis - Detect anomalies in data
 * 6. Metadata Template - Define and manage metadata
 * 7. Detailed Profiling - View detailed table profiling information
 */

import { useEffect, useCallback, useMemo, useRef, useState } from "react";
import { Download, ChevronRight } from "lucide-react";
import { useChat } from "../contexts/ChatContext";
import { useLocation, useNavigate } from "react-router-dom";
import { API_BASE_URL } from "../config/env";
import { useDispatch, useSelector } from "react-redux";
import MetadataTemplate from "../components/MetadataTemplate";
import DataAnomalyAnalyzer from "../components/DataAnomalyAnalyzer";
import StepSidebar from "../components/StepSidebar";
import LoadingSpinner from "../components/LoadingSpinner";
import ChatSidebar from "../components/ChatSidebar";
import DatasetOverviewStep from "../components/steps/DatasetOverviewStep";
import RelationshipAnalysisStep from "../components/steps/RelationshipAnalysisStep";
import DataDictionaryStep from "../components/steps/DataDictionaryStep";
import SimilarityCheckStep from "../components/steps/SimilarityCheckStep";
import GenericAnalysisStep from "../components/steps/GenericAnalysisStep";
import ProfilingReportModal from "../components/ProfilingReportModal";
import NoDataView from "../components/NoDataView";
import DartSuggestion from "./DartSuggestion";
import { setStep3Response } from "../state/reducers/dartReducer";
import { buildStep3ReduxPayload } from "../utils/profilingUtils";
import { useSessionStorage } from "../hooks/useSessionStorage";
import { useStepProgress } from "../hooks/useStepProgress";
import { useProfilingState } from "../hooks/useProfilingState";
import { useChatManagement } from "../hooks/useChatManagement";
import { useProfilingHandlers } from "../hooks/useProfilingHandlers";
import { formatDate } from "../utils/dateFormatter";
import { renderStep7Content } from "../utils/profilingUtils";
import { MESSAGES } from "../config/messages";
import { showDownloadSelectionModal, downloadFilesIndividually, showLoadingModal } from "../utils/fileDownloadUtils";
import { getDefaultDatabase } from "../end-points/databaseApi";
import {
  getAppSessionDetail,
  saveProfilingResumeState,
  type AppSessionDetail,
} from "../end-points/appSessionsApi";
import {
  hasProfilingToolResult,
  isProfilingLlmError,
} from "../end-points/profilingResultApi";
import {
  getCurrentAppSessionId,
  onSessionChanged,
  setSessionRuntime,
} from "../utils/appSessionStorage";
import { sttmNav } from "../utils/sttmRoutes";
import {
  initialDataDictionaryState,
  addDartTableEntry as addEntry,
  updateDartTableEntry as updateEntry,
  removeDartTableEntry as removeEntry,
} from "../state/reducers/profilingResultReducers";
import {
  updateStepCompletion as updateStepCompletionHelper,
  resetStepData,
} from "../state/reducers/stepStateHelpers";

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
  profiling_report_url: string;
  access_info: {
    sql_query: string;
    table_url: string;
    python_example: string;
    table_reference: {
      project_id: string;
      dataset_id: string;
      table_name: string;
      full_table_id: string;
    };
    tables_created?: {
      sheet_name: string | null;
      table_name: string;
      rows_uploaded: number;
    }[];
  };
  data_quality_score: Record<string, any>;
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

export default function ProfilingResult() {
  const location = useLocation();
  const navigate = useNavigate();
  const routeState = (location as any).state;
  const dispatch = useDispatch();
  const { isChatOpen, setIsChatOpen } = useChat();
  const { getStoredSession } = useSessionStorage();
  const { dartSuggestionResponse } = useSelector((state: any) => state.dart);

  const [restoredProfilingData, setRestoredProfilingData] =
    useState<ApiResponse | null>(null);
  const [sessionHydrated, setSessionHydrated] = useState(false);
  const hydrationCompleteRef = useRef(false);
  const metadataExportRef = useRef<(() => void) | null>(null);
  const detailedProfilingExportRef = useRef<(() => void) | null>(null);

  // Get profiling data from router state
  const profilingData = useMemo(
    () => (routeState?.data as ApiResponse) || restoredProfilingData,
    [routeState?.data, restoredProfilingData],
  );
  const baseUrl = useMemo(() => API_BASE_URL, []);

  // Initialize all component state using custom hook
  const state = useProfilingState();

  // Initialize chat management hooks
  const { initializeChat, handleSendQAMessage, handleKeyPress } =
    useChatManagement({
      profilingData,
      apiInProgress: state.apiInProgress,
      initialMessageData: state.initialMessageData,
      messages: state.messages,
      inputMessage: state.inputMessage,
      setApiInProgress: state.setApiInProgress,
      setLoadingStates: state.setLoadingStates,
      setInitialMessageData: state.setInitialMessageData,
      setMessages: state.setMessages,
      setQaMessages: state.setQaMessages,
      setInputMessage: state.setInputMessage,
      getStoredSession,
    });

  // Initialize business logic handlers
  const handlers = useProfilingHandlers({
    apiInProgress: state.apiInProgress,
    similarityResponse: state.similarityResponse,
    profilingData,
    dartTableEntries: state.dartTableEntries,
    databaseName: state.databaseName,
    defaultDatabaseName: state.defaultDatabaseName,
    dynamicFilters: state.dynamicFilters,
    setApiInProgress: state.setApiInProgress,
    setLoadingStates: state.setLoadingStates,
    setSimilarityResponse: state.setSimilarityResponse,
    setDatabaseName: state.setDatabaseName,
    setSteps: state.setSteps,
    setTableSchemaError: state.setTableSchemaError,
    setTableSchemaFields: state.setTableSchemaFields,
    setDartTableEntries: state.setDartTableEntries,
    getStoredSession,
  });

  /**
   * Clears all data dictionary related state
   * Used when starting a new session or resetting step 3
   */
  const clearDataDictionaryState = useCallback(() => {
    state.setDataDictionaryState(initialDataDictionaryState);
    state.setDataDictionaryJson(undefined);
    state.setDataDictionaryResponse("");
  }, [
    state.setDataDictionaryState,
    state.setDataDictionaryJson,
    state.setDataDictionaryResponse,
  ]);

  // Add state for session detail
  const [showDropdown, setShowDropdown] = useState(false);
  const [workflowFinished, setWorkflowFinished] = useState(false);
  const [sessionDetail, setSessionDetail] = useState<AppSessionDetail | null>(
    null,
  );
  const [isDownloadingFiles, setIsDownloadingFiles] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(event.target as Node)
      ) {
        setShowDropdown(false);
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Add useEffect to fetch session detail
  useEffect(() => {
    const fetchSessionDetail = async () => {
      const appSessionId = getCurrentAppSessionId();
      if (!appSessionId) return;

      try {
        const detail = await getAppSessionDetail(appSessionId);
        setSessionDetail(detail);
      } catch (error) {
        console.error("Failed to fetch session detail:", error);
      }
    };

    fetchSessionDetail();
  }, []);

  // Update the condition for showing buttons
  const shouldShowButtons = useMemo(() => {
    const completed = state.steps
      .filter((s) => s.id <= 7)
      .every((s) => s.completed || state.skippedSteps.has(s.id));
    const step8Completed =
      workflowFinished || !!state.steps.find((s) => s.id === 8)?.completed;

    const status =
      sessionDetail?.profiling_run?.resume_state?.uploadResponse?.status;

    return completed || step8Completed || status === "completed";
  }, [state.steps, state.skippedSteps, sessionDetail, workflowFinished]);

  /**
   * Effect: Clear state when user starts a new session
   * Listens for storage changes and resets data dictionary state when session is cleared
   */
  useEffect(() => {
    const handleStorageChange = () => {
      const sessionId = sessionStorage.getItem("session_id");
      if (!sessionId) {
        clearDataDictionaryState();
      }
    };

    globalThis.addEventListener("storage", handleStorageChange);
    return () => globalThis.removeEventListener("storage", handleStorageChange);
  }, [clearDataDictionaryState]);

  /**
   * Effect: Fetch default database configuration on component mount
   * Runs only once to avoid duplicate API calls
   */
  useEffect(() => {
    const fetchDefaultDatabase = async () => {
      try {
        const datasetId = await getDefaultDatabase();
        state.setDatabaseName(datasetId);
        state.setDefaultDatabaseName(datasetId);
      } catch {
        state.setDatabaseName("");
        state.setDefaultDatabaseName("");
      }
    };
    fetchDefaultDatabase();
  }, []);

  useEffect(() => {
    const restoreSession = async () => {
      setSessionHydrated(false);
      const appSessionId = getCurrentAppSessionId();
      if (!appSessionId) {
        hydrationCompleteRef.current = true;
        setSessionHydrated(true);
        return;
      }
      try {
        const detail = await getAppSessionDetail(appSessionId);
        const runtime = {
          vertex_session_id:
            detail.runtime?.vertex_session_id ||
            detail.profiling_run?.active_vertex_session_id ||
            detail.session.active_vertex_session_id ||
            null,
          vertex_app_name:
            detail.runtime?.vertex_app_name ||
            detail.profiling_run?.active_vertex_app_name ||
            detail.session.active_vertex_app_name ||
            null,
          vertex_user_id: detail.runtime?.vertex_user_id || null,
        };
        if (runtime.vertex_session_id && runtime.vertex_app_name) {
          setSessionRuntime(runtime);
        }

        const profilingRun = detail.profiling_run;
        const resumeState = profilingRun?.resume_state || {};
        if (resumeState.uploadResponse && !routeState?.data) {
          setRestoredProfilingData(resumeState.uploadResponse as ApiResponse);
        }
        if (Object.keys(resumeState).length > 0) {
          if (resumeState.activeTab !== undefined)
            state.setActiveTab(resumeState.activeTab);
          if (resumeState.currentStep !== undefined)
            state.setCurrentStep(resumeState.currentStep);
          if (resumeState.maxVisitedStep !== undefined)
            state.setMaxVisitedStep(resumeState.maxVisitedStep);
          if (resumeState.profileSummaryCollapsed !== undefined)
            state.setProfileSummaryCollapsed(
              resumeState.profileSummaryCollapsed,
            );
          if (resumeState.initialMessageData !== undefined) {
            const restored = resumeState.initialMessageData;
            const isStaleError =
              Array.isArray(restored) &&
              restored.length > 0 &&
              isProfilingLlmError(restored[0]?.text_response) &&
              !hasProfilingToolResult(restored[0]);
            if (!isStaleError) {
              state.setInitialMessageData(restored);
              if (Array.isArray(restored) && restored.length > 0) {
                state.hasSentRef.current = true;
                state.setIsInitialized(true);
              }
            }
          }
          if (resumeState.relationshipResponse !== undefined)
            state.setRelationshipResponse(resumeState.relationshipResponse);
          if (resumeState.prevRelationshipResponse !== undefined)
            state.setPrevRelationshipResponse(
              resumeState.prevRelationshipResponse,
            );
          if (resumeState.dataDictionaryJson !== undefined)
            state.setDataDictionaryJson(resumeState.dataDictionaryJson);
          if (resumeState.dataDictionaryResponse !== undefined)
            state.setDataDictionaryResponse(resumeState.dataDictionaryResponse);
          if (resumeState.dataDictionaryState !== undefined)
            state.setDataDictionaryState(resumeState.dataDictionaryState);
          if (resumeState.similarityResponse !== undefined)
            state.setSimilarityResponse(resumeState.similarityResponse);
          if (resumeState.anomalyData !== undefined)
            state.setAnomalyData(resumeState.anomalyData);
          if (resumeState.prevMetadataResponse !== undefined)
            state.setPrevMetadataResponse(resumeState.prevMetadataResponse);
          if (resumeState.modifiedRelationshipResponse !== undefined)
            state.setModifiedRelationshipResponse(
              resumeState.modifiedRelationshipResponse,
            );
          if (resumeState.modifiedDataDictionaryResponse !== undefined)
            state.setModifiedDataDictionaryResponse(
              resumeState.modifiedDataDictionaryResponse,
            );
          if (resumeState.modifiedAnomalyResponse !== undefined)
            state.setModifiedAnomalyResponse(
              resumeState.modifiedAnomalyResponse,
            );
          if (resumeState.modifiedMetadataResponse !== undefined)
            state.setModifiedMetadataResponse(
              resumeState.modifiedMetadataResponse,
            );
          if (resumeState.dartTableEntries !== undefined)
            state.setDartTableEntries(resumeState.dartTableEntries);
          if (resumeState.defaultDatabaseName !== undefined)
            state.setDefaultDatabaseName(resumeState.defaultDatabaseName);
          if (resumeState.databaseName !== undefined)
            state.setDatabaseName(resumeState.databaseName);
          if (resumeState.tableSchemaFields !== undefined)
            state.setTableSchemaFields(resumeState.tableSchemaFields);
          if (resumeState.dynamicFilters !== undefined)
            state.setDynamicFilters(resumeState.dynamicFilters);
          if (resumeState.tableSchemaError !== undefined)
            state.setTableSchemaError(resumeState.tableSchemaError);
          if (resumeState.skippedSteps !== undefined)
            state.setSkippedSteps(new Set<number>(resumeState.skippedSteps));
          if (resumeState.steps !== undefined) {
            const wasFinished = !!resumeState.workflowFinished;
            const restoredSteps = resumeState.steps.map(
              (step: { id: number; completed?: boolean }) =>
                step.id === 8 && !wasFinished
                  ? { ...step, completed: false }
                  : step,
            );
            state.setSteps(restoredSteps);
            if (wasFinished) setWorkflowFinished(true);
          }
        }
      } catch (error) {
        console.error("Failed to restore profiling session:", error);
      } finally {
        hydrationCompleteRef.current = true;
        setSessionHydrated(true);
      }
    };

    void restoreSession();
    return onSessionChanged(() => {
      void restoreSession();
    });
  }, []);

  /**
   * Adds a new Reference table entry for similarity checking
   */
  const addDartTableEntry = useCallback(() => {
    state.setDartTableEntries((prev) => addEntry(prev));
  }, [state.setDartTableEntries]);

  /**
   * Updates a specific field in a Reference table entry
   * @param index - Index of the entry to update
   * @param field - Field name to update (dartTable or column)
   * @param value - New value for the field
   */
  const updateDartTableEntry = useCallback(
    (index: number, field: "dartTable" | "column", value: string) => {
      state.setDartTableEntries((prev) =>
        updateEntry(prev, index, field, value),
      );
    },
    [state.setDartTableEntries],
  );

  /**
   * Removes a Reference table entry at the specified index
   * @param index - Index of the entry to remove
   */
  const removeDartTableEntry = useCallback(
    (index: number) =>
      state.setDartTableEntries((prev) => removeEntry(prev, index)),
    [state.setDartTableEntries],
  );

  /**
   * Toggles accordion state for UI components
   * @param key - Unique key for the accordion
   */
  const toggleAccordion = useCallback(
    (key: string) =>
      state.setAccordionStates((prev) => ({ ...prev, [key]: !prev[key] })),
    [state.setAccordionStates],
  );

  /**
   * Effect: Initialize chat when profiling data is available
   * Runs once per session to set up initial chat state
   */
  useEffect(() => {
    if (!sessionHydrated || !profilingData || state.hasSentRef.current !== false) {
      return;
    }
    const validUploads =
      profilingData.successful_uploads?.filter((upload) => upload != null) ||
      [];
    if (validUploads.length > 0 && !state.activeTab) {
      const firstTable = validUploads[0].access_info?.tables_created?.[0];
      state.setActiveTab(
        firstTable?.table_name ?? validUploads[0].table_name,
      );
    }
    if (!state.isInitialized && state.initialMessageData.length === 0) {
      state.hasSentRef.current = true;
      initializeChat();
      state.setIsInitialized(true);
    }
  }, [
    sessionHydrated,
    profilingData,
    state.activeTab,
    state.initialMessageData.length,
    state.isInitialized,
    initializeChat,
    state.setActiveTab,
    state.setIsInitialized,
  ]);

  /**
   * Effect: Update step completion status based on data availability
   * Monitors all step-related state changes to update completion flags
   */
  useEffect(() => {
    state.setSteps((prev) =>
      prev.map((step) =>
        updateStepCompletionHelper(step, {
          profilingData,
          messages: state.messages,
          initialMessageData: state.initialMessageData,
          relationshipResponse: state.relationshipResponse,
          dataDictionaryJson: state.dataDictionaryJson,
          dataDictionaryState: state.dataDictionaryState,
          similarityResponse: state.similarityResponse,
          anomalyData: state.anomalyData,
          prevMetadataResponse: state.prevMetadataResponse,
          dartSuggestionResponse: dartSuggestionResponse,
        }),
      ),
    );
  }, [
    profilingData,
    state.messages,
    state.initialMessageData,
    state.relationshipResponse,
    state.dataDictionaryJson,
    state.anomalyData,
    state.similarityResponse,
    state.prevMetadataResponse,
    state.dataDictionaryState.isCompleted,
    state.setSteps,
    dartSuggestionResponse,
  ]);

  /**
   * Effect: Populate Redux store with data dictionary data for Reference Suggestion
   */
  useEffect(() => {
    const payload = buildStep3ReduxPayload(
      state.dataDictionaryJson,
      state.dataDictionaryState,
    );
    if (payload) {
      dispatch(setStep3Response(payload));
    }
  }, [
    state.dataDictionaryJson,
    state.dataDictionaryState?.resultData,
    state.dataDictionaryState?.json,
    dispatch,
  ]);

  /**
   * Resets all steps from a given step number onwards
   * Used when user wants to retry or modify previous steps
   * @param fromStep - Step number to reset from (inclusive)
   */
  const resetStepsFromStep = useCallback(
    (fromStep: number) => {
      state.setMaxVisitedStep(fromStep);

      state.setSteps((prev) =>
        prev.map((step) => ({
          ...step,
          completed: step.id < fromStep ? step.completed : false,
        })),
      );

      if (fromStep <= 8) {
        setWorkflowFinished(false);
      }

      state.setStepApiCallTracker((prev) => {
        const newTracker = new Set(prev);
        for (let i = fromStep; i <= 8; i++) {
          newTracker.delete(i);
        }
        return newTracker;
      });

      const resetData = resetStepData(fromStep);

      if (resetData.messages !== undefined) {
        state.setMessages(resetData.messages);
        state.setInitialMessageData(resetData.initialMessageData);
        state.hasSentRef.current = false;
        state.setIsInitialized(resetData.isInitialized);
      }
      if (resetData.relationshipResponse !== undefined) {
        state.setRelationshipResponse(resetData.relationshipResponse);
        state.setPrevRelationshipResponse(resetData.prevRelationshipResponse);
        state.setModifiedRelationshipResponse(
          resetData.modifiedRelationshipResponse,
        );
      }
      if (resetData.dataDictionaryJson !== undefined) {
        clearDataDictionaryState();
        state.setModifiedDataDictionaryResponse(
          resetData.modifiedDataDictionaryResponse,
        );
      }
      if (resetData.similarityResponse !== undefined) {
        state.setSimilarityResponse(resetData.similarityResponse);
        state.setDartTableEntries(resetData.dartTableEntries);
        state.setSkippedSteps(resetData.skippedSteps);
      }
      if (resetData.anomalyData !== undefined) {
        state.setAnomalyData(resetData.anomalyData);
        state.setModifiedAnomalyResponse(resetData.modifiedAnomalyResponse);
      }
      if (resetData.prevMetadataResponse !== undefined) {
        state.setPrevMetadataResponse(resetData.prevMetadataResponse);
        state.setModifiedMetadataResponse(resetData.modifiedMetadataResponse);
      }
    },
    [clearDataDictionaryState, state],
  );

  /**
   * Handles retry action for dataset overview step
   * Resets all steps starting from step 1
   */
  const handleDatasetOverviewRetry = useCallback(() => {
    resetStepsFromStep(1);
  }, [resetStepsFromStep]);

  const handleDatasetOverviewHumanInputApply = useCallback((response: any) => {
    state.setInitialMessageData((prev: any[]) => {
      const currentResponse = prev[0] || {};
      return [
        {
          ...currentResponse,
          text_response: response?.text_response ?? currentResponse.text_response,
          tool_response: response?.tool_response ?? currentResponse.tool_response,
        },
      ];
    });
  }, [state]);

  /**
   * Effect: Track maximum visited step for navigation control
   * Updates when user navigates to a new step
   */
  useEffect(() => {
    if (state.currentStep > state.maxVisitedStep) {
      state.setMaxVisitedStep(state.currentStep);
    }
  }, [state.currentStep, state.maxVisitedStep, state]);

  /**
   * Creates a loading state change handler for a specific step
   * @param step - Step number to handle loading state for
   * @returns Function that updates loading state for the step
   */
  const handleLoadingStateChange = useCallback(
    (step: number) => (loading: boolean) => {
      state.setApiInProgress(loading);
      state.setLoadingStates((prev) => ({ ...prev, [step]: loading }));
    },
    [state],
  );

  // Hook to check if user can proceed to a specific step
  const { canProceedToStep } = useStepProgress(
    state.steps,
    state.currentStep,
    state.skippedSteps,
    state.maxVisitedStep,
  );

  // Memoized navigation handlers for each step
  const stepNavigationHandlers = useMemo(
    () => ({
      1: () => state.setCurrentStep(1),
      2: () => state.setCurrentStep(2),
      3: () => state.setCurrentStep(3),
      4: () => state.setCurrentStep(4),
      5: () => {
        const payload = buildStep3ReduxPayload(
          state.dataDictionaryJson,
          state.dataDictionaryState,
        );
        if (payload) {
          dispatch(setStep3Response(payload));
        }
        state.setCurrentStep(5);
      },
      6: () => state.setCurrentStep(6),
      7: () => state.setCurrentStep(7),
      8: () => state.setCurrentStep(8),
    }),
    [state],
  );

  /**
   * Marks a step as having made an API call
   * Used to track which steps have already fetched data
   * @param step - Step number to mark as called
   */
  const markApiCalled = useCallback(
    (step: number) => {
      state.setStepApiCallTracker((prev) => new Set([...prev, step]));
    },
    [state],
  );

  const handleFinishWorkflow = useCallback(async () => {
    if (workflowFinished) {
      window.scrollTo({ top: 0, behavior: "smooth" });
      setShowDropdown(true);
      return;
    }

    const completedSteps = state.steps.map((step) =>
      step.id === 8 ? { ...step, completed: true } : step,
    );
    const nextMaxVisitedStep = Math.max(state.maxVisitedStep, 8);

    state.setSteps(completedSteps);
    state.setMaxVisitedStep(nextMaxVisitedStep);
    setWorkflowFinished(true);
    window.scrollTo({ top: 0, behavior: "smooth" });
    globalThis.setTimeout(() => setShowDropdown(true), 400);

    const appSessionId = getCurrentAppSessionId();
    if (!appSessionId || !profilingData) return;

    try {
      await saveProfilingResumeState(appSessionId, {
        status: "COMPLETED",
        current_step: "step_8",
        resume_state: {
          uploadResponse: profilingData,
          activeTab: state.activeTab,
          currentStep: 8,
          maxVisitedStep: nextMaxVisitedStep,
          profileSummaryCollapsed: state.profileSummaryCollapsed,
          initialMessageData: state.initialMessageData,
          relationshipResponse: state.relationshipResponse,
          prevRelationshipResponse: state.prevRelationshipResponse,
          dataDictionaryJson: state.dataDictionaryJson,
          dataDictionaryResponse: state.dataDictionaryResponse,
          dataDictionaryState: state.dataDictionaryState,
          similarityResponse: state.similarityResponse,
          anomalyData: state.anomalyData,
          prevMetadataResponse: state.prevMetadataResponse,
          modifiedRelationshipResponse: state.modifiedRelationshipResponse,
          modifiedDataDictionaryResponse: state.modifiedDataDictionaryResponse,
          modifiedAnomalyResponse: state.modifiedAnomalyResponse,
          modifiedMetadataResponse: state.modifiedMetadataResponse,
          dartTableEntries: state.dartTableEntries,
          defaultDatabaseName: state.defaultDatabaseName,
          databaseName: state.databaseName,
          tableSchemaFields: state.tableSchemaFields,
          dynamicFilters: state.dynamicFilters,
          tableSchemaError: state.tableSchemaError,
          skippedSteps: Array.from(state.skippedSteps),
          steps: completedSteps,
          workflowFinished: true,
        },
      });
    } catch (error) {
      console.error("Failed to save completed profiling state:", error);
    }
  }, [
    workflowFinished,
    profilingData,
    state.activeTab,
    state.maxVisitedStep,
    state.profileSummaryCollapsed,
    state.initialMessageData,
    state.relationshipResponse,
    state.prevRelationshipResponse,
    state.dataDictionaryJson,
    state.dataDictionaryResponse,
    state.dataDictionaryState,
    state.similarityResponse,
    state.anomalyData,
    state.prevMetadataResponse,
    state.modifiedRelationshipResponse,
    state.modifiedDataDictionaryResponse,
    state.modifiedAnomalyResponse,
    state.modifiedMetadataResponse,
    state.dartTableEntries,
    state.defaultDatabaseName,
    state.databaseName,
    state.tableSchemaFields,
    state.dynamicFilters,
    state.tableSchemaError,
    state.skippedSteps,
    state.steps,
    state.setSteps,
    state.setMaxVisitedStep,
  ]);

  const handleContinueToMapping = useCallback(() => {
    navigate(sttmNav("/mapping"), {
      state: {
        fromProfilingComplete: true,
        profilingMode: "standard",
      },
    });
  }, [navigate]);

  useEffect(() => {
    if (!hydrationCompleteRef.current || !profilingData) return;
    const appSessionId = getCurrentAppSessionId();
    if (!appSessionId) return;

    const timeout = globalThis.setTimeout(() => {
      void saveProfilingResumeState(appSessionId, {
        status: workflowFinished ? "COMPLETED" : "READY",
        current_step: `step_${state.currentStep}`,
        resume_state: {
          uploadResponse: profilingData,
          activeTab: state.activeTab,
          currentStep: state.currentStep,
          maxVisitedStep: state.maxVisitedStep,
          profileSummaryCollapsed: state.profileSummaryCollapsed,
          initialMessageData: state.initialMessageData,
          relationshipResponse: state.relationshipResponse,
          prevRelationshipResponse: state.prevRelationshipResponse,
          dataDictionaryJson: state.dataDictionaryJson,
          dataDictionaryResponse: state.dataDictionaryResponse,
          dataDictionaryState: state.dataDictionaryState,
          similarityResponse: state.similarityResponse,
          anomalyData: state.anomalyData,
          prevMetadataResponse: state.prevMetadataResponse,
          modifiedRelationshipResponse: state.modifiedRelationshipResponse,
          modifiedDataDictionaryResponse: state.modifiedDataDictionaryResponse,
          modifiedAnomalyResponse: state.modifiedAnomalyResponse,
          modifiedMetadataResponse: state.modifiedMetadataResponse,
          dartTableEntries: state.dartTableEntries,
          defaultDatabaseName: state.defaultDatabaseName,
          databaseName: state.databaseName,
          tableSchemaFields: state.tableSchemaFields,
          dynamicFilters: state.dynamicFilters,
          tableSchemaError: state.tableSchemaError,
          skippedSteps: Array.from(state.skippedSteps),
          steps: state.steps,
          workflowFinished,
        },
      }).catch((error) => {
        console.error("Failed to save profiling resume state:", error);
      });
    }, 800);

    return () => globalThis.clearTimeout(timeout);
  }, [
    profilingData,
    state.activeTab,
    state.currentStep,
    state.maxVisitedStep,
    state.profileSummaryCollapsed,
    state.initialMessageData,
    state.relationshipResponse,
    state.prevRelationshipResponse,
    state.dataDictionaryJson,
    state.dataDictionaryResponse,
    state.dataDictionaryState,
    state.similarityResponse,
    state.anomalyData,
    state.prevMetadataResponse,
    state.modifiedRelationshipResponse,
    state.modifiedDataDictionaryResponse,
    state.modifiedAnomalyResponse,
    state.modifiedMetadataResponse,
    state.dartTableEntries,
    state.defaultDatabaseName,
    state.databaseName,
    state.tableSchemaFields,
    state.dynamicFilters,
    state.tableSchemaError,
    state.skippedSteps,
    state.steps,
    workflowFinished,
  ]);

  const handleDownloadUploadedFiles = useCallback(async () => {
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
        console.log('Download data from API:', downloadData);
        
        // Close loading modal
        loadingModal.close();
        
        if (!downloadData.files || downloadData.files.length === 0) {
          alert('No files available for download');
          return;
        }

        const selectedUrls = await showDownloadSelectionModal(downloadData);
        console.log('User selected URLs:', selectedUrls);
        
        if (selectedUrls.length > 0) {
          await downloadFilesIndividually(selectedUrls, 'uploaded-files');
        } else {
          console.log('No URLs selected by user');
        }
      } catch (apiError) {
        // Close loading modal on API error
        loadingModal.close();
        throw apiError;
      }
    } catch (error) {
      console.error('Failed to download uploaded files:', error);
      alert('Failed to download files. Please try again.');
    } finally {
      setIsDownloadingFiles(false);
    }
  }, []);

  const handleDownloadDataDictionaryCSV = useCallback(() => {
    const data = state.dataDictionaryState?.resultData;
    if (!data?.length) return;
    const headers = [
      "File Name",
      "Field Name",
      "Field Business Name",
      "Data Type",
      "Length",
      "Format",
      "Nullable",
      "Most Occurrences",
      "Primary Key",
      "Foreign Key",
      "Field Description",
    ];
    const rows = data.map((item: any) => {
      const mostOcc = item.most_occurrences ?? item["Most Occurrences"] ?? "";
      const mostOccStr = Array.isArray(mostOcc)
        ? mostOcc.join(", ")
        : String(mostOcc);
      return [
        item.file_name || item["File Name"] || "",
        item.field_name || item["Field Name"] || item["Attribute Name"] || "",
        item.business_name ||
          item["Field Business Name"] ||
          item["Logical Attribute Name"] ||
          "",
        item.data_type || item["Data Type"] || "",
        item.length || item["Length"] || "",
        item.format || item["Format"] || "",
        item.nullable || item["Nullable"] || item["Nullability"] || "",
        mostOccStr,
        item.primary_key || item["Primary Key"] || "",
        item.foreign_key || item["Foreign Key"] || "",
        item.field_description ||
          item["Field Description"] ||
          item["Attribute Description"] ||
          "",
      ]
        .map((f) => `"${String(f).replace(/"/g, '""')}"`)
        .join(",");
    });
    const csv = [headers.join(","), ...rows].join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `data-dictionary-${new Date().toISOString().split("T")[0]}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }, [state.dataDictionaryState?.resultData]);

  const handleDownloadDuplicatesExcel = useCallback(() => {
    if (!profilingData?.successful_uploads?.length) return;
    
    import('xlsx').then((XLSX) => {
      const wb = XLSX.utils.book_new();
      let hasData = false;
      
      // Create flatTabs structure to access all_duplicates data
      const flatTabs = profilingData.successful_uploads
        .filter((file: any) => file != null)
        .flatMap((file: any) =>
          (file.access_info?.tables_created ?? [{ sheet_name: null, table_name: file.table_name, rows_uploaded: file.rows_uploaded }])
            .map((tc: any) => ({
              ...file,
              table_name: tc.table_name,
              sheet_name: tc.sheet_name,
              all_duplicates: tc.all_duplicates ?? [],
              _tabLabel: tc.sheet_name ? `${file.filename}(${tc.sheet_name})` : file.filename,
            }))
        );
      
      flatTabs.forEach((tab: any) => {
        if (tab.all_duplicates?.length > 0) {
          const ws = XLSX.utils.json_to_sheet(tab.all_duplicates);
          const sheetName = (tab._tabLabel || tab.table_name || 'duplicates').slice(0, 31);
          XLSX.utils.book_append_sheet(wb, ws, sheetName);
          hasData = true;
        }
      });
      
      if (hasData) {
        XLSX.writeFile(wb, `duplicates_${new Date().toISOString().split('T')[0]}.xlsx`);
      } else {
        alert('No duplicate data available for download.');
      }
    });
  }, [profilingData]);

  // Show loading spinner while profiling data is being fetched
  if (!profilingData) {
    return (
      <LoadingSpinner
        fullScreen
        message={MESSAGES.LOADING.PROFILING_RESULTS}
        size="lg"
      />
    );
  }

  // Show empty state if no successful uploads
  if (
    !profilingData.successful_uploads ||
    profilingData.successful_uploads.length === 0
  ) {
    return <NoDataView failedUploads={profilingData.failed_uploads} />;
  }

  // Construct report URL for modal
  // activeTab holds the table name (see effect that calls setActiveTab), so match on
  // table_name; fall back to file_id for safety. Guard so we never build "…/undefined".
  const activeUpload = profilingData.successful_uploads
    .filter((f) => f != null)
    .find((f) => {
      const tableName = f.access_info?.tables_created?.[0]?.table_name ?? f.table_name;
      return tableName === state.activeTab || f.file_id === state.activeTab;
    });
  const reportUrl = activeUpload?.profiling_report_url
    ? `${baseUrl}${activeUpload.profiling_report_url}`
    : "";

  return (
    <div className="flex flex-col gap-4">
      <div className="w-full mb-6">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-base font-bold text-brand-darkblue">
            Data Profiling Pipeline
          </h2>
          {shouldShowButtons && (
            <div className="relative" ref={dropdownRef}>
              <button
                className="flex items-center gap-1.5 px-3 py-1.5 bg-brand-darkblue hover:bg-brand-blue text-white rounded-lg text-xs font-medium transition-colors"
                onClick={() => setShowDropdown(!showDropdown)}
              >
                <Download size={13} />
                All Downloads
                <svg
                  className={`w-3 h-3 transition-transform ${showDropdown ? "rotate-180" : ""}`}
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M19 9l-7 7-7-7"
                  />
                </svg>
              </button>
              {showDropdown && (
                <div className="absolute right-0 mt-1 w-56 bg-white border border-gray-200 rounded-lg shadow-lg z-10">
                  <button
                    onClick={() => {
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
                      console.log('[ProfilingResult] Metadata Template Excel button clicked');
                      console.log('[ProfilingResult] metadataExportRef.current:', metadataExportRef.current);
                      if (metadataExportRef.current) {
                        try {
                          metadataExportRef.current();
                        } catch (error) {
                          console.error('[ProfilingResult] Error calling metadata export:', error);
                          alert('Failed to export Metadata Template. Please try again.');
                        }
                      } else {
                        console.log('[ProfilingResult] No metadata export function available');
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
                      console.log('[ProfilingResult] Duplicate Excel button clicked');
                      try {
                        handleDownloadDuplicatesExcel();
                      } catch (error) {
                        console.error('[ProfilingResult] Error downloading duplicates:', error);
                        alert('Failed to download Duplicate Excel. Please try again.');
                      }
                      setShowDropdown(false);
                    }}
                    className="w-full text-left px-4 py-2 text-xs hover:bg-gray-50 flex items-center gap-2 border-b border-gray-100"
                  >
                    <Download size={12} className="text-orange-600" />
                    Duplicate Excel
                  </button>
                  <button
                    onClick={() => {
                      console.log('[ProfilingResult] Detailed Profiling Excel button clicked');
                      console.log('[ProfilingResult] detailedProfilingExportRef.current:', detailedProfilingExportRef.current);
                      if (detailedProfilingExportRef.current) {
                        try {
                          detailedProfilingExportRef.current();
                        } catch (error) {
                          console.error('[ProfilingResult] Error calling detailed profiling export:', error);
                          alert('Failed to export Detailed Profiling. Please try again.');
                        }
                      } else {
                        console.log('[ProfilingResult] No detailed profiling export function available');
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

        <div className="flex bg-white rounded-lg border-font-dark/25 border items-start">
          <StepSidebar
            steps={state.steps}
            currentStep={state.currentStep}
            canProceedToStep={(stepId) => {
              if (stepId <= state.maxVisitedStep) return true;
              return canProceedToStep(stepId) && !state.apiInProgress;
            }}
            loadingStates={state.loadingStates}
            onStepClick={(stepId) => {
              if (stepId === state.currentStep) return;
              if (
                stepId <= state.maxVisitedStep ||
                (!state.apiInProgress && canProceedToStep(stepId))
              ) {
                if (stepId === 5) {
                  const payload = buildStep3ReduxPayload(
                    state.dataDictionaryJson,
                    state.dataDictionaryState,
                  );
                  if (payload) {
                    dispatch(setStep3Response(payload));
                  }
                }
                state.setCurrentStep(stepId);
              }
            }}
          />

          <div className="flex-1 overflow-hidden bg-brand-light  max-w-full overflow-x-auto shrink">
            <div className="h-full p-6 overflow-y-auto">
              {state.currentStep === 1 && (
                <DatasetOverviewStep
                  profilingData={profilingData}
                  activeTab={state.activeTab}
                  setActiveTab={state.setActiveTab}
                  formatDate={formatDate}
                  profileSummaryCollapsed={state.profileSummaryCollapsed}
                  setProfileSummaryCollapsed={state.setProfileSummaryCollapsed}
                  isLoadingResponse={state.loadingStates[1]}
                  initialMessageData={state.initialMessageData}
                  accordionStates={state.accordionStates}
                  toggleAccordion={toggleAccordion}
                  setOpen={state.setOpen}
                  onRetry={handleDatasetOverviewRetry}
                  onNext={stepNavigationHandlers[2]}
                  onApplyHumanInputChanges={handleDatasetOverviewHumanInputApply}
                  steps={state.steps}
                  currentStep={state.currentStep}
                />
              )}

              {state.currentStep === 2 && (
                <RelationshipAnalysisStep
                  initialMessageData={state.initialMessageData}
                  setRelationshipResponse={state.setRelationshipResponse}
                  setPrevRelationshipResponse={
                    state.setPrevRelationshipResponse
                  }
                  prevRelationshipResponse={state.prevRelationshipResponse}
                  setIsLoadingRelationship={handleLoadingStateChange(2)}
                  relationshipRetryRef={state.relationshipRetryRef}
                  isLoadingRelationship={state.loadingStates[2]}
                  steps={state.steps}
                  onPrevious={stepNavigationHandlers[1]}
                  onNext={stepNavigationHandlers[3]}
                  onRetry={() => resetStepsFromStep(2)}
                  hasApiBeenCalled={state.stepApiCallTracker.has(2)}
                  markApiCalled={() => markApiCalled(2)}
                  onUseResponse={(response, isModified) =>
                    handlers.handleRelationshipUseResponse(
                      response,
                      isModified,
                      state.setRelationshipResponse,
                      state.setPrevRelationshipResponse,
                      state.setModifiedRelationshipResponse,
                    )
                  }
                  modifiedRelationshipResponse={
                    state.modifiedRelationshipResponse
                  }
                />
              )}

              {state.currentStep === 3 && (
                <DataDictionaryStep
                  initialMessageData={state.initialMessageData}
                  relationshipResponse={state.relationshipResponse}
                  dataDictionaryJson={state.dataDictionaryJson}
                  dataDictionaryResponse={state.dataDictionaryResponse}
                  dataDictionaryState={state.dataDictionaryState}
                  setDataDictionaryJson={state.setDataDictionaryJson}
                  setDataDictionaryResponse={state.setDataDictionaryResponse}
                  setDataDictionaryState={state.setDataDictionaryState}
                  setIsLoadingDataDictionary={handleLoadingStateChange(3)}
                  dataDictionaryRetryRef={state.dataDictionaryRetryRef}
                  clearDataDictionaryState={clearDataDictionaryState}
                  isLoadingDataDictionary={state.loadingStates[3]}
                  steps={state.steps}
                  onPrevious={stepNavigationHandlers[2]}
                  onNext={stepNavigationHandlers[4]}
                  onRetry={() => resetStepsFromStep(3)}
                  hasApiBeenCalled={state.stepApiCallTracker.has(3)}
                  markApiCalled={() => markApiCalled(3)}
                  onUseResponse={(response, isModified) =>
                    handlers.handleDataDictionaryUseResponse(
                      response,
                      isModified,
                      state.setModifiedDataDictionaryResponse,
                      state.setDataDictionaryJson,
                      state.setDataDictionaryState,
                    )
                  }
                  modifiedDataDictionaryResponse={
                    state.modifiedDataDictionaryResponse
                  }
                />
              )}

              {state.currentStep === 4 && (
                <SimilarityCheckStep
                  dartTableEntries={state.dartTableEntries}
                  addDartTableEntry={addDartTableEntry}
                  updateDartTableEntry={updateDartTableEntry}
                  removeDartTableEntry={removeDartTableEntry}
                  handleSimilarityCheck={handlers.handleSimilarityCheck}
                  isLoadingSimilarity={state.loadingStates[4]}
                  similarityResponse={state.similarityResponse}
                  setSimilarityResponse={state.setSimilarityResponse}
                  setDartTableEntries={state.setDartTableEntries}
                  setSkippedSteps={state.setSkippedSteps}
                  steps={state.steps}
                  skippedSteps={state.skippedSteps}
                  onPrevious={stepNavigationHandlers[3]}
                  onNext={stepNavigationHandlers[5]}
                  onRetry={() => resetStepsFromStep(4)}
                  hasApiBeenCalled={state.stepApiCallTracker.has(4)}
                  markApiCalled={() => markApiCalled(4)}
                  databaseName={state.databaseName}
                  setDatabaseName={state.setDatabaseName}
                  tableSchemaFields={state.tableSchemaFields}
                  dynamicFilters={state.dynamicFilters}
                  setDynamicFilters={state.setDynamicFilters}
                  tableSchemaError={state.tableSchemaError}
                  handleValidateDatabase={handlers.handleValidateDatabase}
                />
              )}

              {state.currentStep === 5 && (
                <DartSuggestion
                  onPrevious={stepNavigationHandlers[4]}
                  onNext={stepNavigationHandlers[6]}
                  onRetry={() => resetStepsFromStep(5)}
                />
              )}

              {state.currentStep === 6 && (
                <GenericAnalysisStep
                  title="Data Anomaly Analysis"
                  hasData={!!profilingData?.successful_uploads?.length}
                  isLoading={state.loadingStates[6]}
                  steps={state.steps}
                  stepIndex={6}
                  previousLabel="Previous: Reference Suggestion"
                  nextLabel="Next: Metadata Template"
                  onPrevious={stepNavigationHandlers[5]}
                  onNext={stepNavigationHandlers[7]}
                  onRetry={() => resetStepsFromStep(6)}
                  onUseResponse={(response, isModified) =>
                    handlers.handleAnomalyUseResponse(
                      response,
                      isModified,
                      state.setModifiedAnomalyResponse,
                      state.setAnomalyData,
                    )
                  }
                  modifiedAnomalyResponse={state.modifiedAnomalyResponse}
                >
                  <DataAnomalyAnalyzer
                    setAnomalyData={state.setAnomalyData}
                    anomalyData={state.anomalyData}
                    onLoadingChange={handleLoadingStateChange(6)}
                    hasApiBeenCalled={state.stepApiCallTracker.has(6)}
                    markApiCalled={() => markApiCalled(6)}
                    modifiedResponse={state.modifiedAnomalyResponse}
                  />
                </GenericAnalysisStep>
              )}

              {state.currentStep === 7 && (
                <GenericAnalysisStep
                  title="Metadata Template"
                  hasData={!!profilingData?.successful_uploads?.length}
                  isLoading={state.loadingStates[7]}
                  steps={state.steps}
                  stepIndex={7}
                  previousLabel="Previous: Data Anomaly Analysis"
                  nextLabel="Next: Detailed Profiling"
                  onPrevious={stepNavigationHandlers[6]}
                  onNext={() => {
                    state.setSteps((prev) =>
                      prev.map((step) => ({
                        ...step,
                        completed: step.id <= 7 ? true : step.completed,
                      })),
                    );

                    state.setMaxVisitedStep((prev) => Math.max(prev, 8));
                    state.setCurrentStep(8);
                  }}
                  onRetry={() => resetStepsFromStep(7)}
                  onUseResponse={(response, isModified) =>
                    handlers.handleMetadataUseResponse(
                      response,
                      isModified,
                      state.setModifiedMetadataResponse,
                      state.setPrevMetadataResponse,
                    )
                  }
                  modifiedMetadataResponse={state.modifiedMetadataResponse}
                >
                  <MetadataTemplate
                    profilingData={profilingData}
                    setMetadataTemplateResponse={state.setPrevMetadataResponse}
                    metadataResponse={state.prevMetadataResponse}
                    dataDictionaryTableId={state.dataDictionaryState.dataDictionaryTableId}
                    onLoadingChange={handleLoadingStateChange(7)}
                    hasApiBeenCalled={state.stepApiCallTracker.has(7)}
                    markApiCalled={() => markApiCalled(7)}
                    exportRef={metadataExportRef}
                  />
                </GenericAnalysisStep>
              )}

              {state.currentStep === 8 && (
                <GenericAnalysisStep
                  title="Detailed Table Profiling"
                  hasData={
                    !!state.initialMessageData.length &&
                    !!(
                      state.dataDictionaryJson ||
                      state.dataDictionaryState?.json ||
                      state.dataDictionaryState?.resultData?.length
                    )
                  }
                  isLoading={false}
                  steps={state.steps}
                  stepIndex={8}
                  previousLabel="Previous: Metadata Template"
                  nextLabel={workflowFinished ? "Completed" : "Finish"}
                  alwaysShowNext={
                    !!state.initialMessageData.length &&
                    !!(
                      state.dataDictionaryJson ||
                      state.dataDictionaryState?.json ||
                      state.dataDictionaryState?.resultData?.length
                    )
                  }
                  finishMessage={
                    workflowFinished
                      ? "Profiling workflow complete. Use All Downloads at the top of the page to export your results."
                      : undefined
                  }
                  onPrevious={stepNavigationHandlers[7]}
                  onNext={handleFinishWorkflow}
                  onRetry={() => resetStepsFromStep(8)}
                  showBotIcon={false}
                >
                  {renderStep7Content({
                    initialMessageData: state.initialMessageData,
                    dataDictionaryJson: state.dataDictionaryJson,
                    dataDictionaryState: state.dataDictionaryState,
                    anomalyData: state.anomalyData,
                    similarityResponse: state.similarityResponse,
                    skippedSteps: state.skippedSteps,
                    setCurrentStep: state.setCurrentStep,
                    exportRef: detailedProfilingExportRef,
                    uploadData: profilingData,
                  })}
                  {workflowFinished ? (
                    <div className="mt-6 flex justify-end">
                      <button
                        type="button"
                        onClick={handleContinueToMapping}
                        className="bg-brand-blue hover:bg-brand-blue/75 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors"
                      >
                        Continue to Mapping <ChevronRight size={16} />
                      </button>
                    </div>
                  ) : null}
                </GenericAnalysisStep>
              )}
            </div>
          </div>

          <ProfilingReportModal
            isOpen={state.open}
            onClose={() => state.setOpen(false)}
            reportUrl={reportUrl}
          />

          <ChatSidebar
            isOpen={isChatOpen}
            onClose={() => setIsChatOpen(false)}
            messages={state.qaMessages}
            inputMessage={state.inputMessage}
            onInputChange={state.setInputMessage}
            onSend={handleSendQAMessage}
            onKeyPress={handleKeyPress}
          />
        </div>
      </div>
    </div>
  );
}
