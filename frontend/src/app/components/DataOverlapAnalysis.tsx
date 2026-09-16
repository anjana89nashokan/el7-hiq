import { useDispatch, useSelector } from 'react-redux';
import { Plus } from 'lucide-react';
import { checkDataOverlap, getTableSchema } from '../end-points/dartApi';
import {
  setOverlapState,
  addOverlapFilterRow,
  EMPTY_OVERLAP,
} from '../state/reducers/dartReducer';
import type { DynamicFilterRow } from '../state/reducers/dartReducer';
import FilterTable from './FilterTable';
import OverlapResultDisplay from './OverlapResultDisplay';

interface Props {
  readonly stateKey: string;
  readonly suggestion: any;
  readonly dart: any;
}

export default function DataOverlapAnalysis({ stateKey, suggestion, dart }: Props) {
  const dispatch = useDispatch();
  const { databaseName, defaultDatabaseName, overlapStates } = useSelector((state: any) => state.dart);
  const overlap = overlapStates[stateKey] ?? EMPTY_OVERLAP;

  const handleCheckTableSchema = async () => {
    const db = databaseName || defaultDatabaseName;
    dispatch(setOverlapState({ key: stateKey, patch: { schema: null, loading: true, error: '' } }));
    try {
      const schema = await getTableSchema(overlap.tableName, db);
      const isType2 = schema?.isType2 === true || schema?.isType2 === 'true';
      const activeRecordFilters: DynamicFilterRow[] = isType2
        ? (schema.suggested_active_record_filters ?? []).map((f: any) => ({
            table_name: schema.table_name ?? '',
            fieldname: f.left_field ?? '',
            type: f.left_field_type ?? '',
            operator: f.operator ?? '',
            value: f.right_value != null ? [String(f.right_value)] : [],
          }))
        : [];
      dispatch(setOverlapState({ key: stateKey, patch: { schema, loading: false, useActiveRecordFilter: isType2, activeRecordFilters, dynamicFilters: [] } }));
    } catch (err: any) {
      dispatch(setOverlapState({ key: stateKey, patch: { schema: null, loading: false, error: err?.response?.data?.detail || err?.message || 'Failed to fetch schema' } }));
    }
  };

  const handleCheckDataOverlap = async () => {
    const db = databaseName || defaultDatabaseName;
    const activeRecordFilters = overlap.useActiveRecordFilter
      ? (overlap.activeRecordFilters ?? []).map((f: any) => ({
          left_field: f.fieldname,
          left_field_type: f.type,
          operator: f.operator,
          right_type: 'column',
          right_value: f.value[0] ?? '',
        }))
      : [];

    const payload = {
      source_table: suggestion.source_table,
      source_dataset: defaultDatabaseName,
      source_column: suggestion.source_column,
      dart_table_name: overlap.tableName || dart.table_name,
      dart_dataset: db,
      dart_column: dart.column_name,
      active_record_filters: activeRecordFilters,
      filters: overlap.dynamicFilters ?? [],
    };

    dispatch(setOverlapState({ key: stateKey, patch: { overlapLoading: true, overlapError: null, overlapResult: null } }));
    try {
      const result = await checkDataOverlap(payload);
      dispatch(setOverlapState({ key: stateKey, patch: { overlapLoading: false, overlapResult: result } }));
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      const overlapError = !detail
        ? { message: err?.message || 'Failed to check data overlap' }
        : typeof detail === 'string'
        ? { message: detail }
        : { message: detail.message, errors: detail.errors ?? [] };
      dispatch(setOverlapState({ key: stateKey, patch: { overlapLoading: false, overlapError } }));
    }
  };

  const handleActiveRecordFilterToggle = (checked: boolean) => {
    const activeRecordFilters: DynamicFilterRow[] = checked
      ? (overlap.schema?.suggested_active_record_filters ?? []).map((f: any) => ({
          table_name: overlap.schema?.table_name ?? '',
          fieldname: f.left_field ?? '',
          type: f.left_field_type ?? '',
          operator: f.operator ?? '',
          value: f.right_value != null ? [String(f.right_value)] : [],
        }))
      : [];
    dispatch(setOverlapState({ key: stateKey, patch: { useActiveRecordFilter: checked, activeRecordFilters } }));
  };

  return (
    <div className="border-t pt-3 mt-2">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-xs font-semibold text-gray-700 shrink-0">Data Overlap Analysis</span>
        <input
          type="text"
          value={overlap.tableName}
          onChange={e => dispatch(setOverlapState({ key: stateKey, patch: { tableName: e.target.value } }))}
          className="flex-1 px-2 py-1 border border-gray-300 rounded text-xs font-mono focus:outline-none focus:ring-1 focus:ring-brand-primary"
          placeholder="Table name"
        />
        <button
          type="button"
          onClick={handleCheckTableSchema}
          className="text-xs px-3 py-1 bg-brand-primary hover:bg-brand-primary-hover disabled:bg-gray-400 text-white rounded shrink-0"
        >
          {overlap.loading ? 'Loading...' : 'Check Table Schema'}
        </button>
      </div>

      {overlap.error && <p className="text-xs text-red-600 mt-1">{overlap.error}</p>}

      {overlap.schema && (
        <div className="mt-3 space-y-3">
          {/* Active Record Filters */}
          <div className="border border-gray-200 rounded p-3">
            <label className="flex items-center gap-2 text-xs font-semibold text-gray-700 cursor-pointer mb-2">
              <input
                type="checkbox"
                checked={overlap.useActiveRecordFilter}
                onChange={e => handleActiveRecordFilterToggle(e.target.checked)}
                className="w-4 h-4"
              />
              Suggested Active Record Filters
            </label>
            {overlap.useActiveRecordFilter && (
              <>
                <FilterTable
                  filters={overlap.activeRecordFilters}
                  columns={overlap.schema?.columns ?? []}
                  stateKey={stateKey}
                  field="activeRecordFilters"
                />
                <button
                  type="button"
                  onClick={() => dispatch(addOverlapFilterRow({ key: stateKey, field: 'activeRecordFilters', row: { table_name: overlap.schema?.table_name ?? '', fieldname: '', type: '', operator: '', value: [] } }))}
                  className="mt-2 flex items-center gap-1 text-font-blue hover:text-font-blue text-xs"
                >
                  <Plus size={14} /> Add Row
                </button>
              </>
            )}
          </div>

          {/* Dynamic Filters */}
          <div className="border border-gray-200 rounded p-3">
            <p className="text-xs font-semibold text-gray-700 mb-2">Dynamic Filters</p>
            <FilterTable
              filters={overlap.dynamicFilters}
              columns={overlap.schema?.columns ?? []}
              stateKey={stateKey}
              field="dynamicFilters"
            />
            <button
              type="button"
              onClick={() => dispatch(addOverlapFilterRow({ key: stateKey, field: 'dynamicFilters', row: { table_name: overlap.schema?.table_name ?? '', fieldname: '', type: '', operator: '', value: [] } }))}
              className="mt-2 flex items-center gap-1 text-font-blue hover:text-font-blue text-xs"
            >
              <Plus size={14} /> Add Row
            </button>
          </div>

          {/* Check Data Overlap */}
          <button
            type="button"
            onClick={handleCheckDataOverlap}
            disabled={overlap.overlapLoading}
            className="text-xs px-4 py-1.5 bg-green-600 hover:bg-green-700 disabled:bg-gray-400 text-white rounded"
          >
            {overlap.overlapLoading ? 'Checking...' : 'Check Data Overlap'}
          </button>

          {overlap.overlapError && (
            <div className="mt-1 text-xs text-red-600 space-y-0.5">
              <p>{overlap.overlapError.message}</p>
              {overlap.overlapError.errors?.map((e: any, i: any) => (
                <p key={i} className="pl-2 before:content-['•'] before:mr-1">{e}</p>
              ))}
            </div>
          )}

          {overlap.overlapResult && <OverlapResultDisplay result={overlap.overlapResult} />}
        </div>
      )}
    </div>
  );
}
