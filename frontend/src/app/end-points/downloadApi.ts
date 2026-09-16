import { getCurrentAppSessionId } from '../utils/appSessionStorage';
import { apiFetch } from "../utils/apiFetch";
import { API_BASE_URL } from "../config/env";

export interface DownloadResponse {
  brd?: string;
  files?: string[];
  dd?: string;
}

export interface FileUpload {
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

export interface ApiResponse {
  total_files: number;
  successful_uploads: FileUpload[];
  failed_uploads: { filename: string; error: string }[];
  summary: {
    successful: number;
    failed: number;
    total_rows_uploaded: number;
  };
}

const getBaseUrl = () => API_BASE_URL;

export const fetchDownloadData = async (sessionId?: string): Promise<DownloadResponse> => {
  const id = sessionId || getCurrentAppSessionId();
  if (!id) throw new Error('No session ID found');

  const response = await apiFetch(`${getBaseUrl()}/files/download/${id}`, {
    method: 'GET',
    headers: { 'accept': 'application/json' }
  });

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`);
  }

  return response.json();
};

export const fetchFile = async (url: string, sessionId?: string): Promise<Response> => {
  const id = sessionId || getCurrentAppSessionId();
  if (!id) throw new Error('No session ID found');

  try {
    const encodedUrl = encodeURIComponent(url);
    const downloadUrl = `${getBaseUrl()}/files/download/${id}/${encodedUrl}`;
    const response = await apiFetch(downloadUrl);
    
    if (response.ok) return response;
    throw new Error(`API failed: ${response.status}`);
  } catch {
    const response = await apiFetch(url, { mode: 'cors', credentials: 'omit' });
    if (!response.ok) throw new Error(`Direct failed: ${response.status}`);
    return response;
  }
};

export const prepareDownloadData = (profilingData: ApiResponse): DownloadResponse => {
  const baseUrl = getBaseUrl();
  const downloadData: DownloadResponse = {};
  
  if (profilingData?.successful_uploads?.length > 0) {
    downloadData.files = profilingData.successful_uploads
      .filter(upload => upload?.profiling_report_url)
      .map(upload => `${baseUrl}${upload.profiling_report_url}`);
  }
  
  return downloadData;
};