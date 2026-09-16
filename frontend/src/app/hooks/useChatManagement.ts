import { useCallback } from "react";
import { ensureSessionRuntime, startProfilingRun } from "../end-points/appSessionsApi";
import { sendInitialMessage, sendQAMessage } from "../end-points/profilingResultApi";
import { getCurrentAppSessionId, setSessionRuntime } from "../utils/appSessionStorage";
import type { ChatMessage } from "../state/reducers/profilingResultReducers";

interface UseChatManagementProps {
  profilingData: any;
  apiInProgress: boolean;
  initialMessageData: any[];
  messages: ChatMessage[];
  inputMessage: string;
  setApiInProgress: (value: boolean) => void;
  setLoadingStates: (fn: (prev: any) => any) => void;
  setInitialMessageData: (data: any[]) => void;
  setMessages: (messages: ChatMessage[] | ((prev: ChatMessage[]) => ChatMessage[])) => void;
  setQaMessages: (fn: (prev: ChatMessage[]) => ChatMessage[]) => void;
  setInputMessage: (value: string) => void;
  getStoredSession: () => any;
}

export const useChatManagement = ({
  profilingData,
  apiInProgress,
  initialMessageData,
  messages,
  inputMessage,
  setApiInProgress,
  setLoadingStates,
  setInitialMessageData,
  setMessages,
  setQaMessages,
  setInputMessage,
  getStoredSession,
}: UseChatManagementProps) => {
  const initializeChat = useCallback(async () => {
    if (!profilingData || apiInProgress || initialMessageData.length > 0 || messages.length > 0) {
      return;
    }

    setApiInProgress(true);
    const fileNames = profilingData.successful_uploads
      .filter((f: any) => f != null)
      .flatMap((f: any) => {
        const created = f.access_info?.tables_created;
        if (created?.length) {
          // Multi-sheet: each entry's table_name is the sanitized BQ table name
          return created.map((t: any) => t.table_name as string);
        }
        // Single file: use table_reference.table_name (always sanitized BQ name)
        return [f.access_info?.table_reference?.table_name ?? f.table_name];
      });
    setLoadingStates((prev) => ({ ...prev, 1: true }));

    try {
      let sessionData = await ensureSessionRuntime();
      const appSessionId = getCurrentAppSessionId();
      if (appSessionId) {
        // Fresh ADK session avoids poisoned OpenAI tool-call history after a failed run.
        const runtime = await startProfilingRun(appSessionId);
        setSessionRuntime(runtime);
        sessionData = {
          sessionId: runtime.vertex_session_id,
          appName: runtime.vertex_app_name,
          userId: runtime.vertex_user_id || sessionData.userId,
        };
      }
      if (!sessionData.sessionId || !sessionData.appName) {
        throw new Error(
          "Profiling session is not ready yet. Wait a moment and click Retry, or re-upload your files.",
        );
      }

      const { data, botResponseText, initialText } = await sendInitialMessage(
        fileNames,
        sessionData,
      );
      const timestamp = new Date();

      setInitialMessageData(data);
      setMessages([
        {
          id: "init-user",
          text: initialText,
          isBot: false,
          timestamp,
        },
        {
          id: "init-bot",
          text: botResponseText,
          isBot: true,
          timestamp,
        },
      ]);
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : "Unknown error occurred";
      setInitialMessageData([{
        text_response: `**Error:** ${errorMessage}`,
        tool_response: {}
      }]);
    } finally {
      setLoadingStates((prev) => ({ ...prev, 1: false }));
      setApiInProgress(false);
    }
  }, [profilingData, apiInProgress, initialMessageData.length, messages.length, getStoredSession, setApiInProgress, setLoadingStates, setInitialMessageData, setMessages]);

  const handleSendQAMessage = useCallback(async () => {
    if (!inputMessage.trim() || apiInProgress) return;

    setApiInProgress(true);
    const timestamp = Date.now();
    setQaMessages((prev) => [
      ...prev,
      {
        id: timestamp.toString(),
        text: inputMessage,
        isBot: false,
        timestamp: new Date(),
      },
    ]);

    const currentMessage = inputMessage;
    setInputMessage("");

    try {
      const botResponseText = await sendQAMessage(currentMessage, getStoredSession());
      setQaMessages((prev) => [
        ...prev,
        {
          id: (timestamp + 1).toString(),
          text: botResponseText,
          isBot: true,
          timestamp: new Date(),
        },
      ]);
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : "Unknown error occurred";
      setQaMessages((prev) => [
        ...prev,
        {
          id: (timestamp + 1).toString(),
          text: `Sorry, I encountered an error while processing your message: ${errorMessage}. Please try again.`,
          isBot: true,
          timestamp: new Date(),
        },
      ]);
    } finally {
      setApiInProgress(false);
    }
  }, [inputMessage, apiInProgress, getStoredSession, setApiInProgress, setQaMessages, setInputMessage]);

  const handleKeyPress = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSendQAMessage();
    }
  }, [handleSendQAMessage]);

  return {
    initializeChat,
    handleSendQAMessage,
    handleKeyPress,
  };
};
