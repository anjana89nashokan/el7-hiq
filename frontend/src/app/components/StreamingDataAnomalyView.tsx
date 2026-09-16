/**
 * StreamingDataAnomalyView - SSE-powered anomaly analysis experience
 * Mirrors the UX patterns from StreamingRelationshipView with anomaly-specific phases.
 */

import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AlertCircle,
  Activity,
  Loader2,
  RotateCcw,
  ShieldAlert,
} from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useSSEStream } from "../hooks/useSSEStream";
import TableWithExport from "./TableWithExport";
import { useLocation } from "react-router-dom";

interface StreamingDataAnomalyViewProps {
  setAnomalyData: React.Dispatch<React.SetStateAction<any>>;
  anomalyData?: any;
  prevResponse?: string;
  isStreamingEnabled?: boolean;
  hasApiBeenCalled: boolean;
  markApiCalled: () => void;
  onStreamingStart?: () => void;
  onStreamingEnd?: () => void;
  modifiedResponse?: any;
  sourceTables?: string;
}

const StreamingDataAnomalyView: React.FC<StreamingDataAnomalyViewProps> = ({
  setAnomalyData,
  anomalyData,
  prevResponse,
  isStreamingEnabled = true,
  hasApiBeenCalled,
  markApiCalled,
  onStreamingStart,
  onStreamingEnd,
  modifiedResponse,
  sourceTables = "",
}) => {
  const [response, setResponse] = useState<string>(
    prevResponse || anomalyData?.text_response || "",
  );
  const [isScrolled, setIsScrolled] = useState(false);
  const hasSentRef = useRef(false);
  const location = useLocation();

  function getStoredSession() {
    const sessionId = sessionStorage.getItem("session_id");
    const appName = sessionStorage.getItem("app_name");
    const userId = sessionStorage.getItem("user_id");
    return { sessionId, appName, userId };
  }

  const {
    isStreaming,
    progress,
    statusMessage,
    error,
    result,
    toolResponse,
    llmAnalysis,
    isAnalyzing,
    anomalyMetadata,
    phaseHistory,
    startStream,
    resetStream,
  } = useSSEStream({
    featureType: "anomaly",
    onProgress: (value, message) => {
      console.log(`[Anomaly SSE] Progress ${value}% - ${message}`);
    },
    onComplete: (finalResult) => {
      setResponse(finalResult?.text_response || "Anomaly analysis complete.");
      setAnomalyData(finalResult);

      // Notify parent that streaming ended
      onStreamingEnd?.();
    },
    onError: (errorMsg) => {
      console.error("[Anomaly SSE] Error:", errorMsg);

      // Notify parent that streaming ended due to error
      onStreamingEnd?.();
    },
  });

  const anomalySummary = useMemo(() => {
    // Check for modified response first
    const modifiedData = modifiedResponse && modifiedResponse.length > 0 ? modifiedResponse[0] : null;
    
    const summarySource =
      modifiedData?.tool_response?.summary_statistics ||
      toolResponse?.summary_statistics ||
      anomalyData?.tool_response?.summary_statistics ||
      anomalyData?.summary_statistics ||
      {};
    return {
      tablesAnalyzed:
        modifiedData?.tool_response?.tables_analyzed ||
        toolResponse?.tables_analyzed ||
        anomalyData?.tool_response?.tables_analyzed ||
        anomalyMetadata?.tablesAnalyzed ||
        summarySource.total_tables_analyzed ||
        0,
      severityDistribution: summarySource.severity_distribution ||
        anomalyMetadata?.severityDistribution || { low: 0, medium: 0, high: 0 },
      overallScore: summarySource.overall_data_quality_score || 0,
    };
  }, [toolResponse, anomalyData, anomalyMetadata, modifiedResponse]);

  const tableReports = useMemo(() => {
    // Check for modified response first
    const modifiedData = modifiedResponse && modifiedResponse.length > 0 ? modifiedResponse[0] : null;
    
    const reportSource =
      modifiedData?.tool_response?.table_anomaly_reports ||
      toolResponse?.table_anomaly_reports ||
      anomalyData?.tool_response?.table_anomaly_reports ||
      anomalyData?.table_anomaly_reports ||
      {};
    return Object.values(reportSource || {})
      .filter((report: any) => report?.total_anomalies_found)
      .sort(
        (a: any, b: any) =>
          (b.total_anomalies_found || 0) - (a.total_anomalies_found || 0),
      )
      .slice(0, 5);
  }, [toolResponse, anomalyData, modifiedResponse]);

  const currentPhase = useMemo(() => {
    if (progress >= 99.5) {
      return {
        name: "Report Generation",
        emoji: "🧾",
        description: "Preparing final anomaly insights...",
      };
    }
    if (progress >= 96 || isAnalyzing) {
      return {
        name: "AI Insighting",
        emoji: "🧠",
        description: "Gemini is summarizing anomaly hot spots",
      };
    }
    if (progress >= 60) {
      return {
        name: "Anomaly Scoring",
        emoji: "⚖️",
        description: "Calculating severity and impact",
      };
    }
    return {
      name: "Pattern Detection",
      emoji: "🧪",
      description: "Scanning columns for format and statistical issues",
    };
  }, [progress, isAnalyzing]);

  const displayMarkdown = useMemo(() => {
    // Check for modified response first
    if (modifiedResponse && modifiedResponse.length > 0 && modifiedResponse[0].text_response) {
      return modifiedResponse[0].text_response;
    }
    return llmAnalysis || response || "Awaiting anomaly analysis...";
  }, [modifiedResponse, llmAnalysis, response]);

  const sendStreamingRequest = useCallback(async () => {
    if (!isStreamingEnabled) return;

    // Notify parent that streaming started
    onStreamingStart?.();

    const { sessionId, appName, userId } = getStoredSession();
    const sourceTablesList = sourceTables
      .split(",")
      .map((table) => table.trim())
      .filter(Boolean);
    const tableListText = sourceTablesList.length
      ? sourceTablesList.join(", ")
      : "";
    const message = tableListText
      ? `Use profiling agent to do [Data Anomaly Analysis] on the following tables: ${tableListText}`
      : "Use profiling agent to do [Data Anomaly Analysis]";
    const requestData = {
      appName,
      sessionId,
      userId,
      newMessage: {
        parts: [{ text: message }],
        role: "user",
      },
      streaming: true,
      stateDelta: {
        anomaly_source_tables: sourceTablesList,
      },
    };

    // Wrap in FormData to match server expectation
    const initialUploadData = location.state?.initialUploadData;
    const formData = new FormData();
    formData.append("request", JSON.stringify(requestData));

    // Hardcoded for testing - will be dynamic later
    formData.append("database_name", initialUploadData.databaseName);

    await startStream(formData);
  }, [isStreamingEnabled, startStream, onStreamingStart, sourceTables]);

  useEffect(() => {
    if (
      isStreamingEnabled &&
      !hasSentRef.current &&
      !response &&
      !isStreaming &&
      !hasApiBeenCalled
    ) {
      hasSentRef.current = true;
      markApiCalled();
      sendStreamingRequest();
    }
  }, [response, isStreaming, isStreamingEnabled, hasApiBeenCalled]);

  useEffect(() => {
    if (result && result.text_response && !isStreaming) {
      setResponse(result.text_response);
      setAnomalyData(result);
    }
  }, [result, isStreaming, setAnomalyData]);

  // Sync prevResponse prop changes (from ChatModal "apply changes")
  useEffect(() => {
    if (prevResponse && prevResponse !== response) {
      setResponse(prevResponse);
    }
  }, [prevResponse, response]);

  // Handle modified response from ChatModal
  useEffect(() => {
    if (modifiedResponse && modifiedResponse.length > 0) {
      const modifiedData = modifiedResponse[0];
      if (modifiedData.text_response) {
        setResponse(modifiedData.text_response);
      }
      // Update anomalyData with the modified response
      setAnomalyData(modifiedData);
    }
  }, [modifiedResponse, setAnomalyData]);

  // Sync anomalyData prop changes (from ChatModal "apply changes")
  useEffect(() => {
    if (anomalyData?.text_response && anomalyData.text_response !== response && !isStreaming) {
      setResponse(anomalyData.text_response);
    }
  }, [anomalyData, isStreaming]);

  // Handle scroll for floating button
  useEffect(() => {
    const handleScroll = () => {
      setIsScrolled(window.scrollY > 100);
    };
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  const handleRetry = () => {
    resetStream();
    setResponse("");
    hasSentRef.current = false;
    sendStreamingRequest();
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900 flex items-center gap-2">
            <ShieldAlert className="h-6 w-6 text-brand-blue" />
            Streaming Data Anomaly Analysis
          </h2>
          <p className="text-sm text-gray-600">
            Real-time detection of format issues, statistical outliers, and
            duplicate patterns
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleRetry}
            className="text-sm text-gray-600 hover:text-gray-900 flex items-center gap-1"
          >
            <RotateCcw className="w-4 h-4" />
            Retry
          </button>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-3">
          <p className="text-sm text-gray-500">Current phase</p>
          <div className="flex items-center gap-3">
            <div className="text-3xl">{currentPhase.emoji}</div>
            <div>
              <p className="font-semibold text-gray-900">{currentPhase.name}</p>
              <p className="text-sm text-gray-600">
                {statusMessage || currentPhase.description}
              </p>
            </div>
          </div>
          <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
            <div
              className="h-full bg-brand-blue transition-all duration-500"
              style={{ width: `${Math.min(progress, 100)}%` }}
            ></div>
          </div>
          <p className="text-xs text-gray-500">
            Progress: {progress.toFixed(1)}%
          </p>
        </div>

        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-sm text-gray-500 mb-2">Recent milestones</p>
          <div className="space-y-2 max-h-32 overflow-y-auto">
            {phaseHistory.length === 0 && (
              <p className="text-sm text-gray-500">
                Waiting for streaming updates...
              </p>
            )}
            {phaseHistory.map((entry, idx) => (
              <div key={idx} className="flex items-start gap-2 text-sm">
                <Activity className="h-4 w-4 text-brand-blue flex-shrink-0 mt-0.5" />
                <div>
                  <p className="text-gray-800">{entry.message}</p>
                  {entry.progress !== undefined && (
                    <p className="text-xs text-gray-500">
                      {entry.progress.toFixed(1)}%
                    </p>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-4">
        <div className="flex items-center gap-2">
          <ShieldAlert className="h-5 w-5 text-brand-blue" />
          <p className="font-semibold text-gray-900">
            Severity snapshot ({anomalySummary.tablesAnalyzed} tables)
          </p>
        </div>
        <div className="grid grid-cols-3 gap-3">
          {(["high", "medium", "low"] as const).map((level) => (
            <div
              key={level}
              className="border border-gray-200 rounded-lg p-3 text-center bg-gray-50 flex flex-col gap-1"
            >
              <p className="text-xs uppercase text-gray-500">{level}</p>
              <p className="text-2xl font-semibold text-gray-900">
                {anomalySummary.severityDistribution[level] || 0}
              </p>
            </div>
          ))}
        </div>
        <p className="text-sm text-gray-600">
          Overall data quality score:{" "}
          <span className="font-semibold text-gray-900">
            {(anomalySummary.overallScore * 100).toFixed(1)}%
          </span>
        </p>
      </div>

      <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-3">
        <p className="font-semibold text-gray-900 flex items-center gap-2">
          <Activity className="h-5 w-5 text-brand-blue" />
          Streaming Analysis
          {modifiedResponse && modifiedResponse.length > 0 && (
            <span className="text-xs bg-green-100 text-green-700 px-2 py-1 rounded ml-2">
              Modified
            </span>
          )}
          {/* Always show loading wheel until complete response */}
          {(isStreaming ||
            (!response && !(modifiedResponse && modifiedResponse.length > 0)) ||
            response === "Awaiting anomaly analysis...") && (
            <Loader2 className="h-4 w-4 animate-spin text-brand-blue ml-auto" />
          )}
        </p>
        <div className="prose prose-sm max-w-none text-gray-800">
          <Markdown
            remarkPlugins={[remarkGfm]}
            components={{ table: TableWithExport }}
          >
            {displayMarkdown}
          </Markdown>
        </div>
      </div>

      {tableReports.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-3">
          <p className="font-semibold text-gray-900">Top impacted tables</p>
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-left text-gray-500">
                  <th className="py-2 pr-4">Table</th>
                  <th className="py-2 pr-4">Columns with issues</th>
                  <th className="py-2 pr-4">Total anomalies</th>
                  <th className="py-2 pr-4">Severity</th>
                </tr>
              </thead>
              <tbody>
                {tableReports.map((report: any, idx: number) => (
                  <tr key={idx} className="border-t border-gray-100">
                    <td className="py-2 pr-4 font-medium text-gray-900">
                      {report.table_name}
                    </td>
                    <td className="py-2 pr-4 text-gray-700">
                      {report.anomaly_summary?.columns_with_anomalies || 0}
                    </td>
                    <td className="py-2 pr-4 text-gray-700">
                      {report.total_anomalies_found || 0}
                    </td>
                    <td className="py-2 pr-4 text-gray-700">
                      High:{" "}
                      {report.anomaly_summary?.severity_distribution?.high || 0}{" "}
                      / Medium:{" "}
                      {report.anomaly_summary?.severity_distribution?.medium ||
                        0}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 flex items-start gap-2">
          <AlertCircle className="h-5 w-5 text-red-600 mt-0.5" />
          <div>
            <p className="font-semibold text-red-800">Streaming error</p>
            <p className="text-sm text-red-700">{error}</p>
          </div>
        </div>
      )}

      {/* Floating Retry Button */}
      {isScrolled && response && !isStreaming && (
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

export default StreamingDataAnomalyView;
