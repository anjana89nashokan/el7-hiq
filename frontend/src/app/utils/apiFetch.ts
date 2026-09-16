import { getAuthToken, getUserIdentity } from "./userIdentity";

/**
 * fetch() wrapper that attaches the Launchpad SSO token (and the SSO-derived
 * identity headers), matching the axios interceptor. Use this for all raw-fetch
 * API calls so sessions/uploads/messages are scoped to the SAME user (otherwise
 * the backend sees a different user_key for fetch vs axios calls and returns
 * "Session not found").
 */
export function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers || {});
  const token = getAuthToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const { userId, userEmail } = getUserIdentity();
  if (userId) headers.set("x-dev-user-id", userId);
  if (userEmail) headers.set("x-dev-user-email", userEmail);
  return fetch(input, { ...init, headers });
}
