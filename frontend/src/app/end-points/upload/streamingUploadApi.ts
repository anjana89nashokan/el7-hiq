import axiosInstance from "../../utils/axios-interceptor";
import { createSession } from "../uploadIdentifier";
import { PROJECT_ID } from "../../config/env";
import { getCurrentAppSessionId } from "../../utils/appSessionStorage";

// Type definitions
export interface StreamingUploadData {
  tableNames: string[];
  databaseName: string;
  projectName: string;
  vendorName: string;
  contactPerson?: string;
  contactName?: string;
  phoneNumber?: string;
  serverName?: string;
  deliveryFrequency: string;
  frequencyMode?: string;
  transferMethod?: string;
  compressionType?: string;
  populationType?: string;
  headerRecordNumber?: string;
  trailerRecordNumber?: string;
  quoteIndicator?: string;
  dateTimestampFormat?: string;
  receiveFileWhenNoData?: string;
  emailNotificationDl?: string;
  assumptions?: string;
  dependencies?: string;
  brdDescription?: string;
  erwinModelDescription?: string;
  dataDictionaryFile?: File | null;
  dataDictionaryFiles?: File[];
  brdFile?: File | null;
  erwinModelFile?: File | null;
}

export interface MockApiResponse {
  total_files: number;
  successful_uploads: Array<{
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
  }>;
  failed_uploads: any[];
  summary: {
    successful: number;
    failed: number;
    total_rows_uploaded: number;
  };
}

// Constants
const MAX_FILE_SIZE_MB = 2;

const getOrCreateSessionId = async (): Promise<string | null> => {
  const existingSession = sessionStorage.getItem("session_id");
  if (existingSession) {
    return existingSession;
  }
  await createSession();
  return sessionStorage.getItem("session_id");
};

// File validation
export const validateStreamingFile = (
  file: File,
  label: string,
): string | null => {
  const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
  const validExtensions =
    label === "Data Dictionary"
      ? [".csv", ".xlsx", ".txt", ".docx"]
      : [".pdf", ".docx", ".txt"];

  if (!validExtensions.includes(ext)) {
    return `${label}: Invalid file extension. Allowed: ${validExtensions.join(", ")}`;
  }

  const maxSize = MAX_FILE_SIZE_MB * 1024 * 1024;
  if (file.size > maxSize) {
    return `${label}: File size exceeds ${MAX_FILE_SIZE_MB} MB.`;
  }

  return null;
};

// Form validation
export const validateStreamingForm = (data: StreamingUploadData): string[] => {
  const errors: string[] = [];
  const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  if (data.tableNames.length === 0)
    errors.push("At least one table name is required");
  if (!data.projectName.trim()) errors.push("Project Name is required");
  if (!data.vendorName.trim()) errors.push("Vendor Name is required");
  if (!data.deliveryFrequency)
    errors.push("File Delivery Frequency is required");
  if (data.contactPerson && !emailRegex.test(data.contactPerson)) {
    errors.push("Contact Person email must be a valid email address");
  }

  return errors;
};

// Table name validation
export const validateTableName = (
  name: string,
  existingNames: string[],
): string | null => {
  if (!name.trim()) {
    return "Table name cannot be empty";
  }
  if (/\s/.test(name)) {
    return "Table name cannot contain spaces";
  }
  if (existingNames.includes(name.trim())) {
    return "Table name already added";
  }
  return null;
};

// Start streaming profiling
export const startStreamingProfiling = async (
  data: StreamingUploadData,
): Promise<MockApiResponse> => {
  // Use existing session if available
  const sessionId = await getOrCreateSessionId();
  if (!sessionId) {
    throw new Error("Failed to create session");
  }

  // Save project details to sessionStorage (tab-isolated)
  sessionStorage.setItem(
    "project_details",
    JSON.stringify({
      projectName: data.projectName,
      vendorName: data.vendorName,
      contactPerson: data.contactPerson,
      contactName: data.contactName,
      phoneNumber: data.phoneNumber,
      serverName: data.serverName,
      deliveryFrequency: data.deliveryFrequency,
      frequencyMode: data.frequencyMode,
      transferMethod: data.transferMethod,
      compressionType: data.compressionType,
      populationType: data.populationType,
      headerRecordNumber: data.headerRecordNumber,
      trailerRecordNumber: data.trailerRecordNumber,
      quoteIndicator: data.quoteIndicator,
      dateTimestampFormat: data.dateTimestampFormat,
      receiveFileWhenNoData: data.receiveFileWhenNoData,
      emailNotificationDl: data.emailNotificationDl,
      assumptions: data.assumptions,
      dependencies: data.dependencies,
      brdDescription: data.brdDescription,
      erwinModelDescription: data.erwinModelDescription,
      databaseName: data.databaseName,
    }),
  );

  // Create mock ApiResponse structure
  const mockApiResponse: MockApiResponse = {
    total_files: data.tableNames.length,
    successful_uploads: data.tableNames.map((tableName, index) => ({
      sessionID: sessionId,
      user: sessionStorage.getItem("user_id") || "user-123",
      createdDate: new Date().toISOString(),
      lastUpdateDate: new Date().toISOString(),
      file_id: `direct-${index}`,
      filename: tableName,
      table_name: `${PROJECT_ID}.${data.databaseName}.${tableName}`,
      dataset_id: data.databaseName,
      project_id: PROJECT_ID ?? "",
      rows_uploaded: 0,
      upload_timestamp: new Date().toISOString(),
      initial_profiling_report: "",
      profiling_report_url: "",
    })),
    failed_uploads: [],
    summary: {
      successful: data.tableNames.length,
      failed: 0,
      total_rows_uploaded: 0,
    },
  };

  return mockApiResponse;
};

// Upload supplemental files and resolve DD candidates (streaming flow)
export const uploadStreamingBatch = async (payload: StreamingUploadData) => {
  const sessionId = await getOrCreateSessionId();
  if (!sessionId) {
    throw new Error("Failed to create session - cannot proceed with upload");
  }

  const formData = new FormData();
  formData.append("session_id", sessionId);
  const appSessionId = getCurrentAppSessionId();
  if (appSessionId) {
    formData.append("app_session_id", appSessionId);
  }
  formData.append("vendor_name", payload.vendorName || "");
  formData.append("vendor_contact_person", payload.contactPerson || "");
  formData.append("project_name", payload.projectName || "");
  formData.append("file_delivery_frequency", payload.deliveryFrequency || "");
  formData.append("brd_description", payload.brdDescription || "");
  formData.append("spec_description", payload.erwinModelDescription || "");
  formData.append("transfer_method", "");
  formData.append("vendor_contact_name", "");
  formData.append("frequency_mode", "");
  formData.append("vendor_phone_number", "");
  formData.append("dependencies", "");
  formData.append("vendor_email", payload.contactPerson || "");
  formData.append("email_notification_dl", "");
  formData.append("date_timestamp_format", "");
  formData.append("header_record_number", "");
  formData.append("trailer_record_number", "");
  formData.append("quote_indicator", "");
  formData.append("file_population_type", "");
  formData.append("file_compression_type", "");
  formData.append("receive_file_when_no_data", "");
  formData.append("assumptions", "");
  formData.append("vendor_server_name", "");

  if (payload.brdFile) {
    formData.append("brd_file", payload.brdFile);
  }
  if (payload.erwinModelFile) {
    formData.append("file_spec_file", payload.erwinModelFile);
  }

  if (Array.isArray(payload.dataDictionaryFiles) && payload.dataDictionaryFiles.length > 0) {
    payload.dataDictionaryFiles.forEach((file) => {
      formData.append("data_dict_files", file, file.name);
    });
  } else if (payload.dataDictionaryFile) {
    formData.append("data_dict_file", payload.dataDictionaryFile);
  }

  const response = await axiosInstance.post("/messages-strm/upload-batch", formData, {
    headers: {
      "Content-Type": "multipart/form-data",
      Accept: "application/json",
    },
  });

  if (response.status !== 200) {
    throw new Error(`Upload failed with status ${response.status}: ${response.statusText}`);
  }

  return response.data;
};

export const saveSelectedStreamingDD = async (
  sessionId: string,
  selectedPaths: string[],
  shouldMerge: boolean = false,
  columnMappings?: any[],
  targetSchema?: any[],
) => {
  const formData = new FormData();
  formData.append("session_id", sessionId);
  selectedPaths.forEach((path) => {
    formData.append("selected_paths", path);
  });
  formData.append("should_merge", shouldMerge.toString());

  if (columnMappings) {
    formData.append("column_mappings", JSON.stringify(columnMappings));
  }

  if (targetSchema) {
    formData.append("target_schema", JSON.stringify(targetSchema));
  }

  const response = await axiosInstance.post("/messages-strm/save-selected-dd", formData, {
    headers: {
      "Content-Type": "multipart/form-data",
      Accept: "application/json",
    },
  });

  if (response.status !== 200) {
    throw new Error(`Failed to save DD selection: ${response.statusText}`);
  }

  return response.data;
};
