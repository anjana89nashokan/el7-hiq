/**
 * Standalone STTM — no login. Fixed dev identity sent to the backend on every request.
 */

const DEFAULT_USER_ID = "local-user";
const DEFAULT_USER_EMAIL = "user@local";
const DEFAULT_USER_NAME = "Local User";

export function getAuthToken(): string {
  return "";
}

export function consumeSsoTokenFromUrl(): void {
  // No SSO in standalone mode.
}

export function getUserIdentity(): { userId: string; userEmail: string } {
  return { userId: DEFAULT_USER_ID, userEmail: DEFAULT_USER_EMAIL };
}

export function setUserIdentity(_userId: string, _userEmail?: string): void {
  // No-op — identity is fixed.
}

export function login(_name: string, _email?: string): void {
  // No-op — no login in standalone mode.
}

export function isLoggedIn(): boolean {
  return true;
}

export function getUserName(): string {
  return DEFAULT_USER_NAME;
}

export function logout(): void {
  // No-op — no login in standalone mode.
}
