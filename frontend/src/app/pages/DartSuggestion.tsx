import { useState, useEffect } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { Plus, Trash2, ChevronDown, ChevronUp } from 'lucide-react';
import { submitDartSuggestions, getDefaultDataset } from '../end-points/dartApi';
import {
  addRow,
  deleteRow,
  updateRow,
  setDartSuggestionResponse,
  setDatabaseName,
  setDefaultDatabaseName,
  resetDartState,
  initOverlapStates,
  EMPTY_OVERLAP,
} from '../state/reducers/dartReducer';
import {
  normalizeTableKey,
  resolveDataDictionaryFieldName,
  resolveDataDictionaryTableName,
  tableNamesMatch,
} from '../utils/profilingUtils';
import NavigationButtons from '../components/NavigationButtons';
import DataOverlapAnalysis from '../components/DataOverlapAnalysis';

function resolveDescription(item: any): string {
  return (
    item['Attribute Description'] ||
    item.field_description ||
    item.description ||
    item['Field Description'] ||
    item.Field_Description ||
    ''
  );
}

interface DartSuggestionProps {
  readonly onPrevious: () => void;
  readonly onNext: () => void;
  readonly onRetry?: () => void;
}

export default function DartSuggestion({ onPrevious, onNext, onRetry }: DartSuggestionProps) {
  const dispatch = useDispatch();
  const { rows, toolResponse, dartSuggestionResponse, databaseName } = useSelector((state: any) => state.dart);

  const [submitting, setSubmitting] = useState(false);
  const [block1Open, setBlock1Open] = useState(true);
  const [block2Open, setBlock2Open] = useState(true);

  useEffect(() => {
    getDefaultDataset()
      .then(data => {
        dispatch(setDatabaseName(data.dataset_id));
        dispatch(setDefaultDatabaseName(data.dataset_id));
      })
      .catch(() => {});
  }, [dispatch]);

  const dataList = toolResponse?.[0] || [];

  const getUniqueFileNames = (): string[] => {
    const names = dataList
      .map((item: any) => normalizeTableKey(resolveDataDictionaryTableName(item)))
      .filter((n: string) => Boolean(n));
    return [...new Set<string>(names)];
  };

  const getAttributesForTable = (tableName: string) =>
    dataList.filter((item: any) =>
      tableNamesMatch(resolveDataDictionaryTableName(item), tableName),
    );

  useEffect(() => {
    const tables = getUniqueFileNames();
    if (tables.length !== 1 || rows.length !== 1) return;
    const onlyTable = tables[0];
    if (rows[0]?.tableName === onlyTable) return;
    dispatch(updateRow({ id: rows[0].id, field: 'tableName', value: onlyTable }));
  }, [dataList, rows, dispatch]);

  const getUsedAttributes = (currentRowId: string) =>
    rows.filter((row: any) => row.id !== currentRowId && row.attributeName).map((row: any) => row.attributeName);

  const handleTableChange = (id: string, tableName: string) => {
    dispatch(updateRow({ id, field: 'tableName', value: tableName }));
    dispatch(updateRow({ id, field: 'attributeName', value: '' }));
    dispatch(updateRow({ id, field: 'description', value: '' }));
  };

  const handleAttributeChange = (id: string, attributeName: string, tableName: string) => {
    dispatch(updateRow({ id, field: 'attributeName', value: attributeName }));
    const item = dataList.find(
      (it: any) =>
        resolveDataDictionaryFieldName(it) === attributeName &&
        tableNamesMatch(resolveDataDictionaryTableName(it), tableName),
    );
    if (item) dispatch(updateRow({ id, field: 'description', value: resolveDescription(item) }));
  };

  const handleSubmit = async () => {
    const sourceColumns = rows
      .filter((row: any) => row.tableName && row.attributeName && row.description)
      .map((row: any) => {
        const matched = dataList.find(
          (item: any) =>
            tableNamesMatch(resolveDataDictionaryTableName(item), row.tableName) &&
            resolveDataDictionaryFieldName(item) === row.attributeName,
        );
        return {
          source_table: resolveDataDictionaryTableName(matched) || row.tableName,
          column_name: row.attributeName,
          column_description: row.description,
        };
      });

    if (sourceColumns.length === 0) { alert('Please fill at least one complete row'); return; }

    const sessionData = {
      appName: sessionStorage.getItem('app_name'),
      sessionId: sessionStorage.getItem('session_id'),
      userId: sessionStorage.getItem('user_id'),
    };

    setSubmitting(true);
    try {
      const response = await submitDartSuggestions(
        `Find the matching Reference tables and columns for the following source columns: ${JSON.stringify(sourceColumns)}`,
        sessionData
      );
      dispatch(setDartSuggestionResponse(response));

      const initial: Record<string, any> = {};
      response?.[0]?.suggestions?.forEach((s: any, si: number) => {
        s.dart_suggestions?.forEach((d: any, di: number) => {
          initial[`${si}-${di}`] = { ...EMPTY_OVERLAP, tableName: d.table_name };
        });
      });
      dispatch(initOverlapStates(initial));
    } catch {
      alert('Failed to submit');
    } finally {
      setSubmitting(false);
    }
  };

  const handleRetry = () => {
    dispatch(resetDartState());
    setBlock1Open(true);
    setBlock2Open(true);
    onRetry?.();
  };

  if (!toolResponse?.length || !toolResponse[0]?.length) {
    return (
      <div className="p-6">
        <h2 className="text-2xl font-bold mb-6">Reference Suggestion</h2>
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
          <p className="text-yellow-800">No data available from Step 3 (Data Dictionary). Please complete the data dictionary step first.</p>
        </div>
      </div>
    );
  }

  const suggestions = dartSuggestionResponse?.[0]?.suggestions || [];

  return (
    <div className="p-6">
      <h2 className="text-2xl font-bold mb-6">Reference Suggestion</h2>

      {/* BLOCK 1: Reference Database Name */}
      <div className="border border-gray-200 rounded-lg bg-white overflow-hidden mb-6">
        <button type="button" onClick={() => setBlock1Open(v => !v)} className="w-full flex items-center justify-between px-4 py-3 bg-gray-50 hover:bg-gray-100 cursor-pointer">
          <div className="text-left">
            <div className="text-sm font-semibold">Reference Database Name</div>
            <div className="text-xs text-gray-500">Enter the Reference BigQuery dataset name</div>
          </div>
          <div className="flex items-center gap-2 text-xs text-gray-600">
            {block1Open ? <><span>Hide</span><ChevronUp size={16} /></> : <><span>Show</span><ChevronDown size={16} /></>}
          </div>
        </button>
        {block1Open && (
          <div className="p-4 bg-gray-50">
            <label htmlFor="dart-database-name" className="block text-sm font-medium text-gray-700 mb-2">
              Reference Database Name (BQ Dataset) <span className="text-red-500">*</span>
            </label>
            <input
              id="dart-database-name"
              type="text"
              value={databaseName}
              onChange={e => dispatch(setDatabaseName(e.target.value))}
              className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-xs focus:outline-none focus:ring-2 focus:ring-brand-primary font-mono"
              placeholder='Enter Reference database name (e.g. "DB_WRK")'
            />
          </div>
        )}
      </div>

      {/* BLOCK 2: Reference Suggestion */}
      <div className="border border-gray-200 rounded-lg bg-white overflow-hidden mb-6">
        <button type="button" onClick={() => setBlock2Open(v => !v)} className="w-full flex items-center justify-between px-4 py-3 bg-gray-50 hover:bg-gray-100 cursor-pointer">
          <div className="text-left">
            <div className="text-sm font-semibold">Reference Suggestion</div>
            <div className="text-xs text-gray-500">Select source table + attributes and submit to get Reference suggestions</div>
          </div>
          <div className="flex items-center gap-2 text-xs text-gray-600">
            {block2Open ? <><span>Hide</span><ChevronUp size={16} /></> : <><span>Show</span><ChevronDown size={16} /></>}
          </div>
        </button>

        {block2Open && (
          <div className="p-4">
            <div className="space-y-4">
              {rows.map((row: any) => {
                const attributes = row.tableName ? getAttributesForTable(row.tableName) : [];
                const usedAttributes = getUsedAttributes(row.id);
                return (
                  <div key={row.id} className="border rounded-lg p-4 bg-white shadow-sm">
                    <div className="flex items-start gap-4">
                      <div className="flex-1 space-y-4">
                        <div>
                          <label className="block text-sm font-medium mb-2">Table Name</label>
                          <select value={row.tableName} onChange={e => handleTableChange(row.id, e.target.value)} className="w-full border rounded px-3 py-2">
                            <option value="">Select Table</option>
                            {getUniqueFileNames().map(name => <option key={name} value={name}>{name}</option>)}
                          </select>
                        </div>
                        <div>
                          <label className="block text-sm font-medium mb-2">Attribute Name</label>
                          <select value={row.attributeName} onChange={e => handleAttributeChange(row.id, e.target.value, row.tableName)} disabled={!row.tableName} className="w-full border rounded px-3 py-2 disabled:bg-gray-100">
                            <option value="">Select Attribute</option>
                            {attributes.map((item: any) => {
                              const attrName = resolveDataDictionaryFieldName(item);
                              return (
                                <option key={attrName} value={attrName} disabled={usedAttributes.includes(attrName)}>
                                  {attrName}
                                </option>
                              );
                            })}
                          </select>
                        </div>
                        <div>
                          <label className="block text-sm font-medium mb-2">Description</label>
                          <textarea
                            value={row.description}
                            onChange={e => dispatch(updateRow({ id: row.id, field: 'description', value: e.target.value }))}
                            className="w-full border rounded px-3 py-2 min-h-[80px]"
                            placeholder="Description will appear here"
                          />
                        </div>
                      </div>
                      {rows.length > 1 && (
                        <button onClick={() => dispatch(deleteRow(row.id))} className="text-red-500 hover:text-red-700 p-2 cursor-pointer" type="button">
                          <Trash2 size={20} />
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>

            <button onClick={() => dispatch(addRow())} className="mt-4 flex items-center gap-2 text-font-blue hover:text-font-blue cursor-pointer" type="button">
              <Plus size={20} /> Add Row
            </button>

            <div className="mt-6">
              <button onClick={handleSubmit} disabled={submitting} className="bg-green-600 hover:bg-green-700 disabled:bg-gray-400 disabled:cursor-not-allowed text-white px-6 py-2 rounded-lg cursor-pointer" type="button">
                {submitting ? 'Submitting...' : 'Submit'}
              </button>
            </div>

            {/* Suggestion Results */}
            {suggestions.length > 0 && (
              <div className="mt-6 space-y-6">
                <h3 className="text-xl font-semibold">Reference Suggestions Results</h3>
                {suggestions.map((suggestion: any, si: number) => (
                  <div key={`${suggestion.source_table}-${suggestion.source_column}`} className="border rounded-lg p-4 bg-gray-50">
                    <div className="mb-4">
                      <h4 className="font-semibold text-lg">{suggestion.source_column}</h4>
                      <p className="text-sm text-gray-600">{suggestion.source_column_description}</p>
                      <p className="text-xs text-gray-500 mt-1">Table: {suggestion.source_table}</p>
                    </div>

                    {suggestion.no_results ? (
                      <div className="bg-yellow-50 border border-yellow-200 rounded p-3">
                        <p className="text-yellow-800">{suggestion.no_results_reason}</p>
                      </div>
                    ) : (
                      <div className="space-y-3">
                        {suggestion.dart_suggestions?.map((dart: any, di: number) => (
                          <div key={`${dart.table_name}-${dart.column_name}`} className="bg-white border rounded p-3">
                            <div className="flex justify-between items-start mb-2">
                              <div>
                                <p className="font-medium">{dart.table_name} → {dart.column_name}</p>
                                {dart.rcmnd_sts_dsc && (
                                  <span className={`inline-block px-2 py-1 text-xs rounded mt-1 ${dart.rcmnd_sts_dsc === 'Recommended' ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-800'}`}>
                                    {dart.rcmnd_sts_dsc}
                                  </span>
                                )}
                              </div>
                              <span className="text-xs bg-teal-100 text-font-blue px-2 py-1 rounded">{dart.match_source}</span>
                            </div>
                            <p className="text-sm text-gray-600 mb-1">{dart.column_description}</p>
                            <p className="text-xs text-gray-500 mb-3">{dart.table_description}</p>

                            <DataOverlapAnalysis
                              stateKey={`${si}-${di}`}
                              suggestion={suggestion}
                              dart={dart}
                            />
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <NavigationButtons
        onPrevious={onPrevious}
        onNext={onNext}
        onRetry={handleRetry}
        previousLabel="Previous: Similarity Check"
        nextLabel="Next: Data Anomaly Analysis"
        showNext={true}
        showRetry={true}
        disabled={submitting}
      />
    </div>
  );
}
