import { useState } from "react";
import { X, FileText, Eye } from "lucide-react";
import ColumnMappingModal from "./ColumnMappingModal";
import axiosInstance from "../utils/axios-interceptor";

interface ColumnInfo {
  name: string;
  dataType?: string;
  sampleValues?: string[];
}

interface TableSchema {
  tableName: string;
  filePath: string;
  columns: ColumnInfo[];
}

interface ColumnMapping {
  sourceColumn: string;
  targetColumn: string;
  sourceFile: string;
}

interface DDCandidate {
  table_index: number;
  heading: string;
  row_count: number;
  columns: string[];
  file_path: string;
  sample_rows: string[][];
}

interface DDSelectionModalProps {
  isOpen: boolean;
  onClose: () => void;
  candidates: DDCandidate[];
  onConfirm: (selectedPaths: string[], shouldMerge?: boolean, columnMappings?: ColumnMapping[], targetSchema?: ColumnInfo[]) => void;
  isLoading?: boolean;
}

export default function DDSelectionModal({
  isOpen,
  onClose,
  candidates,
  onConfirm,
  isLoading = false
}: DDSelectionModalProps) {
  const [selectedPaths, setSelectedPaths] = useState<string[]>([]);
  const [previewTable, setPreviewTable] = useState<DDCandidate | null>(null);
  const [shouldMerge, setShouldMerge] = useState(false);
  const [showColumnMapping, setShowColumnMapping] = useState(false);
  const [tableSchemas, setTableSchemas] = useState<TableSchema[]>([]);
  const [columnMappingLoading, setColumnMappingLoading] = useState(false);

  if (!isOpen) return null;

  const handleToggleSelection = (filePath: string) => {
    setSelectedPaths(prev => 
      prev.includes(filePath) 
        ? prev.filter(path => path !== filePath)
        : [...prev, filePath]
    );
  };

  const handleConfirm = () => {
    onConfirm(selectedPaths, shouldMerge);
  };

  const handleMergeConfirm = async () => {
    if (selectedPaths.length > 1) {
      // Check for schema mismatches before proceeding
      setColumnMappingLoading(true);
      try {
        console.log('Fetching schemas for paths:', selectedPaths);
        const schemas = await fetchTableSchemas(selectedPaths);
        console.log('Received schemas:', schemas);
        
        const hasMismatches = hasSchemaMismatches(schemas);
        console.log('Schema mismatches detected:', hasMismatches);
        console.log('Schemas details:', schemas.map(s => ({ name: s.tableName, columns: s.columns.map(c => c.name) })));
        
        // For testing: always show column mapping for multiple files
        // Remove this condition in production if you only want it for actual mismatches
        if (hasMismatches || selectedPaths.length > 1) {
          console.log('Showing column mapping modal');
          setTableSchemas(schemas);
          setShowColumnMapping(true);
        } else {
          // No schema mismatches, proceed with direct merge
          console.log('No schema mismatches, proceeding with direct merge');
          onConfirm(selectedPaths, true);
        }
      } catch (error) {
        console.error('Failed to fetch table schemas:', error);
        // For debugging, let's always show column mapping if there's an error
        alert(`Schema fetch failed: ${error instanceof Error ? error.message : String(error)}. Proceeding with direct merge.`);
        onConfirm(selectedPaths, true);
      } finally {
        setColumnMappingLoading(false);
      }
    } else {
      onConfirm(selectedPaths, true);
    }
  };

  const fetchTableSchemas = async (filePaths: string[]): Promise<TableSchema[]> => {
    console.log('Making API call to fetch schemas for:', filePaths);
    
    try {
      // Use axios instance to call backend API
      const response = await axiosInstance.post('/files/get-table-schemas', {
        file_paths: filePaths
      });
      
      console.log('Schema API response status:', response.status);
      console.log('Schema API result:', response.data);
      
      return response.data;
    } catch (error: any) {
      console.error('Schema API error:', error);
      if (error.response) {
        console.error('Error response data:', error.response.data);
        console.error('Error response status:', error.response.status);
        throw new Error(`Failed to fetch table schemas: ${error.response.status} - ${JSON.stringify(error.response.data)}`);
      } else {
        throw new Error(`Failed to fetch table schemas: ${error.message}`);
      }
    }
  };

  const hasSchemaMismatches = (schemas: TableSchema[]): boolean => {
    if (schemas.length <= 1) return false;
    
    const firstSchema = schemas[0];
    const firstCols = new Set(firstSchema.columns.map(col => col.name.toLowerCase()));
    
    return schemas.some(schema => {
      const schemaCols = new Set(schema.columns.map(col => col.name.toLowerCase()));
      
      // Check if column count differs
      if (schemaCols.size !== firstCols.size) return true;
      
      // Check if any column names differ
      for (let col of firstCols) {
        if (!schemaCols.has(col)) return true;
      }
      
      return false;
    });
  };

  const handleColumnMappingConfirm = (mappings: ColumnMapping[], targetSchema: ColumnInfo[]) => {
    setShowColumnMapping(false);
    onConfirm(selectedPaths, true, mappings, targetSchema);
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg max-w-4xl w-full mx-4 max-h-[90vh] overflow-hidden">
        <div className="flex items-center justify-between p-4 border-b">
          <h2 className="text-lg font-semibold text-gray-900">
            Select Data Dictionary Tables
          </h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600"
          >
            <X size={20} />
          </button>
        </div>

        <div className="p-4 overflow-y-auto max-h-[70vh]">
          <p className="text-sm text-gray-600 mb-4">
            Multiple data dictionary tables were found in your BRD. Please select which ones to use:
          </p>

          <div className="space-y-3">
            {candidates.map((candidate) => (
              <div
                key={candidate.table_index}
                className={`border rounded-lg p-4 cursor-pointer transition-colors ${
                  selectedPaths.includes(candidate.file_path)
                    ? "border-brand-primary bg-brand-surface"
                    : "border-gray-200 hover:border-gray-300"
                }`}
                onClick={() => handleToggleSelection(candidate.file_path)}
              >
                <div className="flex items-start justify-between">
                  <div className="flex items-start space-x-3">
                    <input
                      type="checkbox"
                      checked={selectedPaths.includes(candidate.file_path)}
                      onChange={() => handleToggleSelection(candidate.file_path)}
                      className="mt-1"
                    />
                    <div className="flex-1">
                      <div className="flex items-center space-x-2">
                        <FileText size={16} className="text-gray-500" />
                        <h3 className="font-medium text-gray-900">
                          {candidate.heading || `Table ${candidate.table_index + 1}`}
                        </h3>
                      </div>
                      <p className="text-sm text-gray-600 mt-1">
                        {candidate.row_count} rows • {candidate.columns.length} columns
                      </p>
                      <div className="text-xs text-gray-500 mt-1">
                        Columns: {candidate.columns.slice(0, 4).join(", ")}
                        {candidate.columns.length > 4 && ` +${candidate.columns.length - 4} more`}
                      </div>
                    </div>
                  </div>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setPreviewTable(candidate);
                    }}
                    className="text-font-blue hover:text-font-blue text-sm flex items-center space-x-1"
                  >
                    <Eye size={14} />
                    <span>Preview</span>
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="border-t bg-gray-50">
          {selectedPaths.length > 0 && (
            <div className="px-4 py-3 border-b bg-brand-surface">
              <label className="flex items-center space-x-2">
                <input
                  type="checkbox"
                  checked={shouldMerge}
                  onChange={(e) => setShouldMerge(e.target.checked)}
                  className="rounded"
                />
                <span className="text-sm text-font-blue font-medium">
                  Merge selected tables into a single data dictionary
                </span>
              </label>
              <p className="text-xs text-font-blue mt-1">
                This will combine all selected tables into one unified data dictionary
              </p>
            </div>
          )}
          <div className="flex items-center justify-between p-4">
            <div className="text-sm text-gray-600">
              {selectedPaths.length} of {candidates.length} tables selected
            </div>
            <div className="flex space-x-3">
              <button
                onClick={onClose}
                className="px-4 py-2 text-gray-600 hover:text-gray-800"
              >
                Cancel
              </button>
              <button
                onClick={() => onConfirm([])}
                disabled={isLoading}
                className="px-4 py-2 border border-gray-300 text-gray-700 rounded hover:bg-gray-50 disabled:bg-gray-400 disabled:cursor-not-allowed"
              >
                {isLoading ? "Processing Files..." : "Skip Selection"}
              </button>
              {selectedPaths.length > 0 && shouldMerge ? (
                <button
                  onClick={handleMergeConfirm}
                  disabled={isLoading || columnMappingLoading}
                  className="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 disabled:bg-gray-400 disabled:cursor-not-allowed"
                >
                  {isLoading || columnMappingLoading ? "Processing..." : `Merge & Use Tables (${selectedPaths.length})`}
                </button>
              ) : (
                <button
                  onClick={handleConfirm}
                  disabled={selectedPaths.length === 0 || isLoading}
                  className="px-4 py-2 bg-brand-primary text-white rounded hover:bg-brand-primary-hover disabled:bg-gray-400 disabled:cursor-not-allowed"
                >
                  {isLoading ? "Processing Files..." : `Use Selected Tables (${selectedPaths.length})`}
                </button>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Preview Modal */}
      {previewTable && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-60 p-4">
          <div className="bg-white rounded-lg w-full max-w-7xl h-full max-h-[95vh] flex flex-col">
            <div className="flex items-center justify-between p-4 border-b bg-white rounded-t-lg flex-shrink-0">
              <h3 className="text-lg font-semibold text-gray-900">
                Preview: {previewTable.heading || `Table ${previewTable.table_index + 1}`}
              </h3>
              <button
                onClick={() => setPreviewTable(null)}
                className="text-gray-400 hover:text-gray-600 p-1 rounded-full hover:bg-gray-100"
              >
                <X size={24} />
              </button>
            </div>
            
            <div className="flex-1 overflow-hidden p-4">
              <div className="h-full border border-gray-300 rounded-lg overflow-hidden bg-white">
                <div className="h-full overflow-auto">
                  <table className="min-w-full table-auto">
                    <thead>
                      <tr className="bg-gray-100 sticky top-0 z-10">
                        {previewTable.columns.map((col, idx) => (
                          <th key={idx} className="px-4 py-3 text-left text-sm font-semibold text-gray-700 border-b border-gray-300 whitespace-nowrap min-w-[120px]">
                            {col}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {previewTable.sample_rows.map((row, rowIdx) => (
                        <tr key={rowIdx} className={`${rowIdx % 2 === 0 ? 'bg-white' : 'bg-gray-50'} hover:bg-brand-surface transition-colors`}>
                          {row.map((cell, cellIdx) => (
                            <td key={cellIdx} className="px-4 py-3 text-sm text-gray-900 border-b border-gray-200 whitespace-nowrap min-w-[120px]">
                              <div className="max-w-[200px] truncate" title={cell || 'N/A'}>
                                {cell || 'N/A'}
                              </div>
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Column Mapping Modal */}
      {showColumnMapping && (
        <ColumnMappingModal
          isOpen={showColumnMapping}
          onClose={() => {
            console.log('Closing column mapping modal');
            setShowColumnMapping(false);
          }}
          tableSchemas={tableSchemas}
          onConfirm={handleColumnMappingConfirm}
          isLoading={isLoading}
        />
      )}
    </div>
  );
}