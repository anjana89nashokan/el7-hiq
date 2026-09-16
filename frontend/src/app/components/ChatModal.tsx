import React, { useState } from "react";
import { X, Send, Loader2 } from "lucide-react";
import { sendHumanInLoopMessage, sendHumanInLoopProfilingMessage } from "../end-points/chatApi";
import { getAgentType } from "../state/reducers/chatReducer";

interface Message {
  text: string;
  role: "user" | "assistant";
  timestamp: Date;
  toolResponse?: any;
}

interface ChatModalProps {
  isOpen: boolean;
  onClose: () => void;
  stepTitle: string;
  stepNumber?: number;
  onUseResponse?: (response: any, isModified?: boolean) => void;
}

function getStoredSession() {
  const sessionId = sessionStorage.getItem("session_id") || "";
  const appName = sessionStorage.getItem("app_name") || "";
  const userId = sessionStorage.getItem("user_id") || "";
  return { sessionId, appName, userId };
}

const ChatModal: React.FC<ChatModalProps> = ({
  isOpen,
  onClose,
  stepTitle,
  stepNumber,
  onUseResponse
}) => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  const handleUseResponse = (msg: Message) => {
    if (onUseResponse) {
      let finalUIValues;
      
      // Determine which response type to use based on step number
      // Steps that display text_response: 2, 5
      // Steps that display tool_response: 3, 6
      if (stepNumber === 2 || stepNumber === 5) {
        // For steps showing text_response in UI
        finalUIValues = {
          text_response: msg.text,
          tool_response: msg.toolResponse
        };
      } else if (stepNumber === 3 || stepNumber === 6) {
        // For steps showing tool_response in UI
        finalUIValues = {
          text_response: msg.text,
          tool_response: msg.toolResponse
        };
      } else {
        // Default: include both
        finalUIValues = {
          text_response: msg.text,
          tool_response: msg.toolResponse
        };
      }
      
      onUseResponse(finalUIValues, true);
    }
    onClose();
  };

  if (!isOpen) return null;

  const handleSend = async () => {
    if (!input.trim() || isLoading) return;

    // user message
    const userMessage: Message = {
      text: input,
      role: "user",
      timestamp: new Date()
    };

    setMessages(prev => [...prev, userMessage]);
    const currentInput = input;
    setInput("");
    setIsLoading(true);

    try {
      const { sessionId, appName, userId } = getStoredSession();
      const agentType = getAgentType(stepTitle);

      let rawResponse;
      
      if (stepNumber === 2 || stepNumber === 5) {
        rawResponse = await sendHumanInLoopProfilingMessage({
          user_id: userId,
          session_id: sessionId,
          app_name: appName,
          agent_type: agentType,
          user_message: `for ${stepTitle}: ${currentInput}`
        });
      } else if (stepNumber === 6) {
        rawResponse = await sendHumanInLoopMessage({
          user_id: userId,
          session_id: sessionId,
          app_name: appName,
          agent_type: agentType,
          user_message: `for ${stepTitle}: ${currentInput}`
        });
      } else {
        rawResponse = await sendHumanInLoopMessage({
          user_id: userId,
          session_id: sessionId,
          app_name: appName,
          agent_type: agentType,
          user_message: `${stepTitle}: ${currentInput}`
        });
      }

      // works for axios/fetch/custom API
      const apiResponse =
        rawResponse?.data ??
        rawResponse?.response ??
        rawResponse ??
        {};

      const textResponse =
        apiResponse.text_response ??
        apiResponse.message ??
        "No text response provided";

      const toolResponse =
        apiResponse.tool_response ??
        apiResponse.result ??
        null;

      const assistantMessage: Message = {
        text: textResponse,
        role: "assistant",
        timestamp: new Date(),
        toolResponse
      };

      setMessages(prev => [...prev, assistantMessage]);
    } catch (error) {
      console.error("Error sending message:", error);

      const errorMessage: Message = {
        text: "Sorry, there was an error processing your message. Please try again.",
        role: "assistant",
        timestamp: new Date()
      };

      setMessages(prev => [...prev, errorMessage]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4">
      <div className="flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-lg bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
          <div>
            <h3 className="text-base font-semibold text-brand-darkblue">Chat: {stepTitle}</h3>
            <p className="text-xs text-gray-500">Ask for assistance, then apply the updated response.</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-800"
            aria-label="Close chat"
          >
            <X size={20} />
          </button>
        </div>

        <div className="flex-1 space-y-3 overflow-y-auto p-4">
          {messages.length === 0 && (
            <div className="rounded-md border border-dashed border-gray-300 bg-gray-50 p-4 text-sm text-gray-600">
              Enter your question or request for assistance.
            </div>
          )}

          {messages.map((msg, i) => (
            <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[85%] rounded-lg px-4 py-3 text-sm ${
                msg.role === "user"
                  ? "bg-brand-darkblue text-white"
                  : "bg-gray-100 text-gray-800"
              }`}>
                <div className="whitespace-pre-wrap">{msg.text}</div>
                {msg.role === "assistant" && (
                  <button
                    type="button"
                    onClick={() => handleUseResponse(msg)}
                    className="mt-3 rounded-md bg-green-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-green-700"
                  >
                    Apply changes
                  </button>
                )}
              </div>
            </div>
          ))}

          {isLoading && (
            <div className="flex justify-start">
              <div className="rounded-lg bg-gray-100 px-4 py-3">
                <Loader2 size={18} className="animate-spin text-gray-600" />
              </div>
            </div>
          )}
        </div>

        <div className="border-t border-gray-200 p-4">
          <div className="flex items-end gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Describe your request..."
              disabled={isLoading}
              rows={3}
              className="min-h-[84px] flex-1 resize-none rounded-md border border-gray-300 px-3 py-2 text-sm outline-none transition-colors focus:border-brand-darkblue focus:ring-1 focus:ring-brand-darkblue disabled:bg-gray-50"
            />
            <button
              type="button"
              onClick={handleSend}
              disabled={isLoading || !input.trim()}
              className="flex h-10 w-10 items-center justify-center rounded-md bg-brand-darkblue text-white transition-colors hover:bg-brand-darkblue/80 disabled:cursor-not-allowed disabled:opacity-60"
              aria-label="Send message"
            >
              {isLoading ? <Loader2 size={18} className="animate-spin" /> : <Send size={18} />}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ChatModal;