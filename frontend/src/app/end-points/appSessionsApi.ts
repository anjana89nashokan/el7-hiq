import { getCurrentAppSessionId, setCurrentAppSessionId, setSessionRuntime, setUserEmail } from "../utils/appSessionStorage";
import { apiFetch } from "../utils/apiFetch";
import { API_BASE_URL } from "../config/env";

const baseUrl = API_BASE_URL;

export interface AppSessionHl7Summary {
  hl7_session_id: string;
  status: string;
  messages_parsed: number;
  mappings_total: number;
  mappings_approved: number;
  mappings_pending: number;
  latest_version: number | null;
  profiling_complete: boolean;
  mapping_complete: boolean;
}

export interface AppSessionItem {
  id: string;
  title: string;
  status: string;
  user_email: string | null;
  current_profiling_run_id: string | null;
  current_mapping_run_id: string | null;
  current_extract_run_id: string | null;
  current_hl7_session_id?: string | null;
  hl7?: AppSessionHl7Summary | null;
  active_vertex_session_id: string | null;
  active_vertex_app_name: string | null;
  last_opened_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ProfilingRunDetail {
  id: string;
  status: string;
  current_step: string | null;
  profiling_mode: "normal" | "streaming";
  resume_state: Record<string, any>;
  profiling_context_uri: string | null;
  active_vertex_session_id: string | null;
  active_vertex_app_name: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  error_message?: string | null;
}

export interface MappingRunDetail {
  id: string;
  status: string;
  current_step: string | null;
  mapping_run_id: string | null;
  resume_state: Record<string, any>;
  artifacts: Record<string, string | null>;
  review_draft?: {
    answers: Record<string, any>;
    feedbacks: Record<string, any>;
    changed_rows: any[];
    active_tab: string | null;
    selected_row_id: string | null;
    last_saved_at: string | null;
  } | null;
  started_at?: string | null;
  completed_at?: string | null;
  error_message?: string | null;
}

export interface ExtractRunDetail {
  id: string;
  status: string;
  current_step: string | null;
  resume_state: Record<string, any>;
  upload_session_id: string | null;
  brd_gcs_uri: string | null;
  layout_gcs_uri: string | null;
  metadata_gcs_uri: string | null;
  driver_gcs_uri: string | null;
  active_vertex_session_id: string | null;
  active_vertex_app_name: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  error_message?: string | null;
}

export interface AppSessionDetail {
  session: AppSessionItem;
  profiling_run: ProfilingRunDetail | null;
  mapping_run: MappingRunDetail | null;
  extract_run: ExtractRunDetail | null;
  runtime: {
    vertex_session_id: string | null;
    vertex_app_name: string | null;
    vertex_user_id: string | null;
  };
}

export interface ProfilingRunSummary {
  id: string;
  status: string;
  current_step: string | null;
  profiling_mode: "normal" | "streaming";
  profiling_context_uri: string | null;
  active_vertex_session_id: string | null;
  active_vertex_app_name: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  error_message?: string | null;
  has_resume: boolean;
}

export interface MappingRunSummary {
  id: string;
  status: string;
  current_step: string | null;
  mapping_run_id: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  error_message?: string | null;
  has_resume: boolean;
}

export interface ExtractRunSummary {
  id: string;
  status: string;
  current_step: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  error_message?: string | null;
  has_resume: boolean;
}

export interface AppSessionSummaryDetail {
  session: AppSessionItem;
  profiling_run: ProfilingRunSummary | null;
  mapping_run: MappingRunSummary | null;
  extract_run: ExtractRunSummary | null;
  flags: {
    has_profiling_resume: boolean;
    has_mapping_resume: boolean;
    has_extract_resume: boolean;
  };
}

export function canResumeProfilingRun(detail: AppSessionDetail): boolean {
  return Boolean(detail.profiling_run?.resume_state?.uploadResponse);
}

export function getProfilingRoute(
  detail: AppSessionDetail,
): "/profiling" | "/streaming-profiling" | "/upload" {
  const hasResume = canResumeProfilingRun(detail);
  if (!hasResume) {
    return "/upload";
  }
  const resumeState = detail.profiling_run?.resume_state || {};
  const profilingMode = String(
    resumeState.profilingMode || resumeState.profiling_mode || "",
  )
    .trim()
    .toLowerCase();
  return profilingMode === "streaming" ? "/streaming-profiling" : "/profiling";
}

export function canResumeProfilingRunFromSummary(detail: AppSessionSummaryDetail): boolean {
  return Boolean(detail.flags?.has_profiling_resume || detail.profiling_run?.has_resume);
}

export function canResumeMappingRun(detail: AppSessionDetail): boolean {
  const resumeState = detail.mapping_run?.resume_state;
  if (!resumeState) {
    return false;
  }
  return Boolean(
    resumeState.step4Data ||
    resumeState.mappingData ||
    (typeof resumeState.currentStep === "number" && resumeState.currentStep >= 2),
  );
}

export function canResumeMappingRunFromSummary(detail: AppSessionSummaryDetail): boolean {
  return Boolean(detail.flags?.has_mapping_resume || detail.mapping_run?.has_resume);
}

export function canResumeExtractRun(detail: AppSessionDetail): boolean {
  const resumeState = detail.extract_run?.resume_state;
  if (!resumeState) return false;
  return Boolean(resumeState.uploadSessionId || resumeState.brdInfo);
}

async function parseJson(response: Response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data?.detail ?? data?.message;
    throw new Error(detail ? messageFromDetail(detail) : `Request failed: ${response.status}`);
  }
  return data;
}

function messageFromDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((entry) =>
        entry && typeof entry === "object"
          ? (entry as { msg?: string; message?: string }).msg ||
            (entry as { msg?: string; message?: string }).message
          : String(entry),
      )
      .filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  if (detail && typeof detail === "object") {
    const value = detail as { message?: string; request_id?: string; detail?: string };
    if (typeof value.message === "string" && value.message.trim()) {
      return value.request_id ? `${value.message} (request id: ${value.request_id})` : value.message;
    }
    if (typeof value.detail === "string") return value.detail;
    try {
      return JSON.stringify(detail);
    } catch {
      return "Request failed";
    }
  }
  return "Request failed";
}

function runtimeFromDetail(detail: AppSessionDetail): AppSessionDetail["runtime"] {
  return {
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
}

function applySessionDetail(detail: AppSessionDetail): AppSessionDetail {
  setCurrentAppSessionId(detail.session.id);
  setUserEmail(detail.session.user_email);
  setSessionRuntime(runtimeFromDetail(detail));
  return detail;
}

export async function listAppSessions(module?: "sess" | "extract"): Promise<AppSessionItem[]> {
  let url = `${baseUrl}/sessions/app/list`;
  if (module) url += `?module=${encodeURIComponent(module)}`;
  const response = await apiFetch(url, { signal: AbortSignal.timeout(15000) });
  const data = await parseJson(response);
  return data.sessions || [];
}

export async function createAppSession(title?: string, module?: string): Promise<AppSessionDetail> {
  let url = `${baseUrl}/sessions/app`;
  if (module) url += `?module=${encodeURIComponent(module)}`;
  const response = await apiFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  const data = await parseJson(response);
  if (data.session?.active_vertex_session_id) {
    sessionStorage.setItem("vertex_session_id", data.session.active_vertex_session_id);
  }
  const detail = await getAppSessionDetail(data.session.id);
  return applySessionDetail(detail);
}

export async function getAppSessionDetail(sessionId: string): Promise<AppSessionDetail> {
  const response = await apiFetch(`${baseUrl}/sessions/app/${sessionId}`);
  const data = await parseJson(response);
  return data as AppSessionDetail;
}

export async function getAppSessionSummary(sessionId: string): Promise<AppSessionSummaryDetail> {
  const response = await apiFetch(`${baseUrl}/sessions/app/${sessionId}/summary`);
  const data = await parseJson(response);
  return data as AppSessionSummaryDetail;
}

export async function selectAppSession(sessionId: string): Promise<AppSessionDetail> {
  const detail = await getAppSessionDetail(sessionId);
  return applySessionDetail(detail);
}

export async function renameAppSession(sessionId: string, title: string): Promise<AppSessionItem> {
  const response = await apiFetch(`${baseUrl}/sessions/app/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  const data = await parseJson(response);
  return data.session;
}

export async function deleteAppSession(sessionId: string): Promise<void> {
  const response = await apiFetch(`${baseUrl}/sessions/app/${sessionId}`, { method: "DELETE" });
  await parseJson(response);
  if (getCurrentAppSessionId() === sessionId) {
    setCurrentAppSessionId(null);
  }
}

export async function startProfilingRun(sessionId: string): Promise<AppSessionDetail["runtime"]> {
  const response = await apiFetch(`${baseUrl}/sessions/app/${sessionId}/profiling/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force_new: true }),
  });
  const data = await parseJson(response);
  const runtime = data.runtime as AppSessionDetail["runtime"];
  setSessionRuntime(runtime);
  return runtime;
}

export async function ensureSessionRuntime(): Promise<{
  sessionId: string | null;
  appName: string | null;
  userId: string | null;
}> {
  const stored = {
    sessionId: sessionStorage.getItem("session_id"),
    appName: sessionStorage.getItem("app_name"),
    userId: sessionStorage.getItem("user_id"),
  };
  if (stored.sessionId && stored.appName) {
    return stored;
  }

  const appSessionId = getCurrentAppSessionId();
  if (!appSessionId) {
    return stored;
  }

  const detail = await getAppSessionDetail(appSessionId);
  const fromDetail = runtimeFromDetail(detail);
  if (fromDetail.vertex_session_id && fromDetail.vertex_app_name) {
    setSessionRuntime(fromDetail);
    return {
      sessionId: fromDetail.vertex_session_id,
      appName: fromDetail.vertex_app_name,
      userId: fromDetail.vertex_user_id || stored.userId,
    };
  }

  // Do not mint a blank ADK session for chat — profiling/upload must create the runtime first.
  return stored;
}

export async function saveProfilingResumeState(sessionId: string, payload: Record<string, any>) {
  const response = await apiFetch(`${baseUrl}/sessions/app/${sessionId}/profiling`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJson(response);
}

export async function saveMappingResumeState(sessionId: string, payload: Record<string, any>) {
  const response = await apiFetch(`${baseUrl}/sessions/app/${sessionId}/mapping`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJson(response);
}

export async function saveExtractResumeState(sessionId: string, payload: Record<string, any>) {
  const response = await apiFetch(`${baseUrl}/sessions/app/${sessionId}/extract`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJson(response);
}

export async function saveMappingReviewDraft(sessionId: string, payload: Record<string, any>) {
  const response = await apiFetch(`${baseUrl}/sessions/app/${sessionId}/mapping-review-draft`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJson(response);
}

function runTimestamp(value?: string | null): number {
  if (!value) return 0;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
}

export function getAppSessionOpenRoute(detail: AppSessionDetail): string {
  // Extract sessions (extract_* ids) always open the Extract workflow.
  if (detail.session.id.startsWith("extract_") || detail.extract_run) {
    return "/extract";
  }
  const profilingRun = detail.profiling_run;
  const mappingRun = detail.mapping_run;
  const profilingRoute = getProfilingRoute(detail);
  const hasProfilingResume = canResumeProfilingRun(detail);

  if (profilingRun && !mappingRun) {
    if (String(profilingRun.status || "").toUpperCase() === "COMPLETED") {
      return "/mapping";
    }
    return profilingRoute;
  }
  if (mappingRun && !profilingRun) {
    return "/mapping";
  }
  if (profilingRun && mappingRun) {
    if (!hasProfilingResume) {
      return "/mapping";
    }
    const profilingTime = Math.max(
      runTimestamp(profilingRun.completed_at),
      runTimestamp(profilingRun.started_at),
    );
    const mappingTime = Math.max(
      runTimestamp(mappingRun.completed_at),
      runTimestamp(mappingRun.started_at),
    );
    return profilingTime >= mappingTime ? profilingRoute : "/mapping";
  }
  return "/upload";
}
