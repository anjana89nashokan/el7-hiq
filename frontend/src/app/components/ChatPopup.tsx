import React, { useState } from "react";
import { X, Send, Loader2 } from "lucide-react";
import axiosInstance from "../utils/axios-interceptor";

interface Message {
  text: string | { message?: string };
  role: "user" | "assistant";
  textResponse?: string;
}

interface ChatPopupProps {
  isOpen: boolean;
  onClose: () => void;
  currentStep: string;
  onUseResponse?: (text: string) => void;
}

function getStoredSession() {
    const sessionId = sessionStorage.getItem("session_id");
    const appName = sessionStorage.getItem("app_name");
    const userId = sessionStorage.getItem("user_id");
    return { sessionId, appName, userId };
}

const ChatPopup: React.FC<ChatPopupProps> = ({ 
  isOpen, 
  onClose, 
  currentStep
}) => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  if (!isOpen) return null;

  const handleSend = async () => {
    if (!input.trim() || isLoading) return;

    const userMessage: Message = {
      text: input,
      role: "user"
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setIsLoading(true);

    try {
      const response = await axiosInstance.post('/messages/send', {
        appName: getStoredSession().appName,
        sessionId: getStoredSession().sessionId,
        userId: getStoredSession().userId,
        newMessage: {
          parts: [
            {
              text: `Question regarding [${currentStep}]: \n` + input
            }
          ],
          role: "user"
        },
        streaming: false,
        stateDelta: {}
      });

      const data = response.data;
      
      // Extract text_response from the first item in the array
      const textResponse = data[0]?.text_response || "No response received";

      const assistantMessage: Message = {
        text: textResponse,
        role: "assistant",
        textResponse: textResponse
      };

      setMessages((prev) => [...prev, assistantMessage]);
    } catch (error) {
      console.error("Error sending message:", error);
      const errorMessage: Message = {
        text: "Sorry, there was an error processing your message. Please try again.",
        role: "assistant"
      };
      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-40 flex items-center justify-center z-50">
      <div className="bg-white w-full max-w-xl h-[80vh] rounded-lg shadow-lg flex flex-col">
        {/* Header */}
        <div className="flex justify-between items-center p-3 border-b">
          <h2 className="font-semibold">Chat: {currentStep}</h2>
          <button onClick={onClose}>
            <X className="w-5 h-5 text-gray-600 hover:text-black" />
          </button>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 space-y-3 text-sm text-gray-800">
          {messages.length === 0 && (
            <p className="text-gray-400 text-center">No messages yet</p>
          )}
          {messages.map((msg, i) => (
            <div
              key={i}
              className={`flex flex-col ${msg.role === "user" ? "items-end" : "items-start"}`}
            >
              <div
                className={`px-3 py-2 rounded-lg max-w-[80%] ${
                  msg.role === "user"
                    ? "bg-teal-100 text-brand-darkblue"
                    : "bg-gray-100 text-gray-900"
                }`}
              >
                {typeof msg.text === 'object' && msg.text && 'message' in msg.text && msg.text.message ? msg.text.message : typeof msg.text === 'string' ? msg.text : ''}
              </div>
              {msg.role !== "user" && (
                <button
                  // onClick={() => onUseResponse(msg.textResponse || msg.text)}
                  className="mt-1 text-xs text-font-blue hover:text-font-blue font-medium px-2 py-1 rounded hover:bg-brand-surface"
                >
                  Use this
                </button>
              )}
            </div>
          ))}
          {isLoading && (
            <div className="flex items-start">
              <div className="bg-gray-100 px-3 py-2 rounded-lg">
                <Loader2 className="w-4 h-4 animate-spin text-gray-600" />
              </div>
            </div>
          )}
        </div>

        {/* Input */}
        <div className="p-3 border-t flex space-x-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type a message..."
            disabled={isLoading}
            className="flex-1 border rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary disabled:bg-gray-100 disabled:cursor-not-allowed"
          />
          <button
            onClick={handleSend}
            disabled={isLoading || !input.trim()}
            className="bg-brand-primary hover:bg-brand-primary-hover text-white px-3 py-2 rounded-md disabled:bg-gray-400 disabled:cursor-not-allowed"
          >
            {isLoading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Send className="w-4 h-4" />
            )}
          </button>
        </div>
      </div>
    </div>
  );
};

export default ChatPopup;