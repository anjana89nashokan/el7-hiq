import { useState } from "react";
import { FileSpreadsheet, Download, AlertCircle } from "lucide-react";
import axiosInstance from "../utils/axios-interceptor";

export default function GenerateMetadataExcel() {
  const [prompt, setPrompt] = useState("");
  const [templatePath, setTemplatePath] = useState("templates/Medata_template.xlsx");
  const [response, setResponse] = useState<any>(null);
  const [excelDownloadReady, setExcelDownloadReady] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");


  function getStoredSession() {
    const sessionId = sessionStorage.getItem("session_id");
    const appName = sessionStorage.getItem("app_name");
    const userId = sessionStorage.getItem("user_id");
    return { sessionId, appName, userId };
  }

  const loadSamplePrompt = () => {
    const samplePrompt = `Generate metadata Excel file using template: ${templatePath}

For the following data:

Data Dictionary:
| File Name | Field Name | Data Type | Length | Primary Key | Foreign Key | Field Description |
|----|----|----|----|----|----|----|
| members | member_id | STRING | 255 | Yes | No | The unique identifier for member |
| members | first_name | STRING | 100 | No | No | Member's first name |
| members | last_name | STRING | 100 | No | No | Member's last name |
| members | email | STRING | 255 | No | No | Member's email address |
| orders | order_id | STRING | 255 | Yes | No | The unique identifier for order |
| orders | member_id | STRING | 255 | No | Yes | Foreign key to members table |
| orders | order_date | DATE | 10 | No | No | Date when order was placed |
| orders | total_amount | NUMERIC | 18,2 | No | No | Total order amount |

Profiling Output:
### *Table: members*
**Data Quality Score: 97.14%**

| Column | Data Type | Uniqueness | Nulls | Blanks | Insights |
|---|---|---|---|---|---|
| member_id | STRING | 100.00% | 0.00% | 0.00% | Unique identifier, Primary Key. Sample: MEM001, MEM002, MEM003 |
| first_name | STRING | 85.30% | 0.00% | 0.00% | High variance. Sample: John, Jane, Michael |
| last_name | STRING | 78.50% | 0.00% | 0.00% | High variance. Sample: Smith, Johnson, Williams |
| email | STRING | 100.00% | 0.00% | 0.00% | Unique email addresses. Sample: john@example.com, jane@example.com |

### *Table: orders*
**Data Quality Score: 95.20%**

| Column | Data Type | Uniqueness | Nulls | Blanks | Insights |
|---|---|---|---|---|---|
| order_id | STRING | 100.00% | 0.00% | 0.00% | Unique identifier, Primary Key. Sample: ORD001, ORD002, ORD003 |
| member_id | STRING | 45.20% | 0.00% | 0.00% | Foreign key reference. Sample: MEM001, MEM002, MEM001 |
| order_date | DATE | 65.30% | 0.00% | 0.00% | Date values. Sample: 2024-01-15, 2024-01-16, 2024-01-17 |
| total_amount | NUMERIC | 78.90% | 0.00% | 0.00% | Numeric amounts. Sample: 125.50, 89.99, 250.00 |`;

    setPrompt(samplePrompt);
  };

  const handleSubmit = async () => {
    if (!prompt.trim()) {
      setError("Please enter a prompt or load the sample prompt");
      return;
    }

    setIsLoading(true);
    setError("");
    setExcelDownloadReady(false);
    setResponse(null);

    try {
      const { sessionId, appName, userId } = getStoredSession();

      const res = await axiosInstance.post("/messages/send", {
        appName: appName || "data_map_copilot",
        sessionId: sessionId || `session-${Date.now()}`,
        userId: userId || "user-test-123",
        newMessage: {
          parts: [{ text: prompt }],
          role: "user"
        },
        streaming: false,
        stateDelta: {}
      });

      if (res.status === 200) {
        const data = res.data;
        setResponse(data);

        console.log("=== Response Analysis ===");
        console.log("Response type:", Array.isArray(data) ? "Array (legacy)" : "Object (new format)");
        console.log("Has stateDelta:", !!data.stateDelta);
        console.log("Has conversation:", !!data.conversation);

        if (data.stateDelta) {
          console.log("StateDelta keys:", Object.keys(data.stateDelta));
        }

        // Check if Excel is ready - either in stateDelta or as BASE64 marker
        let excelReady = false;

        // Check stateDelta first (preferred)
        if (data.stateDelta && data.stateDelta.metadata_excel_file) {
          console.log(" Excel found in stateDelta!");
          console.log("Excel data type:", typeof data.stateDelta.metadata_excel_file);
          excelReady = true;
        }

        // Fallback: Check if response contains BASE64_EXCEL marker (: or =)
        const responseStr = JSON.stringify(data);
        if (responseStr.includes("[BASE64_EXCEL:") || responseStr.includes("[BASE64_EXCEL=")) {
          console.log(" Excel found in text response (fallback)");
          excelReady = true;
        }

        if (!excelReady) {
          console.log(" No Excel data found in response");
        }

        setExcelDownloadReady(excelReady);
      }
    } catch (err: any) {
      console.error("Error generating Excel:", err);
      setError(err.response?.data?.detail || "Failed to generate Excel file");
    } finally {
      setIsLoading(false);
    }
  };

  const handleDownloadExcel = async () => {
    if (!response) return;

    try {
      // Prepare request body with both stateDelta and text response
      const requestBody: any = {};

      // Also include text response as fallback
      requestBody.response_text = JSON.stringify(response);

      const res = await axiosInstance.post(
        "/metadata/extract-excel-from-response",
        requestBody,
        { responseType: "blob" }
      );

      // Create blob and download
      const blob = new Blob([res.data], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
      });
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `metadata_output_${Date.now()}.xlsx`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch (err: any) {
      console.error("Error downloading Excel:", err);
      setError(err.response?.data?.detail || "Failed to download Excel file");
    }
  };

  const extractBotResponse = (data: any) => {
    if (!data) return null;

    // Handle new format: {conversation: [...], stateDelta: {...}}
    if (data.conversation && Array.isArray(data.conversation)) {
      const modelResponse = data.conversation.find(
        (item: any) => item.content?.role === "model" && item.content?.parts?.[0]?.text
      );
      if (modelResponse) {
        return modelResponse.content.parts[0].text;
      }
    }

    // Handle old format: [...]
    if (Array.isArray(data)) {
      const modelResponse = data.find(
        (item) => item.content?.role === "model" && item.content?.parts?.[0]?.text
      );
      if (modelResponse) {
        return modelResponse.content.parts[0].text;
      }
    }

    return JSON.stringify(data, null, 2);
  };

  return (
    <div className="max-w-6xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-font-blue mb-2">
          Generate Metadata Excel
        </h1>
        <p className="text-gray-600">
          Use AI to intelligently generate metadata Excel files from data dictionary and profiling outputs
        </p>
      </div>

      {/* Template Path Input */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <label className="block text-sm font-semibold text-gray-700 mb-2">
          Template Path
        </label>
        <input
          type="text"
          value={templatePath}
          onChange={(e) => setTemplatePath(e.target.value)}
          placeholder="e.g., templates/Medata_template.xlsx"
          className="w-full p-3 border rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-primary"
        />
        <p className="text-xs text-gray-500 mt-1">
          Path to the Excel template file (relative to server root)
        </p>
      </div>

      {/* Prompt Input Section */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <div className="flex justify-between items-center mb-4">
          <label className="block text-sm font-semibold text-gray-700">
            Enter Prompt with Data Dictionary and Profiling Information
          </label>
          <button
            onClick={loadSamplePrompt}
            className="text-sm text-font-blue hover:text-font-blue underline cursor-pointer"
          >
            Load Sample Prompt
          </button>
        </div>

        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Enter your prompt with data dictionary and profiling information..."
          className="w-full h-96 p-4 border rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-primary font-mono text-sm"
        />

        <div className="flex justify-end mt-4">
          <button
            onClick={handleSubmit}
            disabled={isLoading || !prompt.trim()}
            className="bg-brand-primary hover:bg-brand-primary-hover disabled:bg-gray-300 disabled:cursor-not-allowed text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors cursor-pointer"
          >
            {isLoading ? (
              <>
                <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white"></div>
                Generating...
              </>
            ) : (
              <>
                <FileSpreadsheet size={16} />
                Generate Excel
              </>
            )}
          </button>
        </div>
      </div>

      {/* Error Display */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6 flex items-start gap-3">
          <AlertCircle className="text-red-600 flex-shrink-0 mt-0.5" size={20} />
          <div>
            <h3 className="font-semibold text-red-800">Error</h3>
            <p className="text-sm text-red-700 mt-1">{error}</p>
          </div>
        </div>
      )}

      {/* Response Section */}
      {response && (
        <div className="bg-white rounded-lg shadow p-6 mb-6">
          <h2 className="text-lg font-semibold text-gray-700 mb-4">Agent Response</h2>

          {excelDownloadReady && (
            <div className="bg-green-50 border border-green-200 rounded-lg p-4 mb-4 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <FileSpreadsheet className="text-green-600" size={24} />
                <div>
                  <h3 className="font-semibold text-green-800">Excel File Ready!</h3>
                  <p className="text-sm text-green-700">
                    Your metadata Excel file has been generated successfully
                  </p>
                </div>
              </div>
              <button
                onClick={handleDownloadExcel}
                className="bg-green-600 hover:bg-green-700 text-white px-4 py-2 rounded-lg flex items-center gap-2 transition-colors cursor-pointer"
              >
                <Download size={16} />
                Download Excel
              </button>
            </div>
          )}

          <div className="bg-gray-50 rounded-lg p-4 max-h-96 overflow-y-auto">
            <pre className="text-xs text-gray-700 whitespace-pre-wrap font-mono">
              {extractBotResponse(response)}
            </pre>
          </div>
        </div>
      )}

      {/* Instructions */}
      <div className="bg-brand-surface border border-teal-200 rounded-lg p-6">
        <h3 className="font-semibold text-font-blue mb-3">How to Use</h3>
        <ol className="text-sm text-font-blue space-y-2 list-decimal list-inside">
          <li>Click "Load Sample Prompt" to see an example format</li>
          <li>Modify the prompt with your actual data dictionary and profiling information</li>
          <li>Click "Generate Excel" to start the AI-powered generation</li>
          <li>Wait for the agent to analyze and map columns intelligently</li>
          <li>Download the generated Excel file when ready</li>
        </ol>

        <div className="mt-4 pt-4 border-t border-teal-200">
          <h4 className="font-semibold text-font-blue mb-2">Features:</h4>
          <ul className="text-sm text-font-blue space-y-1 list-disc list-inside">
            <li>Intelligent column mapping using AI reasoning</li>
            <li>Automatic data transformations (Yes/No → Y/N)</li>
            <li>Template formatting preservation</li>
            <li>Multi-sheet Excel generation (one sheet per file)</li>
            <li>Semantic understanding of column names</li>
          </ul>
        </div>
      </div>
    </div>
  );
}
