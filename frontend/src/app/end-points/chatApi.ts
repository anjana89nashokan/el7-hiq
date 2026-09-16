import { apiFetch } from "../utils/apiFetch";
import { API_BASE_URL } from "../config/env";

const baseUrl = API_BASE_URL;

interface HumanInLoopRequest {
  user_id: string;
  session_id: string;
  app_name: string;
  agent_type: string;
  user_message: string;
}

type ProfilingHumanInLoopRequest = Omit<HumanInLoopRequest, 'agent_type'>;

const parseErrorMessage = async (response: Response): Promise<string> => {
  const errorData = await response.json().catch(() => ({}));
  return errorData.detail || `HTTP error! status: ${response.status} ${response.statusText}`;
};

export const sendHumanInLoopMessage = async (request: HumanInLoopRequest): Promise<any> => {
  const response = await apiFetch(`${baseUrl}/messages/messages/chat/human-in-the-loop`, {
    method: 'POST',
    headers: {
      'accept': 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response));
  }

  const data = await response.json();
  return data;
};

export const sendHumanInLoopProfilingMessage = async (request: HumanInLoopRequest): Promise<any> => {
  const response = await apiFetch(`${baseUrl}/messages/messages/chat/human-in-the-loop/profiling`, {
    method: 'POST',
    headers: {
      'accept': 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response));
  }

  const data = await response.json();
  return data;
};

export const sendProfilingHumanInLoopMessage = async (request: ProfilingHumanInLoopRequest): Promise<any> => {
  const response = await apiFetch(`${baseUrl}/messages/messages/chat/human-in-the-loop/profiling-chat`, {
    method: 'POST',
    headers: {
      'accept': 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response));
  }

  return response.json();
};

export const sendProfilingChatHITLMessage = sendProfilingHumanInLoopMessage;

export const sendStreamingProfilingChatHITLMessage = async (request: ProfilingHumanInLoopRequest): Promise<any> => {
  const response = await apiFetch(`${baseUrl}/messages-strm/messages/chat/human-in-the-loop/profiling-chat-streaming`, {
    method: 'POST',
    headers: {
      'accept': 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response));
  }

  return response.json();
};

export const sendSimilarityHITLMessage = async (request: {
  user_id: string;
  session_id: string;
  app_name: string;
  user_message: string;
  apply_changes?: boolean;
  text_response?: string;
  tool_response?: Record<string, any>;
}): Promise<any> => {
  const response = await apiFetch(`${baseUrl}/messages/messages/chat/human-in-the-loop/similarity-chat`, {
    method: 'POST',
    headers: {
      'accept': 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response));
  }

  return response.json();
};

export type SimilarityChatHITLRequest = {
  user_id: string;
  session_id: string;
  app_name: string;
  user_message: string;
  apply_changes?: boolean;
  text_response?: string;
  tool_response?: Record<string, unknown>;
};

export const sendStreamingSimilarityChatHITLMessage = async (
  request: SimilarityChatHITLRequest,
): Promise<any> => {
  const response = await apiFetch(
    `${baseUrl}/messages-strm/messages/chat/human-in-the-loop/similarity-check-streaming-chat`,
    {
      method: 'POST',
      headers: {
        accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(request),
    },
  );

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response));
  }

  return response.json();
};
