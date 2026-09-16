/* eslint-disable import/first -- STTM endpoint modules; CRA eslint cache can false-positive */
import axiosInstance from '../utils/axios-interceptor';
import { apiFetch } from "../utils/apiFetch";
import { API_BASE_URL } from "../config/env";

const BASE_URL = API_BASE_URL;

interface SessionData {
  appName: string | null;
  sessionId: string | null;
  userId: string | null;
}

export const getDefaultDataset = async (): Promise<{ dataset_id: string }> => {
  const response = await axiosInstance.get('/data/default-dataset');
  return response.data;
};

export const validateDatasetTables = async (datasetId: string, tableIds: string[]): Promise<any> => {
  const response = await axiosInstance.post('/messages-strm/validate-dataset-tables', {
    dataset_id: datasetId,
    table_ids: tableIds
  });
  return response.data;
};

export const getTableSchema = async (tableName: string, datasetId: string): Promise<any> => {
  const response = await axiosInstance.get(`/data/table-schema?table_name=${tableName}&dataset_id=${datasetId}`);
  return response.data;
};

export const submitDartSuggestions = async (
  message: string,
  sessionData: SessionData,
  dart_database_name?: string,
  filters?: string[],
): Promise<any> => {
  const response = await apiFetch(`${BASE_URL}/dart/dart-suggestion`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      appName: sessionData.appName,
      sessionId: sessionData.sessionId,
      userId: sessionData.userId,
      newMessage: {
        role: "user",
        parts: [{ text: message }],
      },
      streaming: false,
      stateDelta: {},
      additional_data: {},
      dart_database_name,
      filters,
    }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    const errorMessage = errorData.detail || `Failed to submit Reference suggestions: ${response.status} ${response.statusText}`;
    throw new Error(errorMessage);
  }

  return response.json();
};

export const checkDataOverlap = async (payload: any): Promise<any> => {
  const response = await axiosInstance.post('/dart/data-overlap', payload);
  return response.data;
};
