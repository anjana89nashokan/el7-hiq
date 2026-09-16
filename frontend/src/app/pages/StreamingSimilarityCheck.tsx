/**
 * StreamingSimilarityCheck - Input page for similarity analysis
 * Allows users to specify Reference tables/columns and source tables for matching
 */

import { useState } from 'react';
import { GitCompare, Plus, X, AlertCircle } from 'lucide-react';
import StreamingSimilarityView from '../components/StreamingSimilarityView';

interface DartTableConfig {
  id: string;
  tableName: string;
  columns: string;
}

export default function StreamingSimilarityCheck() {
  const [dartTables, setDartTables] = useState<DartTableConfig[]>([
    { id: '1', tableName: '', columns: '' }
  ]);
  const [sourceTables, setSourceTables] = useState<string>('');
  const [databaseName, setDatabaseName] = useState<string>('');
  const [error, setError] = useState<string>('');
  const [hasStarted, setHasStarted] = useState(false);

  const addDartTable = () => {
    setDartTables([
      ...dartTables,
      { id: Date.now().toString(), tableName: '', columns: '' }
    ]);
  };

  const removeDartTable = (id: string) => {
    if (dartTables.length > 1) {
      setDartTables(dartTables.filter(t => t.id !== id));
    }
  };

  const updateDartTable = (id: string, field: 'tableName' | 'columns', value: string) => {
    setDartTables(dartTables.map(t =>
      t.id === id ? { ...t, [field]: value } : t
    ));
  };

  const handleStartSimilarityCheck = () => {
    setError('');

    // Validate Reference tables
    const validDartTables = dartTables.filter(t => t.tableName.trim());
    if (validDartTables.length === 0) {
      setError('Please enter at least one Reference table name');
      return;
    }

    // Validate database name
    if (!databaseName.trim()) {
      setError('Please enter a database name');
      return;
    }

    // Validate source tables
    if (!sourceTables.trim()) {
      setError('Please enter at least one source table name');
      return;
    }

    const sourceTablesList = sourceTables
      .split(',')
      .map(t => t.trim())
      .filter(Boolean);

    if (sourceTablesList.length === 0) {
      setError('Please enter valid source table names');
      return;
    }

    // Build Reference tables JSON structure
    const dartTablesConfig = validDartTables.map(dt => ({
      table: dt.tableName.trim(),
      columns: dt.columns
        .split(',')
        .map(c => c.trim())
        .filter(Boolean)
    }));

    // Build similarity message with rounded percentage instruction
    const message = `Find column matching between source tables [${sourceTablesList.join(', ')}] and Reference tables. Calculate percentage of similarity (rounded to the nearest integer). Reference tables and column details: ${JSON.stringify(dartTablesConfig)}`;

    console.log('[Similarity Check] Starting with message:', message);
    setHasStarted(true);
  };

  if (hasStarted) {
    return (
      <div className="min-h-screen bg-gray-50 py-8 px-4">
        <div className="max-w-7xl mx-auto">
          <StreamingSimilarityView
            sourceTables={sourceTables}
            databaseName={databaseName}
            onReset={() => {
              setHasStarted(false);
              setError('');
            }}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50 py-8 px-4">
      <div className="max-w-4xl mx-auto">
        {/* Header */}
        <div className="bg-white rounded-lg shadow-sm p-6 mb-6">
          <div className="flex items-center gap-3 mb-2">
            <GitCompare className="w-8 h-8 text-font-blue" />
            <h1 className="text-2xl font-bold text-gray-900">
              Similarity Check
            </h1>
          </div>
          <p className="text-gray-600">
            Find matching columns between your source tables and multiple Reference reference tables
          </p>
        </div>

        {/* Main Form */}
        <div className="bg-white rounded-lg shadow-sm p-6">
          <div className="space-y-6">
            {/* Reference Tables Section */}
            <div>
              <div className="flex items-center justify-between mb-3">
                <label className="block text-sm font-medium text-gray-700">
                  Reference Tables Configuration
                  <span className="text-red-500 ml-1">*</span>
                </label>
                <button
                  onClick={addDartTable}
                  className="flex items-center gap-1 px-3 py-1.5 text-sm bg-teal-100 hover:bg-teal-200 text-font-blue rounded-md transition-colors cursor-pointer"
                >
                  <Plus className="w-4 h-4" />
                  Add Reference Table
                </button>
              </div>

              <div className="space-y-4">
                {dartTables.map((dartTable, index) => (
                  <div key={dartTable.id} className="border border-gray-200 rounded-lg p-4 bg-gray-50">
                    <div className="flex items-start gap-3">
                      <div className="flex-1 space-y-3">
                        <div className="flex items-center gap-2 mb-2">
                          <span className="text-sm font-semibold text-gray-700">
                            Reference Table {index + 1}
                          </span>
                          {dartTables.length > 1 && (
                            <button
                              onClick={() => removeDartTable(dartTable.id)}
                              className="ml-auto p-1 text-red-600 hover:bg-red-50 rounded transition-colors cursor-pointer"
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
                            onChange={(e) => updateDartTable(dartTable.id, 'tableName', e.target.value)}
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
                            onChange={(e) => updateDartTable(dartTable.id, 'columns', e.target.value)}
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
                Enter Reference reference table names (without project/dataset prefix). Specify columns or leave empty to match against all columns.
              </p>
            </div>

            {/* Database Name */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Database Name
                <span className="text-red-500 ml-1">*</span>
              </label>
              <input
                type="text"
                value={databaseName}
                onChange={(e) => setDatabaseName(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-brand-primary"
                placeholder="Enter database name (e.g., my-project.my-dataset)"
              />
              <p className="text-sm text-gray-500 mt-2">
                Enter the database/dataset name where your tables are located
              </p>
            </div>

            {/* Source Tables */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Source Table Names
                <span className="text-red-500 ml-1">*</span>
              </label>
              <textarea
                value={sourceTables}
                onChange={(e) => setSourceTables(e.target.value)}
                className="w-full px-3 py-3 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-brand-primary"
                rows={4}
                placeholder="Enter table names separated by commas, e.g.:&#10;employee_table, customer_data, sales_records"
              />
              <p className="text-sm text-gray-500 mt-2">
                Enter your source table names (without project/dataset prefix)
              </p>
            </div>

            {/* Example */}
            <div className="bg-brand-surface border border-teal-200 rounded-lg p-4">
              <div className="flex items-start gap-2">
                <GitCompare className="w-5 h-5 text-font-blue mt-0.5" />
                <div>
                  <h3 className="text-sm font-semibold text-brand-darkblue mb-1">
                    Example Configuration
                  </h3>
                  <p className="text-sm text-font-blue mb-2">
                    <strong>Reference Table 1:</strong> gender_lookup (columns: gender_code, gender_description)
                  </p>
                  <p className="text-sm text-font-blue mb-2">
                    <strong>Reference Table 2:</strong> country_codes (columns: country_id, country_name)
                  </p>
                  <p className="text-sm text-font-blue">
                    <strong>Source Tables:</strong> employees, customers, patient_records
                  </p>
                </div>
              </div>
            </div>

            {/* Error Display */}
            {error && (
              <div className="bg-red-50 border border-red-200 rounded-lg p-4">
                <div className="flex items-start gap-2">
                  <AlertCircle className="w-5 h-5 text-red-600 mt-0.5" />
                  <div>
                    <h3 className="text-sm font-semibold text-red-900 mb-1">
                      Error
                    </h3>
                    <p className="text-sm text-red-800">{error}</p>
                  </div>
                </div>
              </div>
            )}

            {/* Start Button */}
            <button
              onClick={handleStartSimilarityCheck}
              className="w-full bg-brand-primary hover:bg-brand-primary-hover text-white px-6 py-3 rounded-lg font-medium transition-colors flex items-center justify-center gap-2 cursor-pointer"
            >
              <GitCompare className="w-5 h-5" />
              Start Similarity Check
            </button>
          </div>
        </div>

        {/* Info Card */}
        <div className="bg-gradient-to-r from-brand-surface to-teal-50 rounded-lg p-6 mt-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-3">
            How Similarity Check Works
          </h3>
          <ul className="space-y-2 text-sm text-gray-700">
            <li className="flex items-start gap-2">
              <span className="text-font-blue font-bold">1.</span>
              <span><strong>Metadata Fetching:</strong> Retrieves column names, types, and sample values from both Reference and source tables</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-font-blue font-bold">2.</span>
              <span><strong>Semantic Matching:</strong> AI analyzes column names and sample values to identify potential matches</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-font-blue font-bold">3.</span>
              <span><strong>Overlap Validation:</strong> Calculates actual data overlap percentage between matched columns</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-font-blue font-bold">4.</span>
              <span><strong>Insights Generation:</strong> Provides detailed analysis and recommendations for each match</span>
            </li>
          </ul>
        </div>
      </div>
    </div>
  );
}