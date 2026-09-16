import { useEffect, useReducer, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { Loader2, RefreshCw, Download, ChevronLeft, ChevronRight } from "lucide-react";
import { exportMappingDataToExcel } from "../utils/excelExport";
import { mappingService } from "../end-points/mappingService";
import type { IngestRequest, SubjectAreaStatus } from "../end-points/mappingService";
import { mappingReducer, initialState } from "../state/reducers/mappingReducer";
import ConfigurationForm from "../components/mapping/ConfigurationForm";
import MappingTable from "../components/mapping/MappingTable";
import QuestionsPanel from "../components/mapping/QuestionsPanel";
import ReviewQuestionsGrid from "../components/mapping/ReviewQuestionsGrid";
import ProgressStep from "../components/mapping/ProgressStep";
import IssuesPanel from "../components/mapping/IssuesPanel";
import IssuesGrid from "../components/mapping/IssuesGrid";
import Toast from "../components/Toast";
import {
  canResumeMappingRun,
  canResumeMappingRunFromSummary,
  getAppSessionDetail,
  getAppSessionSummary,
  saveMappingResumeState,
  saveMappingReviewDraft,
  type AppSessionDetail,
  type AppSessionSummaryDetail,
} from "../end-points/appSessionsApi";
import { getCurrentAppSessionId, onSessionChanged } from "../utils/appSessionStorage";

interface SessionSummary {
  name: string;
  createTime: string;
  updateTime: string;
}

export default function Mapping() {
  const location = useLocation();
  const [state, dispatch] = useReducer(mappingReducer, initialState);
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [subjectAreaStatuses, setSubjectAreaStatuses] = useState<SubjectAreaStatus[]>([]);
  const [isLoadingSubjectAreas, setIsLoadingSubjectAreas] = useState<boolean>(true);
  const [isQuestionsPanelCollapsed, setIsQuestionsPanelCollapsed] = useState(true);
  const [session, setSession] = useState<SessionSummary | null>(null);
  const [sessionDetail, setSessionDetail] = useState<AppSessionDetail | null>(null);
  const [sessionSummary, setSessionSummary] = useState<AppSessionSummaryDetail | null>(null);
  const hydratedRef = useRef(false);
  const isRefreshingSummaryRef = useRef(false);
  const requestedQuestionsRunIdRef = useRef<string | null>(null);
  const MAPPING_POLL_INTERVAL_MS = 10000;
  const shouldOpenSavedMapping =
    new URLSearchParams(location.search).get("resume") === "1" ||
    Boolean((location.state as { openSavedMapping?: boolean } | null)?.openSavedMapping);
  const fromProfilingComplete = Boolean(
    (location.state as { fromProfilingComplete?: boolean } | null)?.fromProfilingComplete,
  );

  const resetToConfigurationView = () => {
    dispatch({
      type: 'HYDRATE_STATE',
      payload: {
        currentStep: 1,
        mappingData: null,
        baselineMappingData: null,
        step4Data: null,
        step4StatePath: null,
        step3Questions: [],
        selectedMappingIndex: 0,
        answers: {},
        feedbacks: {},
        editingCell: null,
        activeTab: 'mappings',
        validationErrors: [],
        subjectAreaBuilt: false,
      },
    });
  };

  const applySessionDetail = (detail: AppSessionDetail) => {
    setSessionDetail(detail);
    setSessionSummary({
      session: detail.session,
      profiling_run: null,
      mapping_run: detail.mapping_run
        ? {
            id: detail.mapping_run.id,
            status: detail.mapping_run.status,
            current_step: detail.mapping_run.current_step,
            mapping_run_id: detail.mapping_run.mapping_run_id,
            started_at: detail.mapping_run.started_at,
            completed_at: detail.mapping_run.completed_at,
            error_message: detail.mapping_run.error_message,
            has_resume: canResumeMappingRun(detail),
          }
        : null,
      extract_run: null,
      flags: {
        has_profiling_resume: false,
        has_mapping_resume: canResumeMappingRun(detail),
        has_extract_resume: false,
      },
    });
    setSession({
      name: detail.session.title,
      createTime: detail.session.created_at || new Date().toISOString(),
      updateTime: detail.session.updated_at || new Date().toISOString(),
    });
    const mappingRun = detail.mapping_run;
    if (!mappingRun?.resume_state) {
      return;
    }
    const resumeState = mappingRun.resume_state || {};
    if (canResumeMappingRun(detail)) {
      dispatch({
        type: 'HYDRATE_STATE',
        payload: {
          currentStep: Number(resumeState.currentStep || 1),
          mappingData: resumeState.mappingData || null,
          baselineMappingData: resumeState.baselineMappingData || null,
          step4Data: resumeState.step4Data || null,
          step4StatePath: resumeState.step4StatePath || null,
          step3Questions: resumeState.step3Questions || [],
          selectedMappingIndex: resumeState.selectedMappingIndex ?? 0,
          answers: mappingRun.review_draft?.answers || resumeState.answers || {},
          feedbacks: mappingRun.review_draft?.feedbacks || resumeState.feedbacks || {},
          activeTab: (mappingRun.review_draft?.active_tab || resumeState.activeTab || 'mappings') as any,
        },
      });
    }
  };

  const loadSessionState = async () => {
    const appSessionId = getCurrentAppSessionId();
    if (!appSessionId) {
      setSession(null);
      setSessionDetail(null);
      setSessionSummary(null);
      hydratedRef.current = true;
      return;
    }
    try {
      const detail = await getAppSessionDetail(appSessionId);
      applySessionDetail(detail);
      hydratedRef.current = true;
    } catch (error: any) {
      console.error("Failed to restore mapping session:", error);
      setSession(null);
      setSessionDetail(null);
      setSessionSummary(null);
      setToastMessage(error?.message || "Failed to restore mapping session.");
      hydratedRef.current = true;
    }
  };

  const loadSessionSummary = async () => {
    const appSessionId = getCurrentAppSessionId();
    resetToConfigurationView();
    if (!appSessionId) {
      setSession(null);
      setSessionDetail(null);
      setSessionSummary(null);
      hydratedRef.current = true;
      return;
    }
    try {
      const detail = await getAppSessionSummary(appSessionId);
      setSessionSummary(detail);
      setSession({
        name: detail.session.title,
        createTime: detail.session.created_at || new Date().toISOString(),
        updateTime: detail.session.updated_at || new Date().toISOString(),
      });
      hydratedRef.current = true;
    } catch (error: any) {
      console.error("Failed to load mapping session summary:", error);
      setSession(null);
      setSessionSummary(null);
      hydratedRef.current = true;
    }
  };

  const fetchSubjectAreaStatuses = async () => {
    setIsLoadingSubjectAreas(true);
    try {
      const statuses = await mappingService.getSubjectAreaStatuses();
      setSubjectAreaStatuses(statuses);
    } catch (error: any) {
      console.error("Failed to load subject area statuses:", error);
      setToastMessage(error?.message || "Failed to load subject areas.");
    } finally {
      setIsLoadingSubjectAreas(false);
    }
  };

  useEffect(() => {
    fetchSubjectAreaStatuses();
  }, []);

  useEffect(() => {
    if (shouldOpenSavedMapping) {
      void loadSessionState();
    } else {
      void loadSessionSummary();
    }
    return onSessionChanged(() => {
      if (shouldOpenSavedMapping) {
        void loadSessionState();
      } else {
        void loadSessionSummary();
      }
    });
  }, [shouldOpenSavedMapping]);

  useEffect(() => {
    const mappingRun = sessionSummary?.mapping_run;
    if (!mappingRun) {
      return;
    }
    if (canResumeMappingRunFromSummary(sessionSummary)) {
      return;
    }
    if (!["RUNNING", "INGESTED"].includes(mappingRun.status)) {
      return;
    }

    const interval = globalThis.setInterval(() => {
      const appSessionId = getCurrentAppSessionId();
      if (!appSessionId || isRefreshingSummaryRef.current) {
        return;
      }
      isRefreshingSummaryRef.current = true;
      void getAppSessionSummary(appSessionId)
        .then((detail) => {
          setSessionSummary(detail);
          setSession({
            name: detail.session.title,
            createTime: detail.session.created_at || new Date().toISOString(),
            updateTime: detail.session.updated_at || new Date().toISOString(),
          });
          if (canResumeMappingRunFromSummary(detail) && shouldOpenSavedMapping) {
            void loadSessionState();
          }
        })
        .catch((error) => {
          console.warn("Failed to refresh mapping session status", error);
        })
        .finally(() => {
          isRefreshingSummaryRef.current = false;
        });
    }, MAPPING_POLL_INTERVAL_MS);

    return () => globalThis.clearInterval(interval);
  }, [sessionSummary, shouldOpenSavedMapping]);

  const editableFields = [
    "rule_type",
    "source_entity",
    "source_field_names",
    "lookup_tables",
    "join_condition",
    "row_filter_text",
    "transformation_rules_text",
    "special_considerations_text",
  ];

  const unansweredQuestionIds = new Set(
    (state.step3Questions || [])
      .map((q: any) => q?.question_id)
      .filter((qid: any) => qid && !(state.answers?.[qid] || "").trim())
  );

  const rowsMissingFeedback = (() => {
    if (!state.mappingData?.column_mappings || !state.baselineMappingData?.column_mappings) return new Set<string>();
    const baselineById = new Map(
      (state.baselineMappingData.column_mappings || []).map((r: any) => [r.row_id, r])
    );
    const out = new Set<string>();
    (state.mappingData.column_mappings || []).forEach((row: any) => {
      const base = baselineById.get(row.row_id);
      if (!base) return;
      const changed = editableFields.some(
        (f) => JSON.stringify((row as any)?.[f] ?? null) !== JSON.stringify((base as any)?.[f] ?? null)
      );
      const hasFeedback = Boolean((state.feedbacks?.[row.row_id] || "").trim());
      if (changed && !hasFeedback) out.add(row.row_id);
    });
    return out;
  })();

  const step2ActiveTab = state.activeTab === 'questions' ? 'questions' : 'mappings';
  const step4ActiveTab = state.activeTab === 'issues' ? 'issues' : 'mappings';
  const activeMappingRun = sessionSummary?.mapping_run || sessionDetail?.mapping_run || null;
  const mappingIsRunning = activeMappingRun ? ["RUNNING", "INGESTED"].includes(activeMappingRun.status) : false;
  const mappingIsReady = sessionSummary
    ? canResumeMappingRunFromSummary(sessionSummary)
    : sessionDetail
      ? canResumeMappingRun(sessionDetail)
      : false;
  const mappingStatusLabel = activeMappingRun?.status === "INGESTED" ? "Drafting in progress" : "Ingestion in progress";

  const handleAnswerChange = (qId: string, value: string) => {
    dispatch({ type: 'UPDATE_ANSWER', payload: { id: qId, value } });
  };

  const handleFeedbackChange = (rowId: string, value: string) => {
    dispatch({ type: 'UPDATE_FEEDBACK', payload: { id: rowId, value } });
  };

  const updateMappingField = (rowIdx: number, field: string, value: any) => {
    dispatch({ type: 'UPDATE_MAPPING_FIELD', payload: { rowIdx, field, value } });
  };


  const validateForm = (data: {
    interfaceCode: string;
    subjectAreas: string[];
    targetLayout: "UPLOAD_FILES" | "INDEMAP";
    sourceFiles: FileList | null;
    targetFiles: FileList | null;
    indemapPairs: Array<{ databaseName: string; tableName: string }>;
  }): boolean => {
    const errors: string[] = [];
    if (!data.interfaceCode.trim()) errors.push("Interface Code is required.");
    if (!data.subjectAreas.length) errors.push("At least one subject area is required.");
    if (!data.sourceFiles || data.sourceFiles.length === 0) errors.push("Source files metadata is required.");
    if (data.targetLayout === "UPLOAD_FILES") {
      if (!data.targetFiles || data.targetFiles.length === 0) errors.push("Target files metadata is required.");
    } else {
      if (!data.indemapPairs || data.indemapPairs.length === 0) {
        errors.push("At least one case-sensitive (database, table) pair is required for IndeMap layout.");
      }
    }
    dispatch({ type: 'SET_ERRORS', payload: errors });
    return errors.length === 0;
  };

  const handleSubmit = async (formData: {
    interfaceCode: string;
    instructionsText: string;
    subjectAreas: string[];
    targetLayout: "UPLOAD_FILES" | "INDEMAP";
    indemapPairs: Array<{ databaseName: string; tableName: string }>;
    sourceFiles: FileList | null;
    targetFiles: FileList | null;
  }) => {
    if (!validateForm(formData)) return;
    const appSessionId = getCurrentAppSessionId();
    if (!appSessionId) {
      setToastMessage("Please create or select a session first.");
      return;
    }

    dispatch({ type: 'SET_LOADING', payload: { isSubmitting: true } });
    dispatch({ type: 'SET_ERRORS', payload: [] });

    try {
      const ingestData: IngestRequest = {
        interface_code: formData.interfaceCode,
        instructions_text: formData.instructionsText,
        subject_areas: formData.subjectAreas,
        target_layout: formData.targetLayout,
        source_files: formData.sourceFiles!,
        target_files: formData.targetLayout === "UPLOAD_FILES" ? formData.targetFiles || undefined : undefined,
        indemap_pairs: formData.targetLayout === "INDEMAP"
          ? formData.indemapPairs.map((p) => ({
              database_name: p.databaseName,
              table_name: p.tableName,
            }))
          : undefined,
      };

      const { run_id } = await mappingService.ingest(ingestData);

      dispatch({ type: 'SET_LOADING', payload: { isDrafting: true } });
      const data = await mappingService.draft(run_id);
      
      dispatch({ type: 'SET_MAPPING_DATA', payload: data });
      dispatch({ type: 'SET_STEP', payload: 2 });

      dispatch({ type: 'SET_LOADING', payload: { isFetchingQuestions: true } });
      try {
        const questionsData = await mappingService.getReviewQuestions(data.metadata.run_id);
        dispatch({ type: 'SET_QUESTIONS', payload: questionsData.step3_questions });
      } catch (qError) {
        console.error("Failed to fetch questions:", qError);
      } finally {
        dispatch({ type: 'SET_LOADING', payload: { isFetchingQuestions: false } });
      }

    } catch (error: any) {
      console.error("Error in mapping pipeline:", error);
      dispatch({ type: 'SET_ERRORS', payload: [error.message || "Failed to process mapping. Please try again."] });
    } finally {
      dispatch({ type: 'SET_LOADING', payload: { isSubmitting: false, isDrafting: false } });
    }
  };

  const handleBuildSubjectArea = async (data: {
    subjectArea: string;
    tablesAndColumnsFile: File;
    tablesAndIndexesFile: File;
  }) => {
    dispatch({ type: 'SET_LOADING', payload: { isBuildingSubjectArea: true } });
    
    try {
      const result = await mappingService.buildErwinSubjectArea({
        subject_area: data.subjectArea,
        tables_and_columns_file: data.tablesAndColumnsFile,
        tables_and_indexes_file: data.tablesAndIndexesFile,
      });

      if (result.inserted) {
        await fetchSubjectAreaStatuses();
      } else {
        setToastMessage("Failed to build subject area. Please try again.");
      }
    } catch (error: any) {
      setToastMessage(error.message || "Failed to build subject area.");
      throw error;
    } finally {
      dispatch({ type: 'SET_LOADING', payload: { isBuildingSubjectArea: false } });
    }
  };

  const handleProceedToUpdate = async () => {
    if (state.loading.isSubmitting || state.loading.isApplyingStep4) {
      return;
    }
    if (state.currentStep === 2) {
      if (!state.mappingData?.metadata?.run_id || !state.baselineMappingData?.column_mappings) {
        dispatch({ type: 'SET_ERRORS', payload: ["Missing baseline mapping data for review submission."] });
        return;
      }

      const baselineById = new Map(
        (state.baselineMappingData.column_mappings || []).map((r: any) => [r.row_id, r])
      );

      const nonEmptyFeedbacks: Record<string, string> = {};
      Object.entries(state.feedbacks || {}).forEach(([rowId, txt]) => {
        const t = (txt || "").trim();
        if (t) nonEmptyFeedbacks[rowId] = t;
      });

      // --- Validation before capture (do NOT submit if incomplete) ---
      const validationErrors: string[] = [];

      // 1) Require all review questions to be answered (text-only v1).
      if (unansweredQuestionIds.size > 0) {
        validationErrors.push(`Please answer all review questions (${unansweredQuestionIds.size} missing).`);
      }

      const changedRows = (state.mappingData.column_mappings || []).filter((row: any) => {
        const base = baselineById.get(row.row_id);
        const hasFeedback = Boolean(nonEmptyFeedbacks[row.row_id]);
        if (!base) return hasFeedback;

        const changed = editableFields.some((f) => {
          const a = row?.[f] ?? null;
          const b = (base as any)?.[f] ?? null;
          return JSON.stringify(a) !== JSON.stringify(b);
        });

        return changed || hasFeedback;
      });

      // 2) If a row was edited, require feedback for that row.
      const changedRowsMissingFeedback = changedRows.filter((row: any) => {
        const base = baselineById.get(row.row_id);
        if (!base) return false;
        const hasFeedback = Boolean(nonEmptyFeedbacks[row.row_id]);
        if (hasFeedback) return false;
        return editableFields.some(
          (f) => JSON.stringify((row as any)?.[f] ?? null) !== JSON.stringify((base as any)?.[f] ?? null)
        );
      });
      if (changedRowsMissingFeedback.length > 0) {
        validationErrors.push(`Please add feedback for each edited row (${changedRowsMissingFeedback.length} missing).`);
      }

      if (validationErrors.length > 0) {
        dispatch({ type: 'SET_ERRORS', payload: validationErrors });
        return;
      }

      dispatch({ type: 'SET_LOADING', payload: { isSubmitting: true } });
      dispatch({ type: 'SET_ERRORS', payload: [] });

      try {
        const submitResp = await mappingService.submitReview({
          run_id: state.mappingData.metadata.run_id,
          changed_rows: changedRows,
          answers: state.answers || {},
          feedbacks: nonEmptyFeedbacks,
          answered_by: null,
        });

        // Step 3.5 persisted -> immediately run Step 4 and then display final output.
        dispatch({ type: 'SET_STEP', payload: 3.5 });
        dispatch({ type: 'SET_LOADING', payload: { isApplyingStep4: true } });

        const runId = submitResp?.run_id || state.mappingData.metadata.run_id;
        const step4Resp = await mappingService.applyReview(runId);
        if (step4Resp?.step4_state) {
          dispatch({
            type: 'SET_STEP4_DATA',
            payload: {
              step4Data: step4Resp.step4_state,
              step4StatePath: step4Resp.step4_state_path || null,
            }
          });
          dispatch({ type: 'SET_ACTIVE_TAB', payload: 'mappings' });
          dispatch({ type: 'SET_SELECTED_INDEX', payload: 0 });
          dispatch({ type: 'SET_STEP', payload: 4 });
        } else {
          throw new Error("Step 4 returned no step4_state.");
        }
      } catch (error: any) {
        console.error("Failed to submit review:", error);
        dispatch({ type: 'SET_ERRORS', payload: [error.message || "Failed to submit review changes."] });
      } finally {
        dispatch({ type: 'SET_LOADING', payload: { isSubmitting: false, isApplyingStep4: false } });
      }
    } else if (state.currentStep === 3.5) {
      console.log("Finalizing updates...");
    }
  };

  const handleRefresh = async () => {
    if (!state.mappingData?.metadata?.run_id) return;

    dispatch({ type: 'SET_LOADING', payload: { isSubmitting: true, isDrafting: true } });
    dispatch({ type: 'SET_ERRORS', payload: [] });

    try {
      const data = await mappingService.draft(state.mappingData.metadata.run_id);
      dispatch({ type: 'SET_MAPPING_DATA', payload: data });
    } catch (error: any) {
      console.error("Error refreshing mapping:", error);
      dispatch({ type: 'SET_ERRORS', payload: [error.message || "Failed to refresh mapping. Please try again."] });
    } finally {
      dispatch({ type: 'SET_LOADING', payload: { isSubmitting: false, isDrafting: false } });
    }
  };

  useEffect(() => {
    if (!hydratedRef.current) return;
    const appSessionId = getCurrentAppSessionId();
    if (!appSessionId) return;
    if (state.currentStep === 1 && !state.mappingData && !state.step4Data) return;

    const timeout = globalThis.setTimeout(() => {
      void saveMappingResumeState(appSessionId, {
        status: state.currentStep === 4 ? "COMPLETED" : state.currentStep >= 2 ? "REVIEW" : "IDLE",
        current_step: state.currentStep === 4 ? "apply_review" : state.currentStep === 2 ? "review" : "ingest",
        resume_state: {
          currentStep: state.currentStep,
          mappingData: state.mappingData,
          baselineMappingData: state.baselineMappingData,
          step4Data: state.step4Data,
          step4StatePath: state.step4StatePath,
          step3Questions: state.step3Questions,
          selectedMappingIndex: state.selectedMappingIndex,
          activeTab: state.activeTab,
          answers: state.answers,
          feedbacks: state.feedbacks,
        },
      }).catch((error) => {
        console.error("Failed to save mapping resume state:", error);
      });
    }, 700);

    return () => globalThis.clearTimeout(timeout);
  }, [
    state.currentStep,
    state.mappingData,
    state.baselineMappingData,
    state.step4Data,
    state.step4StatePath,
    state.step3Questions,
    state.selectedMappingIndex,
    state.activeTab,
    state.answers,
    state.feedbacks,
  ]);

  useEffect(() => {
    if (!hydratedRef.current || state.currentStep !== 2 || !state.mappingData) return;
    const appSessionId = getCurrentAppSessionId();
    if (!appSessionId) return;
    const selectedRow = state.selectedMappingIndex != null
      ? state.mappingData?.column_mappings?.[state.selectedMappingIndex]?.row_id ?? null
      : null;

    const timeout = globalThis.setTimeout(() => {
      void saveMappingReviewDraft(appSessionId, {
        answers: state.answers,
        feedbacks: state.feedbacks,
        changed_rows: state.mappingData?.column_mappings || [],
        active_tab: state.activeTab,
        selected_row_id: selectedRow,
      }).catch((error) => {
        console.error("Failed to save mapping review draft:", error);
      });
    }, 800);

    return () => globalThis.clearTimeout(timeout);
  }, [
    state.currentStep,
    state.mappingData,
    state.answers,
    state.feedbacks,
    state.activeTab,
    state.selectedMappingIndex,
  ]);

  useEffect(() => {
    if (state.currentStep !== 2 || !state.mappingData?.metadata?.run_id) {
      requestedQuestionsRunIdRef.current = null;
      return;
    }
    if (state.step3Questions.length > 0 || state.loading.isFetchingQuestions) {
      return;
    }

    const runId = state.mappingData.metadata.run_id;
    if (requestedQuestionsRunIdRef.current === runId) {
      return;
    }

    requestedQuestionsRunIdRef.current = runId;
    dispatch({ type: 'SET_LOADING', payload: { isFetchingQuestions: true } });
    void mappingService
      .getReviewQuestions(runId)
      .then((questionsData) => {
        dispatch({ type: 'SET_QUESTIONS', payload: questionsData.step3_questions || [] });
      })
      .catch((error) => {
        console.error("Failed to fetch questions:", error);
        setToastMessage(error?.message || "Failed to load review questions.");
        requestedQuestionsRunIdRef.current = null;
      })
      .finally(() => {
        dispatch({ type: 'SET_LOADING', payload: { isFetchingQuestions: false } });
      });
  }, [
    state.currentStep,
    state.mappingData,
    state.step3Questions,
    state.loading.isFetchingQuestions,
  ]);

  return (
    <div className="flex flex-col gap-4">
      {session && (
        <div className="bg-green-50 border border-green-200 rounded-lg p-4">
          <p className="text-sm text-green-700 font-semibold">Active Session:</p>
          <p className="text-xs text-gray-600">Name: {session.name}</p>
          <p className="text-xs text-gray-600">
            Created: {new Date(session.createTime).toLocaleString()}
          </p>
        </div>
      )}
      {fromProfilingComplete && state.currentStep === 1 && !mappingIsReady ? (
        <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-900">
          Profiling is complete. Configure the mapping session below by selecting subject areas
          and uploading source/target metadata files to begin Phase 1 mapping.
        </div>
      ) : null}
      {session && activeMappingRun && state.currentStep === 1 && (
        <div
          className={`rounded-lg p-4 border ${
            mappingIsRunning
              ? "bg-amber-50 border-amber-200"
              : mappingIsReady
                ? "bg-green-50 border-green-200"
                : "bg-slate-50 border-slate-200"
          }`}
        >
          <p
            className={`text-sm font-semibold ${
              mappingIsRunning
                ? "text-amber-700"
                : mappingIsReady
                  ? "text-green-700"
                  : "text-slate-700"
            }`}
          >
            {mappingIsRunning
              ? "Mapping is running in this session"
              : mappingIsReady
                ? "Mapping is ready in this session"
                : "Mapping exists in this session"}
          </p>
          <p className="text-xs text-gray-600 mt-1">
            {mappingIsRunning ? mappingStatusLabel : "Open the saved mapping flow for this session."}
          </p>
        </div>
      )}
      <div className="w-full mb-6">
        <h2 className="text-base font-bold text-brand-darkblue mb-3">
          Data Mapping Pipeline
        </h2>

        <div className="flex bg-white rounded-lg border-font-dark/25 border items-start">
          <div className="flex-1 overflow-hidden bg-brand-light max-w-full overflow-x-auto shrink min-h-[600px]">
            <div className="h-full p-6 overflow-y-auto">

              {/* Step 1: Configuration */}
              {state.currentStep === 1 && (
                <div className="flex-col w-full">
                  <ConfigurationForm
                    onSubmit={handleSubmit}
                    onBuildSubjectArea={handleBuildSubjectArea}
                    isSubmitting={state.loading.isSubmitting}
                    isDrafting={state.loading.isDrafting}
                    validationErrors={state.validationErrors}
                    isBuildingSubjectArea={state.loading.isBuildingSubjectArea}
                    subjectAreaStatuses={subjectAreaStatuses}
                    isLoadingSubjectAreas={isLoadingSubjectAreas}
                  />
                </div>
              )}

              {/* Step 2: Mapping Review */}
              {state.currentStep === 2 && state.mappingData && (
                <div className="animate-in fade-in duration-500 h-full flex flex-col">
                  <div className="flex justify-between items-center mb-2">
                    <div className="flex items-center gap-6">
                      <div>
                        <h2 className="text-lg font-bold text-brand-darkblue">Mapping Review</h2>
                        <p className="text-sm text-gray-500">Review generated mappings and answer review questions.</p>
                      </div>
                      <div className="flex bg-gray-100 p-1 rounded-lg">
                        <button
                          onClick={() => dispatch({ type: 'SET_ACTIVE_TAB', payload: 'mappings' })}
                          className={`px-4 py-1.5 rounded-md text-xs font-semibold transition-all cursor-pointer ${step2ActiveTab === 'mappings' ? 'bg-white text-font-blue shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                        >
                          Mapping Table
                        </button>
                        <button
                          onClick={() => dispatch({ type: 'SET_ACTIVE_TAB', payload: 'questions' })}
                          className={`px-4 py-1.5 rounded-md text-xs font-semibold transition-all flex items-center gap-2 cursor-pointer ${step2ActiveTab === 'questions' ? 'bg-white text-font-blue shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                        >
                          Review Questions
                          {state.step3Questions.length > 0 && <span className="bg-teal-100 text-font-blue px-1.5 py-0.5 rounded-full text-[10px]">{state.step3Questions.length}</span>}
                          {state.loading.isFetchingQuestions && <Loader2 size={12} className="animate-spin text-font-blue" />}
                        </button>
                      </div>
                      <button
                        onClick={handleRefresh}
                        disabled={state.loading.isSubmitting}
                        title="Refresh Mapping"
                        className="p-2 text-gray-500 hover:text-font-blue hover:bg-brand-surface rounded-full transition-all disabled:opacity-50 cursor-pointer"
                      >
                        <RefreshCw size={20} className={state.loading.isSubmitting ? "animate-spin" : ""} />
                      </button>
                    </div>
                    <button
                      onClick={handleProceedToUpdate}
                      disabled={state.loading.isSubmitting || state.loading.isApplyingStep4}
                      className="bg-brand-darkblue text-white p-2 rounded-md hover:bg-brand-darkblue/90 transition-all font-medium shadow-md text-xs disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
                    >
                      {state.loading.isApplyingStep4
                        ? "Applying Review..."
                        : state.loading.isSubmitting
                          ? "Submitting Review..."
                          : "Finalize Update"}
                    </button>
                  </div>
                  <div className="flex justify-end mb-4">
                    <button
                      onClick={() => exportMappingDataToExcel(state.mappingData)}
                      className="flex gap-2 items-center bg-green-600 text-white p-2 rounded-md hover:bg-green-700 transition-all font-medium shadow-md cursor-pointer text-xs"
                    >
                      <Download size={16} />
                      Export as Excel
                    </button>
                  </div>
                  {step2ActiveTab === 'mappings' ? (
                    <div className="flex flex-row gap-6 h-[calc(100vh-280px)] min-h-[500px]">
                    <MappingTable
                        mappingData={state.mappingData}
                        selectedMappingIndex={state.selectedMappingIndex}
                        editingCell={state.editingCell}
                        feedbacks={state.feedbacks}
                        rowsMissingFeedback={rowsMissingFeedback}
                        onRowSelect={(index) => {
                          dispatch({ type: 'SET_SELECTED_INDEX', payload: index });
                          setIsQuestionsPanelCollapsed(false);
                        }}
                        onCellEdit={(cell) => dispatch({ type: 'SET_EDITING_CELL', payload: cell })}
                        onFieldUpdate={updateMappingField}
                        onFeedbackChange={handleFeedbackChange}
                      />
                      <div className="relative">
                        <button
                          onClick={() => setIsQuestionsPanelCollapsed(!isQuestionsPanelCollapsed)}
                          className="absolute -left-3 top-4 z-10 bg-white border border-gray-200 rounded-full p-1 hover:bg-gray-50 shadow-sm"
                        >
                          {isQuestionsPanelCollapsed ? <ChevronLeft size={16} /> : <ChevronRight size={16} />}
                        </button>
                        {!isQuestionsPanelCollapsed && (
                          <QuestionsPanel
                            mappingData={state.mappingData}
                            selectedMappingIndex={state.selectedMappingIndex}
                            answers={state.answers}
                            onAnswerChange={handleAnswerChange}
                            step3Questions={state.step3Questions}
                            unansweredQuestionIds={unansweredQuestionIds}
                          />
                        )}
                      </div>
                    </div>
                  ) : (
                    <ReviewQuestionsGrid
                      questions={state.step3Questions}
                      answers={state.answers}
                      isFetchingQuestions={state.loading.isFetchingQuestions}
                      onAnswerChange={handleAnswerChange}
                      unansweredQuestionIds={unansweredQuestionIds}
                    />
                  )}
                </div>
              )}

              {/* Step 3: Progress/Update In Progress */}
              {state.currentStep === 3.5 && (
                <ProgressStep onStartNew={() => dispatch({ type: 'SET_STEP', payload: 1 })} />
              )}

              {/* Step 4: Final Output (read-only) */}
              {state.currentStep === 4 && state.step4Data && (
                <div className="animate-in fade-in duration-500 h-full flex flex-col">
                  <div className="flex justify-between items-center mb-2">
                    <div className="flex items-center gap-6">
                      <div>
                        <h2 className="text-lg font-bold text-brand-darkblue">Final Mapping (Step 4)</h2>
                        <p className="text-sm text-gray-500">Step 4 applied review feedback and resolved issues where possible.</p>
                      </div>
                      <div className="flex bg-gray-100 p-1 rounded-lg">
                        <button
                          onClick={() => dispatch({ type: 'SET_ACTIVE_TAB', payload: 'mappings' })}
                          className={`px-4 py-1.5 rounded-md text-xs font-semibold transition-all cursor-pointer ${step4ActiveTab === 'mappings' ? 'bg-white text-font-blue shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                        >
                          Mapping Table
                        </button>
                        <button
                          onClick={() => dispatch({ type: 'SET_ACTIVE_TAB', payload: 'issues' })}
                          className={`px-4 py-1.5 rounded-md text-xs font-semibold transition-all flex items-center gap-2 cursor-pointer ${step4ActiveTab === 'issues' ? 'bg-white text-font-blue shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                        >
                          Issues
                          {Array.isArray(state.step4Data.issue_resolutions) && state.step4Data.issue_resolutions.length > 0 && (
                            <span className="bg-teal-100 text-font-blue px-1.5 py-0.5 rounded-full text-[10px]">
                              {state.step4Data.issue_resolutions.length}
                            </span>
                          )}
                        </button>
                      </div>
                    </div>
                  </div>

                  <div className="flex justify-end mb-4">
                    <button
                      onClick={() => exportMappingDataToExcel(state.step4Data)}
                      className="flex gap-2 items-center bg-green-600 text-white p-2 rounded-md hover:bg-green-700 transition-all font-medium shadow-md cursor-pointer text-xs"
                    >
                      <Download size={16} />
                      Export as Excel
                    </button>
                  </div>

                  {step4ActiveTab === 'mappings' ? (
                    <div className="flex flex-row gap-6 h-[calc(100vh-280px)] min-h-[500px]">
                      <MappingTable
                        mappingData={state.step4Data}
                        selectedMappingIndex={state.selectedMappingIndex}
                        editingCell={null}
                        feedbacks={{}}
                        rowsMissingFeedback={new Set()}
                        readOnly
                        showFeedback={false}
                        onRowSelect={(index) => dispatch({ type: 'SET_SELECTED_INDEX', payload: index })}
                        onCellEdit={() => {}}
                        onFieldUpdate={() => {}}
                        onFeedbackChange={() => {}}
                      />
                      <IssuesPanel
                        step4Data={state.step4Data}
                        selectedMappingIndex={state.selectedMappingIndex}
                        step3Questions={state.step3Questions}
                        answers={state.answers}
                        feedbacks={state.feedbacks}
                      />
                    </div>
                  ) : (
                    <IssuesGrid
                      step4Data={state.step4Data}
                      step3Questions={state.step3Questions}
                      answers={state.answers}
                      feedbacks={state.feedbacks}
                      onSelectRowId={(rowId) => {
                        const idx = (state.step4Data.column_mappings || []).findIndex((r: any) => r.row_id === rowId);
                        if (idx >= 0) dispatch({ type: 'SET_SELECTED_INDEX', payload: idx });
                        dispatch({ type: 'SET_ACTIVE_TAB', payload: 'mappings' });
                      }}
                    />
                  )}
                </div>
              )}

            </div>
          </div>
        </div>
      </div>
      {toastMessage && (
        <Toast
          message={toastMessage}
          onClose={() => setToastMessage(null)}
        />
      )}
    </div>
  );
}
