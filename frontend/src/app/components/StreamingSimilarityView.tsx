import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import {
  AlertCircle,
  Activity,
  Loader2,
  RotateCcw,
  GitCompare,
  Plus,
  X,
  Database,
  Bot,
  Send,
} from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import axiosInstance from "../utils/axios-interceptor";
import { useSSEStream } from "../hooks/useSSEStream";
import { sendStreamingSimilarityChatHITLMessage } from "../end-points/chatApi";
import TableWithExport from "./TableWithExport";
import { DynamicSimilarity } from "./DynamicSimilarity";

interface StreamingSimilarityViewProps {
  sourceTables: string;
  databaseName?: string;
  onReset?: () => void;
  onComplete?: (response: string) => void;
}

interface DartTableConfig {
  id: string;
  tableName: string;
  columns: string;
}

interface SimilarityChatMessage {
  text: string;
  role: "user" | "assistant";
  mode?: string;
  response?: {
    text_response?: string;
    tool_response?: Record<string, unknown>;
    mode?: string;
  };
}

interface AppliedSimilarityResult {
  text_response: string;
  tool_response: Record<string, unknown>;
}

// Helper to safely format and round percentage values to the nearest integer
const formatPercentage = (value: any, fallback: string = "—"): string => {
  if (value === null || value === undefined || value === '') return fallback;
  
  let cleanValue = value;
  if (typeof value === 'string' && value.endsWith('%')) {
    cleanValue = value.slice(0, -1);
  }
  
  const num = Number(cleanValue);
  return isNaN(num) ? String(value) : `${Math.round(num)}%`;
};

function regenerateSimilarityMarkdown(
  toolResponse: Record<string, unknown>,
  existingText: string,
): string {
  const matches = (toolResponse.potential_matches as Record<string, unknown>[]) ?? [];
  const stats = toolResponse.summary_statistics as Record<string, number> | undefined;
  if (!matches.length && !stats) {
    return existingText;
  }

  const high = stats?.high_confidence_matches ?? matches.filter((m) => m.confidence === "HIGH").length;
  const medium = stats?.medium_confidence_matches ?? matches.filter((m) => m.confidence === "MEDIUM").length;
  const low = stats?.low_confidence_matches ?? matches.filter((m) => m.confidence === "LOW").length;

  const lines = [
    "# Similarity Analysis Results",
    "",
    "## Summary",
    `- Total Matches: ${stats?.total_matches_found ?? matches.length}`,
    `- High Confidence: ${high}`,
    `- Medium Confidence: ${medium}`,
    `- Low Confidence: ${low}`,
    "",
    "## High-Confidence Matches",
  ];

  matches
    .filter((m) => m.confidence === "HIGH")
    .slice(0, 10)
    .forEach((match, index) => {
      const rank = (match.rank as number) ?? index + 1;
      const sourceCol = match.source_column_name ?? "—";
      const dartField = match.dart_field_name ?? match.dart_column_name ?? "—";
      const dartTable = match.dart_table_name ?? "—";
      lines.push(
        "",
        `### ${rank}. \`${sourceCol}\` → \`${dartField}\``,
        `- **Reference Table:** \`${dartTable}\``,
        `- **Reference Column:** \`${dartField}\``,
        `- **Source Column:** \`${sourceCol}\``,
        `- **Header Similarity:** ${formatPercentage(match.header_name_similarity ?? match.semantic_score)}`,
        `- **Data Overlap:** ${formatPercentage(match.data_overlap_similarity ?? match.data_overlap_percent)}`,
        `- **Combined Score:** ${formatPercentage(match.combined_score)}`,
        `- **Confidence:** ${match.confidence ?? "—"}`,
      );
    });

  return lines.join("\n");
}

const StreamingSimilarityView: React.FC<StreamingSimilarityViewProps> = ({
  sourceTables,
  databaseName: propDatabaseName,
  onReset,
  onComplete,
}) => {
  const [response, setResponse] = useState<string>("");
  const [isScrolled, setIsScrolled] = useState(false);
  const hasSentRef = useRef(false);

  // Configuration state - BEFORE starting stream
  const [hasConfigured, setHasConfigured] = useState(false);
  const [dartTables, setDartTables] = useState<DartTableConfig[]>([
    { id: "1", tableName: "", columns: "" },
  ]);
  const [databaseName, setDatabaseName] = useState(propDatabaseName || "");
  const [tableSchemaError, setTableSchemaError] = useState("");
  const [isValidating, setIsValidating] = useState(false);
  const [configError, setConfigError] = useState<string>("");
  const [finalSimilarityMessage, setFinalSimilarityMessage] =
    useState<string>("");
  const [tableSchemaFields, setTableSchemaFields] = useState<any[]>([]);
  const [dynamicFilters, setDynamicFilters] = useState<any[]>([]);
  const [appliedSimilarityResult, setAppliedSimilarityResult] =
    useState<AppliedSimilarityResult | null>(null);
  const [isSimilarityChatOpen, setIsSimilarityChatOpen] = useState(false);
  const [similarityChatInput, setSimilarityChatInput] = useState("");
  const [similarityChatMessages, setSimilarityChatMessages] = useState<
    SimilarityChatMessage[]
  >([]);
  const [isSubmittingSimilarityChat, setIsSubmittingSimilarityChat] =
    useState(false);
  const [similarityChatError, setSimilarityChatError] = useState("");

  // Update databaseName when prop changes
  useEffect(() => {
    if (propDatabaseName) {
      setDatabaseName(propDatabaseName);
    }
  }, [propDatabaseName]);

  const [defaultDatabaseName] = useState("");

  function getStoredSession() {
    const sessionId = sessionStorage.getItem("session_id");
    const appName = sessionStorage.getItem("app_name");
    const userId = sessionStorage.getItem("user_id");
    return { sessionId, appName, userId };
  }

  const addDartTable = () => {
    setDartTables([
      ...dartTables,
      { id: Date.now().toString(), tableName: "", columns: "" },
    ]);
  };

  const removeDartTable = (id: string) => {
    if (dartTables.length > 1) {
      setDartTables(dartTables.filter((t) => t.id !== id));
    }
  };

  const updateDartTable = (
    id: string,
    field: "tableName" | "columns",
    value: string,
  ) => {
    setDartTables(
      dartTables.map((t) => (t.id === id ? { ...t, [field]: value } : t)),
    );
  };

  const handleStartSimilarityCheck = () => {
    setConfigError("");

    // Validate Reference tables
    const validDartTables = dartTables.filter((t) => t.tableName.trim());
    if (validDartTables.length === 0) {
      setConfigError("Please enter at least one Reference table name");
      return;
    }

    // Build Reference tables JSON structure
    const dartTablesConfig = validDartTables.map((dt) => ({
      table: dt.tableName.trim(),
      columns: dt.columns
        .split(",")
        .map((c) => c.trim())
        .filter(Boolean),
    }));

    // Parse source tables from prop
    const sourceTablesList = sourceTables
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);

    if (sourceTablesList.length === 0) {
      setConfigError("No source tables provided from profiling data");
      return;
    }

    // Build similarity message - SEND AS JSON for Windows compatibility
    const message = `Perform similarity check with these parameters (use them EXACTLY as provided):

${JSON.stringify(
  {
    dart_references: dartTablesConfig,
    source_tables: sourceTablesList,
  },
  null,
  2,
)}

Call fetch_metadata_tool with the above parameters to start the analysis.`;

    setFinalSimilarityMessage(message);
    setHasConfigured(true);
  };

  const {
    isStreaming,
    progress,
    statusMessage,
    error,
    result,
    toolResponse,
    llmAnalysis,
    isAnalyzing,
    phaseHistory,
    startStream,
    resetStream,
  } = useSSEStream({
    endpoint: "/messages-strm/similarity-check-stream",
    featureType: "similarity",
    onProgress: (value, message) => {
      console.log(`[Similarity SSE] Progress ${value}% - ${message}`);
    },
    onComplete: (finalResult) => {
      const responseText =
        finalResult?.text_response || "Similarity analysis complete.";
      const toolPayload = finalResult?.tool_response ?? {};
      setResponse(responseText);
      setAppliedSimilarityResult({
        text_response: responseText,
        tool_response: toolPayload,
      });

      if (onComplete) {
        onComplete(responseText);
      }
    },
    onError: (errorMsg) => {
      console.error("[Similarity SSE] Error:", errorMsg);
    },
  });

  const effectiveToolResponse = useMemo(
    () =>
      appliedSimilarityResult?.tool_response ??
      toolResponse ??
      result?.tool_response ??
      null,
    [appliedSimilarityResult, toolResponse, result],
  );

  const displayMarkdown = useMemo(
    () =>
      appliedSimilarityResult?.text_response ||
      llmAnalysis ||
      response ||
      "Awaiting similarity analysis...",
    [appliedSimilarityResult, llmAnalysis, response],
  );

  const canOpenSimilarityChat =
    !isStreaming && !!(appliedSimilarityResult || result || response) && !error;

  const buildSimilarityUpdatePreview = (hitlResponse: SimilarityChatMessage["response"]) => {
    if (!hitlResponse) return "Similarity results have been updated.";
    const matchCount =
      (hitlResponse.tool_response?.potential_matches as unknown[] | undefined)?.length ?? 0;
    const stats = hitlResponse.tool_response?.summary_statistics as
      | Record<string, number>
      | undefined;
    const parts = ["Proposed similarity changes are ready."];
    if (matchCount > 0) {
      parts.push(`${matchCount} potential match(es) in the revised tool response.`);
    }
    if (stats) {
      parts.push(
        `High: ${stats.high_confidence_matches ?? 0}, Medium: ${stats.medium_confidence_matches ?? 0}, Low: ${stats.low_confidence_matches ?? 0}.`,
      );
    }
    if (hitlResponse.text_response) {
      parts.push("The markdown analysis summary was also updated.");
    }
    parts.push("Click Apply changes to update the view.");
    return parts.join(" ");
  };

  const applyStreamingSimilarityChatResponse = useCallback(
    async (hitlResponse: SimilarityChatMessage["response"]) => {
      if (!hitlResponse) return;

      const toolPayload = hitlResponse.tool_response ?? {};
      const previousText =
        appliedSimilarityResult?.text_response ?? response ?? "";
      const apiText = hitlResponse.text_response?.trim() ?? "";
      const textResponse = apiText
        ? apiText
        : regenerateSimilarityMarkdown(toolPayload, previousText);

      const { sessionId, appName, userId } = getStoredSession();
      if (!sessionId || !appName || !userId) {
        setSimilarityChatError("Session not found. Cannot apply changes.");
        return;
      }

      setIsSubmittingSimilarityChat(true);
      setSimilarityChatError("");

      try {
        await sendStreamingSimilarityChatHITLMessage({
          user_id: userId,
          session_id: sessionId,
          app_name: appName,
          user_message: "Apply similarity changes",
          apply_changes: true,
          text_response: textResponse,
          tool_response: toolPayload,
        });

        setAppliedSimilarityResult({
          text_response: textResponse,
          tool_response: toolPayload,
        });
        setResponse(textResponse);

        if (onComplete) {
          onComplete(textResponse);
        }

        setIsSimilarityChatOpen(false);
      } catch (applyErr) {
        const message =
          applyErr instanceof Error
            ? applyErr.message
            : "Failed to apply similarity changes.";
        setSimilarityChatError(message);
      } finally {
        setIsSubmittingSimilarityChat(false);
      }
    },
    [appliedSimilarityResult, onComplete, response],
  );

  const handleStreamingSimilarityChatSend = async () => {
    const trimmedInput = similarityChatInput.trim();
    if (!trimmedInput || isSubmittingSimilarityChat) return;

    const { sessionId, appName, userId } = getStoredSession();
    setSimilarityChatMessages((prev) => [
      ...prev,
      { role: "user", text: trimmedInput },
    ]);
    setSimilarityChatInput("");
    setSimilarityChatError("");
    setIsSubmittingSimilarityChat(true);

    try {
      const hitlResponse = await sendStreamingSimilarityChatHITLMessage({
        user_id: userId ?? "",
        session_id: sessionId ?? "",
        app_name: appName ?? "",
        user_message: trimmedInput,
      });

      const mode = hitlResponse?.mode;
      let assistantText = "";

      if (mode === "QUESTION") {
        assistantText = hitlResponse?.text_response ?? "No answer returned.";
      } else if (mode === "UPDATE") {
        assistantText = buildSimilarityUpdatePreview(hitlResponse);
      } else {
        assistantText = hitlResponse?.text_response ?? JSON.stringify(hitlResponse);
      }

      setSimilarityChatMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text: assistantText,
          mode,
          response: hitlResponse,
        },
      ]);
    } catch (chatErr) {
      const message =
        chatErr instanceof Error
          ? chatErr.message
          : "Failed to process similarity chat request.";
      setSimilarityChatError(message);
      setSimilarityChatMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text: `Sorry, I could not process that request. ${message}`,
        },
      ]);
    } finally {
      setIsSubmittingSimilarityChat(false);
    }
  };

  const handleStreamingSimilarityChatKeyDown = (
    event: KeyboardEvent<HTMLTextAreaElement>,
  ) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleStreamingSimilarityChatSend();
    }
  };

  // Extract similarity metadata from toolResponse
  const similarityMetadata = useMemo(() => {
    const matches =
      effectiveToolResponse?.potential_matches ||
      [];
    const highConfidence = matches.filter(
      (m: any) => m.confidence === "HIGH",
    ).length;
    const mediumConfidence = matches.filter(
      (m: any) => m.confidence === "MEDIUM",
    ).length;
    const lowConfidence = matches.filter(
      (m: any) => m.confidence === "LOW",
    ).length;

    return {
      totalMatches: matches.length,
      highConfidence,
      mediumConfidence,
      lowConfidence,
      matches,
    };
  }, [effectiveToolResponse]);

  // Determine current phase based on progress
  const currentPhase = useMemo(() => {
    if (progress >= 90 || isAnalyzing) {
      return {
        name: "Insights Generation",
        emoji: "💡",
        description: "Generating AI-powered similarity insights...",
      };
    }
    if (progress >= 50) {
      return {
        name: "Overlap Validation",
        emoji: "🔍",
        description: "Calculating data overlap percentages...",
      };
    }
    if (progress >= 30) {
      return {
        name: "Semantic Matching",
        emoji: "🧠",
        description: "AI analyzing column name similarity...",
      };
    }
    return {
      name: "Metadata Fetching",
      emoji: "📊",
      description: "Fetching table schemas and sample values...",
    };
  }, [progress, isAnalyzing]);

  const sendStreamingRequest = useCallback(async () => {
    const { sessionId, appName, userId } = getStoredSession();

    // Re-parse Reference config from stored message for structured data
    const validDartTables = dartTables.filter((t) => t.tableName.trim());
    const dartTablesConfig = validDartTables.map((dt) => ({
      table: dt.tableName.trim(),
      columns: dt.columns
        .split(",")
        .map((c) => c.trim())
        .filter(Boolean),
    }));

    const sourceTablesList = sourceTables
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);

    const requestData = {
      appName,
      sessionId,
      userId,
      newMessage: {
        parts: [{ text: finalSimilarityMessage }],
        role: "user",
      },
      streaming: true,
      stateDelta: {
        // Pass structured data for backend to use directly (avoids LLM parsing issues)
        similarity_dart_references: dartTablesConfig,
        similarity_source_tables: sourceTablesList,
        // Use dynamic filters from component state
        similarity_filters: dynamicFilters,
      },
    };


    // Wrap in FormData to match server expectation
    const formData = new FormData();
    formData.append("request", JSON.stringify(requestData));
    // HARDCODED FOR TESTING: Set database_name parameter (for source tables)
    formData.append("database_name", propDatabaseName || "DATAMAP_COPILOT");
    formData.append("dart_database_name", databaseName || "DATAMAP_COPILOT");

    await startStream(formData);
  }, [finalSimilarityMessage, dartTables, sourceTables, startStream, databaseName]);

  
  const handleValidateDatabase = async () => {
    if (!databaseName && !defaultDatabaseName) {
      setTableSchemaError("Database name is required");
      return;
    }
    
    const tablesToValidate = dartTables.filter(table => table.tableName.trim());
    if (tablesToValidate.length === 0) {
      setTableSchemaError("At least one table name is required");
      return;
    }

    setTableSchemaError("");
    setIsValidating(true);
    
    // Create array of all table IDs for the API call
    const table_ids = tablesToValidate.map(table => table.tableName.trim());
    
    try {
      const response = await axiosInstance.post(
        `messages-strm/validate-dataset-tables`,
        {
          dataset_id: databaseName || defaultDatabaseName,
          table_ids: table_ids
        }
      );
      const data = response.data;
      
      // Check if validation is complete, then fetch table schemas for each table
      if (data?.phase === 'complete') {
        for (const table of tablesToValidate) {
          await getIndividualTableSchema(table.tableName);
        }
        setTableSchemaError("");
      } else {
        setTableSchemaError(data?.message || "Validation failed");
      }
    } catch (err: any) {
      console.error("Table schema validation error:", err);
      let errorMessage = "Table validation failed.";
      if (err.response?.data?.message) {
        errorMessage = err.response.data.message;
      } else if (err.response?.data?.detail) {
        errorMessage = err.response.data.detail;
      } else if (err.response) {
        errorMessage = `Server error: ${err.response.status}`;
      } else if (err.message) {
        errorMessage = err.message;
      }
      setTableSchemaError(errorMessage);
    } finally {
      setIsValidating(false);
    }
  };

  const getIndividualTableSchema = async (dartTable: string) => {
    try {
      const schemaResponse = await axiosInstance.get(
        `/data/table-schema?table_name=${dartTable}&dataset_id=${databaseName || defaultDatabaseName}`
      );
      const schemaData = schemaResponse.data;
      
      setTableSchemaFields((prev) => [...prev, schemaData]);
      // Handle Type 2 tables if needed
    } catch (err: any) {
      console.error(`Table schema fetch error for ${dartTable}:`, err);
      // Individual table errors are handled at the validation level
    }
  };



  // Only send streaming request AFTER configuration is complete
  useEffect(() => {
    if (
      hasConfigured &&
      !hasSentRef.current &&
      !response &&
      !isStreaming &&
      finalSimilarityMessage
    ) {
      hasSentRef.current = true;
      sendStreamingRequest();
    }
  }, [
    hasConfigured,
    finalSimilarityMessage,
    response,
    isStreaming,
    sendStreamingRequest,
  ]);

  useEffect(() => {
    if (result?.text_response && !isStreaming) {
      setResponse(result.text_response);
      setAppliedSimilarityResult({
        text_response: result.text_response,
        tool_response: result.tool_response ?? {},
      });
    }
  }, [result, isStreaming]);

  const handleRetry = () => {
    resetStream();
    setResponse("");
    setAppliedSimilarityResult(null);
    setSimilarityChatMessages([]);
    setSimilarityChatError("");
    hasSentRef.current = false;
    sendStreamingRequest();
  };

  const handleReset = () => {
    resetStream();
    setResponse("");
    setAppliedSimilarityResult(null);
    setSimilarityChatMessages([]);
    setSimilarityChatError("");
    setIsSimilarityChatOpen(false);
    setHasConfigured(false);
    setFinalSimilarityMessage("");
    setDartTables([{ id: "1", tableName: "", columns: "" }]);
    setTableSchemaFields([]);
    setDynamicFilters([]);
    setTableSchemaError("");
    setConfigError("");
    hasSentRef.current = false;
    if (onReset) {
      onReset();
    }
  };

  // Handle scroll for floating button
  useEffect(() => {
    const handleScroll = () => {
      setIsScrolled(window.scrollY > 100);
    };
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  // Show configuration form if not yet configured
  if (!hasConfigured) {
    return (
      <div className="space-y-6">
        {/* Configuration Header */}
        <div>
          <h3 className="text-lg font-semibold text-gray-900 flex items-center gap-2">
            <GitCompare className="h-6 w-6 text-brand-blue" />
            Configure Similarity Check
          </h3>
          <p className="text-sm text-gray-600 mt-1">
            Specify Reference reference tables to compare against your source tables:{" "}
            <strong>{sourceTables}</strong>
          </p>
        </div>

        {/* Database Name Input */}
        <div>
          <div className="block text-sm font-medium text-gray-700 mb-2">
            Database Name <span className="text-red-500 ml-1">*</span>
          </div>
          <input
            type="text"
            value={databaseName}
            onChange={(e) => setDatabaseName(e.target.value)}
            className="w-full px-3 py-2 text-sm border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-brand-primary"
          />
        </div>

        {/* Reference Tables Configuration */}
        <div>
          <div className="flex items-center justify-between mb-3">
            <div className="block text-sm font-medium text-gray-700">
              Reference Tables Configuration <span className="text-red-500 ml-1">*</span>
            </div>
            <button
              onClick={addDartTable}
              className="flex items-center gap-1 px-3 py-1.5 text-sm bg-teal-100 hover:bg-teal-200 text-font-blue rounded-md transition-colors"
            >
              <Plus className="w-4 h-4" />
              Add Reference Table
            </button>
          </div>

          <div className="space-y-4">
            {dartTables.map((dartTable, index) => (
              <div
                key={dartTable.id}
                className="border border-gray-200 rounded-lg p-4 bg-gray-50"
              >
                <div className="flex items-start gap-3">
                  <div className="flex-1 space-y-3">
                    <div className="flex items-center gap-2 mb-2">
                      <span className="text-sm font-semibold text-gray-700">
                        Reference Table {index + 1}
                      </span>
                      {dartTables.length > 1 && (
                        <button
                          onClick={() => removeDartTable(dartTable.id)}
                          className="ml-auto p-1 text-red-600 hover:bg-red-50 rounded transition-colors"
                          title="Remove this Reference table"
                        >
                          <X className="w-4 h-4" />
                        </button>
                      )}
                    </div>

                    {/* Table Name */}
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">
                        Table Name
                      </label>
                      <input
                        type="text"
                        value={dartTable.tableName}
                        onChange={(e) =>
                          updateDartTable(
                            dartTable.id,
                            "tableName",
                            e.target.value,
                          )
                        }
                        className="w-full px-3 py-2 text-sm border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-brand-primary"
                        placeholder="e.g., gender_lookup or country_codes"
                      />
                    </div>

                    {/* Columns */}
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">
                        Columns (comma-separated, optional)
                      </label>
                      <input
                        type="text"
                        value={dartTable.columns}
                        onChange={(e) =>
                          updateDartTable(
                            dartTable.id,
                            "columns",
                            e.target.value,
                          )
                        }
                        className="w-full px-3 py-2 text-sm border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-brand-primary"
                        placeholder="e.g., gender_code, gender_desc (leave empty for all columns)"
                      />
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>

          <p className="text-sm text-gray-500 mt-2">
            Enter Reference reference table names (without project/dataset prefix).
            Specify columns or leave empty to match against all columns.
          </p>
        </div>

        {/* Validate Database & Tables Button */}
        <div>
          <button
            onClick={handleValidateDatabase}
            disabled={isValidating || !databaseName || !dartTables.some(table => table.tableName.trim())}
            className="px-4 py-2 bg-green-600 text-white rounded-md hover:bg-green-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors flex items-center gap-2 text-sm font-medium"
            type="button"
          >
            <Database className="w-4 h-4" />
            {isValidating ? "Validating..." : "Validate Database & Tables"}
          </button>
        </div>

        {/* Dynamic Similarity Section */}
        <DynamicSimilarity
          tableSchemaFields={tableSchemaFields}
          dynamicFilters={dynamicFilters}
          setDynamicFilters={setDynamicFilters}
        />

        {/* Table Schema Error Display */}
        {tableSchemaError && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4">
            <div className="flex items-center gap-2 text-red-700">
              <AlertCircle className="w-5 h-5" />
              <p className="font-medium">Table Validation Error</p>
            </div>
            <p className="text-sm text-red-600 mt-1">
              {tableSchemaError}
            </p>
          </div>
        )}

        {/* Error Display */}
        {configError && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4">
            <div className="flex items-start gap-2">
              <AlertCircle className="w-5 h-5 text-red-600 mt-0.5" />
              <div>
                <h3 className="text-sm font-semibold text-red-900 mb-1">
                  Error
                </h3>
                <p className="text-sm text-red-800">{configError}</p>
              </div>
            </div>
          </div>
        )}

        {/* Start Button */}
        <button
          onClick={handleStartSimilarityCheck}
          className="w-full bg-brand-primary hover:bg-brand-primary-hover text-white px-6 py-3 rounded-lg font-medium transition-colors flex items-center justify-center gap-2"
        >
          <GitCompare className="w-5 h-5" />
          Start Similarity Check
        </button>

        {/* Example */}
        <div className="bg-brand-surface border border-teal-200 rounded-lg p-4">
          <div className="flex items-start gap-2">
            <GitCompare className="w-5 h-5 text-font-blue mt-0.5" />
            <div>
              <h3 className="text-sm font-semibold text-brand-darkblue mb-1">
                Example Configuration
              </h3>
              <p className="text-sm text-font-blue mb-2">
                <strong>Reference Table 1:</strong> gender_lookup (columns:
                gender_code, gender_description)
              </p>
              <p className="text-sm text-font-blue mb-2">
                <strong>Reference Table 2:</strong> country_codes (columns:
                country_id, country_name)
              </p>
              <p className="text-sm text-font-blue">
                <strong>Source Tables:</strong> {sourceTables}
              </p>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // Show streaming analysis after configuration
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900 flex items-center gap-2">
            <GitCompare className="h-6 w-6 text-brand-blue" />
            Streaming Similarity Analysis
          </h2>
          <p className="text-sm text-gray-600">
            Comparing <strong>{sourceTables}</strong> against configured Reference
            tables
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleRetry}
            disabled={isStreaming}
            className="text-sm text-gray-600 hover:text-gray-900 flex items-center gap-1 disabled:opacity-50"
          >
            <RotateCcw className="w-4 h-4" />
            Retry
          </button>
          <button
            onClick={handleReset}
            className="text-sm px-3 py-1 bg-gray-200 hover:bg-gray-300 rounded-md"
          >
            New Check
          </button>
          <button
            type="button"
            className="p-2 rounded-full hover:bg-gray-100 transition-colors cursor-pointer flex items-center gap-1 disabled:cursor-not-allowed disabled:opacity-50"
            onClick={() => setIsSimilarityChatOpen(true)}
            disabled={!canOpenSimilarityChat}
            aria-label="Chat about similarity results"
          >
            <Bot size={20} className="text-gray-500 hover:text-brand-blue" />
            <span>Chat</span>
          </button>
        </div>
      </div>

      {/* Progress Cards */}
      <div className="grid gap-4 md:grid-cols-2">
        {/* Current Phase */}
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

        {/* Recent Milestones */}
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

      {/* Match Summary */}
      <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-4">
        <div className="flex items-center gap-2">
          <GitCompare className="h-5 w-5 text-brand-blue" />
          <p className="font-semibold text-gray-900">
            Match Summary ({similarityMetadata.totalMatches} potential matches
            found)
          </p>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <div className="border border-gray-200 rounded-lg p-3 text-center bg-green-50 flex flex-col gap-1">
            <p className="text-xs uppercase text-gray-500">High Confidence</p>
            <p className="text-2xl font-semibold text-green-700">
              {similarityMetadata.highConfidence}
            </p>
          </div>
          <div className="border border-gray-200 rounded-lg p-3 text-center bg-yellow-50 flex flex-col gap-1">
            <p className="text-xs uppercase text-gray-500">Medium Confidence</p>
            <p className="text-2xl font-semibold text-yellow-700">
              {similarityMetadata.mediumConfidence}
            </p>
          </div>
          <div className="border border-gray-200 rounded-lg p-3 text-center bg-gray-50 flex flex-col gap-1">
            <p className="text-xs uppercase text-gray-500">Low Confidence</p>
            <p className="text-2xl font-semibold text-gray-700">
              {similarityMetadata.lowConfidence}
            </p>
          </div>
        </div>
      </div>

      {/* Top Matches Table (if available) */}
      {similarityMetadata.matches.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-3">
          <p className="font-semibold text-gray-900">Top Matches</p>
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-left text-gray-500">
                  <th className="py-2 pr-4">Source Column</th>
                  <th className="py-2 pr-4">Reference Column</th>
                  <th className="py-2 pr-4">Semantic Score</th>
                  <th className="py-2 pr-4">Data Overlap</th>
                  <th className="py-2 pr-4">Combined Score</th>
                  <th className="py-2 pr-4">Confidence</th>
                </tr>
              </thead>
              <tbody>
                {similarityMetadata.matches
                  .slice(0, 10)
                  .map((match: any, idx: number) => (
                    <tr key={idx} className="border-t border-gray-100">
                      <td className="py-2 pr-4 font-medium text-gray-900">
                        {match.filename || match.source_table_name || "—"}.
                        {match.source_column_name || "—"}
                      </td>
                      <td className="py-2 pr-4 text-gray-700">
                        {match.dart_table_name || "—"}.
                        {match.dart_field_name || match.dart_column_name || "—"}
                      </td>
                      <td className="py-2 pr-4 text-gray-700">
                        {formatPercentage(match.header_name_similarity ?? match.semantic_score)}
                      </td>
                      <td className="py-2 pr-4 text-gray-700">
                        {formatPercentage(match.data_overlap_similarity ?? match.data_overlap_percent, "Pending...")}
                      </td>
                      <td className="py-2 pr-4 text-gray-700">
                        {formatPercentage(match.combined_score, "N/A")}
                      </td>
                      <td className="py-2 pr-4">
                        <span
                          className={`px-2 py-1 rounded text-xs font-medium ${
                            match.confidence === "HIGH"
                              ? "bg-green-100 text-green-800"
                              : match.confidence === "MEDIUM"
                                ? "bg-yellow-100 text-yellow-800"
                                : "bg-gray-100 text-gray-800"
                          }`}
                        >
                          {match.confidence || "N/A"}
                        </span>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Streaming Analysis */}
      <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-3">
        <p className="font-semibold text-gray-900 flex items-center gap-2">
          <Activity className="h-5 w-5 text-brand-blue" />
          AI-Powered Analysis
          {/* Always show loading wheel until complete response */}
          {(isStreaming ||
            !response ||
            response === "Awaiting similarity analysis...") && (
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

      {/* Error Display */}
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

      {isSimilarityChatOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4">
          <div className="flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-lg bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
              <div>
                <h3 className="text-base font-semibold text-brand-blue">
                  Similarity Results Chat
                </h3>
                <p className="text-xs text-gray-500">
                  Ask a question or request an edit, then apply the updated
                  response to the view.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setIsSimilarityChatOpen(false)}
                className="rounded-md p-1 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-800"
                aria-label="Close similarity chat"
              >
                <X size={20} />
              </button>
            </div>

            <div className="flex-1 space-y-3 overflow-y-auto p-4">
              {similarityChatMessages.length === 0 && (
                <div className="rounded-md border border-dashed border-gray-300 bg-gray-50 p-4 text-sm text-gray-600">
                  Ask about the similarity matches, or describe a change (for
                  example, &quot;Change confidence of rank 1 match to
                  MEDIUM&quot;).
                </div>
              )}

              {similarityChatMessages.map((message, index) => (
                <div
                  key={`${message.role}-${index}`}
                  className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}
                >
                  <div
                    className={`max-w-[85%] rounded-lg px-4 py-3 text-sm ${message.role === "user" ? "bg-brand-blue text-white" : "bg-gray-100 text-gray-800"}`}
                  >
                    <div className="whitespace-pre-wrap">{message.text}</div>
                    {message.role === "assistant" && message.mode === "UPDATE" && (
                        <button
                          type="button"
                          onClick={() =>
                            void applyStreamingSimilarityChatResponse(
                              message.response,
                            )
                          }
                          disabled={isSubmittingSimilarityChat}
                          className="mt-3 rounded-md bg-green-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-green-700 disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          Apply changes
                        </button>
                      )}
                  </div>
                </div>
              ))}

              {isSubmittingSimilarityChat && (
                <div className="flex justify-start">
                  <div className="rounded-lg bg-gray-100 px-4 py-3">
                    <Loader2
                      size={18}
                      className="animate-spin text-gray-600"
                    />
                  </div>
                </div>
              )}

              {similarityChatError && (
                <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                  {similarityChatError}
                </div>
              )}
            </div>

            <div className="border-t border-gray-200 p-4">
              <div className="flex items-end gap-2">
                <textarea
                  value={similarityChatInput}
                  onChange={(event) =>
                    setSimilarityChatInput(event.target.value)
                  }
                  onKeyDown={handleStreamingSimilarityChatKeyDown}
                  placeholder="Ask about similarity results or describe an edit..."
                  disabled={isSubmittingSimilarityChat}
                  rows={3}
                  className="min-h-[84px] flex-1 resize-none rounded-md border border-gray-300 px-3 py-2 text-sm outline-none transition-colors focus:border-brand-blue focus:ring-1 focus:ring-brand-blue disabled:bg-gray-50"
                />
                <button
                  type="button"
                  onClick={() => void handleStreamingSimilarityChatSend()}
                  disabled={
                    !similarityChatInput.trim() || isSubmittingSimilarityChat
                  }
                  className="flex h-10 w-10 items-center justify-center rounded-md bg-brand-blue text-white transition-colors hover:bg-brand-blue/80 disabled:cursor-not-allowed disabled:opacity-60"
                  aria-label="Send similarity chat message"
                >
                  {isSubmittingSimilarityChat ? (
                    <Loader2 size={18} className="animate-spin" />
                  ) : (
                    <Send size={18} />
                  )}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default StreamingSimilarityView;