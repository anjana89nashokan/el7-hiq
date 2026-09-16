import { type KeyboardEvent } from 'react';
import { MessageCircle, X, Send } from 'lucide-react';

import { BRANDING } from '../config/branding';

interface ChatMessage {
  id: string;
  text: string;
  isBot: boolean;
  timestamp: Date;
}

interface ChatSidebarProps {
  readonly isOpen: boolean;
  readonly onClose: () => void;
  readonly messages: ChatMessage[];
  readonly inputMessage: string;
  readonly onInputChange: (value: string) => void;
  readonly onSend: () => void;
  readonly onKeyPress: (e: KeyboardEvent) => void;
}

export default function ChatSidebar({
  isOpen,
  onClose,
  messages,
  inputMessage,
  onInputChange,
  onSend,
  onKeyPress
}: ChatSidebarProps) {
  if (!isOpen) return null;

  return (
    <div className="fixed top-0 right-0 h-full w-[400px] sm:w-[480px] bg-white shadow-xl z-50 flex flex-col border-l">
      <div className="bg-brand-darkblue text-white p-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <MessageCircle size={18} />
            <h3 className="font-semibold">{BRANDING.ASSISTANT_NAME}</h3>
          </div>
          <button
            onClick={onClose}
            className="hover:bg-white/15 p-1 rounded transition-colors"
          >
            <X size={16} />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.length === 0 ? (
          <div className="flex justify-center items-center h-full text-gray-400">
            <div className="text-center">
              <MessageCircle size={32} className="mx-auto mb-2 opacity-50" />
              <p>Start a conversation...</p>
              <p className="text-xs mt-1">Ask questions about your data profiling results</p>
            </div>
          </div>
        ) : (
          messages.map((message) => (
            <div
              key={message.id}
              className={`flex ${message.isBot ? 'justify-start' : 'justify-end'}`}
            >
              <div
                className={`max-w-[85%] p-3 rounded-lg shadow-sm ${
                  message.isBot ? 'bg-gray-100 text-gray-800' : 'bg-brand-primary text-white'
                }`}
              >
                <div className="text-sm leading-relaxed whitespace-pre-wrap">
                  {message.text || 'No message content'}
                </div>
                {message.isBot && !message.text && (
                  <div className="text-xs text-gray-500 italic">
                    Error: No response received
                  </div>
                )}
              </div>
            </div>
          ))
        )}
      </div>

      <div className="border-t p-3 flex gap-2">
        <label htmlFor="chat-input" className="sr-only">Type a message</label>
        <input
          id="chat-input"
          type="text"
          value={inputMessage}
          onChange={(e) => onInputChange(e.target.value)}
          onKeyDown={onKeyPress}
          placeholder="Type a message..."
          className="flex-1 border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-primary"
        />
        <button
          onClick={onSend}
          className="bg-brand-primary hover:bg-brand-primary-hover text-white px-4 rounded-lg flex items-center justify-center"
          aria-label="Send message"
        >
          <Send size={18} />
        </button>
      </div>
    </div>
  );
}
