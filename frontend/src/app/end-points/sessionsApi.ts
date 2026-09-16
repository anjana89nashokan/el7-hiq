/* eslint-disable import/first -- STTM endpoint modules; CRA eslint cache can false-positive */
import { apiFetch } from "../utils/apiFetch";
import { API_BASE_URL } from "../config/env";

const baseUrl = API_BASE_URL;

export const fetchSessions = async (): Promise<any> => {
  const response = await apiFetch(`${baseUrl}/sessions/app/list`, {
    method: 'GET',
    headers: {
      'accept': 'application/json',
      'Content-Type': 'application/json',
    },
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    const errorMessage = errorData.detail || `Failed to fetch sessions: ${response.status} ${response.statusText}`;
    throw new Error(errorMessage);
  }

  return await response.json();
};
