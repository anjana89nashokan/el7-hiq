import axios from 'axios';
import { API_BASE_URL } from '../config/env';
import { getAuthToken, getUserIdentity } from './userIdentity';

// Create axios instance for upload API (port 8000)
const axiosInstance = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json'
  }
});

// Request interceptor: attach the per-user identity so the backend scopes
// sessions/uploads/profiling to the actual user (no hardcoded default user).
axiosInstance.interceptors.request.use(
  (config) => {
    // Let the browser set multipart boundaries — a bare "multipart/form-data"
    // header without boundary breaks file uploads.
    if (config.data instanceof FormData) {
      config.headers.delete('Content-Type');
    }

    // Primary auth: the Launchpad SSO JWT (validated server-side).
    const token = getAuthToken();
    if (token) {
      config.headers.set('Authorization', `Bearer ${token}`);
    }
    // Also send the SSO-derived identity headers so the backend scopes data to
    // the user in either auth mode (launchpad_sso reads the bearer; dev reads these).
    const { userId, userEmail } = getUserIdentity();
    if (userId) config.headers.set('x-dev-user-id', userId);
    if (userEmail) config.headers.set('x-dev-user-email', userEmail);
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// Response interceptor for upload API
axiosInstance.interceptors.response.use(
  (response) => {
    return response;
  },
  (error) => {
    if (error.response?.data?.detail) {
      const enhancedError = new Error(messageFromDetail(error.response.data.detail));
      enhancedError.name = 'APIError';
      return Promise.reject(enhancedError);
    }
    return Promise.reject(error);
  }
);

function messageFromDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((entry) =>
        entry && typeof entry === 'object'
          ? (entry as { msg?: string; message?: string }).msg ||
            (entry as { msg?: string; message?: string }).message
          : String(entry),
      )
      .filter(Boolean);
    if (parts.length) return parts.join('; ');
  }
  if (detail && typeof detail === 'object') {
    const value = detail as { message?: string; request_id?: string; detail?: string };
    if (typeof value.message === 'string' && value.message.trim()) {
      return value.request_id ? `${value.message} (request id: ${value.request_id})` : value.message;
    }
    if (typeof value.detail === 'string') return value.detail;
    try {
      return JSON.stringify(detail);
    } catch {
      return 'Request failed';
    }
  }
  return 'Request failed';
}

export default axiosInstance;
