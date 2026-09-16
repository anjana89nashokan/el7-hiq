import axiosInstance from "../utils/axios-interceptor";
import { store } from "../state/store";
import { resetAllState } from "../state/actions";
import { getAppSessionDetail, startProfilingRun } from "./appSessionsApi";
import { getCurrentAppSessionId, setSessionRuntime } from "../utils/appSessionStorage";

// Type definitions
// Constants
const MAX_FILE_SIZE = 100 * 1024 * 1024; // 100MB

// Allowed file extensions
const ALLOWED_EXTENSIONS = [
  '.csv', '.json', '.xml', '.xlsx', '.xls', '.psv', '.txt', '.zip',
  '.dat', '.fwf', '.asc', '.prn', '.out', '.log', '.data', '.tsv', '.ced',
  '.hl7'
];

// Helper Functions
export const validateFileSize = (file: File): boolean => {
  if (file.size > MAX_FILE_SIZE) {
    throw new Error(
      `File size exceeds the maximum limit of 100MB. Current file size: ${(file.size / (1024 * 1024)).toFixed(2)}MB`
    );
  }
  return true;
};

export const validateFileExtension = (file: File): boolean => {
  const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
  if (!ALLOWED_EXTENSIONS.includes(ext)) {
    throw new Error(`File type not allowed. Allowed types: ${ALLOWED_EXTENSIONS.join(', ')}`);
  }
  return true;
};

// Session Management
export const initializeSessions = async (): Promise<{ name: string; createTime: string; updateTime: string }> => {
  const currentSessionId = getCurrentAppSessionId();
  if (!currentSessionId) {
    throw new Error("No app session selected.");
  }
  const detail = await getAppSessionDetail(currentSessionId);
  const name = detail.runtime.vertex_app_name || "app-session";
  const ts = detail.session.created_at || new Date().toISOString();
  return { name, createTime: ts, updateTime: detail.session.updated_at || ts };
};

export const checkAndInitializeSessions = async (): Promise<void> => {
  return;
};

export const createSession = async (): Promise<string | null> => {
  try {
    const currentSessionId = getCurrentAppSessionId();
    if (!currentSessionId) {
      throw new Error("Please create or select a session first.");
    }
    const detail = await getAppSessionDetail(currentSessionId);
    store.dispatch(resetAllState());
    let runtime = detail.runtime;
    if (!runtime?.vertex_session_id) {
      // The backend creates the ADK/Vertex runtime lazily (not at app-session
      // creation), so start a profiling run here to obtain one before upload.
      runtime = await startProfilingRun(currentSessionId);
    }
    setSessionRuntime(runtime);
    return detail.session.id;
  } catch (error) {
    console.error("Error creating session:", error);
    throw error; // Re-throw to prevent upload API call
  }
};

// File Upload
export const uploadFilesApi = async (files: File[], payload?: any) => {
  const appSessionId = await createSession();
  if (!appSessionId) {
    throw new Error('Failed to create session - cannot proceed with upload');
  }
  
  const formData = new FormData();

  files.forEach((file) => {
    validateFileSize(file);
    validateFileExtension(file);
    formData.append("files", file, file.name);
  });

  if (payload) {
    formData.append("vendor_name", payload.vendor_details?.name || "");
    formData.append("app_session_id", appSessionId || "");
    formData.append("vendor_contact_person", payload.vendor_details?.contact_person || "");
    formData.append("vendor_contact_name", payload.vendor_details?.contact_name || "");
    formData.append("vendor_email", payload.vendor_details?.contact_person || "");
    formData.append("vendor_phone_number", payload.vendor_details?.phone_number || "");
    formData.append("vendor_server_name", payload.vendor_details?.server_name || "");
    formData.append("project_name", payload.project_context?.project_name || "");
    formData.append("file_delivery_frequency", payload.vendor_details?.file_delivery_frequency?.toString() || "");
    formData.append("frequency_mode", payload.vendor_details?.frequency_mode || "");
    formData.append("transfer_method", payload.vendor_details?.transfer_method || "");
    formData.append("file_compression_type", payload.file_details?.compression_type || "");
    formData.append("file_population_type", payload.file_details?.population_type || "");
    formData.append("header_record_number", payload.file_details?.header_record_number || "");
    formData.append("trailer_record_number", payload.file_details?.trailer_record_number || "");
    formData.append("quote_indicator", payload.file_details?.quote_indicator || "");
    formData.append("date_timestamp_format", payload.file_details?.date_timestamp_format || "");
    formData.append("receive_file_when_no_data", payload.file_details?.receive_file_when_no_data || "0");
    formData.append("email_notification_dl", payload.notification?.email_dl || "");
    formData.append("assumptions", payload.additional_info?.assumptions || "");
    formData.append("dependencies", payload.additional_info?.dependencies || "");
    formData.append("brd_description", payload.brd_details?.description || "");
    formData.append("spec_description", payload.erwin_model?.description || "");
    
    if (payload.brd_file) {
      formData.append("brd_file", payload.brd_file);
    }
    if (payload.erwin_model_file) {
      formData.append("file_spec_file", payload.erwin_model_file);
    }
    if (Array.isArray(payload.data_dictionary_files) && payload.data_dictionary_files.length > 0) {
      payload.data_dictionary_files.forEach((file: File) => {
        formData.append("data_dict_files", file, file.name);
      });
    } else if (payload.data_dictionary_file) {
      // Backward compatibility
      formData.append("data_dict_file", payload.data_dictionary_file);
    }
  }

  const response = await axiosInstance.post('/files/upload-batch', formData, {
    headers: {
      Accept: "application/json",
    },
  });

  if (response.status !== 200) {
    throw new Error(`Upload failed with status ${response.status}: ${response.statusText}`);
  }

  return response.data;
};

// Process files after DD selection - STATELESS
export const processFiles = async (files: File[], metadataPath: string, sessionId: string) => {
  const formData = new FormData();
  
  files.forEach((file) => {
    formData.append("files", file, file.name);
  });
  
  formData.append("metadata_path", metadataPath);
  formData.append("session_id", sessionId);

  const response = await axiosInstance.post('/files/process-files', formData, {
    headers: {
      "Content-Type": "multipart/form-data",
      Accept: "application/json"
    }
  });

  if (response.status !== 200) {
    throw new Error(`Failed to process files: ${response.statusText}`);
  }

  return response.data;
};

// Save selected DD paths and optionally merge them
export const saveSelectedDD = async (sessionId: string, selectedPaths: string[], shouldMerge: boolean = false, columnMappings?: any[], targetSchema?: any[]) => {
  const formData = new FormData();
  formData.append("session_id", sessionId);
  selectedPaths.forEach(path => {
    formData.append("selected_paths", path);
  });
  formData.append("should_merge", shouldMerge.toString());
  
  if (columnMappings) {
    formData.append("column_mappings", JSON.stringify(columnMappings));
  }
  
  if (targetSchema) {
    formData.append("target_schema", JSON.stringify(targetSchema));
  }

  const response = await axiosInstance.post('/files/save-selected-dd', formData, {
    headers: {
      "Content-Type": "multipart/form-data",
      Accept: "application/json"
    }
  });

  if (response.status !== 200) {
    throw new Error(`Failed to save DD selection: ${response.statusText}`);
  }

  return response.data;
};

// Utility Functions
export const getSerializableFileInfo = (file: File) => ({
  name: file.name,
  type: file.type,
  size: file.size,
  lastModified: file.lastModified,
  lastModifiedDate: file.lastModified
    ? new Date(file.lastModified).toISOString()
    : null,
});
