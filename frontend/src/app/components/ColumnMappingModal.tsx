import { useState, useEffect, useCallback } from "react";
import { X, ArrowRight, Check, AlertTriangle } from "lucide-react";

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

interface ColumnMappingModalProps {
  isOpen: boolean;
  onClose: () => void;
  tableSchemas: TableSchema[];
  onConfirm: (mappings: ColumnMapping[], targetSchema: ColumnInfo[]) => void;
  isLoading?: boolean;
}

export default function ColumnMappingModal({
  isOpen,
  onClose,
  tableSchemas,
  onConfirm,
  isLoading = false
}: ColumnMappingModalProps) {
  const [mappings, setMappings] = useState<ColumnMapping[]>([]);
  const [targetSchema, setTargetSchema] = useState<ColumnInfo[]>([]);
  const [selectedTargetTable, setSelectedTargetTable] = useState<string>("");

  useEffect(() => {
    if (isOpen && tableSchemas.length > 0) {
      initializeMappings();
    }
  }, [isOpen, tableSchemas]);

  const normalizeColumnName = useCallback((name: string): string => {
    return name
      .toLowerCase()
      .replace(/[_\s-]/g, '')  // Remove underscores, spaces, hyphens
      .replace(/[^a-z0-9]/g, ''); // Remove other special characters
  }, []);

  const similarity = useCallback((str1: string, str2: string): number => {
    // First try normalized comparison for common patterns
    const norm1 = normalizeColumnName(str1);
    const norm2 = normalizeColumnName(str2);
    
    if (norm1 === norm2) return 1.0;
    
    // Fallback to Levenshtein for partial matches
    const longer = str1.length > str2.length ? str1 : str2;
    const shorter = str1.length > str2.length ? str2 : str1;
    
    if (longer.length === 0) return 1.0;
    
    const editDistance = levenshteinDistance(longer, shorter);
    return (longer.length - editDistance) / longer.length;
  }, [normalizeColumnName]);

  const generateAutoMappings = useCallback((targetColumns: ColumnInfo[]) => {
    const autoMappings: ColumnMapping[] = [];
    
    tableSchemas.forEach(table => {
      table.columns.forEach(sourceCol => {
        // Find exact match first
        let targetCol = targetColumns.find(col => 
          col.name.toLowerCase() === sourceCol.name.toLowerCase()
        );
        
        // If no exact match, find similar match
        if (!targetCol) {
          targetCol = targetColumns.find(col => 
            similarity(col.name.toLowerCase(), sourceCol.name.toLowerCase()) > 0.8
          );
        }
        
        if (targetCol) {
          autoMappings.push({
            sourceColumn: sourceCol.name,
            targetColumn: targetCol.name,
            sourceFile: table.filePath
          });
        }
      });
    });
    
    setMappings(autoMappings);
  }, [tableSchemas, similarity]);

  const initializeMappings = useCallback(() => {
    // Use the first table as the initial target schema
    const firstTable = tableSchemas[0];
    setSelectedTargetTable(firstTable.tableName);
    setTargetSchema([...firstTable.columns]);

    // Auto-map columns with identical or similar names
    generateAutoMappings(firstTable.columns);
  }, [tableSchemas, generateAutoMappings]);

  const levenshteinDistance = (str1: string, str2: string): number => {
    const matrix = Array(str2.length + 1).fill(null).map(() => Array(str1.length + 1).fill(null));
    
    for (let i = 0; i <= str1.length; i++) matrix[0][i] = i;
    for (let j = 0; j <= str2.length; j++) matrix[j][0] = j;
    
    for (let j = 1; j <= str2.length; j++) {
      for (let i = 1; i <= str1.length; i++) {
        const indicator = str1[i - 1] === str2[j - 1] ? 0 : 1;
        matrix[j][i] = Math.min(
          matrix[j][i - 1] + 1,
          matrix[j - 1][i] + 1,
          matrix[j - 1][i - 1] + indicator
        );
      }
    }
    
    return matrix[str2.length][str1.length];
  };

  const updateMapping = (sourceFile: string, sourceColumn: string, targetColumn: string) => {
    setMappings(prev => {
      const filtered = prev.filter(m => !(m.sourceFile === sourceFile && m.sourceColumn === sourceColumn));
      if (targetColumn) {
        filtered.push({ sourceFile, sourceColumn, targetColumn });
      }
      return filtered;
    });
  };

  const addColumnToSchema = (columnName: string) => {
    if (!targetSchema.find(col => col.name === columnName)) {
      setTargetSchema(prev => [...prev, { name: columnName }]);
    }
  };

  const removeColumnFromSchema = (columnName: string) => {
    setTargetSchema(prev => prev.filter(col => col.name !== columnName));
    // Remove any mappings to this column
    setMappings(prev => prev.filter(m => m.targetColumn !== columnName));
  };

  const changeTargetSchema = (tableName: string) => {
    const selectedTable = tableSchemas.find(t => t.tableName === tableName);
    if (selectedTable) {
      setSelectedTargetTable(tableName);
      setTargetSchema([...selectedTable.columns]);
      // Re-generate mappings with new target schema
      generateAutoMappings(selectedTable.columns);
    }
  };

  const getMappingForColumn = (sourceFile: string, sourceColumn: string): string => {
    const mapping = mappings.find(m => m.sourceFile === sourceFile && m.sourceColumn === sourceColumn);
    return mapping?.targetColumn || "";
  };

  const getUnmappedColumns = (table: TableSchema): ColumnInfo[] => {
    return table.columns.filter(col => 
      !mappings.some(m => m.sourceFile === table.filePath && m.sourceColumn === col.name)
    );
  };

  const getAllUnmappedColumns = (): ColumnInfo[] => {
    const unmapped: ColumnInfo[] = [];
    tableSchemas.forEach(table => {
      unmapped.push(...getUnmappedColumns(table));
    });
    return unmapped;
  };

  const handleConfirm = () => {
    onConfirm(mappings, targetSchema);
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg max-w-7xl w-full mx-4 max-h-[95vh] overflow-hidden flex flex-col">
        <div className="flex items-center justify-between p-4 border-b">
          <h2 className="text-lg font-semibold text-gray-900">
            Column Mapping & Schema Selection
          </h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600"
          >
            <X size={20} />
          </button>
        </div>

        <div className="flex-1 overflow-hidden flex flex-col">
          {/* Target Schema Selection */}
          <div className="p-4 border-b bg-brand-surface">
            <h3 className="text-md font-medium text-brand-darkblue mb-2">Target Schema Selection</h3>
            <div className="flex items-center space-x-4">
              <span className="text-sm text-font-blue">Use schema from:</span>
              <select
                value={selectedTargetTable}
                onChange={(e) => changeTargetSchema(e.target.value)}
                className="px-3 py-1 border border-teal-300 rounded text-sm"
              >
                {tableSchemas.map(table => (
                  <option key={table.tableName} value={table.tableName}>
                    {table.tableName} ({table.columns.length} columns)
                  </option>
                ))}
              </select>
              <span className="text-xs text-font-blue">
                Target schema: {targetSchema.length} columns
              </span>
            </div>
          </div>

          {/* Column Mapping Interface */}
          <div className="flex-1 overflow-auto p-4">
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Source Tables */}
              <div>
                <h3 className="text-md font-medium text-gray-900 mb-3">Source Tables</h3>
                <div className="space-y-4">
                  {tableSchemas.map(table => (
                    <div key={table.tableName} className="border border-gray-200 rounded-lg p-3">
                      <h4 className="font-medium text-sm text-gray-800 mb-2">
                        {table.tableName}
                        <span className="ml-2 text-xs text-gray-500">
                          ({table.columns.length} columns)
                        </span>
                      </h4>
                      <div className="space-y-2 max-h-60 overflow-y-auto">
                        {table.columns.map(column => {
                          const currentMapping = getMappingForColumn(table.filePath, column.name);
                          const isUnmapped = !currentMapping;
                          
                          return (
                            <div key={column.name} className="flex items-center space-x-2 text-sm">
                              <div className={`flex-1 px-2 py-1 rounded ${isUnmapped ? 'bg-red-50 border border-red-200' : 'bg-green-50 border border-green-200'}`}>
                                <span className="font-medium">{column.name}</span>
                                {column.dataType && (
                                  <span className="ml-2 text-xs text-gray-500">({column.dataType})</span>
                                )}
                              </div>
                              <ArrowRight size={14} className="text-gray-400" />
                              <select
                                value={currentMapping}
                                onChange={(e) => updateMapping(table.filePath, column.name, e.target.value)}
                                className="px-2 py-1 border border-gray-300 rounded text-xs min-w-32"
                              >
                                <option value="">-- Unmapped --</option>
                                {targetSchema.map(targetCol => (
                                  <option key={targetCol.name} value={targetCol.name}>
                                    {targetCol.name}
                                  </option>
                                ))}
                              </select>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Target Schema */}
              <div>
                <h3 className="text-md font-medium text-gray-900 mb-3">Target Schema</h3>
                <div className="border border-gray-200 rounded-lg p-3">
                  <div className="space-y-2 max-h-96 overflow-y-auto">
                    {targetSchema.map(column => {
                      const mappedSources = mappings.filter(m => m.targetColumn === column.name);
                      
                      return (
                        <div key={column.name} className="border border-gray-100 rounded p-2">
                          <div className="flex items-center justify-between">
                            <span className="font-medium text-sm">{column.name}</span>
                            <button
                              onClick={() => removeColumnFromSchema(column.name)}
                              className="text-red-500 hover:text-red-700 text-xs"
                            >
                              Remove
                            </button>
                          </div>
                          <div className="mt-1 text-xs text-gray-600">
                            {mappedSources.length > 0 ? (
                              <span className="text-green-600">
                                Mapped from: {mappedSources.map(m => `${m.sourceFile.split('/').pop()}.${m.sourceColumn}`).join(", ")}
                              </span>
                            ) : (
                              <span className="text-orange-600">No mappings (will be filled with NULL)</span>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                  
                  {/* Add new column */}
                  <div className="mt-3 pt-3 border-t border-gray-200">
                    <input
                      type="text"
                      placeholder="Add new column name..."
                      className="w-full px-2 py-1 border border-gray-300 rounded text-sm"
                      onKeyPress={(e) => {
                        if (e.key === 'Enter') {
                          const input = e.target as HTMLInputElement;
                          if (input.value.trim()) {
                            addColumnToSchema(input.value.trim());
                            input.value = '';
                          }
                        }
                      }}
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* Unmapped Columns Warning */}
            {getAllUnmappedColumns().length > 0 && (
              <div className="mt-4 p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
                <div className="flex items-center space-x-2">
                  <AlertTriangle size={16} className="text-yellow-600" />
                  <span className="text-sm font-medium text-yellow-800">
                    Unmapped Columns ({getAllUnmappedColumns().length})
                  </span>
                </div>
                <p className="text-xs text-yellow-700 mt-1">
                  These columns will be filled with NULL values in the merged table. Map them to target columns or add new columns to the target schema.
                </p>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between p-4 border-t bg-gray-50">
          <div className="text-sm text-gray-600">
            {mappings.length} mappings configured • {targetSchema.length} target columns
          </div>
          <div className="flex space-x-3">
            <button
              onClick={onClose}
              className="px-4 py-2 text-gray-600 hover:text-gray-800"
            >
              Cancel
            </button>
            <button
              onClick={handleConfirm}
              disabled={isLoading}
              className="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 disabled:bg-gray-400 disabled:cursor-not-allowed flex items-center space-x-2"
            >
              {isLoading ? (
                <>
                  <span>Processing...</span>
                </>
              ) : (
                <>
                  <Check size={16} />
                  <span>Apply Mapping & Merge</span>
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}