import axiosInstance from "../../utils/axios-interceptor";
import { getCurrentAppSessionId } from "../../utils/appSessionStorage";

// ── Abort controller registry ─────────────────────────────────────────────────
// Cancels any in-flight request for the same key before starting a new one.
const controllers = new Map<string, AbortController>();

function getSignal(key: string): AbortSignal {
  controllers.get(key)?.abort();
  const controller = new AbortController();
  controllers.set(key, controller);
  return controller.signal;
}

// ── Types ────────────────────────────────────────────────────────────────────

export interface ApproveExtractResponse {
  success: boolean;
  session_id: string;
  message: string;
  validated_requirement_layer: {
    validated_requirement_layer: RequirementLayer;
  };
}

export interface RejectExtractResponse {
  success: boolean;
  session_id: string;
  message: string;
  validated_requirement_layer: RequirementLayer;
}

export interface UploadExtractPayload {
  brdFile: File;
  fileLayoutFile: File;
  transcriptFile?: File | null;
  bsaNotes?: string;
  sessionId: string;
  userId: string;
  interfaceCode: string;
}

export interface UploadExtractResponse {
  success: boolean;
  session_id: string;
  message: string;
  gcs_prefix: string;
}

export interface BrdInfoResponse {
  success: boolean;
  session_id: string;
  message: string;
  artifacts_found: string[];
  brd_filename: string | null;
  file_layout_filename: string | null;
  transcript_filename: string | null;
  bsa_notes: string | null;
  markdown_uploads: string[];
  validated_requirement_layer: RequirementLayer;
}

export interface ValidateRequirementLayerResponse {
  success: boolean;
  session_id: string;
  validation_status: string;
  corrections_made: boolean;
  message: string;
  validated_requirement_layer: RequirementLayer;
  gcs_output_uri?: string;
}

export type FileLayoutField = Record<string, any>;

export interface FileLayoutResponse {
  success: boolean;
  session_id: string;
  message: string;
  file_layout_filename: string;
  total_pages: number;
  tables_extracted: number;
  file_layout_tables: Record<string, FileLayoutField[]>;
  gcs_output_uri?: string;
}

export interface RequirementLayer {
  scope: { in_scope: string; out_of_scope: string };
  bsa_input: string;
  requirements: string;
  filters_and_parameters: {
    company: string;
    business: string;
    state: string;
    line_of_business: string;
    financial_arrangement: string;
    product_plan_type: string;
    extended_product: string;
    coverage_plan: string;
    customer_id: string;
    group_id: string;
    claim_status: string;
    blue_card_indicator: string;
    excluded_companies: string;
    excluded_lob: string;
    sensitive_data_exclusion: string;
    opt_out_groups: string;
    date_parameters: {
      member_active_enrollment: string;
      active_plan_group: string;
      start_date: string;
      history_lookback: string;
      rollover_period: string;
      claim_service_dates: string;
      claim_posted_dates: string;
      paid_dates: string;
      pharmacy_fill_dates: string;
      pharmacy_cut_dates: string;
    };
    [key: string]: unknown;
  };
  file_attributes_mapping: {
    file_count: string;
    subject_areas: string;
    file_frequency: string;
    file_type: string;
    file_delimiter: string;
    file_naming_convention: string;
    file_compression: string;
    file_encryption: string;
    control_file_required: string;
    file_delivery_method: string;
    field_headers: string;
    trailer_required: string;
    field_requirements: string;
    default_values: string;
    data_format_rules: string;
    [key: string]: unknown;
  };
  file_specs: {
    physical_file_name: string;
    vendor_name: string;
    transfer_method: string;
    vendor_contact_name: string;
    frequency_mode: string;
    vendor_phone_number: string;
    dependencies: string;
    vendor_email: string;
    email_notification_dl: string;
    file_delimiter: string;
    file_extension: string;
    date_timestamp_format: string;
    header_record_number: string;
    trailer_record_number: string;
    quote_indicator: string;
    file_population_type: string;
    file_compression_type: string;
    receive_files_when_no_data: string;
    assumptions: string;
    vendor_server_name: string;
    vendor_file_drop_location: string;
    control_file_name: string;
    control_file_delimiter: string;
    control_file_extension: string;
    control_file_header_present: string;
    control_record_number: string;
    control_file_amount_column_count: string;
    done_file_present: string;
    file_arrival_schedule: string;
    estimated_record_count_initial: string;
    estimated_record_count_ongoing: string;
    [key: string]: unknown;
  };
  common_rules: {
    interface_code: string;
    history_required: string;
    effective_dates_from: string;
    effective_dates_to: string;
    posted_dates_from: string;
    posted_dates_to: string;
    rolling_month_requirement: string;
    driver_required: string;
    incremental_history_required: string;
    runout_required: string;
    number_of_months: string;
    sensitive_category_list: string;
    deidentity_extract: string;
    comments: string;
    last_updated_date: string;
    [key: string]: unknown;
  };
}

// ── API Calls ────────────────────────────────────────────────────────────────

export const uploadExtractApi = async (payload: UploadExtractPayload): Promise<UploadExtractResponse> => {
  const formData = new FormData();
  formData.append("brd_file", payload.brdFile);
  formData.append("file_layout", payload.fileLayoutFile);
  formData.append("session_id", payload.sessionId);
  formData.append("user_id", payload.userId);
  formData.append("interface_code", payload.interfaceCode);
  if (payload.transcriptFile) formData.append("transcript", payload.transcriptFile);
  if (payload.bsaNotes) formData.append("bsa_notes", payload.bsaNotes);
  const { data } = await axiosInstance.post<UploadExtractResponse>("/extracts/upload-extract", formData, {
    headers: { "Content-Type": "multipart/form-data" },
    signal: getSignal("uploadExtract"),
  });
  return data;
};

export const fetchBrdInfoApi = async (sessionId: string): Promise<BrdInfoResponse> => {
  const { data } = await axiosInstance.get<BrdInfoResponse>(`/extracts/extract-brd-information/${sessionId}`, {
    signal: getSignal("fetchBrdInfo"),
  });
  return data;
};

export const validateRequirementLayerApi = async (sessionId: string): Promise<ValidateRequirementLayerResponse> => {
  const { data } = await axiosInstance.get<ValidateRequirementLayerResponse>(`/extracts/validate-requirement-layer/${sessionId}`, {
    signal: getSignal("validateRequirementLayer"),
  });
  return data;
};

export const extractFileLayoutApi = async (sessionId: string): Promise<FileLayoutResponse> => {
  const { data } = await axiosInstance.get<FileLayoutResponse>(`/extracts/extract-file-layout/${sessionId}`, {
    signal: getSignal("extractFileLayout"),
  });
  return data;
};

export const fileLayoutCheckpointApi = async (sessionId: string, fileLayoutTables: Record<string, FileLayoutField[]>): Promise<void> => {
  await axiosInstance.post(`/extracts/file-layout-checkpoint/${sessionId}`, { edited_tables: { file_layout_tables: fileLayoutTables } }, {
    signal: getSignal("fileLayoutCheckpoint"),
  });
};

export const rejectExtractApi = async (sessionId: string, instruction: string): Promise<RejectExtractResponse> => {
  const { data } = await axiosInstance.post<RejectExtractResponse>(`/extracts/brd-reject/${sessionId}`, { instruction }, {
    signal: getSignal("rejectExtract"),
  });
  return data;
};

export const approveExtractApi = async (sessionId: string, requirementLayer: RequirementLayer): Promise<ApproveExtractResponse> => {
  const { data } = await axiosInstance.post<ApproveExtractResponse>(`/extracts/brd-accept/${sessionId}`, { accepted_edits: { validated_requirement_layer: requirementLayer } }, {
    signal: getSignal("approveExtract"),
  });
  return data;
};

// ── Extract Metadata ─────────────────────────────────────────────────────────

export interface ExtractMetadataPayload {
  user_id: string;
  session_id: string;
  brd_gcs_uri: string;
  layout_gcs_uri: string;
}

export interface FileAttribute {
  "Attribute Name": string;
  "Logical Attribute Name": string | null;
  "Attribute Description": string | null;
  "Data Type": string;
  "Length": string | null;
  "Precision": string | null;
  "Format": string | null;
  "Nullability": string | null;
  "Default Value": string | null;
  "Primary Key": string | null;
  "Foreign Key": string | null;
  "Alternate Key1": string | null;
}

export interface ExtractedFile {
  entity_type: string;
  file_type: string;
  entity_physical_name: string;
  entity_business_name: string;
  entity_description: string;
  attributes: FileAttribute[];
}

export interface ExtractMetadataResponse {
  success: boolean;
  session_id: string;
  extracted_filespecs: Record<string, string | null>;
  extracted_file1?: ExtractedFile;
  bq_reference: {
    metadata_table: string;
    filespecs_table: string;
  };
}

export interface ReviewMetadataResponse {
  session_id: string;
  status: string;
  gcs_output_uri: string;
}

export const extractMetadataApi = async (payload: ExtractMetadataPayload): Promise<ExtractMetadataResponse> => {
  const session_id = sessionStorage.getItem("session_id") ?? payload.session_id;
  const { data } = await axiosInstance.post<ExtractMetadataResponse>("/extracts/extract-metadata", {
    ...payload,
    session_id,
  }, { signal: getSignal("extractMetadata") });
  return data;
};

export const reviewMetadataApi = async (payload: ExtractMetadataResponse): Promise<ReviewMetadataResponse> => {
  const sessionId = getCurrentAppSessionId();
  console.debug("[reviewMetadataApi] payload:", JSON.stringify(payload, null, 2));
  const { data } = await axiosInstance.post<ReviewMetadataResponse>(`/extracts/${sessionId}/final_metadata_save`, {
    success: payload.success,
    session_id: payload.session_id,
    extracted_filespecs: payload.extracted_filespecs,
    extracted_file1: payload.extracted_file1,
    bq_reference: payload.bq_reference,
  }, { signal: getSignal("reviewMetadata") });
  return data;
};

export interface ManualUpdateMetadataPayload {
  user_id: string;
  session_id: string;
  updated_metadata: Record<string, any>;
  bq_reference: Record<string, any>;
}

export const manualUpdateMetadataApi = async (payload: ManualUpdateMetadataPayload): Promise<ReviewMetadataResponse> => {
  const { data } = await axiosInstance.post<ReviewMetadataResponse>("/extracts/extract-metadata/manual-update", payload, {
    signal: getSignal("manualUpdateMetadata"),
  });
  return data;
};

// ── Extract Mapping ──────────────────────────────────────────────────────────

export interface MappingRow {
  target_attribute: string | null;
  logical_attribute_name: string | null;
  attribute_description: string | null;
  data_type: string | null;
  length: string | null;
  precision: string | null;
  format: string | null;
  nullable: string | null;
  default_value: string | null;
  order_no: string | null;
  cdc_indicator: string | null;
  key_columns: string | null;
  rule_type: string | null;
  rule_name: string | null;
  source_entity: string | null;
  source_attribute: string | null;
  join: string | null;
  filter: string | null;
  transformation_rule: string | null;
  special_consideration: string | null;
  last_updated: string | null;
  match_level: string | null;
  match_score: number | null;
  open_item: boolean;
  open_item_reason: string | null;
}

export interface ExtractMappingResponse {
  session_id: string;
  status: string;
  common_rules: { Field: string; Value: string }[];
  transformation_rules: {
    target_entity: string | null;
    driver_table_required: string | null;
    history_data_pull: string | null;
    common_filter: string;
    rows: MappingRow[];
  };
}

export interface MappingAcceptPayload {
  common_rules: { Field: string; Value: string }[];
  transformation_rules: ExtractMappingResponse["transformation_rules"];
}

export const extractMappingApi = async (sessionId: string): Promise<ExtractMappingResponse> => {
  const { data } = await axiosInstance.post<ExtractMappingResponse>(`/extracts/${sessionId}/mapping`, {
    appName: `projects/677861082546/locations/us-central1/reasoningEngines/${sessionStorage.getItem("app_name")}`,
    sessionId: sessionStorage.getItem("session_id"),
    user_id: sessionStorage.getItem("user_id"),
    _test_field_limit: 5,
  }, { signal: getSignal("extractMapping") });
  return data;
};

export const acceptMappingApi = async (payload: MappingAcceptPayload): Promise<ExtractMappingResponse> => {
  const sessionId = getCurrentAppSessionId();
  const { data } = await axiosInstance.post<ExtractMappingResponse>(`/extracts/${sessionId}/mapping/accept`, {
    common_rules: payload.common_rules,
    transformation_rules: payload.transformation_rules,
  }, { signal: getSignal("acceptMapping") });
  return data;
};

export interface FieldHumanCheckpointPayload {
  appName: string;
  sessionId: string;
  user_id: string;
  target_attribute: string;
  current_row: MappingRow;
  bsa_instruction: string;
}

export interface FieldHumanCheckpointResponse {
  success: boolean;
  session_id: string;
  row: MappingRow;
}

export const fieldHumanCheckpointApi = async (payload: FieldHumanCheckpointPayload): Promise<MappingRow> => {
  const sessionId = getCurrentAppSessionId();
  const { target_attribute, logical_attribute_name, attribute_description, data_type, length, precision, format,
    nullable, default_value, order_no, cdc_indicator, key_columns, rule_type, rule_name, source_entity,
    source_attribute, join, filter, transformation_rule, special_consideration, last_updated,
    match_level, match_score, open_item, open_item_reason } = payload.current_row;
  const { data } = await axiosInstance.post<FieldHumanCheckpointResponse>(`/extracts/${sessionId}/mapping/field/human-checkpoint`, {
    appName: `projects/677861082546/locations/us-central1/reasoningEngines/${sessionStorage.getItem("app_name")}`,
    sessionId: sessionStorage.getItem("session_id"),
    user_id: payload.user_id,
    target_attribute: payload.target_attribute,
    current_row: {
      target_attribute, logical_attribute_name, attribute_description, data_type, length, precision, format,
      nullable, default_value, order_no, cdc_indicator, key_columns, rule_type, rule_name, source_entity,
      source_attribute, join, filter, transformation_rule, special_consideration, last_updated,
      match_level, match_score, open_item, open_item_reason,
    },
    bsa_instruction: payload.bsa_instruction,
  }, { signal: getSignal(`fieldHumanCheckpoint:${payload.target_attribute}`) });
  return data.row;
};

// ── Judge H1 ─────────────────────────────────────────────────────────────────

export interface JudgeH1Payload {
  user_id: string;
  session_id: string;
  brd_gcs_uri: string;
  layout_gcs_uri: string;
  transcript_gcs_uri: string;
  brd_markdown_gcs_uri: string;
  layout_markdown_gcs_uri: string;
  judge_mode: string;
  bsa_rejection_feedback: string;
  revision_number: number;
}

export interface JudgeKpiScore {
  score: number;
  numerator: number;
  denominator: number;
  definition: string;
}

export interface JudgeH1LlmJudgment {
  verdict: string;
  summary: string;
  findings: string[];
  per_item_judgments: {
    item_id: string;
    item_type: string;
    present_in_output: boolean;
    supported_by_source: boolean;
    contradicts_source: boolean;
    follows_instructions: boolean;
    evidence_quote: string;
    rationale: string;
  }[];
}

export interface JudgeH1Response {
  success: boolean;
  session_id: string;
  layer: string;
  revision_number: number;
  judged_at: string;
  kpis: Record<string, JudgeKpiScore>;
  llm_judgment: JudgeH1LlmJudgment;
  artifact_gcs_uri: string;
}

// kept for other judge endpoints that still use rule_scores
export interface JudgeRuleScore {
  rule_id: string;
  rule_name: string;
  verdict: string;
  score: number;
  weight: number;
  evidence: string;
  citations: string[];
  blocking: boolean;
  recommendations: string[];
}

export const judgeH1Api = async (payload: JudgeH1Payload): Promise<JudgeH1Response> => {
  const { data } = await axiosInstance.post<JudgeH1Response>("/quality/requirements/judge", {
    ...payload,
    session_id: sessionStorage.getItem("session_id") ?? payload.session_id,
  }, {
    signal: getSignal("judgeH1"),
  });
  return data;
};

// ── Driver Mapping ────────────────────────────────────────────────────────────

export interface FilterCandidate {
  brd_concept: string;
  brd_source: string;
  filter_category: string;
  dart_field: string;
  dart_table: string;
  dart_layer: string;
  filter_type: string;
  suggested_values: string[];
  sql_clause: string | null;
  standards_reference: string | null;
  confidence: number;
  needs_fyi_lookup: boolean;
  mapping_notes: string | null;
  open_item: boolean;
  open_item_reason: string | null;
  bsa_question: string | null;
  filter_scope: string;
  file_name: string | null;
}

export interface DriverMapping {
  filter_candidates: FilterCandidate[];
  unmapped_concepts: string[];
  ibc_aha_context: string;
}

export interface DriverMappingPayload {
  appName: string;
  sessionId: string;
  userId: string;
  brd_uri: string;
  brd: Record<string, any>;
}

export interface DriverMappingResponse {
  status: string;
  req_id: string;
  elapsed_sec: number;
  events: number;
  event_dir: string;
  driver_mapping: DriverMapping;
}

export const driverMappingApi = async (payload: DriverMappingPayload): Promise<DriverMappingResponse> => {
  const { data } = await axiosInstance.post<DriverMappingResponse>("/extract/driver/business-mapping", {
    appName: payload.appName,
    sessionId: payload.sessionId,
    userId: payload.userId,
    brd_uri: payload.brd_uri,
    brd: payload.brd,
  }, { signal: getSignal("driverMapping") });
  return data;
};

// ── Driver Logic ──────────────────────────────────────────────────────────────

export interface BsaQuestion {
  filter_id: string;
  dart_field: string;
  bsa_question: string;
}

export interface DriverLogicFilter {
  filter_id: string;
  filter_category: string;
  filter_scope: string;
  file_name: string | null;
  dart_field: string;
  dart_table: string;
  dart_layer: string;
  filter_type: string;
  filter_values: string[];
  sql_clause: string | null;
  odf_sel_crta_ref: string | null;
  brd_traceability: string[];
  confidence: number;
  source: string;
  open_item: boolean;
  open_item_reason: string | null;
  bsa_question: string | null;
  notes: string;
}

export interface DriverLogic {
  common_filters: DriverLogicFilter[];
  sql_where_clause: string;
}

export interface DriverLogicResponse {
  status: string;
  req_id: string;
  elapsed_sec: number;
  events: number;
  event_dir: string;
  summary: {
    filter_count: number;
    open_item_count: number;
    bsa_question_count: number;
    ibc_aha_context: string;
  };
  bsa_questions: BsaQuestion[];
  sql_where_clause: string;
  driver_logic: DriverLogic;
}

export const driverLogicApi = async (payload: DriverMappingPayload): Promise<DriverLogicResponse> => {
  const { data } = await axiosInstance.post<DriverLogicResponse>("/extract/driver/logic", {
    appName: payload.appName,
    sessionId: payload.sessionId,
    userId: payload.userId,
    brd_uri: payload.brd_uri,
    brd: payload.brd,
  }, { signal: getSignal("driverLogic") });
  return data;
};

// ── Driver Validate ───────────────────────────────────────────────────────────

export interface ValidationIssue {
  issue_type: string;
  severity: string;
  filter_id: string;
  description: string;
  recommended_action: string;
}

export interface DriverValidation {
  issues: ValidationIssue[];
  total_high: number;
  total_medium: number;
  all_brd_requirements_traced: boolean;
  no_transformation_logic: boolean;
  standards_compliant: boolean;
  can_proceed: boolean;
}

export interface DriverValidateResponse {
  status: string;
  req_id: string;
  elapsed_sec: number;
  driver_validation: DriverValidation;
}

export const driverValidateApi = async (payload: DriverMappingPayload): Promise<DriverValidateResponse> => {
  const { data } = await axiosInstance.post<DriverValidateResponse>("/extract/driver/validate", {
    appName: payload.appName,
    sessionId: payload.sessionId,
    userId: payload.userId,
    brd_uri: payload.brd_uri,
    brd: payload.brd,
  }, { signal: getSignal("driverValidate") });
  return data;
};

// ── Driver Approve ────────────────────────────────────────────────────────────

export interface DriverApprovePayload {
  appName: string;
  sessionId: string;
  userId: string;
  bsa_notes?: string;
}

export interface DriverApproveResponse {
  status: string;
  req_id: string;
  approved_driver_logic: DriverLogic;
}

export const driverApproveApi = async (payload: DriverApprovePayload): Promise<DriverApproveResponse> => {
  const { data } = await axiosInstance.post<DriverApproveResponse>("/extract/driver/approve", {
    appName: payload.appName,
    sessionId: payload.sessionId,
    userId: payload.userId,
    bsa_notes: payload.bsa_notes || "",
    user_session_id: getCurrentAppSessionId(),
    brd_gcs_uri: sessionStorage.getItem("brd_gcs_uri"),
  }, { signal: getSignal("driverApprove") });
  return data;
};

// ── Driver Checkpoint ─────────────────────────────────────────────────────────

export interface CheckpointSummary {
  ibc_aha_context: string;
  filter_count: number;
  open_item_count: number;
  bsa_question_count: number;
  validation_high_issues: number;
  validation_medium_issues: number;
  standards_compliant: boolean;
  no_transformation_logic: boolean;
  all_brd_requirements_traced: boolean;
}

export interface DriverCheckpointPayload {
  appName: string;
  sessionId: string;
  userId: string;
  brd_uri: string;
  instruction: string;
}

export interface DriverCheckpointResponse {
  status: string;
  req_id: string;
  elapsed_sec: number;
  total_events: number;
  can_proceed: boolean;
  summary: CheckpointSummary;
  bsa_questions: BsaQuestion[];
  sql_where_clause: string;
  driver_mapping: DriverMapping;
  driver_logic: DriverLogic & { global_filter_count: number; file_level_filter_count: number; open_item_count: number; ibc_aha_context: string };
  driver_validation: DriverValidation;
}

// ── Driver Patch Filter ───────────────────────────────────────────────────────

export interface DriverPatchFilterPayload {
  appName: string;
  sessionId: string;
  userId: string;
  filter_id: string;
  edits: Record<string, unknown>;
}

export interface DriverPatchFilterResponse {
  status: string;
  req_id: string;
  updated_filter: DriverLogicFilter;
}

export const driverPatchFilterApi = async (payload: DriverPatchFilterPayload): Promise<DriverPatchFilterResponse> => {
  const { data } = await axiosInstance.post<DriverPatchFilterResponse>("/extract/driver/patch-filter", {
    appName: payload.appName,
    sessionId: payload.sessionId,
    userId: payload.userId,
    filter_id: payload.filter_id,
    edits: payload.edits,
  }, { signal: getSignal(`driverPatchFilter:${payload.filter_id}`) });
  return data;
};

export const driverCheckpointApi = async (payload: DriverCheckpointPayload): Promise<DriverCheckpointResponse> => {
  const { data } = await axiosInstance.post<DriverCheckpointResponse>("/extract/driver/checkpoint", {
    appName: payload.appName,
    sessionId: payload.sessionId,
    userId: payload.userId,
    brd_uri: payload.brd_uri,
    instruction: payload.instruction,
  }, { signal: getSignal("driverCheckpoint") });
  return data;
};

// ── Driver Save ───────────────────────────────────────────────────────────────

export interface DriverSavePayload {
  appName: string;
  sessionId: string;
  userId: string;
  driver_logic: DriverLogic;
}

export interface DriverSaveResponse {
  status: string;
  driver_logic: DriverLogic;
}

export const driverSaveApi = async (payload: DriverSavePayload): Promise<DriverSaveResponse> => {
  const { data } = await axiosInstance.post<DriverSaveResponse>("/extract/driver/save", {
    appName: payload.appName,
    sessionId: payload.sessionId,
    userId: payload.userId,
    driver_logic: payload.driver_logic,
  }, { signal: getSignal("driverSave") });
  return data;
};

// ── Judge Driver ──────────────────────────────────────────────────────────────

export interface JudgeDriverPayload {
  userId: string;
  sessionId: string;
  brd_uri: string;
  driver_mapping: Record<string, any>;
  driver_logic: Record<string, any>;
  driver_validation: Record<string, any>;
  revision_number: number;
}

export interface JudgeDriverResponse {
  success: boolean;
  session_id: string;
  layer: string;
  revision_number: number;
  judged_at: string;
  kpis: Record<string, JudgeKpiScore>;
  llm_judgment: JudgeH1LlmJudgment;
  artifact_gcs_uri: string;
}

export const judgeDriverApi = async (payload: JudgeDriverPayload): Promise<JudgeDriverResponse> => {
  const { data } = await axiosInstance.post<JudgeDriverResponse>("/quality/driver/judge", {
    ...payload,
    sessionId: sessionStorage.getItem("session_id") ?? payload.sessionId,
  }, {
    signal: getSignal("judgeDriver"),
  });
  return data;
};

// ── Judge Mapping ────────────────────────────────────────────────────────────

export interface JudgeMappingPayload {
  userId: string;
  sessionId: string;
  brd_uri: string;
  driver_uri: string;
  metadata_uri: string;
  mapping_result: Record<string, any>;
  mapping_uri: string;
  revision_number: number;
}

export interface JudgeMappingResponse {
  success: boolean;
  session_id: string;
  layer: string;
  revision_number: number;
  judged_at: string;
  kpis: Record<string, JudgeKpiScore>;
  llm_judgment: JudgeH1LlmJudgment;
  artifact_gcs_uri: string;
}

export const judgeMappingApi = async (payload: JudgeMappingPayload): Promise<JudgeMappingResponse> => {
  const { data } = await axiosInstance.post<JudgeMappingResponse>("/quality/mapping/judge", {
    ...payload,
    sessionId: sessionStorage.getItem("session_id") ?? payload.sessionId,
  }, {
    signal: getSignal("judgeMapping"),
  });
  return data;
};

// ── Judge Metadata ────────────────────────────────────────────────────────────

export interface JudgeMetadataPayload {
  userId: string;
  sessionId: string;
  brd_uri: string;
  layout_uri: string;
  extracted_metadata: {
    extracted_filespecs: Record<string, any>;
    extracted_file1: Record<string, any>;
  };
  revision_number: number;
}

export interface JudgeMetadataResponse {
  success: boolean;
  session_id: string;
  layer: string;
  revision_number: number;
  judged_at: string;
  kpis: Record<string, JudgeKpiScore>;
  llm_judgment: JudgeH1LlmJudgment;
  artifact_gcs_uri: string;
}

export const judgeMetadataApi = async (payload: JudgeMetadataPayload): Promise<JudgeMetadataResponse> => {
  const { data } = await axiosInstance.post<JudgeMetadataResponse>("/quality/metadata/judge", {
    ...payload,
    sessionId: sessionStorage.getItem("session_id") ?? payload.sessionId,
  }, {
    signal: getSignal("judgeMetadata"),
  });
  return data;
};
