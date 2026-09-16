import React, { useEffect, useState, useRef, useCallback } from "react";

const LogViewer: React.FC = () => {
  const [logs, setLogs] = useState<string[]>([]);
  const [showLogs, setShowLogs] = useState<boolean>(false);
  const [connectionStatus, setConnectionStatus] = useState<string>("Connecting...");
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wsUrl = "ws://localhost:8001/logs/ws/logs";

  const connectWebSocket = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return; // Already connected
    }

    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnectionStatus("Connected");
        setLogs((prev) => [...prev, "[Connected to log stream]"]);
      };

      ws.onmessage = (event) => {
        setLogs((prev) => [...prev, event.data]);
      };

      ws.onerror = (err) => {
        console.error("WebSocket error:", err);
        setConnectionStatus("Error");
        setLogs((prev) => [...prev, "[WebSocket error: check backend connection]"]);
      };

      ws.onclose = (event) => {
        setConnectionStatus("Disconnected");
        setLogs((prev) => [...prev, "[Connection closed]"]);
        
        // Attempt to reconnect after 3 seconds unless it was a clean close
        if (event.code !== 1000) {
          reconnectTimeoutRef.current = setTimeout(() => {
            console.log("Attempting to reconnect...");
            setConnectionStatus("Reconnecting...");
            connectWebSocket();
          }, 3000);
        }
      };
    } catch (error) {
      console.error("Failed to create WebSocket:", error);
      setConnectionStatus("Failed to connect");
    }
  }, [wsUrl]);

  useEffect(() => {
    connectWebSocket();

    return () => {
      // Cleanup on unmount
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (wsRef.current) {
        wsRef.current.close(1000); // Clean close
      }
    };
  }, [connectWebSocket]);

  // Auto scroll to bottom on new log
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  const clearLogs = () => {
    setLogs([]);
  };

  const manualReconnect = () => {
    if (wsRef.current) {
      wsRef.current.close(1000);
    }
    setConnectionStatus("Connecting...");
    connectWebSocket();
  };

  return (
    <div>
    
    <div className="flex items-center space-x-4 mb-2">
      <input type="checkbox" id="log-viewer-toggle" className="mx-4 w-4 h-4 check" defaultChecked onClick={() => setShowLogs(!showLogs)} />
    <label htmlFor="log-viewer-toggle" className="cursor-pointer">{showLogs ? "Hide logs" : "Show logs"}</label>
    </div>
    {
        showLogs && (

  <div className="bg-black text-green-400 font-mono rounded-lg shadow-md">
      {/* Header with connection status and controls */}
      <div className="flex justify-between items-center p-4 border-b border-green-800">
        <div className="flex items-center space-x-4">
          <span className="text-sm">
            Status: 
            <span className={`ml-2 ${
              connectionStatus === "Connected" ? "text-green-400" :
              connectionStatus === "Connecting..." || connectionStatus === "Reconnecting..." ? "text-yellow-400" :
              "text-red-400"
            }`}>
              {connectionStatus}
            </span>
          </span>
          <span className="text-sm">Logs: {logs.length}</span>
        </div>
        <div className="space-x-2">
          <button
            onClick={manualReconnect}
            className="px-3 py-1 text-xs bg-green-800 hover:bg-green-700 rounded"
            disabled={connectionStatus === "Connected"}
          >
            Reconnect
          </button>
          <button
            onClick={clearLogs}
            className="px-3 py-1 text-xs bg-red-800 hover:bg-red-700 rounded"
          >
            Clear
          </button>
        </div>
      </div>
      
      {/* Log content */}
      <div className="p-4 h-96 overflow-y-auto">
        {logs.map((log, idx) => (
          <div key={idx} className="whitespace-pre-wrap text-sm">
            {log}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
        )
    }
   
  
    </div>
  );
};

export default LogViewer;