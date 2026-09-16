import { getCurrentAppSessionId } from "../utils/appSessionStorage";
import { apiFetch } from "../utils/apiFetch";
import { API_BASE_URL } from "../config/env";

const baseUrl = API_BASE_URL;

export interface IngestRequest {
  interface_code: string;
  instructions_text: string;
  subject_areas: string[];
  target_layout?: "UPLOAD_FILES" | "INDEMAP";
  source_files: FileList;
  target_files?: FileList;
  indemap_pairs?: Array<{ database_name: string; table_name: string }>;
}

export interface SubjectAreaStatus {
  subject_area: string;
  enabled: boolean;
  last_uploaded_at: string | null;
  graph_artifact_path: string | null;
}

export const mappingService = {
  async ingest(data: IngestRequest) {
    const appSessionId = getCurrentAppSessionId();
    const formData = new FormData();
    if (appSessionId) {
      formData.append("app_session_id", appSessionId);
    }
    formData.append("interface_code", data.interface_code);
    formData.append("instructions_text", data.instructions_text);
    data.subject_areas.forEach((subjectArea) => {
      formData.append("subject_areas", subjectArea);
    });
    formData.append("target_layout", data.target_layout || "UPLOAD_FILES");
    
    Array.from(data.source_files).forEach(file => {
      formData.append("source_files", file);
    });
    
    if (data.target_files) {
      Array.from(data.target_files).forEach(file => {
        formData.append("target_files", file);
      });
    }

    if (data.indemap_pairs && data.indemap_pairs.length > 0) {
      formData.append("indemap_pairs_json", JSON.stringify(data.indemap_pairs));
    }

    const response = await apiFetch(`${baseUrl}/mapping/ingest`, {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(errorData.detail || "Failed to ingest mapping metadata.");
    }

    return response.json();
  },

  async getSubjectAreaStatuses(): Promise<SubjectAreaStatus[]> {
    const response = await apiFetch(`${baseUrl}/graphs/subject-areas/status`, {
      method: "GET",
    });
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || "Failed to load subject area statuses.");
    }
    return response.json();
  },

  async draft(runId: string) {
    const appSessionId = getCurrentAppSessionId();
    const formData = new FormData();
    formData.append("run_id", runId);
    if (appSessionId) {
      formData.append("app_session_id", appSessionId);
    }

    const response = await apiFetch(`${baseUrl}/mapping/draft`, {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(errorData.detail || "Failed to draft mapping.");
    }

    return response.json();
  },

  async getReviewQuestions(runId: string) {
    const appSessionId = getCurrentAppSessionId();
    const formData = new FormData();
    formData.append("run_id", runId);
    if (appSessionId) {
      formData.append("app_session_id", appSessionId);
    }

    const response = await apiFetch(`${baseUrl}/mapping/review/questions`, {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      const errorMessage = errorData.detail || `Failed to fetch questions: ${response.status} ${response.statusText}`;
      throw new Error(errorMessage);
    }

    return response.json();
  },
  async submitReview(payload: {
    run_id: string;
    changed_rows: any[];
    answers: Record<string, string>;
    feedbacks: Record<string, string>;
    answered_by?: string | null;
  }) {
    const appSessionId = getCurrentAppSessionId();
    const response = await apiFetch(`${baseUrl}/mapping/review/submit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, app_session_id: appSessionId }),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || "Failed to submit review changes.");
    }

    return response.json();
  },

  async applyReview(runId: string) {
    const appSessionId = getCurrentAppSessionId();
    const response = await apiFetch(`${baseUrl}/mapping/review/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: runId, app_session_id: appSessionId }),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || "Failed to apply Step 4 review.");
    }

    return response.json();
  },

  async validateDbAndTables(db_name: string, table_names: string[]) {
    const response = await apiFetch(`${baseUrl}/validate/db-and-tables`, {
      method: "POST",
      headers: { "accept": "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ db_name, table_names }),
    });
    return response.json();
  },

  async buildErwinSubjectArea(data: {
    subject_area: string;
    tables_and_columns_file: File;
    tables_and_indexes_file: File;
  }) {
    const formData = new FormData();
    formData.append("subject_area", data.subject_area);
    formData.append("tables_and_columns_file", data.tables_and_columns_file);
    formData.append("tables_and_indexes_file", data.tables_and_indexes_file);

    const response = await apiFetch(`${baseUrl}/graphs/build-erwin-subject-area`, {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(errorData.detail || "Failed to build subject area.");
    }

    return response.json();
  }
};
