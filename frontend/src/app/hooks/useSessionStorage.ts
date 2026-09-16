export function useSessionStorage() {
  const getStoredSession = () => {
    const sessionId = sessionStorage.getItem("session_id");
    const appName = sessionStorage.getItem("app_name");
    const userId = sessionStorage.getItem("user_id");
    return { sessionId, appName, userId };
  };

  return { getStoredSession };
}
