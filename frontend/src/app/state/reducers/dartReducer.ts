import { createSlice } from '@reduxjs/toolkit';
import { resetAllState } from '../actions';
import { extractDataDictionaryRows } from '../../utils/profilingUtils';

interface DartRow {
  id: string;
  tableName: string;
  attributeName: string;
  description: string;
}

export interface DynamicFilterRow {
  table_name: string;
  fieldname: string;
  type: string;
  operator: string;
  value: string[];
}

export interface OverlapResult {
  source_table: string;
  source_column: string;
  dart_table: string;
  dart_column: string;
  active_record_filter_applied: boolean;
  source_values_checked: number;
  data_overlap_percent: number;
  overlap_count: number;
  overlap_summary: string;
  sample_matching_values: string[];
  sample_non_matching_values: string[];
  timestamp: string;
}

export interface OverlapError {
  message: string;
  errors?: string[];
}

export interface OverlapState {
  tableName: string;
  schema: any;
  loading: boolean;
  error: string;
  useActiveRecordFilter: boolean;
  activeRecordFilters: DynamicFilterRow[];
  dynamicFilters: DynamicFilterRow[];
  overlapResult: OverlapResult | null;
  overlapLoading: boolean;
  overlapError: OverlapError | null;
}

export const EMPTY_OVERLAP: OverlapState = {
  tableName: '',
  schema: null,
  loading: false,
  error: '',
  useActiveRecordFilter: false,
  activeRecordFilters: [],
  dynamicFilters: [],
  overlapResult: null,
  overlapLoading: false,
  overlapError: null,
};

interface DartState {
  rows: DartRow[];
  toolResponse: any[];
  step3Response: any;
  dartSuggestionResponse: any;
  databaseName: string;
  defaultDatabaseName: string;
  tableNames: string[];
  currentTableName: string;
  tableNameError: string;
  dbValidating: boolean;
  dbError: string;
  dbValidated: boolean;
  tableSchemas: Record<string, any>;
  selectedTables: Record<string, any>;
  activeRecordFilters: Array<{ id: string; left_field: string; left_field_type: string; operator: string; right_type: string; right_value: string | null }>;
  dynamicFilters: Array<{ id: string; table_name: string; fieldname: string; type: string; operator: string; value: string[] }>;
  overlapData: any;
  overlapLoading: boolean;
  overlapStates: Record<string, OverlapState>;
}

const initialState: DartState = {
  rows: [{ id: '1', tableName: '', attributeName: '', description: '' }],
  toolResponse: [],
  step3Response: null,
  dartSuggestionResponse: null,
  databaseName: '',
  defaultDatabaseName: '',
  tableNames: [],
  currentTableName: '',
  tableNameError: '',
  dbValidating: false,
  dbError: '',
  dbValidated: false,
  tableSchemas: {},
  selectedTables: {},
  activeRecordFilters: [],
  dynamicFilters: [],
  overlapData: null,
  overlapLoading: false,
  overlapStates: {},
};

const dartSlice = createSlice({
  name: 'dart',
  initialState,
  reducers: {
    setToolResponse: (state, action) => { state.toolResponse = action.payload; },
    setStep3Response: (state, action) => {
      state.step3Response = action.payload;
      const raw = action.payload?.tool_response ?? action.payload;
      const rows = extractDataDictionaryRows(raw);
      // Keep existing rows when unrelated payloads (e.g. similarity JSON) arrive.
      if (rows.length > 0) {
        state.toolResponse = [rows];
      }
    },
    setDartSuggestionResponse: (state, action) => { state.dartSuggestionResponse = action.payload; },
    addRow: (state) => { state.rows.push({ id: Date.now().toString(), tableName: '', attributeName: '', description: '' }); },
    deleteRow: (state, action) => { state.rows = state.rows.filter(row => row.id !== action.payload); },
    updateRow: (state, action) => {
      const row = state.rows.find(r => r.id === action.payload.id);
      if (row) (row as any)[action.payload.field] = action.payload.value;
    },
    resetRows: (state) => { state.rows = [{ id: '1', tableName: '', attributeName: '', description: '' }]; },
    setDatabaseName: (state, action) => { state.databaseName = action.payload; },
    setDefaultDatabaseName: (state, action) => { state.defaultDatabaseName = action.payload; },
    setTableNames: (state, action) => { state.tableNames = action.payload; },
    setCurrentTableName: (state, action) => { state.currentTableName = action.payload; },
    setTableNameError: (state, action) => { state.tableNameError = action.payload; },
    setDbValidating: (state, action) => { state.dbValidating = action.payload; },
    setDbError: (state, action) => { state.dbError = action.payload; },
    setDbValidated: (state, action) => { state.dbValidated = action.payload; },
    addTableName: (state, action) => {
      state.tableNames.push(action.payload);
      state.currentTableName = '';
      state.tableNameError = '';
      state.dbValidated = false;
    },
    removeTableName: (state, action) => {
      state.tableNames = state.tableNames.filter((_, index) => index !== action.payload);
      state.dbValidated = false;
    },
    setTableSchemas: (state, action) => { state.tableSchemas = action.payload; },
    setSelectedTables: (state, action) => { state.selectedTables = action.payload; },
    addActiveRecordFilter: (state, action) => { state.activeRecordFilters.push(action.payload); },
    updateActiveRecordFilter: (state, action) => {
      const { id, field, value } = action.payload;
      const filter = state.activeRecordFilters.find(f => f.id === id);
      if (filter) (filter as any)[field] = value;
    },
    removeActiveRecordFilter: (state, action) => {
      state.activeRecordFilters = state.activeRecordFilters.filter(f => f.id !== action.payload);
    },
    setActiveRecordFilters: (state, action) => { state.activeRecordFilters = action.payload; },
    addDynamicFilter: (state, action) => { state.dynamicFilters.push(action.payload); },
    updateDynamicFilter: (state, action) => {
      const { id, field, value } = action.payload;
      const filter = state.dynamicFilters.find(f => f.id === id);
      if (filter) (filter as any)[field] = value;
    },
    removeDynamicFilter: (state, action) => {
      state.dynamicFilters = state.dynamicFilters.filter(f => f.id !== action.payload);
    },
    setOverlapData: (state, action) => { state.overlapData = action.payload; },
    setOverlapLoading: (state, action) => { state.overlapLoading = action.payload; },
    initOverlapStates: (state, action: { payload: Record<string, OverlapState> }) => {
      state.overlapStates = action.payload;
    },
    setOverlapState: (state, action: { payload: { key: string; patch: Partial<OverlapState> } }) => {
      const { key, patch } = action.payload;
      state.overlapStates[key] = { ...(state.overlapStates[key] ?? EMPTY_OVERLAP), ...patch };
    },
    setOverlapFilterRow: (state, action: { payload: { key: string; field: 'activeRecordFilters' | 'dynamicFilters'; index: number; patch: Partial<DynamicFilterRow> } }) => {
      const { key, field, index, patch } = action.payload;
      const os = state.overlapStates[key] ?? { ...EMPTY_OVERLAP };
      const filters = [...(os[field] ?? [])];
      filters[index] = { ...filters[index], ...patch };
      state.overlapStates[key] = { ...os, [field]: filters };
    },
    removeOverlapFilterRow: (state, action: { payload: { key: string; field: 'activeRecordFilters' | 'dynamicFilters'; index: number } }) => {
      const { key, field, index } = action.payload;
      const os = state.overlapStates[key] ?? { ...EMPTY_OVERLAP };
      state.overlapStates[key] = { ...os, [field]: (os[field] ?? []).filter((_, i) => i !== index) };
    },
    addOverlapFilterRow: (state, action: { payload: { key: string; field: 'activeRecordFilters' | 'dynamicFilters'; row: DynamicFilterRow } }) => {
      const { key, field, row } = action.payload;
      const os = state.overlapStates[key] ?? { ...EMPTY_OVERLAP };
      state.overlapStates[key] = { ...os, [field]: [...(os[field] ?? []), row] };
    },
    resetDartState: (state) => {
      Object.assign(state, {
        ...initialState,
        toolResponse: state.toolResponse,
        defaultDatabaseName: state.defaultDatabaseName,
      });
    },
  },
  extraReducers: (builder) => {
    builder.addCase(resetAllState, () => initialState);
  },
});

export const {
  setToolResponse,
  setStep3Response,
  setDartSuggestionResponse,
  addRow,
  deleteRow,
  updateRow,
  resetRows,
  setDatabaseName,
  setDefaultDatabaseName,
  setTableNames,
  setCurrentTableName,
  setTableNameError,
  setDbValidating,
  setDbError,
  setDbValidated,
  addTableName,
  removeTableName,
  setTableSchemas,
  setSelectedTables,
  addActiveRecordFilter,
  updateActiveRecordFilter,
  removeActiveRecordFilter,
  setActiveRecordFilters,
  addDynamicFilter,
  updateDynamicFilter,
  removeDynamicFilter,
  setOverlapData,
  setOverlapLoading,
  initOverlapStates,
  setOverlapState,
  setOverlapFilterRow,
  removeOverlapFilterRow,
  addOverlapFilterRow,
  resetDartState,
} = dartSlice.actions;
export default dartSlice.reducer;
