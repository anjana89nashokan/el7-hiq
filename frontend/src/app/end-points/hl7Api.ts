import axiosInstance from "../utils/axios-interceptor";

export type HL7Routing =
  | "AUTO_MAP"
  | "AUTO_MAP_OPTIONAL"
  | "REVIEW"
  | "HUMAN_REQUIRED";

export interface HL7Inference {
  segment: string;
  path: string;
  label: string | null;
  inferred_meaning: string;
  datatype: string;
  semantic_confidence: number;
  stability: number;
  routing: HL7Routing;
  reasoning: string[];
  fhir_target: string;
  observed_values: string[];
  present_in: number;
  total_occurrences: number;
}

export interface HL7FieldProfile {
  path: string;
  populated_in: number;
  total: number;
  samples: string[];
}

export interface HL7SegmentProfile {
  name: string;
  is_z: boolean;
  occurrences: number;
  messages_present: number;
  max_fields: number;
  fields: HL7FieldProfile[];
}

export interface HL7FileResult {
  filename: string;
  status: "parsed" | "failed";
  error?: string;
  fhir_error?: string;
  message_type?: string;
  version?: string;
  control_id?: string;
  segment_count?: number;
  segments?: string[];
  z_segments?: string[];
  profile?: {
    message_count: number;
    per_type_structure: Record<string, Record<string, number>>;
    segments: HL7SegmentProfile[];
  };
  inference?: Record<string, HL7Inference[]>;
}

export interface HL7Result {
  hl7_session_id: string;
  created_at: string;
  summary: {
    files_received: number;
    messages_parsed: number;
    messages_failed: number;
    message_types: Record<string, number>;
    versions: Record<string, number>;
    z_segment_names: string[];
    z_field_count: number;
    fhir_documents: number;
  };
  routing_summary: Record<HL7Routing, number>;
  files: HL7FileResult[];
  profile: {
    message_count: number;
    per_type_structure: Record<string, Record<string, number>>;
    segments: HL7SegmentProfile[];
  };
  inference: Record<string, HL7Inference[]>;
  fhir_available: string[];
  canonical_entities?: string[];
  mapping_summary?: {
    total: number;
    by_status: Record<string, number>;
    standard: number;
    custom: number;
    auto_approvable: number;
    low_confidence: number;
  };
}

/* -------------------------------------------------------------------------
 * Mapping review workbench
 * ---------------------------------------------------------------------- */

export type MappingStatus =
  | "proposed"
  | "approved"
  | "rejected"
  | "deferred"
  | "unresolved";

export type ReviewAction = "approve" | "reject" | "defer" | "edit" | "reset";

export interface FieldMapping {
  id: string;
  source_path: string;
  source_segment: string;
  category: "standard" | "custom";
  origin: "rule" | "inference" | "unmatched" | "fallback";
  target_path: string;
  target_entity: string;
  transformation: string;
  confidence: number;
  rationale: string[];
  status: MappingStatus;
  auto_approvable: boolean;
  source_label: string | null;
  applies_to_families: string[];
  examples: string[];
  occurrences: number;
  messages_present: number;
  stability: number | null;
  target_required: boolean;
  target_terminology: string | null;
  reviewer: string | null;
  reviewer_comment: string | null;
  reviewed_at: string | null;
  original_target_path: string | null;
  source_files?: string[];
}

export interface Readiness {
  can_publish: boolean;
  blockers: { target_path: string; reason: string; description: string }[];
  warnings: { mapping_id: string; source_path: string; reason: string }[];
  approved_count: number;
  total_count: number;
}

export interface PackageVersion {
  version: number;
  published_at: string;
  published_by: string;
  mapping_count: number;
  note: string | null;
}

export interface SavedCustomTarget {
  target_path: string;
  slug: string;
  description: string;
  created_from_source: string;
  created_by: string;
  created_at: string;
}

export interface MappingsResponse {
  hl7_session_id: string;
  mappings: FieldMapping[];
  files: HL7FileResult[];
  summary: {
    total: number;
    by_status: Record<string, number>;
    standard: number;
    custom: number;
    auto_approvable: number;
  };
  readiness: Readiness;
  canonical_entities: string[];
  versions: PackageVersion[];
  custom_targets?: SavedCustomTarget[];
}

export interface CanonicalAttribute {
  name: string;
  datatype: string;
  cardinality: string;
  description: string;
  terminology: string | null;
  required: boolean;
  repeating: boolean;
}

export interface CanonicalEntity {
  name: string;
  domain: string;
  description: string;
  attributes: CanonicalAttribute[];
}

export const isHL7File = (file: File): boolean =>
  file.name.toLowerCase().endsWith(".hl7");

export const uploadHL7Files = async (
  files: File[],
  appSessionId?: string | null
): Promise<HL7Result> => {
  const formData = new FormData();
  files.forEach((file) => formData.append("files", file, file.name));
  if (appSessionId) {
    formData.append("app_session_id", appSessionId);
  }

  const response = await axiosInstance.post("/hl7/upload", formData);
  return response.data as HL7Result;
};

export interface HL7SessionListItem {
  hl7_session_id: string;
  created_at: string | null;
  status: string;
  messages_parsed: number;
  messages_failed: number;
  message_types: Record<string, number>;
  z_segment_names: string[];
  mappings_total: number;
  mappings_approved: number;
  mappings_pending: number;
  latest_version: number | null;
  published_at: string | null;
}

export const listHL7Sessions = async (): Promise<HL7SessionListItem[]> => {
  const response = await axiosInstance.get("/hl7/sessions");
  return response.data.sessions as HL7SessionListItem[];
};

export const deleteHL7Session = async (id: string): Promise<void> => {
  await axiosInstance.delete(`/hl7/sessions/${id}`);
};

export const getHL7Session = async (id: string): Promise<HL7Result> => {
  const response = await axiosInstance.get(`/hl7/sessions/${id}`);
  return response.data as HL7Result;
};

export const getHL7Fhir = async (
  id: string,
  name: string
): Promise<Record<string, unknown>> => {
  const response = await axiosInstance.get(`/hl7/sessions/${id}/fhir/${name}`);
  return response.data as Record<string, unknown>;
};

export const getCanonicalModel = async (): Promise<CanonicalEntity[]> => {
  const response = await axiosInstance.get("/hl7/canonical-model");
  return response.data.entities as CanonicalEntity[];
};

export const getHL7Mappings = async (id: string): Promise<MappingsResponse> => {
  const response = await axiosInstance.get(`/hl7/sessions/${id}/mappings`);
  return response.data as MappingsResponse;
};

export const reviewHL7Mapping = async (
  id: string,
  body: {
    mapping_id: string;
    action: ReviewAction;
    target_path?: string;
    transformation?: string;
    comment?: string;
  }
): Promise<{ mapping: FieldMapping; readiness: Readiness; custom_targets?: SavedCustomTarget[] }> => {
  const response = await axiosInstance.post(`/hl7/sessions/${id}/review`, body);
  return response.data;
};

export const bulkApproveHL7 = async (
  id: string
): Promise<{ approved: number; skipped: number; readiness: Readiness; mappings: FieldMapping[] }> => {
  const response = await axiosInstance.post(`/hl7/sessions/${id}/bulk-approve`);
  return response.data;
};

export const bulkApproveHL7Mappings = async (
  id: string,
  body: { mapping_ids: string[]; comment?: string }
): Promise<{ approved: number; skipped: number; readiness: Readiness; mappings: FieldMapping[] }> => {
  const response = await axiosInstance.post(`/hl7/sessions/${id}/bulk-approve-selected`, body);
  return response.data;
};

export const publishHL7Package = async (
  id: string,
  note?: string
): Promise<{ package: Record<string, unknown>; versions: PackageVersion[] }> => {
  const response = await axiosInstance.post(`/hl7/sessions/${id}/publish`, { note });
  return response.data;
};

export const getHL7Version = async (
  id: string,
  version: number
): Promise<Record<string, unknown>> => {
  const response = await axiosInstance.get(`/hl7/sessions/${id}/versions/${version}`);
  return response.data;
};

export const getHL7Audit = async (
  id: string
): Promise<Record<string, unknown>[]> => {
  const response = await axiosInstance.get(`/hl7/sessions/${id}/audit`);
  return response.data.events as Record<string, unknown>[];
};

const parseDownloadFilename = (header: string | undefined, fallback: string): string => {
  if (!header) return fallback;
  const match = /filename="?([^";\n]+)"?/.exec(header);
  return match?.[1]?.trim() || fallback;
};

export const downloadHL7Mappings = async (id: string): Promise<void> => {
  const response = await axiosInstance.get(`/hl7/sessions/${id}/export/mappings`, {
    responseType: "blob",
  });
  const fallback = `hl7-mappings-${id.slice(0, 8)}.json`;
  const filename = parseDownloadFilename(
    response.headers["content-disposition"] as string | undefined,
    fallback
  );
  const blob = new Blob([response.data], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
};
