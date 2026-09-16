import React, { useState, useEffect } from 'react';
import * as XLSX from "xlsx";
import { saveAs } from "file-saver";
import { loadDetailedProfilingDashboard } from "../utils/ibcSheetDashboard";

interface StreamingTableProfilingDisplayProps {
  profilingData: any;
  dataDictionary: any;
  anomalyData: any;
  similarityData?: any;
  uploadData?: any;
  relationshipData?: any;
  profilingSession?: {
    sessionId?: string | null;
    userId?: string | null;
    appName?: string | null;
  };
  isStep4Skipped?: boolean;
  exportRef?: React.MutableRefObject<(() => void) | null>;
}

// Helper function to safely round percentage and score values to the nearest integer
const safeRound = (value: any, fallback: any = "N/A"): any => {
  if (value === null || value === undefined || value === '') return fallback;
  const num = Number(value);
  return isNaN(num) ? value : Math.round(num);
};

const StreamingTableProfilingDisplay: React.FC<StreamingTableProfilingDisplayProps> = ({ profilingData, dataDictionary, anomalyData, similarityData, uploadData, relationshipData, profilingSession, isStep4Skipped = false, exportRef }) => {
  const [error, setError] = useState<string>('');
  const [activeTab, setActiveTab] = useState<number>(1);
  
  console.log("------------------------")
  console.log('Received profilingData:', profilingData); 
  console.log('Received dataDictionary:', dataDictionary); 
  console.log('Received anomalyData:', anomalyData.tool_response);
  console.log("------------------------")

  const [tab1, setTab1] = useState<any[]>([]);
  const [tab2, setTab2] = useState<any[]>([]);
  const [tab3, setTab3] = useState<any[]>([]);
  const [tab4, setTab4] = useState<any[]>([]);
  const [tab5, setTab5] = useState<any[]>([]);

  const processApiResponse = (response: any | string) => {
    console.log("=== processApiResponse Debug ===");
    console.log("Raw response:", response);
    
    try {
      const parsedResponse = typeof response === "string" ? JSON.parse(response) : response;
      console.log("Full parsed response:", parsedResponse);
      console.log("Response keys:", Object.keys(parsedResponse));
      
      // Handle new JSON structure with all_tables
      let result = [];
      if (parsedResponse?.all_tables) {
        result = parsedResponse.all_tables;
      } else if (parsedResponse?.result) {
        result = parsedResponse.result;
      } else if (Array.isArray(parsedResponse)) {
        result = parsedResponse;
      } else if (parsedResponse?.tables) {
        result = parsedResponse.tables;
      } else {
        result = [parsedResponse];
      }
      
      console.log("Extracted result:", result);
      const tab1Rows: any[] = [];
      const tab2Rows: any[] = [];

      result.forEach((table: any, index: number) => {
        console.log(`Processing table ${index}:`, table);
        console.log(`Table keys:`, Object.keys(table));
        
        // Handle different table structures
        const hasColumnAnalysis = table.column_analysis || table.columns || table.column_stats;
        const tableRef = table.table_reference || table.table_name || table.name || `Table_${index + 1}`;
        const fileName = tableRef.includes('.') ? tableRef.split(".").pop() : tableRef;
        
        console.log(`Table ${index} - fileName: ${fileName}, hasColumnAnalysis:`, !!hasColumnAnalysis);
        
        if (hasColumnAnalysis) {
          const columnData = table.column_analysis || table.columns || table.column_stats;
          console.log(`Column data for ${fileName}:`, columnData);
          console.log(`Column data keys:`, Object.keys(columnData));
          
          Object.entries(columnData).forEach(([columnName, col]: [string, any]) => {
            console.log(`Processing column ${columnName}:`, col);
            console.log(`Column ${columnName} keys:`, Object.keys(col));
            
            // Handle null data - check multiple possible property names
            let nullPercentage = 0;
            if (col.null_percentage !== undefined) nullPercentage = col.null_percentage;
            else if (col.null_pct !== undefined) nullPercentage = col.null_pct;
            else if (col.nulls_pct !== undefined) nullPercentage = col.nulls_pct;
            else if (col.null_count !== undefined && col.total_count !== undefined) {
              nullPercentage = (col.null_count / col.total_count) * 100;
            }
            
            console.log(`Column ${columnName} - nullPercentage: ${nullPercentage}`);
            
            // Handle length data
            let maxLength = 0;
            if (col.avg_length !== undefined) maxLength = col.avg_length;
            else if (col.average_length !== undefined) maxLength = col.average_length;
            else if (col.max_length !== undefined) maxLength = col.max_length;
            else if (col.length !== undefined) maxLength = col.length;
            
            console.log(`Column ${columnName} - maxLength: ${maxLength}`);
            
            tab1Rows.push({
              fileName,
              fieldName: columnName,
              null: `${Math.round(nullPercentage)}%`,
              notNull: `${Math.round(100 - nullPercentage)}%`,
              maxLength: Math.ceil(maxLength) || 0,
            });

            // Handle default value data - check multiple sources
            let defaultValue = "N/A";
            let defaultPct = 0;
            
            // Check table-level default analysis first
            if (table.default_value_analysis?.[columnName]) {
              const defaultAnalysis = table.default_value_analysis[columnName];
              defaultValue = defaultAnalysis.default_value ?? "N/A";
              defaultPct = defaultAnalysis.default_pct ?? 0;
            }
            // Check column-level default data
            else if (col.default_value !== undefined) {
              defaultValue = col.default_value;
              defaultPct = col.default_pct ?? 0;
            }
            // Check for default analysis within column
            else if (col.default_analysis) {
              defaultValue = col.default_analysis.default_value ?? "N/A";
              defaultPct = col.default_analysis.default_pct ?? 0;
            }
            
            console.log(`Column ${columnName} - defaultValue: ${defaultValue}, defaultPct: ${defaultPct}`);
            
            tab2Rows.push({
              fileName,
              fieldName: columnName,
              defaultValue: defaultValue === null ? "NULL" : defaultValue,
              defaultPct: `${Math.round(defaultPct)}%`,
            });
          });
        } else {
          console.warn(`No column analysis found for table:`, table);
          // Try to extract basic info if available
          if (table.columns || table.fields) {
            const columns = table.columns || table.fields;
            Object.keys(columns).forEach(columnName => {
              tab1Rows.push({
                fileName,
                fieldName: columnName,
                null: "0%",
                notNull: "100%",
                maxLength: 0,
              });
              
              tab2Rows.push({
                fileName,
                fieldName: columnName,
                defaultValue: "N/A",
                defaultPct: "0%",
              });
            });
          }
        }
      });

      console.log("Final tab1 rows:", tab1Rows);
      console.log("Final tab2 rows:", tab2Rows);
      
      setTab1(tab1Rows);
      setTab2(tab2Rows);
      
    } catch (err) {
      console.error("Error in processApiResponse:", err);
      setError(`Failed to parse API response: ${err instanceof Error ? err.message : "Unknown error"}`);
    }
  };

    const processDataDictionary = (dictionary: any) => {
    try {
      const tab3Rows: any[] = [];
      let rows: any[] = [];

      if (Array.isArray(dictionary)) {
        if (dictionary.length === 0) return;
        if (Array.isArray(dictionary[0])) {
          rows = dictionary[0];
        } else if (typeof dictionary[0] === "object") {
          rows = dictionary;
        }
      }

      if (rows.length > 0) {
        rows.forEach((entry: any) => {
          console.log("[Tab3 entry raw]", entry);
          console.log("[Tab3 entry keys]", Object.keys(entry));
          const fileName = entry.file_name || entry["File Name"] || "Unknown";
          const fieldName = entry.field_name || entry["Field Name"] || entry["Attribute Name"] || "Unknown";
          console.log("[Tab3 resolved]", { fileName, fieldName, foreignKey: entry.foreign_key || entry["Foreign Key"] });
          const dataType = entry.data_type || entry["Data Type"] || "";
          const length = entry.length || entry["Length"] || "";
          const primaryKey = entry.primary_key || entry["Primary Key"] || "";
          const foreignKey = entry.foreign_key || entry["Foreign Key"] || "";
          const foreignKeyStr = String(foreignKey).trim();
          if (foreignKeyStr && foreignKeyStr !== "" && foreignKeyStr.toLowerCase() !== "false" && foreignKeyStr.toLowerCase() !== "no") {
            tab3Rows.push({
              fileName,
              fieldName,
              dataType,
              length,
              primaryKey,
              foreignKey: "Yes",
              reference: foreignKeyStr,
            });
          }
        });
      }

      if (tab3Rows.length > 0) {
        setTab3(tab3Rows);
      }
    } catch (err) {
      console.error("Error processing data dictionary:", err);
    }
  };

const processAnomalyData = (anomaly: any) => {
  try {
    const anomalyRows: any[] = [];
    const anomalyResponse =
      anomaly?.tool_response?.table_anomaly_reports ? anomaly.tool_response :
      anomaly?.data_anomaly_analysis_tool_response ||
      anomaly?.tool_response?.data_anomaly_analysis_tool_response;

    console.log("Raw anomalyResponse:", anomalyResponse);

    // If no structured response, try to parse from markdown text
    if (!anomalyResponse && anomaly?.text_response) {
      const markdownRows = parseAnomalyMarkdown(anomaly.text_response);
      setTab4(markdownRows);
      return;
    }

    if (anomalyResponse?.table_anomaly_reports) {
      Object.values(anomalyResponse.table_anomaly_reports).forEach((report: any) => {
        const tableName = report.table_name || report.table_reference || "Unknown";

        // Handle column-level anomalies (original structure)
        if (report.column_anomalies) {
          Object.entries(report.column_anomalies).forEach(([columnName, anomalies]: [string, any]) => {
            if (Array.isArray(anomalies)) {
              anomalies.forEach((a: any) => {
                anomalyRows.push({
                  fileName: tableName,
                  fieldName: columnName,
                  anomalyType: a.anomaly_type || "Unknown",
                  issue: a.issue || a.description || "N/A",
                  severity: a.severity || "low",
                });
              });
            }
          });
        }

        //  Handle table-level anomalies
        if (Array.isArray(report.table_level_anomalies)) {
          report.table_level_anomalies.forEach((a: any) => {
            anomalyRows.push({
              fileName: tableName,
              fieldName: "Table Level",
              anomalyType: a.anomaly_type || "Unknown",
              issue: a.issue || a.description || "N/A",
              severity: a.severity || "low",
            });
          });
        }

        //  Handle anomaly_summary.anomaly_types (your new case)
        if (report.anomaly_summary?.anomaly_types) {
          Object.entries(report.anomaly_summary.anomaly_types).forEach(
            ([anomalyType, anomalyData]: [string, any]) => {
              const columns = anomalyData.columns || ["Unknown"];
              const severityCounts = anomalyData.severity_counts || {};

              columns.forEach((col: string) => {
                // Push once for each severity category that has > 0
                Object.entries(severityCounts).forEach(([sev, count]) => {
                  if (typeof count === 'number' && count > 0) {
                    anomalyRows.push({
                      fileName: tableName,
                      fieldName: col,
                      anomalyType,
                      issue: `${count} issue(s) detected`,
                      severity: sev,
                    });
                  }
                });
              });
            }
          );
        }
      });
    }

    setTab4(anomalyRows);
  } catch (err) {
    console.error("Error processing anomaly data:", err);
  }
};

const parseAnomalyMarkdown = (textResponse: string): any[] => {
  const rows: any[] = [];
  const lines = textResponse.split('\n');
  let inTable = false;
  
  for (const line of lines) {
    const trimmed = line.trim();
    
    // Detect table start (header row with pipes)
    if (trimmed.includes('|') && !inTable) {
      trimmed.split('|').map(h => h.trim()).filter(h => h);
      inTable = true;
      continue;
    }
    
    // Skip separator row
    if (inTable && trimmed.match(/^\|?\s*[-:]+\s*\|/)) {
      continue;
    }
    
    // Process data rows
    if (inTable && trimmed.includes('|')) {
      const cells = trimmed.split('|').map(c => c.trim()).filter(c => c);
      
      if (cells.length >= 4) {
        rows.push({
          fileName: cells[0] || "Unknown",
          fieldName: cells[1] || "Unknown", 
          anomalyType: cells[2] || "Unknown",
          issue: cells[3] || "N/A",
          severity: cells[4] || "low"
        });
      }
    }
    
    // End table if empty line or no pipes
    if (inTable && !trimmed.includes('|')) {
      inTable = false;
    }
  }
  
  return rows;
};

const processSimilarityData = (similarity: any) => {
  try {
    const similarityRows: any[] = [];
    
    if (!similarity) return;
    
    let parsedData;
    try {
      parsedData = typeof similarity === 'string' ? JSON.parse(similarity) : similarity;
    } catch {
      return;
    }
    
    console.log("Similarity parsed data:", parsedData);
    
    // Check multiple possible structures
    let potentialMatches = null;
    
    // Structure 1: data[0].tool_response.potential_matches
    if (parsedData[0]?.tool_response?.potential_matches) {
      potentialMatches = parsedData[0].tool_response.potential_matches;
    }
    // Structure 2: direct potential_matches in response
    else if (parsedData.potential_matches) {
      potentialMatches = parsedData.potential_matches;
    }
    // Structure 3: potential_matches in first array item
    else if (parsedData[0]?.potential_matches) {
      potentialMatches = parsedData[0].potential_matches;
    }
    
    console.log("Found potential matches:", potentialMatches);
    
    if (potentialMatches && Array.isArray(potentialMatches)) {
      potentialMatches.forEach((match: any) => {
        console.log("Processing match:", match);
        similarityRows.push({
          rank: match.rank ?? "N/A",
          dartTableName: match.dart_table_name ?? "N/A",
          dartFieldName: match.dart_field_name ?? "N/A",
          filename: match.filename ?? "N/A",
          sourceColumnName: match.source_column_name ?? "N/A",
          headerNameSimilarity: safeRound(match.header_name_similarity),
          dataOverlapSimilarity: safeRound(match.data_overlap_similarity),
          combinedScore: safeRound(match.combined_score),
          confidence: match.confidence ?? "N/A"
        });
      });
    }
    
    console.log("Final similarity rows:", similarityRows);
    setTab5(similarityRows);
  } catch (err) {
    console.error("Error processing similarity data:", err);
  }
};


  const exportToExcel = () => {
    const wb = XLSX.utils.book_new();

    const ws1 = XLSX.utils.json_to_sheet(tab1);
    const ws2 = XLSX.utils.json_to_sheet(tab2);
    const ws3 = XLSX.utils.json_to_sheet(tab3);
    const ws4 = XLSX.utils.json_to_sheet(tab4);
    const ws5 = XLSX.utils.json_to_sheet(tab5);

    XLSX.utils.book_append_sheet(wb, ws1, "Nulls & Lengths");
    XLSX.utils.book_append_sheet(wb, ws2, "Defaults");
    XLSX.utils.book_append_sheet(wb, ws3, "Foreign Keys");
    XLSX.utils.book_append_sheet(wb, ws4, "Anomalies");
    XLSX.utils.book_append_sheet(wb, ws5, "Similarity Check");

    const excelBuffer = XLSX.write(wb, { bookType: "xlsx", type: "array" });
    saveAs(
      new Blob([excelBuffer], { type: "application/octet-stream" }),
      `profiling_${new Date().toISOString().slice(0, 19)}.xlsx`
    );
  };

  // Expose the export function through the ref
  useEffect(() => {
    if (exportRef) {
      exportRef.current = exportToExcel;
    }
  }, [exportRef, tab1, tab2, tab3, tab4, tab5]);

  useEffect(() => {
    console.log("=== useEffect Debug ===");
    console.log("profilingData:", profilingData);
    console.log("uploadData:", uploadData);
    console.log("Is array:", Array.isArray(profilingData));
    console.log("Length:", profilingData?.length);

    let cancelled = false;

    const run = async () => {
      try {
        const dashboard = await loadDetailedProfilingDashboard({
          uploadData,
          profilingMessageData: profilingData,
          relationshipData,
          session: profilingSession,
        });
        if (!cancelled && dashboard) {
          if (dashboard.tab1.length) setTab1(dashboard.tab1);
          if (dashboard.tab2.length) setTab2(dashboard.tab2);
          if (dashboard.tab3.length) setTab3(dashboard.tab3);
        } else if (!cancelled && profilingData) {
          if (Array.isArray(profilingData) && profilingData.length > 0) {
            const toolResponse = profilingData[0].tool_response;
            if (toolResponse?.intelligent_profiling_tool_response) {
              processApiResponse(toolResponse.intelligent_profiling_tool_response);
            } else {
              processApiResponse(toolResponse);
            }
          } else if (!Array.isArray(profilingData)) {
            processApiResponse(profilingData);
          }
        }
      } catch (err) {
        console.error("Failed to load detailed profiling dashboard:", err);
      }

      if (!cancelled && dataDictionary) {
        processDataDictionary(dataDictionary);
      }

      if (anomalyData) {
        processAnomalyData(anomalyData);
      }
      
      if (similarityData) {
        processSimilarityData(similarityData);
      }
    };

    run();

    return () => {
      cancelled = true;
    };
  }, [profilingData, dataDictionary, anomalyData, similarityData, uploadData, relationshipData, profilingSession]);

  const getCellClass = (header: string, value: any) => {
    if (header.toLowerCase().includes("null")) {
      const num = parseFloat(value);
      if (!isNaN(num)) {
        if (num > 50) return "text-green-600 font-semibold";
        if (num > 20) return "text-yellow-600 font-medium";
        return "text-red-600 font-medium";
      }
    }

    if (header.toLowerCase().includes("defaultpct")) {
      const num = parseFloat(value);
      if (!isNaN(num)) {
        if (num > 50) return "text-font-blue font-semibold";
        if (num > 20) return "text-font-blue font-medium";
      }
    }

    if (header.toLowerCase().includes("foreignkey")) {
      return value === "Yes" ? "text-font-blue font-semibold" : "text-gray-600";
    }
    
    if (header.toLowerCase() === "severity") {
      if (value === "high") return "text-red-600 font-bold";
      if (value === "medium") return "text-yellow-600 font-semibold";
      if (value === "low") return "text-green-600 font-medium";
    }

    return "";
  };

  const renderTable = (rows: any[], headers: string[]) => (
    <div className="overflow-x-auto">
      <table className="min-w-full bg-white border border-gray-200 rounded-lg shadow">
        <thead className="bg-gray-50">
          <tr>
            {headers.map(h => (
              <th key={h} className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase border-b">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="bg-white divide-y divide-gray-200">
          {rows.length === 0 ? (
            <tr>
              <td colSpan={headers.length} className="px-6 py-4 text-center text-gray-500">
                No data available
              </td>
            </tr>
          ) : (
            rows.map((row, i) => (
              <tr key={i} className="hover:bg-gray-50">
                {headers.map(h => {
                  const key = h.charAt(0).toLowerCase() + h.slice(1).replace(/\s+/g, '');
                  const cellValue = row[key];
                  return (
                    <td key={h} className={`px-6 py-4 text-sm border-b ${getCellClass(h, cellValue)}`}>
                      {cellValue ?? "N/A"}
                    </td>
                  );
                })}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <h2 className="text-base font-bold text-brand-darkblue mb-6">Table Profiling Dashboard</h2>

      {error && (
        <div className="mb-4 p-4 bg-red-100 border border-red-400 text-red-700 rounded">
          {error}
        </div>
      )}

      <div className="flex flex-wrap gap-2 mb-6">
        <button
          onClick={() => setActiveTab(1)}
          className={`px-4 py-2 rounded-lg transition-colors ${
            activeTab === 1 ? "bg-brand-primary text-white shadow-md" : "bg-gray-200 text-gray-700 hover:bg-gray-300"
          }`}
        >
          Nulls & Lengths
        </button>
        <button
          onClick={() => setActiveTab(2)}
          className={`px-4 py-2 rounded-lg transition-colors ${
            activeTab === 2 ? "bg-brand-primary text-white shadow-md" : "bg-gray-200 text-gray-700 hover:bg-gray-300"
          }`}
        >
          Defaults
        </button>
        <button
          onClick={() => setActiveTab(3)}
          className={`px-4 py-2 rounded-lg transition-colors ${
            activeTab === 3 ? "bg-brand-primary text-white shadow-md" : "bg-gray-200 text-gray-700 hover:bg-gray-300"
          }`}
        >
          Foreign Keys
        </button>
        <button
          onClick={() => setActiveTab(4)}
          className={`px-4 py-2 rounded-lg transition-colors ${
            activeTab === 4 ? "bg-brand-primary text-white shadow-md" : "bg-gray-200 text-gray-700 hover:bg-gray-300"
          }`}
        >
          Anomalies
        </button>
        {!isStep4Skipped && (
          <button
            onClick={() => setActiveTab(5)}
            className={`px-4 py-2 rounded-lg transition-colors ${
              activeTab === 5 ? "bg-brand-primary text-white shadow-md" : "bg-gray-200 text-gray-700 hover:bg-gray-300"
            }`}
          >
            Similarity Check
          </button>
        )}
        <button
          onClick={exportToExcel}
          className="ml-auto px-4 py-2 bg-green-600 text-white rounded-lg shadow hover:bg-green-700 transition-colors"
        >
          Export All to Excel
        </button>
      </div>

      <div className="bg-white rounded-lg shadow p-4">
        {activeTab === 1 && (
          <>
            {console.log("Rendering Tab1 - Nulls & Lengths:", tab1)}
            {renderTable(tab1, ["FileName", "FieldName", "Null", "NotNull", "MaxLength"])}
          </>
        )}
        {activeTab === 2 && (
          <>
            {console.log("Rendering Tab2 - Defaults:", tab2)}
            {renderTable(tab2, ["FileName", "FieldName", "DefaultValue", "DefaultPct"])}
          </>
        )}
        {activeTab === 3 && (
          <>
            {console.log("Rendering Tab3 - Foreign Keys:", tab3)}
            {renderTable(tab3, ["FileName", "FieldName", "ForeignKey", "Reference"])}
          </>
        )}
        {activeTab === 4 && (
          <>
            {console.log("Rendering Tab4 - Anomalies:", tab4)}
            {renderTable(tab4, ["FileName", "FieldName", "AnomalyType", "Issue", "Severity"])}
          </>
        )}
        {activeTab === 5 && !isStep4Skipped && (
          <>
            {console.log("Rendering Tab5 - Similarity:", tab5)}
            {renderTable(tab5, ["Rank", "DartTableName", "DartFieldName", "Filename", "SourceColumnName", "HeaderNameSimilarity", "DataOverlapSimilarity", "CombinedScore", "Confidence"])}
          </>
        )}
      </div>

    </div>
  );
};

export default StreamingTableProfilingDisplay;