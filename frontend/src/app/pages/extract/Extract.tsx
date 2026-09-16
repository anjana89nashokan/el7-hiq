import { useState, useEffect, useRef } from "react";
import { useDispatch, useSelector } from "react-redux";
import { Info, RefreshCw } from "lucide-react";
import GenerateDriverMapping from "./GenerateDriverMapping";
import ExtractMetadata from "./ExtractMetadata";
import ExtractMapping from "./ExtractMapping";
import { createAppSession, getAppSessionDetail, saveExtractResumeState } from "../../end-points/appSessionsApi";
import {
  runUploadExtract,
  approveExtract,
  rejectExtract,
  fileLayoutCheckpoint,
  resetExtract,
  hydrateExtract,
  runExtractMetadata,
  resetForRetry,
  runJudgeH1,
} from "../../state/reducers/extract/extractReducer";
import type { AppDispatch, RootState } from "../../state/store";
import { getCurrentAppSessionId, getCurrentSessionRuntime, onSessionChanged } from "../../utils/appSessionStorage";
import BrdInfoReview from "../../components/extract/BrdInfoReview";
import FileLayoutReview from "../../components/extract/FileLayoutReview";
import ExtractStepSidebar from "../../components/extract/ExtractStepSidebar";
import FileUploadField from "../../components/extract/FileUploadField";
import type { RequirementLayer, FileLayoutField, JudgeKpiScore } from "../../end-points/extract/extractApi";
import { useExtractForm } from "../../hooks/useExtractForm";
import { useExtractSteps } from "../../hooks/useExtractSteps";
import { LAYOUT_ALLOWED_EXTENSIONS, UPLOAD_STEP_LABELS, FIELD_INFO } from "../../config/extractConfig";
import { getUserIdentity } from "../../utils/userIdentity";


export default function Extract() {
  const dispatch = useDispatch<AppDispatch>();
  const {
    uploadLoading, uploadStep, approveLoading, rejectLoading, fileLayoutLoading,
    error, brdInfo, validatedLayer, fileLayoutData, uploadSessionId, reviewStatus,
    brdGcsUri, layoutGcsUri, editedLayer, metadataLoading,
    judgeH1Loading, judgeH1Data, judgeH1Error,
  } = useSelector((state: RootState) => state.extract);

  const [newSessionLoading, setNewSessionLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<"requirement" | "fileLayout">("requirement");
  const [fileLayoutApproved, setFileLayoutApproved] = useState(false);
  const [fileLayoutSuccess, setFileLayoutSuccess] = useState(false);
  const [brdReactivated, setBrdReactivated] = useState(false);
  const [showMetadata, setShowMetadata] = useState(false);
  const [showDriverMapping, setShowDriverMapping] = useState(false);
  const [showExtractMapping, setShowExtractMapping] = useState(false);
  const [extractMappingApproved, setExtractMappingApproved] = useState(false);
  const [showKpiInfo, setShowKpiInfo] = useState(false);
  const [kpiExpanded, setKpiExpanded] = useState(false);

  const brdApproved = reviewStatus === "approved" && !brdReactivated;
  const enableExtractMetadata = brdApproved && fileLayoutSuccess;

  const { form, fieldErrors, submitAttempted, noSessionError, setFileField, setInterfaceCode, setBsaNotes, validate, setSubmitAttempted, setNoSessionError, setFieldErrors, reset: resetForm } = useExtractForm();
  const { currentStep, maxStep, completedSteps, setCurrentStep, reset: resetSteps } = useExtractSteps(showMetadata, showDriverMapping, showExtractMapping, extractMappingApproved);

  // Whole extract slice — used to snapshot state for session auto-save.
  const extractState = useSelector((state: RootState) => state.extract);
  // Tracks which session has been hydrated so auto-save doesn't fire mid-restore
  // and a fresh navigation re-hydrates from the server.
  const hydratedSessionRef = useRef<string | null>(null);
  const [hydrated, setHydrated] = useState(false);

  const hasErrors = Object.keys(fieldErrors).length > 0;

  const handleSubmit: React.ComponentProps<"form">["onSubmit"] = (e) => {
    e.preventDefault();
    setSubmitAttempted(true);
    const errors = validate();
    if (Object.keys(errors).length > 0) { setFieldErrors((prev) => ({ ...prev, ...errors })); return; }
    const sessionId = getCurrentAppSessionId();
    if (!sessionId) { setNoSessionError(true); return; }
    setNoSessionError(false);
    const { userId } = getCurrentSessionRuntime();
    const trimmedCode = form.interfaceCode.trim();
    sessionStorage.setItem("interfaceCode", trimmedCode);
    dispatch(runUploadExtract({
      brdFile: form.brdFile!,
      fileLayoutFile: form.fileLayoutFile!,
      transcriptFile: form.transcriptFile ?? null,
      interfaceCode: trimmedCode,
      bsaNotes: form.bsaNotes || undefined,
      sessionId,
      userId: userId ?? getUserIdentity().userId,
    }));
  };

  const resetLocalState = () => {
    resetForm();
    setFileLayoutApproved(false);
    setActiveTab("requirement");
    setShowMetadata(false);
    setShowDriverMapping(false);
    setBrdReactivated(false);
    setShowExtractMapping(false);
    setExtractMappingApproved(false);
    resetSteps();
  };

  const handleNewSession = async () => {
    setNewSessionLoading(true);
    try {
      const detail = await createAppSession(undefined, "extract");
      resetLocalState();
      dispatch(resetExtract());
      // A brand-new session has no saved progress — mark it hydrated so the
      // resume effect doesn't reset/refetch and auto-save can begin cleanly.
      hydratedSessionRef.current = detail.session.id;
      setHydrated(true);
    }
    catch (err) { console.error("Failed to create session:", err); }
    finally { setNewSessionLoading(false); }
  };

  // ── Resume: hydrate saved extract progress when the active session changes ──
  useEffect(() => {
    const hydrate = async () => {
      const sessionId = getCurrentAppSessionId();
      if (!sessionId) { setHydrated(true); return; }
      if (hydratedSessionRef.current === sessionId) return;
      setHydrated(false);
      // Clear any state from a previously-open session before restoring.
      dispatch(resetExtract());
      resetLocalState();
      try {
        const detail = await getAppSessionDetail(sessionId);
        const rs = detail.extract_run?.resume_state as Record<string, any> | undefined;
        if (rs && Object.keys(rs).length > 0) {
          if (rs.redux) dispatch(hydrateExtract(rs.redux));
          setShowMetadata(!!rs.showMetadata);
          setShowDriverMapping(!!rs.showDriverMapping);
          setShowExtractMapping(!!rs.showExtractMapping);
          setExtractMappingApproved(!!rs.extractMappingApproved);
          setActiveTab(rs.activeTab === "fileLayout" ? "fileLayout" : "requirement");
          setFileLayoutSuccess(!!rs.fileLayoutSuccess);
          setFileLayoutApproved(!!rs.fileLayoutApproved);
          setBrdReactivated(!!rs.brdReactivated);
          if (rs.interfaceCode) setInterfaceCode(rs.interfaceCode);
          if (typeof rs.currentStep === "number") setCurrentStep(rs.currentStep);
        }
      } catch (err) {
        console.error("Failed to hydrate extract session:", err);
      } finally {
        hydratedSessionRef.current = sessionId;
        setHydrated(true);
      }
    };
    hydrate();
    return onSessionChanged(hydrate);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Auto-save: debounced persistence of extract progress ──
  useEffect(() => {
    if (!hydrated) return;
    const sessionId = getCurrentAppSessionId();
    // Only persist once the upload step produced a requirement layer; there is
    // nothing meaningful to resume before that.
    if (!sessionId || !extractState.uploadSessionId) return;
    const handle = setTimeout(() => {
      saveExtractResumeState(sessionId, {
        status: extractMappingApproved ? "COMPLETED" : "RUNNING",
        current_step: `step_${currentStep}`,
        upload_session_id: extractState.uploadSessionId,
        brd_gcs_uri: extractState.brdGcsUri,
        layout_gcs_uri: extractState.layoutGcsUri,
        metadata_gcs_uri: extractState.metadataGcsUri,
        driver_gcs_uri: extractState.driverApproveGcsUri,
        resume_state: {
          currentStep,
          showMetadata,
          showDriverMapping,
          showExtractMapping,
          extractMappingApproved,
          activeTab,
          fileLayoutSuccess,
          fileLayoutApproved,
          brdReactivated,
          interfaceCode: form.interfaceCode,
          // Data needed to render the saved progress on resume.
          redux: {
            uploadSessionId: extractState.uploadSessionId,
            gcsPrefix: extractState.gcsPrefix,
            brdInfo: extractState.brdInfo,
            validatedLayer: extractState.validatedLayer,
            fileLayoutData: extractState.fileLayoutData,
            editedLayer: extractState.editedLayer,
            reviewStatus: extractState.reviewStatus,
            driverMappingData: extractState.driverMappingData,
            driverLogicData: extractState.driverLogicData,
            driverLogicBsaQuestions: extractState.driverLogicBsaQuestions,
            driverValidateData: extractState.driverValidateData,
            driverApproveData: extractState.driverApproveData,
            driverReviewStatus: extractState.driverReviewStatus,
            driverCheckpointData: extractState.driverCheckpointData,
            metadataData: extractState.metadataData,
            metadataReviewStatus: extractState.metadataReviewStatus,
            brdGcsUri: extractState.brdGcsUri,
            layoutGcsUri: extractState.layoutGcsUri,
            driverApproveGcsUri: extractState.driverApproveGcsUri,
            metadataGcsUri: extractState.metadataGcsUri,
            mappingData: extractState.mappingData,
            mappingApproved: extractState.mappingApproved,
            judgeH1Data: extractState.judgeH1Data,
            judgeDriverData: extractState.judgeDriverData,
            judgeMetadataData: extractState.judgeMetadataData,
            judgeMappingData: extractState.judgeMappingData,
          },
        },
      }).catch((err) => console.error("Extract auto-save failed:", err));
    }, 800);
    return () => clearTimeout(handle);
  }, [
    hydrated, extractState, currentStep, showMetadata, showDriverMapping,
    showExtractMapping, extractMappingApproved, activeTab, fileLayoutSuccess,
    fileLayoutApproved, brdReactivated, form.interfaceCode,
  ]);

  const handleApprove = () => {
    setBrdReactivated(false);
    if (uploadSessionId && editedLayer) dispatch(approveExtract({ sessionId: uploadSessionId, requirementLayer: editedLayer }));
  };
  const handleReject = (comment: string) => { if (uploadSessionId) dispatch(rejectExtract({ sessionId: uploadSessionId, comment })); };
  const handleUpdateAndApprove = (layer: RequirementLayer) => {
    if (uploadSessionId) dispatch(approveExtract({ sessionId: uploadSessionId, requirementLayer: layer }));
  };
  const handleFileLayoutCheckpoint = (tables: Record<string, FileLayoutField[]>) => {
  if (!uploadSessionId) return;

  dispatch(
    fileLayoutCheckpoint({
      sessionId: uploadSessionId,
      fileLayoutTables: tables,
    })
  )
    .unwrap()
    .then(() => {
      setFileLayoutApproved(true);
      setFileLayoutSuccess(true);
    })
    .catch(() => {
      setFileLayoutSuccess(false);
    });
};

  const handleFileLayoutUpdate = (tables: Record<string, FileLayoutField[]>) => {
  if (!uploadSessionId) return;

  dispatch(
    fileLayoutCheckpoint({
      sessionId: uploadSessionId,
      fileLayoutTables: tables,
    })
  ).catch(() => {});
};
  const handleRetry = () => {
    const sessionId = getCurrentAppSessionId();
    if (!sessionId || !form.brdFile || !form.fileLayoutFile) return;
    const { userId } = getCurrentSessionRuntime();
    const trimmedCode = form.interfaceCode.trim();
    dispatch(resetForRetry());
    dispatch(runUploadExtract({
      brdFile: form.brdFile,
      fileLayoutFile: form.fileLayoutFile,
      transcriptFile: form.transcriptFile ?? null,
      interfaceCode: trimmedCode,
      bsaNotes: form.bsaNotes || undefined,
      sessionId,
      userId: userId ?? getUserIdentity().userId,
    }));
  };
  const handleExtractMetadata = () => {
    const { userId } = getCurrentSessionRuntime();
    dispatch(runExtractMetadata({
      user_id: userId ?? getUserIdentity().userId,
      session_id: uploadSessionId ?? "",
      brd_gcs_uri: brdGcsUri ?? "",
      layout_gcs_uri: layoutGcsUri ?? "",
    })).unwrap().then(() => {
      setShowMetadata(true);
      setCurrentStep(2);
    }).catch(() => {});
  };

  const brdReviewLayer = validatedLayer?.validated_requirement_layer ?? brdInfo?.validated_requirement_layer;

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl text-brand-darkblue mt-2">Extract BRD</h1>

      <div className="flex bg-white rounded-lg border border-font-dark/25">
        <ExtractStepSidebar
          currentStep={currentStep}
          completedSteps={completedSteps}
          maxStep={maxStep}
          onStepClick={setCurrentStep}
          onNewSession={handleNewSession}
          newSessionLoading={newSessionLoading}
        />

        <div className="flex-1 overflow-hidden bg-brand-light">
          <div className="h-full p-6 overflow-y-auto">

            {/* New session loader */}
            {newSessionLoading && (
              <div className="flex flex-col items-center justify-center py-16 gap-3 text-gray-400">
                <div className="w-6 h-6 border-2 border-brand-darkblue border-t-transparent rounded-full animate-spin" />
                <span className="text-xs">Creating new session…</span>
              </div>
            )}

            {/* Step 1: Upload */}
            {!newSessionLoading && currentStep === 1 && !uploadSessionId && !uploadLoading && (
              <div className="space-y-6">
                <div className="flex items-center gap-2">
                  <h2 className="text-base font-bold text-brand-darkblue">Upload Documents</h2>
                  <span className="relative group">
                    <Info size={14} className="text-gray-400 cursor-pointer hover:text-brand-primary" />
                    <span className="absolute left-5 top-0 z-50 hidden group-hover:block w-72 bg-gray-800 text-white text-xs rounded-lg px-3 py-2 shadow-lg">
                      Upload the required documents to begin the extraction process. All documents will be processed to extract business requirements and file layout information.
                    </span>
                  </span>
                </div>
                <form onSubmit={handleSubmit}>
                  <div className="p-4 border border-gray-200 rounded-lg bg-white">
                    <div className="space-y-6">
                      <FileUploadField
                        label="BRD Document" required file={form.brdFile}
                        error={submitAttempted ? fieldErrors.brd : undefined}
                        onChange={(f, err) => setFileField("brd", f, err)}
                      />
                      <FileUploadField
                        label="File Layout Document" required file={form.fileLayoutFile}
                        error={submitAttempted ? fieldErrors.layout : undefined}
                        onChange={(f, err) => setFileField("layout", f, err)}
                        allowedExts={LAYOUT_ALLOWED_EXTENSIONS}
                      />
                      <FileUploadField
                        label="Transcript (optional)" file={form.transcriptFile}
                        error={submitAttempted ? fieldErrors.transcript : undefined}
                        onChange={(f, err) => setFileField("transcript", f, err)}
                      />
                      <div className="flex flex-col gap-1">
                        <div className="flex items-center gap-1 text-sm font-medium text-gray-700 mb-2">
                          STTM Input (optional)
                          <span className="relative group ml-1">
                            <Info size={13} className="text-gray-400 cursor-pointer hover:text-brand-primary" />
                            <span className="absolute left-5 top-0 z-50 hidden group-hover:block w-64 bg-gray-800 text-white text-xs rounded-lg px-3 py-2 shadow-lg">
                              {FIELD_INFO["STTM Input (optional)"]}
                            </span>
                          </span>
                        </div>
                        <textarea
                          rows={3}
                          value={form.bsaNotes}
                          onChange={(e) => setBsaNotes(e.target.value)}
                          placeholder="Enter STTM notes…"
                          className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs resize-none focus:outline-none focus:ring-2 focus:ring-brand-primary"
                        />
                      </div>
                      <div className="flex flex-col gap-1">
                        <div className="flex items-center gap-1 text-sm font-medium text-gray-700 mb-2">
                          Interface Code <span className="text-red-500">*</span>
                          <span className="relative group ml-1">
                            <Info size={13} className="text-gray-400 cursor-pointer hover:text-brand-primary" />
                            <span className="absolute left-5 top-0 z-50 hidden group-hover:block w-64 bg-gray-800 text-white text-xs rounded-lg px-3 py-2 shadow-lg">
                              {FIELD_INFO["Interface Code"]}
                            </span>
                          </span>
                        </div>
                        <textarea
                          rows={3}
                          value={form.interfaceCode}
                          onChange={(e) => setInterfaceCode(e.target.value)}
                          placeholder="Enter interface code…"
                          className={`w-full px-3 py-2 border rounded-md bg-white text-xs resize-none focus:outline-none focus:ring-2 focus:ring-brand-primary ${
                            submitAttempted && fieldErrors.interfaceCode ? "border-red-400" : "border-gray-300"
                          }`}
                        />
                        {submitAttempted && fieldErrors.interfaceCode && (
                          <p className="text-xs text-red-600 mt-1">{fieldErrors.interfaceCode}</p>
                        )}
                      </div>
                    </div>

                    {(error || (submitAttempted && hasErrors)) && (
                      <div className="bg-red-50 border border-red-200 rounded-lg p-3 mt-4">
                        {error && <p className="text-red-600 text-sm">{error}</p>}
                        {submitAttempted && hasErrors && (
                          <ul className="text-red-600 text-sm list-disc list-inside">
                            {Object.values(fieldErrors).map((msg) => <li key={msg}>{msg}</li>)}
                          </ul>
                        )}
                      </div>
                    )}

                    {noSessionError && (
                      <p className="text-xs text-red-600 mt-4">No active session. Please click <strong>New Extract</strong> to start a session.</p>
                    )}
                    <button
                      type="submit"
                      disabled={uploadLoading}
                      className="mt-6 w-full bg-brand-primary text-white py-2 rounded-lg hover:bg-brand-primary-hover disabled:bg-gray-400 disabled:cursor-not-allowed flex items-center justify-center gap-2 transition-colors text-sm cursor-pointer"
                    >
                      {uploadLoading ? (UPLOAD_STEP_LABELS[uploadStep] ?? "Processing…") : "Run Extract"}
                    </button>
                  </div>
                </form>
              </div>
            )}

            {/* Step 1 (post-upload): Review & Approve */}
            {!newSessionLoading && currentStep === 1 && (uploadSessionId !== null || uploadLoading) && (
              <div className="space-y-4">
                <h2 className="text-base font-bold text-brand-darkblue">Review & Approve</h2>

                {error && (
                  <div className="bg-red-50 border border-red-200 rounded-lg p-3">
                    <p className="text-red-600 text-sm">{error}</p>
                  </div>
                )}

                <div className="flex border-b border-gray-200">
                  {(["requirement", "fileLayout"] as const).map((tab) => (
                    <button
                      key={tab}
                      type="button"
                      onClick={() => setActiveTab(tab)}
                      className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors cursor-pointer ${
                        activeTab === tab
                          ? "border-brand-darkblue text-brand-darkblue"
                          : "border-transparent text-gray-500 hover:text-gray-700"
                      }`}
                    >
                      {tab === "requirement" ? "BRD" : "File Layout"}
                    </button>
                  ))}
                </div>

                <div className="bg-white rounded-lg border border-gray-200 p-4">
                  {activeTab === "requirement" && (
                    brdReviewLayer ? (
                      <BrdInfoReview
                        layer={brdReviewLayer}
                        sessionId={uploadSessionId ?? ""}
                        reviewStatus={reviewStatus}
                        approveLoading={approveLoading}
                        rejectLoading={rejectLoading}
                        disabled={metadataLoading}
                        onApprove={handleApprove}
                        onReject={handleReject}
                        onUpdateAndApprove={(layer) => { setBrdReactivated(false); handleUpdateAndApprove(layer); }}
                        onReactivate={() => {
                          setBrdReactivated(true);
                          setFileLayoutSuccess(false);
                        }}
                      />
                    ) : (
                      <div className="flex flex-col items-center justify-center py-16 gap-3 text-gray-400">
                        <div className="w-6 h-6 border-2 border-brand-darkblue border-t-transparent rounded-full animate-spin" />
                        <span className="text-xs">Validating requirement layer…</span>
                      </div>
                    )
                  )}
                  {activeTab === "fileLayout" && (
                    fileLayoutData ? (
                      <FileLayoutReview
                        data={fileLayoutData}
                        loading={fileLayoutLoading}
                        approved={fileLayoutSuccess}
                        disabled={metadataLoading}
                        onApprove={handleFileLayoutCheckpoint}
                        onUpdate={handleFileLayoutUpdate}
                        onReactivate={() => {
                          setFileLayoutApproved(false);
                          setFileLayoutSuccess(false);
                        }}
                      />
                    ) : (
                      <div className="flex flex-col items-center justify-center py-16 gap-3 text-gray-400">
                        <div className="w-6 h-6 border-2 border-brand-darkblue border-t-transparent rounded-full animate-spin" />
                        <span className="text-xs">Extracting file layout…</span>
                      </div>
                    )
                  )}
                </div>

                {/* Requirements Judge KPI */}
                {(judgeH1Loading || judgeH1Data || judgeH1Error) && (
                  <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => setKpiExpanded(!kpiExpanded)}
                          className="flex items-center gap-1 cursor-pointer group"
                        >
                          <h3 className="text-sm font-semibold text-brand-darkblue">Requirement Quality Evaluation</h3>
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
                        disabled={judgeH1Loading}
                        onClick={() => {
                          const sid = sessionStorage.getItem("session_id");
                          const userId = sessionStorage.getItem("user_id") ?? "";
                          const brdUri = sessionStorage.getItem("brd_gcs_uri") ?? "";
                          const layoutUri = sessionStorage.getItem("layout_gcs_uri") ?? "";
                          const transcriptUri = sessionStorage.getItem("transcript_gcs_uri") ?? "";
                          const brdMarkdownUri = sessionStorage.getItem("brd_markdown_gcs_uri") ?? "";
                          const layoutMarkdownUri = sessionStorage.getItem("layout_markdown_gcs_uri") ?? "";
                          if (sid) dispatch(runJudgeH1({ user_id: userId, session_id: sid, brd_gcs_uri: brdUri, layout_gcs_uri: layoutUri, transcript_gcs_uri: transcriptUri, brd_markdown_gcs_uri: brdMarkdownUri, layout_markdown_gcs_uri: layoutMarkdownUri, judge_mode: "pre", bsa_rejection_feedback: "", revision_number: 0 }));
                        }}
                        className="text-gray-400 hover:text-brand-darkblue disabled:opacity-40 disabled:cursor-not-allowed transition-colors cursor-pointer"
                        title="Retry judge evaluation"
                      >
                        <RefreshCw size={14} className={judgeH1Loading ? "animate-spin" : ""} />
                      </button>
                    </div>
                    
                    {kpiExpanded && (
                    <>
                    {showKpiInfo && (
                      <div className="bg-brand-surface border border-teal-200 rounded-lg p-3 text-xs space-y-2">
                        <div className="font-semibold text-brand-darkblue mb-2">KPI Definitions</div>
                        {judgeH1Data ? Object.entries(judgeH1Data.kpis).map(([name, kpi]) => (
                          (kpi as { definition?: string }).definition ? (
                            <div key={name} className="text-gray-700">
                              {(kpi as { definition: string }).definition}
                            </div>
                          ) : null
                        )) : <p className="text-gray-400 italic">Run evaluation to see KPI definitions.</p>}
                      </div>
                    )}
                    {judgeH1Loading && (
                      <div className="flex items-center gap-2 text-gray-400 text-xs">
                        <div className="w-4 h-4 border-2 border-brand-darkblue border-t-transparent rounded-full animate-spin" />
                        Running judge evaluation…
                      </div>
                    )}
                    {judgeH1Error && <p className="text-xs text-red-600">{judgeH1Error}</p>}
                    {judgeH1Data && (
                        <div className="grid grid-cols-2 gap-2">
                          {Object.entries(judgeH1Data.kpis).map(([name, kpi]) => {
                            const k = kpi as JudgeKpiScore;
                            const pct = k.score * 100;
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

                <div className="flex justify-end pt-2 gap-2 items-center">
                  {!(brdApproved && fileLayoutApproved) && (
                    <p className="text-xs text-gray-400 italic">Note: Approve both BRD and File Layout to enable Extract Metadata.</p>
                  )}
                  <button
                    type="button"
                    onClick={handleExtractMetadata}
                    disabled={!enableExtractMetadata}
                    className="text-sm bg-brand-darkblue text-white px-4 py-2 rounded-lg hover:bg-brand-darkblue/80 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors cursor-pointer"
                  >
                    {metadataLoading ? "Extracting Metadata…" : "Extract Metadata"}
                  </button>
                  {brdReviewLayer && fileLayoutData && (
                    <button
                      type="button"
                      onClick={handleRetry}
                      disabled={uploadLoading}
                      className="text-sm border border-gray-400 text-gray-600 px-4 py-2 rounded-lg hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors cursor-pointer"
                    >
                      {uploadLoading ? "Retrying…" : "Retry"}
                    </button>
                  )}
                </div>
              </div>
            )}

            {/* Step 2: Extract Metadata */}
            {!newSessionLoading && showMetadata && (
              <div className={currentStep === 2 ? "" : "hidden"}>
                <ExtractMetadata
                  onGenerateDriver={() => { setShowDriverMapping(true); setCurrentStep(3); }}
                  onNext={() => setCurrentStep(3)}
                />
              </div>
            )}
            {!newSessionLoading && currentStep === 2 && !showMetadata && (
              <div className="flex flex-col items-center justify-center py-16 gap-3 text-gray-400">
                <span className="text-xs">Metadata has not been extracted yet.</span>
              </div>
            )}

            {/* Step 3: Generate Driver Mapping */}
            {!newSessionLoading && showDriverMapping && (
              <div className={currentStep === 3 ? "" : "hidden"}>
                <GenerateDriverMapping
                  onPrevious={() => setCurrentStep(2)}
                  onNext={() => setCurrentStep(4)}
                  onExtractMapping={() => { setShowExtractMapping(true); setCurrentStep(4); }}
                />
              </div>
            )}
            {!newSessionLoading && currentStep === 3 && !showDriverMapping && (
              <div className="flex flex-col items-center justify-center py-16 gap-3 text-gray-400">
                <span className="text-xs">Driver mapping has not been generated yet.</span>
              </div>
            )}

            {/* Step 4: Extract Mapping */}
            {!newSessionLoading && showExtractMapping && (
              <div className={currentStep === 4 ? "" : "hidden"}>
                <ExtractMapping onApprove={() => setExtractMappingApproved(true)} />
              </div>
            )}

          </div>
        </div>
      </div>
    </div>
  );
}