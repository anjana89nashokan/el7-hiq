import { useState, useRef, useEffect, type FC, type KeyboardEvent } from 'react';
import { Send, Loader2, MessageSquare, Sparkles } from 'lucide-react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  sendSimilarityHITLMessage,
  sendStreamingSimilarityChatHITLMessage,
} from '../end-points/chatApi';

interface Message {
  id: string;
  text: string;
  role: 'user' | 'assistant';
  timestamp: Date;
}

interface SimilarityChatProps {
  readonly onSimilarityUpdate?: (updatedResponse: any) => void;
  readonly useStreamingEndpoint?: boolean;
  readonly disabled?: boolean;
}

function getStoredSession() {
  return {
    sessionId: sessionStorage.getItem('session_id'),
    appName: sessionStorage.getItem('app_name'),
    userId: sessionStorage.getItem('user_id'),
  };
}

const SimilarityChat: FC<SimilarityChatProps> = ({
  onSimilarityUpdate,
  useStreamingEndpoint = false,
  disabled = false,
}) => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSend = async () => {
    if (!input.trim() || isLoading || disabled) return;

    const userMessage: Message = {
      id: `user-${Date.now()}`,
      text: input,
      role: 'user',
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);
    const currentInput = input;
    setInput('');
    setIsLoading(true);

    try {
      const { sessionId, appName, userId } = getStoredSession();

      const hitlRequest = {
        user_id: userId ?? '',
        session_id: sessionId ?? '',
        app_name: appName ?? '',
        user_message: currentInput,
      };

      const result = useStreamingEndpoint
        ? await sendStreamingSimilarityChatHITLMessage(hitlRequest)
        : await sendSimilarityHITLMessage(hitlRequest);

      const mode = result?.mode;
      let assistantText = '';

      if (mode === 'QUESTION') {
        assistantText = result?.text_response ?? 'No answer returned.';
      } else if (mode === 'UPDATE') {
        assistantText =
          'Proposed changes are ready. Use Apply changes to update the similarity results.';
      } else if (mode === 'APPLY_CHANGES') {
        assistantText = 'Changes applied successfully.';
        if (onSimilarityUpdate && result) {
          onSimilarityUpdate(result);
        }
      } else {
        assistantText = result?.text_response ?? JSON.stringify(result);
      }

      setMessages((prev) => [
        ...prev,
        {
          id: `assistant-${Date.now()}`,
          text: assistantText,
          role: 'assistant',
          timestamp: new Date(),
        },
      ]);
    } catch (error) {
      const errorText =
        error instanceof Error ? error.message : 'An error occurred. Please try again.';
      setMessages((prev) => [
        ...prev,
        {
          id: `error-${Date.now()}`,
          text: errorText,
          role: 'assistant',
          timestamp: new Date(),
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const suggestedPrompts = [
    "What is the highest confidence match?",
    "Which column has the best data overlap?",
    "Change confidence of rank 1 match to HIGH",
    "Update match reasoning for the top result",
  ];

  return (
    <div className="mt-4 border border-gray-200 rounded-lg bg-white shadow-sm">
      <button
        className="flex items-center justify-between p-4 border-b border-gray-200 bg-gradient-to-r from-brand-surface to-teal-50 rounded-t-lg cursor-pointer hover:from-teal-100 hover:to-teal-100 transition-colors w-full text-left"
        onClick={() => setIsExpanded(!isExpanded)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setIsExpanded(!isExpanded);
          }
        }}
      >
        <div className="flex items-center gap-2">
          <Sparkles className="w-5 h-5 text-font-blue" />
          <h3 className="font-semibold text-gray-800">Modify Similarity Results</h3>
          <span className="text-xs text-gray-500 bg-white px-2 py-0.5 rounded-full">AI Assistant</span>
        </div>
        <MessageSquare className={`w-5 h-5 text-gray-500 transition-transform ${isExpanded ? 'rotate-180' : ''}`} />
      </button>

      {isExpanded && (
        <div className="p-4 space-y-4">
          {messages.length === 0 && (
            <div className="space-y-2">
              <p className="text-sm text-gray-600 mb-3">Try asking:</p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {suggestedPrompts.map((prompt) => (
                  <button
                    key={prompt}
                    onClick={() => setInput(prompt)}
                    className="text-left text-xs bg-gray-50 hover:bg-gray-100 border border-gray-200 rounded-lg px-3 py-2 transition-colors"
                  >
                    <span className="text-font-blue">💡</span> {prompt}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.length > 0 && (
            <div className="space-y-3 max-h-80 overflow-y-auto">
              {messages.map((msg) => (
                <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  <div
                    className={`max-w-[80%] rounded-lg px-4 py-2 ${
                      msg.role === 'user'
                        ? 'bg-brand-primary text-white'
                        : 'bg-gray-100 text-gray-800 border border-gray-200'
                    }`}
                  >
                    {msg.role === 'assistant' ? (
                      <div className="prose prose-sm max-w-none">
                        <Markdown remarkPlugins={[remarkGfm]}>{msg.text}</Markdown>
                      </div>
                    ) : (
                      <p className="text-sm whitespace-pre-wrap">{msg.text}</p>
                    )}
                    <p className="text-xs opacity-70 mt-1">{msg.timestamp.toLocaleTimeString()}</p>
                  </div>
                </div>
              ))}
              <div ref={messagesEndRef} />
            </div>
          )}

          <div className="flex gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask about results or request a change... (e.g., 'Change confidence of rank 1 to HIGH')"
              disabled={isLoading || disabled}
              rows={2}
              className="flex-1 px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-primary resize-none text-sm disabled:bg-gray-100"
            />
            <button
              onClick={handleSend}
              disabled={isLoading || disabled || !input.trim()}
              className="px-4 py-2 bg-brand-primary text-white rounded-lg hover:bg-brand-primary-hover disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
            >
              {isLoading ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span className="text-sm">Processing...</span>
                </>
              ) : (
                <>
                  <Send className="w-4 h-4" />
                  <span className="text-sm">Send</span>
                </>
              )}
            </button>
          </div>

          <p className="text-xs text-gray-500 text-center">
            Press <kbd className="px-1 py-0.5 bg-gray-100 border border-gray-300 rounded text-xs">Enter</kbd> to send •{' '}
            <kbd className="px-1 py-0.5 bg-gray-100 border border-gray-300 rounded text-xs ml-1">Shift + Enter</kbd> for new line
          </p>
        </div>
      )}
    </div>
  );
};

export default SimilarityChat;
