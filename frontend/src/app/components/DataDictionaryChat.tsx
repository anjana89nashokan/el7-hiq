import { useState, useRef, useEffect, type FC, type KeyboardEvent } from 'react';
import { Send, Loader2, MessageSquare, Sparkles } from 'lucide-react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import axiosInstance from '../utils/axios-interceptor';

interface Message {
  id: string;
  text: string;
  role: 'user' | 'assistant';
  timestamp: Date;
}

interface DataDictionaryChatProps {
  readonly onDataDictionaryUpdate?: (updatedDD: any) => void;
}

function getStoredSession() {
  const sessionId = sessionStorage.getItem('session_id');
  const appName = sessionStorage.getItem('app_name');
  const userId = sessionStorage.getItem('user_id');
  return { sessionId, appName, userId };
}

const DataDictionaryChat: FC<DataDictionaryChatProps> = ({
  onDataDictionaryUpdate
}) => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const processSSELine = (line: string, onDataDictionaryUpdate?: (updatedDD: any) => void) => {
    if (!line.startsWith('data: ')) return { accumulatedText: '', finalResult: null };
    
    try {
      const data = JSON.parse(line.slice(6));
      
      if (data.phase === 'complete' && data.result) {
        const result = data.result;
        const accumulatedText = result?.text_response || 'Data dictionary updated successfully!';
        
        if (onDataDictionaryUpdate && result) {
          onDataDictionaryUpdate(result);
        }
        
        return { accumulatedText, finalResult: result };
      }
    } catch (e) {
      console.error('Error parsing SSE line:', e, 'Line:', line);
    }
    
    return { accumulatedText: '', finalResult: null };
  };

  const readSSEStream = async (reader: ReadableStreamDefaultReader, onDataDictionaryUpdate?: (updatedDD: any) => void) => {
    const decoder = new TextDecoder();
    let accumulatedText = '';
    let finalResult = null;
    
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      
      const chunk = decoder.decode(value);
      const lines = chunk.split('\n');
      
      for (const line of lines) {
        const result = processSSELine(line, onDataDictionaryUpdate);
        if (result.accumulatedText) accumulatedText = result.accumulatedText;
        if (result.finalResult) finalResult = result.finalResult;
      }
    }
    
    return { accumulatedText, finalResult };
  };

  const createRequestData = (input: string) => {
    const session = getStoredSession();
    const requestObj = {
      appName: session.appName,
      sessionId: session.sessionId,
      userId: session.userId,
      newMessage: {
        parts: [{ text: input }],
        role: 'user'
      },
      streaming: true,
      stateDelta: {}
    };
    return new URLSearchParams({ request: JSON.stringify(requestObj) });
  };

  const handleSend = async () => {
    if (!input.trim() || isLoading) return;

    const userMessage: Message = {
      id: `user-${Date.now()}-${Math.random()}`,
      text: input,
      role: 'user',
      timestamp: new Date()
    };

    setMessages((prev) => [...prev, userMessage]);
    const currentInput = input;
    setInput('');
    setIsLoading(true);

    try {
      const formData = createRequestData(currentInput);
      let accumulatedText = '';
      let finalResult = null;

      await fetch(`${axiosInstance.defaults.baseURL}/messages-strm/send-large-data-dict`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded'
        },
        body: formData.toString()
      }).then(async (response) => {
        if (!response.ok) {
          const errorData = await response.json().catch(() => ({}));
          const errorMessage = errorData.detail || `HTTP error! status: ${response.status} ${response.statusText}`;
          throw new Error(errorMessage);
        }
        const reader = response.body?.getReader();
        if (reader) {
          const result = await readSSEStream(reader, onDataDictionaryUpdate);
          accumulatedText = result.accumulatedText;
          finalResult = result.finalResult;
        }
      });

      const assistantMessage: Message = {
        id: `assistant-${Date.now()}-${Math.random()}`,
        text: accumulatedText || 'Update complete!',
        role: 'assistant',
        timestamp: new Date()
      };

      setMessages((prev) => [...prev, assistantMessage]);

      if (finalResult && onDataDictionaryUpdate) {
        onDataDictionaryUpdate(finalResult);
      }
    } catch (error) {
      console.error('Error sending message:', error);
      const errorText = error instanceof Error ? error.message : 'Sorry, there was an error processing your request. Please try again.';
      const errorMessage: Message = {
        id: `error-${Date.now()}-${Math.random()}`,
        text: errorText,
        role: 'assistant',
        timestamp: new Date()
      };
      setMessages((prev) => [...prev, errorMessage]);
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
    "Change description of 'field_name' to 'new description'",
    "Update business name for 'column_name'",
    "Mark 'id_field' as primary key",
    "Fix descriptions that say 'N/A'"
  ];

  return (
    <div className="mt-6 border border-gray-200 rounded-lg bg-white shadow-sm">
      {/* Header */}
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
          <h3 className="font-semibold text-gray-800">Modify Data Dictionary</h3>
          <span className="text-xs text-gray-500 bg-white px-2 py-0.5 rounded-full">AI Assistant</span>
        </div>
        <div className="text-gray-500">
          <MessageSquare className={`w-5 h-5 transition-transform ${isExpanded ? 'rotate-180' : ''}`} />
        </div>
      </button>

      {/* Chat Content - Expandable */}
      {isExpanded && (
        <div className="p-4 space-y-4">
          {/* Suggested Prompts - Show when no messages */}
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

          {/* Messages */}
          {messages.length > 0 && (
            <div className="space-y-3 max-h-96 overflow-y-auto">
              {messages.map((msg) => (
                <div
                  key={msg.id}
                  className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
                >
                  <div
                    className={`max-w-[80%] rounded-lg px-4 py-2 ${
                      msg.role === 'user'
                        ? 'bg-brand-primary text-white'
                        : 'bg-gray-100 text-gray-800 border border-gray-200'
                    }`}
                  >
                    {msg.role === 'assistant' ? (
                      <div className="prose prose-sm max-w-none">
                        <Markdown remarkPlugins={[remarkGfm]}>
                          {msg.text}
                        </Markdown>
                      </div>
                    ) : (
                      <p className="text-sm whitespace-pre-wrap">{msg.text}</p>
                    )}
                    <p className="text-xs opacity-70 mt-1">
                      {msg.timestamp.toLocaleTimeString()}
                    </p>
                  </div>
                </div>
              ))}
              <div ref={messagesEndRef} />
            </div>
          )}

          {/* Input Area */}
          <div className="flex gap-2">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Describe what you want to change... (e.g., 'Change description of livongo_id to...')"
              disabled={isLoading}
              rows={2}
              className="flex-1 px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-primary resize-none text-sm disabled:bg-gray-100"
            />
            <button
              onClick={handleSend}
              disabled={isLoading || !input.trim()}
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

          {/* Helper Text */}
          <p className="text-xs text-gray-500 text-center">
            Press <kbd className="px-1 py-0.5 bg-gray-100 border border-gray-300 rounded text-xs">Enter</kbd> to send •{' '}
            <kbd className="px-1 py-0.5 bg-gray-100 border border-gray-300 rounded text-xs ml-1">Shift + Enter</kbd> for new line
          </p>
        </div>
      )}
    </div>
  );
};

export default DataDictionaryChat;
