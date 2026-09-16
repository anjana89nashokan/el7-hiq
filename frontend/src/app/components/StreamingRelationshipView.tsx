/**
 * StreamingRelationshipView - Enhanced relationship analysis with SSE streaming
 * Provides real-time progress updates during relationship analysis
 */

import React, { useState, useEffect, useRef } from "react";
import { FileSearch, AlertCircle } from "lucide-react";
import { useSSEStream } from "../hooks/useSSEStream";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import TableWithExport from "./TableWithExport";
import ChatPopup from "./ChatPopup";
import { useLocation } from "react-router-dom";

interface StreamingRelationshipViewProps {
  profilingData: any;
  updateRelationshipAnalysisStatus?: (status: string) => void;
  setRelationshipAnalysisResponse: (response: string) => void;
  prevRelationshipAnalysisResponse?: string;
  hasApiBeenCalled: boolean;
  markApiCalled: () => void;
  onStreamingStart?: () => void;
  onStreamingEnd?: () => void;
  modifiedResponse?: any;
}

const StreamingRelationshipView: React.FC<StreamingRelationshipViewProps> = ({
  profilingData,
  updateRelationshipAnalysisStatus,
  setRelationshipAnalysisResponse,
  prevRelationshipAnalysisResponse,
  hasApiBeenCalled,
  markApiCalled,
  onStreamingStart,
  onStreamingEnd,
  modifiedResponse,
}) => {
  const [response, setResponse] = useState<string>(
    prevRelationshipAnalysisResponse || "",
  );
  const [chatOpen, setChatOpen] = useState(false);
  const [showDebugView, setShowDebugView] = useState(false);
  const [fullResult, setFullResult] = useState<any>(null);
  const [isScrolled, setIsScrolled] = useState(false);
  const hasSentRef = useRef(false);
  const location = useLocation();

  function getStoredSession() {
    const sessionId = sessionStorage.getItem("session_id");
    const appName = sessionStorage.getItem("app_name");
    const userId = sessionStorage.getItem("user_id");
    return { sessionId, appName, userId };
  }

  // Use SSE streaming hook with new LLM analysis states
  const {
    isStreaming,
    progress,
    statusMessage,
    error,
    result,
    toolResponse, // NEW: Tool output data
    llmAnalysis, // NEW: Streaming LLM text
    isAnalyzing, // NEW: LLM analysis phase indicator
    startStream,
    resetStream,
  } = useSSEStream({
    onProgress: (progressValue, message) => {
      console.log(`[Relationship] Progress: ${progressValue}% - ${message}`);

      // Log phase transitions
      if (progressValue === 95) {
        console.log(
          "[Relationship] Phase 2: Tool execution complete - FK/PK analysis finished",
        );
      } else if (progressValue >= 96 && progressValue < 100) {
        console.log(
          "[Relationship] Phase 3: AI analyzing relationship patterns and data model architecture",
        );
      }
    },
    onComplete: (finalResult) => {
      const responseText =
        finalResult?.text_response || "Relationship analysis complete!";
      
      setResponse(responseText);
      setRelationshipAnalysisResponse(responseText);
      setFullResult(finalResult); // Store full result for debug view

      if (updateRelationshipAnalysisStatus) {
        updateRelationshipAnalysisStatus(
          finalResult?.tool_response || finalResult,
        );
      }

      // Notify parent that streaming ended
      onStreamingEnd?.();
    },
    onError: (errorMsg) => {
      console.error("[Relationship] Analysis error:", errorMsg);
      console.error("[Relationship] Error details:", {
        errorMsg,
        timestamp: new Date().toISOString(),
      });

      // Notify parent that streaming ended due to error
      onStreamingEnd?.();
    },
  });

  // Helper function to get current phase info based on progress (NEW PHASES)
  const getCurrentPhase = (progress: number, isAnalyzingLLM: boolean) => {
    if (progress < 95) {
      return {
        id: 1,
        name: "Tool Execution",
        emoji: "",
        description:
          "Running relationship analysis - detecting FK/PK patterns and cross-table connections",
        color: "blue",
      };
    } else if (progress === 95) {
      return {
        id: 2,
        name: "Tool Complete",
        emoji: "",
        description:
          "Relationship analysis finished. Preparing intelligent data model insights...",
        color: "green",
      };
    } else if (isAnalyzingLLM && progress < 100) {
      return {
        id: 3,
        name: "AI Analysis",
        emoji: "",
        description:
          "Gemini is analyzing relationship patterns, hub tables, and data model architecture",
        color: "purple",
      };
    } else {
      return {
        id: 4,
        name: "Complete",
        emoji: "",
        description:
          "Analysis complete! Review your intelligent relationship insights below",
        color: "green",
      };
    }
  };

  // Helper to get all phases for visual indicators (NEW PHASES)
  const allPhases = [
    { id: 1, name: "Tool Execution", threshold: 0, emoji: "" },
    { id: 2, name: "Tool Complete", threshold: 95, emoji: "" },
    { id: 3, name: "AI Analysis", threshold: 96, emoji: "" },
    { id: 4, name: "Complete", threshold: 100, emoji: "" },
  ];

  const sendProfilingMessage = async (message: string) => {
    // Notify parent that streaming started
    onStreamingStart?.();

    const requestData = {
      appName: getStoredSession().appName,
      sessionId: getStoredSession().sessionId,
      userId: getStoredSession().userId,
      newMessage: {
        parts: [
          {
            text:
              message +
              (profilingData ? " with the provided profiling data." : "."),
          },
        ],
        role: "user",
      },
      streaming: true,
      stateDelta: {},
    };

    const initialUploadData = location.state?.initialUploadData;
    // Wrap in FormData to match server expectation
    const formData = new FormData();
    formData.append("request", JSON.stringify(requestData));

    // Hardcoded for testing - will be dynamic later
    formData.append("database_name", initialUploadData.databaseName);
    await startStream(formData);
  };

  // Sync result from SSE hook to local response state (backup mechanism)
  useEffect(() => {
    if (result && result.text_response && !isStreaming) {
      setResponse(result.text_response);
      setRelationshipAnalysisResponse(result.text_response);
      setFullResult(result);

      if (updateRelationshipAnalysisStatus) {
        updateRelationshipAnalysisStatus(result?.tool_response || result);
      }
    }
  }, [result, isStreaming]);

  // Sync prevRelationshipAnalysisResponse prop changes (from ChatModal "apply changes")
  useEffect(() => {
    if (prevRelationshipAnalysisResponse && prevRelationshipAnalysisResponse !== response) {
      setResponse(prevRelationshipAnalysisResponse);
    }
  }, [prevRelationshipAnalysisResponse]);

  // Handle modified response from ChatModal
  useEffect(() => {
    if (modifiedResponse && modifiedResponse.length > 0) {
      const modifiedData = modifiedResponse[0];
      if (modifiedData.text_response) {
        setResponse(modifiedData.text_response);
        setRelationshipAnalysisResponse(modifiedData.text_response);
      }
      if (modifiedData.tool_response) {
        setFullResult(modifiedData);
        if (updateRelationshipAnalysisStatus) {
          updateRelationshipAnalysisStatus(modifiedData.tool_response);
        }
      }
    }
  }, [modifiedResponse]);

  // Auto-send message when profiling data is available
  useEffect(() => {
    if (
      profilingData &&
      !hasSentRef.current &&
      !response &&
      !isStreaming &&
      !hasApiBeenCalled
    ) {
      hasSentRef.current = true;
      markApiCalled();

      const fileNames =
        profilingData.successful_uploads?.map((f: any) => f.table_name) || [];

      const message =
        fileNames.length > 0
          ? `Use profiling agent to Analyze and the [Relationship] of the data for the following files: ${fileNames.join(", ")}`
          : "Use profiling agent to analyze the relationship of the data";

      sendProfilingMessage(message);
    }
  }, [profilingData, isStreaming, response, hasApiBeenCalled]);

  const handleRetry = () => {
    setResponse("");
    setFullResult(null);
    setShowDebugView(false);
    resetStream();
    hasSentRef.current = false;

    const fileNames =
      profilingData?.successful_uploads?.map((f: any) => f.table_name) || [];

    const message =
      fileNames.length > 0
        ? `Use profiling agent to Analyze and the [Relationship] of the data for the following files: ${fileNames.join(", ")}`
        : "Use profiling agent to analyze the relationship of the data";

    sendProfilingMessage(message);
  };

  // Handle scroll for floating button
  useEffect(() => {
    const handleScroll = () => {
      setIsScrolled(window.scrollY > 100);
    };
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  return (
    <div className="relationship-view-component space-y-6">
      {/* Response Card */}
      <div className="relationship-content">
        <div className="p-4 border-b border-gray-300 bg-white rounded-t-lg">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2">
              <div
                className={`w-3 h-3 rounded-full ${
                  isStreaming
                    ? "bg-yellow-500 animate-pulse"
                    : error
                      ? "bg-red-500"
                      : "bg-green-500"
                }`}
              ></div>
              <h3 className="font-semibold text-gray-800">
                {isStreaming
                  ? "Processing..."
                  : error
                    ? "Relationship Analysis Error"
                    : "Relationship Analysis Results"}
              </h3>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={handleRetry}
                className="bg-brand-darkblue hover:bg-brand-blue text-white px-3 py-1 rounded text-sm font-medium transition-colors cursor-pointer"
              >
                Retry
              </button>
              {/* <button
                onClick={() => setChatOpen(true)}
                className="text-sm text-gray-600 hover:text-gray-900 flex items-center space-x-1"
              >
                <MessageCircle className="w-5 h-5" />
                <span>Chat</span>
              </button> */}
            </div>
          </div>
        </div>

        <div className="p-6">
          {/* Debug Info Banner (Always visible for debugging) */}
          <div className="mb-4 bg-brand-surface border border-teal-200 rounded-lg p-3">
            <p className="text-xs font-mono text-brand-darkblue">
              <strong>Relationship Debug Status:</strong> isStreaming=
              {isStreaming ? "true" : "false"}
              {" | "}progress={progress.toFixed(1)}%{" | "}isAnalyzing=
              {isAnalyzing ? "true" : "false"}
              {" | "}hasToolResponse={toolResponse ? "true" : "false"}
              {" | "}llmAnalysisLength={llmAnalysis?.length || 0}
            </p>
            <p className="text-xs font-mono text-brand-darkblue mt-1">
              {" "}
              hasResult={fullResult ? "true" : "false"}
              {" | "}hasResponse={response ? "true" : "false"}
              {" | "}currentPhase={getCurrentPhase(progress, isAnalyzing).id}
            </p>
            {statusMessage && (
              <p className="text-xs font-mono text-font-blue mt-1">
                <strong>Status:</strong> {statusMessage}
              </p>
            )}
          </div>

          {/* Streaming Progress - Enhanced 4-Phase UI */}
          {isStreaming && (
            <div className="space-y-5">
              {/* Current Phase Header */}
              <div className="bg-gradient-to-r from-brand-surface to-teal-50 border border-teal-200 rounded-lg p-4">
                <div className="flex items-start gap-3">
                  <span className="text-2xl">
                    {getCurrentPhase(progress, isAnalyzing).emoji}
                  </span>
                  <div className="flex-1">
                    <div className="flex items-center justify-between mb-1">
                      <h4 className="text-sm font-semibold text-gray-900">
                        Phase {getCurrentPhase(progress, isAnalyzing).id}:{" "}
                        {getCurrentPhase(progress, isAnalyzing).name}
                      </h4>
                      <span className="text-xs font-medium text-font-blue bg-teal-100 px-2 py-1 rounded">
                        {progress.toFixed(1)}%
                      </span>
                    </div>
                    <p className="text-xs text-gray-600 mb-3">
                      {getCurrentPhase(progress, isAnalyzing).description}
                    </p>

                    {/* Status message */}
                    {statusMessage && (
                      <div className="bg-white/80 rounded px-2 py-1.5 border border-gray-200">
                        <p className="text-xs text-gray-700">{statusMessage}</p>
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {/* NEW: LLM Token Streaming Display */}
              {isAnalyzing && llmAnalysis && (
                <div className="bg-brand-surface border border-teal-200 rounded-lg p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-lg"></span>
                    <h5 className="text-sm font-semibold text-brand-darkblue">
                      AI Relationship Analysis Preview
                    </h5>
                    <div className="flex-1 flex justify-end">
                      <div className="animate-pulse flex gap-1">
                        <div className="w-2 h-2 bg-brand-primary rounded-full"></div>
                        <div className="w-2 h-2 bg-brand-primary rounded-full animation-delay-200"></div>
                        <div className="w-2 h-2 bg-brand-primary rounded-full animation-delay-400"></div>
                      </div>
                    </div>
                  </div>
                  <div className="bg-white rounded px-3 py-2 max-h-48 overflow-y-auto">
                    <div className="text-xs text-gray-800 prose max-w-none">
                      <Markdown remarkPlugins={[remarkGfm]}>
                        {llmAnalysis}
                      </Markdown>
                    </div>
                  </div>
                  <p className="text-xs text-font-blue mt-2">
                    Streaming relationship insights in real-time from Gemini...
                  </p>
                </div>
              )}

              {/* Progress Bar */}
              <div className="space-y-2">
                <div className="w-full bg-gray-200 rounded-full h-3 overflow-hidden">
                  <div
                    className="bg-gradient-to-r from-brand-primary to-brand-primary-hover h-3 rounded-full transition-all duration-500 ease-out relative"
                    style={{ width: `${progress}%` }}
                  >
                    <div className="absolute inset-0 bg-white/20 animate-pulse"></div>
                  </div>
                </div>
              </div>

              {/* Phase Indicators - NEW 4-PHASE SYSTEM */}
              <div className="flex items-center justify-between gap-2 px-1">
                {allPhases.map((phase, index) => {
                  // Determine phase state based on new thresholds
                  let isCompleted = false;
                  let isActive = false;

                  if (phase.threshold === 0) {
                    // Tool Execution (0-95%)
                    isCompleted = progress >= 95;
                    isActive = progress < 95 && !isCompleted;
                  } else if (phase.threshold === 95) {
                    // Tool Complete (95%)
                    isCompleted = progress > 95;
                    isActive = progress === 95;
                  } else if (phase.threshold === 96) {
                    // AI Analysis (96-100%)
                    isCompleted = progress === 100;
                    isActive = isAnalyzing && progress >= 96 && progress < 100;
                  } else if (phase.threshold === 100) {
                    // Complete (100%)
                    isCompleted = progress === 100 && !isAnalyzing;
                    isActive = false;
                  }

                  return (
                    <div
                      key={phase.id}
                      className="flex flex-col items-center flex-1"
                    >
                      <div className="flex items-center w-full mb-2">
                        {index > 0 && (
                          <div
                            className={`flex-1 h-0.5 ${isCompleted || isActive ? "bg-brand-primary" : "bg-gray-300"} transition-colors duration-300`}
                          ></div>
                        )}
                        <div
                          className={`w-4 h-4 rounded-full flex items-center justify-center transition-all duration-300 ${
                            isCompleted
                              ? "bg-green-500 ring-2 ring-green-200"
                              : isActive
                                ? "bg-brand-primary ring-2 ring-teal-200 animate-pulse"
                                : "bg-gray-300"
                          }`}
                          title={phase.name}
                        >
                          <span className="text-[8px]"></span>
                        </div>
                        {index < allPhases.length - 1 && (
                          <div
                            className={`flex-1 h-0.5 ${isCompleted ? "bg-brand-primary" : "bg-gray-300"} transition-colors duration-300`}
                          ></div>
                        )}
                      </div>
                      <span
                        className={`text-[10px] text-center leading-tight ${
                          isActive
                            ? "text-font-blue font-semibold"
                            : isCompleted
                              ? "text-green-600"
                              : "text-gray-500"
                        }`}
                      >
                        {phase.name.split(" ").map((word, i) => (
                          <span key={i} className="block">
                            {word}
                          </span>
                        ))}
                      </span>
                    </div>
                  );
                })}
              </div>

              {/* Processing Info */}
              <div className="flex items-center justify-center gap-2 text-xs text-gray-500">
                <div className="animate-spin rounded-full h-3 w-3 border-b-2 border-brand-primary"></div>
                <span>Processing relationship analysis...</span>
              </div>
            </div>
          )}

          {/* Error Display */}
          {error && !isStreaming && (
            <div className="flex items-start space-x-3 py-4">
              <AlertCircle className="w-6 h-6 text-red-500 mt-1 flex-shrink-0" />
              <div>
                <p className="text-red-800 font-medium mb-1">Analysis Failed</p>
                <p className="text-red-600 text-sm">{error}</p>
              </div>
            </div>
          )}

          {/* Results Display */}
          {(response || (modifiedResponse && modifiedResponse.length > 0)) && !isStreaming && !error && (
            <div className="prose max-w-none">
              {/* Prominent Debug View Banner */}
              {(fullResult || (modifiedResponse && modifiedResponse.length > 0)) && (
                <div className="mb-6 bg-gradient-to-r from-teal-100 to-teal-100 border-2 border-teal-300 rounded-lg p-4 shadow-lg">
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <h3 className="text-lg font-bold text-brand-darkblue mb-2 flex items-center gap-2">
                        {modifiedResponse && modifiedResponse.length > 0 ? "Modified Response Applied" : "Developer Debug View Available"}
                      </h3>
                      <p className="text-sm text-font-blue mb-3">
                        {modifiedResponse && modifiedResponse.length > 0 
                          ? "This response has been modified through the chat interface."
                          : "Raw API response data is available below. Click to expand and see:"
                        }
                      </p>
                      {!(modifiedResponse && modifiedResponse.length > 0) && (
                        <ul className="text-xs text-font-blue space-y-1 mb-3">
                          <li>
                            <strong>text_response</strong> - Formatted markdown
                            string
                          </li>
                          <li>
                            <strong>tool_response</strong> - Raw JSON from
                            relationship_analysis_tool
                          </li>
                          <li>
                            <strong>Full result</strong> - Complete SSE response
                            object
                          </li>
                        </ul>
                      )}
                    </div>
                  </div>
                  <button
                    onClick={() => setShowDebugView(!showDebugView)}
                    className={`w-full px-4 py-3 rounded-lg font-semibold text-white transition-all ${
                      showDebugView
                        ? "bg-brand-primary hover:bg-brand-primary-hover"
                        : "bg-brand-primary hover:bg-brand-primary-hover animate-pulse"
                    }`}
                  >
                    {showDebugView
                      ? "Hide Debug View"
                      : "Show Debug View (Click Here!)"}
                  </button>
                </div>
              )}

              <div className="text-gray-800 whitespace-pre-wrap leading-relaxed">
                <Markdown
                  remarkPlugins={[remarkGfm]}
                  components={{ table: TableWithExport }}
                >
                  {response}
                </Markdown>
              </div>
              <div className="mt-4 pt-4 border-t border-gray-100">
                <p className="text-xs text-gray-500">
                  Analysis completed at {new Date().toLocaleString()}
                </p>
              </div>

              {/* Developer Debug View */}
              {(fullResult || (modifiedResponse && modifiedResponse.length > 0)) && showDebugView && (
                <div className="mt-6 border-t-4 border-brand-primary pt-4 bg-gray-50 rounded-lg p-4">
                  <div className="space-y-4">
                    {/* Text Response */}
                    <div>
                      <div className="flex items-center justify-between mb-2">
                        <h4 className="text-sm font-semibold text-gray-700">
                          text_response (Markdown)
                        </h4>
                        <button
                          onClick={() =>
                            navigator.clipboard.writeText(
                              (modifiedResponse && modifiedResponse.length > 0 
                                ? modifiedResponse[0].text_response 
                                : fullResult?.text_response) || "",
                            )
                          }
                          className="text-xs text-font-blue hover:text-font-blue"
                        >
                          Copy
                        </button>
                      </div>
                      <pre className="bg-gray-50 border border-gray-300 rounded p-3 text-xs overflow-x-auto max-h-60">
                        {(modifiedResponse && modifiedResponse.length > 0 
                          ? modifiedResponse[0].text_response 
                          : fullResult?.text_response) || "No text_response"}
                      </pre>
                    </div>

                    {/* Tool Response */}
                    <div>
                      <div className="flex items-center justify-between mb-2">
                        <h4 className="text-sm font-semibold text-gray-700">
                          tool_response (JSON)
                        </h4>
                        <button
                          onClick={() =>
                            navigator.clipboard.writeText(
                              JSON.stringify(
                                modifiedResponse && modifiedResponse.length > 0 
                                  ? modifiedResponse[0].tool_response 
                                  : fullResult?.tool_response, 
                                null, 
                                2
                              ),
                            )
                          }
                          className="text-xs text-font-blue hover:text-font-blue"
                        >
                          Copy JSON
                        </button>
                      </div>
                      <pre className="bg-gray-50 border border-gray-300 rounded p-3 text-xs overflow-x-auto max-h-96">
                        {JSON.stringify(
                          modifiedResponse && modifiedResponse.length > 0 
                            ? modifiedResponse[0].tool_response 
                            : fullResult?.tool_response, 
                          null, 
                          2
                        )}
                      </pre>
                    </div>

                    {/* Full Result */}
                    <div>
                      <div className="flex items-center justify-between mb-2">
                        <h4 className="text-sm font-semibold text-gray-700">
                          Full Result Object
                        </h4>
                        <button
                          onClick={() =>
                            navigator.clipboard.writeText(
                              JSON.stringify(
                                modifiedResponse && modifiedResponse.length > 0 
                                  ? modifiedResponse[0] 
                                  : fullResult, 
                                null, 
                                2
                              ),
                            )
                          }
                          className="text-xs text-font-blue hover:text-font-blue"
                        >
                          Copy JSON
                        </button>
                      </div>
                      <pre className="bg-gray-50 border border-gray-300 rounded p-3 text-xs overflow-x-auto max-h-96">
                        {JSON.stringify(
                          modifiedResponse && modifiedResponse.length > 0 
                            ? modifiedResponse[0] 
                            : fullResult, 
                          null, 
                          2
                        )}
                      </pre>
                    </div>

                    {/* API Info */}
                    <div className="bg-brand-surface border border-teal-200 rounded p-3">
                      <p className="text-xs text-font-blue">
                        <strong>Endpoint:</strong> POST /api/send-stream
                        <br />
                        <strong>SSE Event:</strong> complete
                        <br />
                        <strong>Feature:</strong> relationship_analysis
                        {modifiedResponse && modifiedResponse.length > 0 && (
                          <>
                            <br />
                            <strong>Status:</strong> Modified via Chat Interface
                          </>
                        )}
                      </p>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Waiting State */}
          {!isStreaming && !error && !response && !(modifiedResponse && modifiedResponse.length > 0) && (
            <div className="text-center py-8 text-gray-500">
              <FileSearch className="w-12 h-12 mx-auto mb-3 text-gray-300" />
              <p>Waiting for profiling data...</p>
            </div>
          )}
        </div>
      </div>

      <ChatPopup
        isOpen={chatOpen}
        onClose={() => setChatOpen(false)}
        currentStep="Relationship Analysis"
      />

      {/* Floating Retry Button */}
      {isScrolled && response && !isStreaming && !error && (
        <button
          onClick={handleRetry}
          className="fixed bottom-6 right-6 bg-brand-darkblue hover:bg-brand-blue text-white px-3 py-1 rounded text-sm font-medium transition-colors cursor-pointer shadow-lg z-50"
        >
          Retry
        </button>
      )}
    </div>
  );
};

export default StreamingRelationshipView;
