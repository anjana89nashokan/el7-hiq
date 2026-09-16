const APP_SESSION_ID_KEY = "current_app_session_id";
const VERTEX_SESSION_ID_KEY = "session_id";
const VERTEX_APP_NAME_KEY = "app_name";
const USER_ID_KEY = "user_id";
const USER_EMAIL_KEY = "user_email";

const SESSION_EVENT = "app-session-changed";
const NEW_SESSION_LOADING_EVENT = "new-session-loading";

export interface SessionRuntime {
  vertex_session_id?: string | null;
  vertex_app_name?: string | null;
  vertex_user_id?: string | null;
}

export function emitSessionChanged(): void {
  globalThis.dispatchEvent(new Event(SESSION_EVENT));
}

export function onSessionChanged(listener: () => void): () => void {
  globalThis.addEventListener(SESSION_EVENT, listener);
  return () => globalThis.removeEventListener(SESSION_EVENT, listener);
}

export function emitNewSessionLoading(loading: boolean): void {
  globalThis.dispatchEvent(new CustomEvent(NEW_SESSION_LOADING_EVENT, { detail: loading }));
}

export function onNewSessionLoading(listener: (loading: boolean) => void): () => void {
  const handler = (e: Event) => listener((e as CustomEvent<boolean>).detail);
  globalThis.addEventListener(NEW_SESSION_LOADING_EVENT, handler);
  return () => globalThis.removeEventListener(NEW_SESSION_LOADING_EVENT, handler);
}

export function getCurrentAppSessionId(): string | null {
  return sessionStorage.getItem(APP_SESSION_ID_KEY);
}

export function setCurrentAppSessionId(sessionId: string | null): void {
  const currentValue = getCurrentAppSessionId();
  if (currentValue === sessionId) {
    return;
  }
  if (sessionId) {
    sessionStorage.setItem(APP_SESSION_ID_KEY, sessionId);
  } else {
    sessionStorage.removeItem(APP_SESSION_ID_KEY);
  }
  emitSessionChanged();
}

export function setSessionRuntime(runtime?: SessionRuntime | null): void {
  if (runtime?.vertex_session_id) {
    sessionStorage.setItem(VERTEX_SESSION_ID_KEY, runtime.vertex_session_id);
  } else {
    sessionStorage.removeItem(VERTEX_SESSION_ID_KEY);
  }
  if (runtime?.vertex_app_name) {
    sessionStorage.setItem(VERTEX_APP_NAME_KEY, runtime.vertex_app_name);
  } else {
    sessionStorage.removeItem(VERTEX_APP_NAME_KEY);
  }
  if (runtime?.vertex_user_id) {
    sessionStorage.setItem(USER_ID_KEY, runtime.vertex_user_id);
  } else {
    sessionStorage.removeItem(USER_ID_KEY);
  }
}

export function setUserEmail(email?: string | null): void {
  if (email) {
    sessionStorage.setItem(USER_EMAIL_KEY, email);
  } else {
    sessionStorage.removeItem(USER_EMAIL_KEY);
  }
}

export function clearCurrentSessionRuntime(): void {
  const hadRuntime =
    Boolean(sessionStorage.getItem(VERTEX_SESSION_ID_KEY)) ||
    Boolean(sessionStorage.getItem(VERTEX_APP_NAME_KEY)) ||
    Boolean(sessionStorage.getItem(USER_ID_KEY)) ||
    Boolean(sessionStorage.getItem(USER_EMAIL_KEY));
  sessionStorage.removeItem(VERTEX_SESSION_ID_KEY);
  sessionStorage.removeItem(VERTEX_APP_NAME_KEY);
  sessionStorage.removeItem(USER_ID_KEY);
  sessionStorage.removeItem(USER_EMAIL_KEY);
  if (hadRuntime) {
    emitSessionChanged();
  }
}

export function getCurrentSessionRuntime() {
  return {
    appSessionId: getCurrentAppSessionId(),
    sessionId: sessionStorage.getItem(VERTEX_SESSION_ID_KEY),
    appName: sessionStorage.getItem(VERTEX_APP_NAME_KEY),
    userId: sessionStorage.getItem(USER_ID_KEY),
    userEmail: sessionStorage.getItem(USER_EMAIL_KEY),
  };
}
