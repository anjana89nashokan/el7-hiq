import axiosInstance from "../utils/axios-interceptor";
import { MESSAGES } from "../config/messages";

interface SessionData {
  appName: string | null;
  sessionId: string | null;
  userId: string | null;
}

type ConversationItem = {
  actions?: {
    stateDelta?: {
      query_execution_output?: string;
    };
  };
};

export function isProfilingLlmError(textResponse: unknown): boolean {
  if (typeof textResponse !== "string" || !textResponse.trim()) return false;
  const lower = textResponse.toLowerCase();
  if (lower.startsWith("**error:**")) return true;
  if (lower.includes("no api key") || lower.includes("no gemini api key") || lower.includes("no llm api key")) return true;
  if (lower.includes("api key not valid") || lower.includes("api_key_invalid")) return true;
  if (lower.includes("invalid_argument") && lower.includes("api key")) return true;
  if (lower.includes("tool_call_ids did not have response")) return true;
  if (lower.includes("must be followed by tool messages")) return true;
  if (lower.includes("something went wrong")) return true;
  if (lower.includes("could not set session state")) return true;
  return false;
}

export function hasProfilingToolResult(item: unknown): boolean {
  if (!item || typeof item !== "object") return false;
  const tool = (item as { tool_response?: Record<string, unknown> }).tool_response;
  if (!tool || typeof tool !== "object") return false;
  const results =
    (tool.intelligent_profiling_tool_response as { result?: unknown[] } | undefined)
      ?.result ||
    tool.result ||
    tool.all_tables;
  return Array.isArray(results) ? results.length > 0 : Boolean(results);
}

export async function fetchProfilingDashboardPayload(session: SessionData): Promise<unknown | null> {
  if (!session.sessionId || !session.userId || !session.appName) {
    return null;
  }

  try {
    const response = await axiosInstance.get("/messages/profiling-dashboard-data", {
      params: {
        sessionId: session.sessionId,
        userId: session.userId,
        appName: session.appName,
      },
    });
    if (response.data?.status === "success") {
      return response.data.tool_response ?? null;
    }
  } catch (error) {
    console.error("Failed to fetch profiling dashboard payload:", error);
  }
  return null;
}

function normalizeProfilingError(textResponse: string): string {
  return textResponse.replace(/^Something went wrong, Try again\s*/i, "").trim() || textResponse;
}

export const getQueryExecutionOutput = (
  data: ConversationItem[],
): string | null => {
  for (const item of data) {
    const output = item.actions?.stateDelta?.query_execution_output;
    if (output) {
      return output;
    }
  }
  return null;
};

export const sendInitialMessage = async (
  fileNames: string[],
  sessionData: SessionData,
) => {
  const initialText = `[Data Profiling]\nDo the profiling for the following files: ${fileNames.join(", ")}`;

  const response = await axiosInstance.post("/messages/send", {
    appName: sessionData.appName,
    sessionId: sessionData.sessionId,
    userId: sessionData.userId,
    newMessage: {
      parts: [{ text: initialText }],
      role: "user",
    },
    streaming: false,
    stateDelta: {},
  });

  let botResponseText = MESSAGES.DEFAULTS.DEFAULT_BOT_RESPONSE;

  if (response.status === 200) {
    const data = response.data;

    if (data && Array.isArray(data) && data.length > 0) {
      const first = data[0];
      if (isProfilingLlmError(first?.text_response) && !hasProfilingToolResult(first)) {
        throw new Error(normalizeProfilingError(String(first.text_response)));
      }
    }

    if (data && Array.isArray(data)) {
      const modelResponse = data[0].text_response;
      if (modelResponse?.content?.parts?.[0]?.text) {
        botResponseText = modelResponse.content.parts[0].text;
      }
      return { data, botResponseText, initialText };
    } else if (typeof data === "object") {
      const _data = [data];
      const modelResponse = _data.find(
        (item) =>
          item.content?.role === "model" && item.content?.parts?.[0]?.text,
      );
      if (modelResponse?.content?.parts?.[0]?.text) {
        botResponseText = modelResponse.content.parts[0].text;
      }
      return { data: _data, botResponseText, initialText };
    }
  }

  return { data: [], botResponseText, initialText };
};

export const sendQAMessage = async (
  message: string,
  sessionData: SessionData,
) => {
  const response = await axiosInstance.post("/messages/qa", {
    appName: sessionData.appName,
    sessionId: sessionData.sessionId,
    userId: sessionData.userId,
    newMessage: message,
    streaming: false,
    stateDelta: {},
  });

  if (response.status === 200) {
    const data = response.data;
    let botResponseText =
      "I understand your question. Let me help you with that.";

    if (data && Array.isArray(data)) {
      const queryExecutionOutput = getQueryExecutionOutput(data);
      if (queryExecutionOutput) {
        botResponseText = queryExecutionOutput;
      }
    }

    return botResponseText;
  }

  throw new Error("Failed to send QA message");
};

export const checkSimilarity = async (
  dartTableEntries: { dartTable: string; column: string }[],
  sourceTables: string[],
  sessionData: SessionData,
  dynamicFilters: any[],
  databaseName: string,
) => {
  const dartReferences = dartTableEntries
    .filter((entry) => entry.dartTable && entry.column)
    .map(
      (entry) => `- Table: ${entry.dartTable}\n- Columns: ["${entry.column}"]`,
    )
    .join("\n");
  console.log("Calling the API");

  const message = `Match columns with Reference tables using these parameters:\n\nDART References:\n${dartReferences}\n\nSource Tables:\n- ${sourceTables.join("\n- ")}`;

  const response = await axiosInstance.post(
    "/messages/similarity-check",
    {
      appName: sessionData.appName,
      sessionId: sessionData.sessionId,
      userId: sessionData.userId,
      newMessage: {
        parts: [{ text: message }],
        role: "user",
      },
      streaming: false,
      stateDelta: {},
      dart_database_name: databaseName, // HARDCODED FOR TESTING
      filters: dynamicFilters, // HARDCODED FOR TESTING
    },
    {
      headers: { "Content-Type": "application/json" },
    },
  );

  return JSON.stringify(response.data, null, 2);
};
