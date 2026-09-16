/** Standalone STTM API base URL. */
const configured = import.meta.env.VITE_API_BASE_URL?.trim();
const proxyFallback = import.meta.env.DEV ? "/sttm-api" : "http://127.0.0.1:8000";

export const API_BASE_URL = (configured || proxyFallback).replace(/\/$/, "");

export const PROJECT_ID = import.meta.env.VITE_PROJECT_ID;

export const config = {
  apiBaseUrl: API_BASE_URL,
  projectId: PROJECT_ID,
  environment: import.meta.env.MODE,
  isDevelopment: import.meta.env.DEV,
  isProduction: import.meta.env.PROD,
  debug: import.meta.env.VITE_DEBUG === "true",
} as const;

export default config;
