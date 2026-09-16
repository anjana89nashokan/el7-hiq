import { Outlet, useLocation } from "react-router-dom";
import { useState, useEffect, type KeyboardEvent } from "react";
import Header from "./Header";
import Sidebar from "./Sidebar";
import ChatSidebar from "./ChatSidebar";
import { useChat } from "../contexts/ChatContext";
import { sendProfilingChatHITLMessage } from "../end-points/chatApi";
import { ensureSessionRuntime } from "../end-points/appSessionsApi";
import { sttmRelativePath } from "../utils/sttmRoutes";

const SIDEBAR_COLLAPSED_KEY = "sidebar_collapsed";
const NO_SIDEBAR_ROUTES = ["/extract", "/", "/dashboard"];

export default function Layout() {
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const { isChatOpen, setIsChatOpen } = useChat();
  const location = useLocation();
  const relativePath = sttmRelativePath(location.pathname);
  const [inputMessage, setInputMessage] = useState("");
  const [messages, setMessages] = useState<{ id: string; text: string; isBot: boolean; timestamp: Date }[]>([]);
  const [isChatLoading, setIsChatLoading] = useState(false);

  const isProfilingRoute = relativePath === "/profiling";
  const hideSidebar = NO_SIDEBAR_ROUTES.includes(relativePath);

  const handleSend = async () => {
    const text = inputMessage.trim();
    if (!text || isChatLoading) return;

    const thinkingId = `${Date.now()}-bot`;
    setMessages((prev) => [
      ...prev,
      { id: `${Date.now()}-user`, text, isBot: false, timestamp: new Date() },
      { id: thinkingId, text: "Thinking…", isBot: true, timestamp: new Date() },
    ]);
    setInputMessage("");
    setIsChatLoading(true);

    const replyWith = (answer: string) =>
      setMessages((prev) => prev.map((m) => (m.id === thinkingId ? { ...m, text: answer } : m)));

    try {
      const rt = await ensureSessionRuntime();
      if (!rt.sessionId || !rt.appName) {
        replyWith("Open or create a session and run a profiling first, then I can answer questions about your data.");
        return;
      }
      const res = await sendProfilingChatHITLMessage({
        user_id: rt.userId || "local-user",
        session_id: rt.sessionId,
        app_name: rt.appName,
        user_message: text,
      });
      replyWith(res?.text_response || "I couldn't find an answer — try running a profiling for this session first.");
    } catch (error) {
      const message =
        error instanceof Error && error.message
          ? error.message
          : "Sorry, something went wrong. Please try again.";
      replyWith(message);
    } finally {
      setIsChatLoading(false);
    }
  };

  const handleKeyPress = (e: KeyboardEvent) => {
    if (e.key === "Enter") void handleSend();
  };

  useEffect(() => {
    const storedValue = sessionStorage.getItem(SIDEBAR_COLLAPSED_KEY);
    setIsSidebarCollapsed(storedValue === "true");
  }, []);

  const handleToggleSidebar = () => {
    setIsSidebarCollapsed((prev) => {
      const next = !prev;
      sessionStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(next));
      return next;
    });
  };

  return (
    <div className="h-screen flex flex-col">
      {/* Top Header */}
      <Header />

      {/* Sidebar + Main */}
      <div className="flex flex-1 items-stretch overflow-hidden">
        {!hideSidebar && (
          <Sidebar isCollapsed={isSidebarCollapsed} onToggleCollapse={handleToggleSidebar} />
        )}
        <main className="flex-1 min-w-0 px-6 py-4 bg-white overflow-y-auto">
          <Outlet />
        </main>
      </div>

      {!isProfilingRoute && (
        <ChatSidebar
          isOpen={isChatOpen}
          onClose={() => setIsChatOpen(false)}
          messages={messages}
          inputMessage={inputMessage}
          onInputChange={setInputMessage}
          onSend={handleSend}
          onKeyPress={handleKeyPress}
        />
      )}
    </div>
  );
}
