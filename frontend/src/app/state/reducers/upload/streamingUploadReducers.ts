import { createSlice, createAsyncThunk } from "@reduxjs/toolkit";
import {
  startStreamingProfiling,
  validateStreamingForm,
} from "../../../end-points/upload/streamingUploadApi";
import type { StreamingUploadData } from "../../../end-points/upload/streamingUploadApi";

// Type definitions
export interface StreamingUploadState {
  // Table Names State
  tableNames: string[];
  currentTableName: string;
  tableNameError: string;

  //Database Name State
  databaseName: string;
  defaultDatabaseName: string;

  // File States
  dataDictionaryFile: File | null;
  brdFile: File | null;
  erwinModelFile: File | null;

  // UI Toggle States
  showDataDictionarySection: boolean;
  showBrdSection: boolean;
  showErwinModelSection: boolean;
  showFilesDetailsSection: boolean;

  // Form States
  projectName: string;
  vendorName: string;
  contactPerson: string;
  contactName: string;
  phoneNumber: string;
  serverName: string;
  deliveryFrequency: string;
  frequencyMode: string;
  transferMethod: string;
  compressionType: string;
  populationType: string;
  headerRecordNumber: string;
  trailerRecordNumber: string;
  quoteIndicator: string;
  dateTimestampFormat: string;
  receiveFileWhenNoData: string;
  emailNotificationDl: string;
  assumptions: string;
  dependencies: string;
  brdDescription: string;
  erwinModelDescription: string;

  // Validation Response Data
  datasetValue: string;

  // Status States
  isLoading: boolean;
  error: string;
  validationErrors: string[];
}

// Initial state
export const initialStreamingUploadState: StreamingUploadState = {
  tableNames: [],
  currentTableName: "",
  tableNameError: "",
  databaseName: "",
  defaultDatabaseName: "",
  dataDictionaryFile: null,
  brdFile: null,
  erwinModelFile: null,
  showDataDictionarySection: false,
  showBrdSection: false,
  showErwinModelSection: false,
  showFilesDetailsSection: false,
  projectName: "",
  vendorName: "",
  contactPerson: "",
  contactName: "",
  phoneNumber: "",
  serverName: "",
  deliveryFrequency: "",
  frequencyMode: "",
  transferMethod: "",
  compressionType: "",
  populationType: "",
  headerRecordNumber: "",
  trailerRecordNumber: "",
  quoteIndicator: "",
  dateTimestampFormat: "",
  receiveFileWhenNoData: "0",
  emailNotificationDl: "",
  assumptions: "",
  dependencies: "",
  brdDescription: "",
  erwinModelDescription: "",
  datasetValue: "",
  isLoading: false,
  error: "",
  validationErrors: [],
};

// Async thunk for streaming profiling
export const submitStreamingProfiling = createAsyncThunk(
  "streamingUpload/submit",
  async (data: StreamingUploadData, thunkAPI) => {
    try {
      const validationErrors = validateStreamingForm(data);
      if (validationErrors.length > 0) {
        return thunkAPI.rejectWithValue(validationErrors[0]);
      }

      const result = await startStreamingProfiling(data);
      return result;
    } catch (error: any) {
      return thunkAPI.rejectWithValue(error.message);
    }
  },
);

// Simple reducer for useReducer hook
export const streamingUploadReducer = (
  state: StreamingUploadState,
  action: any,
): StreamingUploadState => {
  switch (action.type) {
    case "SET_TABLE_NAMES":
      return { ...state, tableNames: action.payload };
    case "SET_DATABASE_NAME":
      return { ...state, databaseName: action.payload };
    case "SET_DEFAULT_DATABASE_NAME":
      return { ...state, defaultDatabaseName: action.payload };
    case "SET_CURRENT_TABLE_NAME":
      return { ...state, currentTableName: action.payload };
    case "SET_TABLE_NAME_ERROR":
      return { ...state, tableNameError: action.payload };
    case "SET_FILE":
      return { ...state, [action.payload.field]: action.payload.file };
    case "SET_TOGGLE":
      return { ...state, [action.payload.field]: action.payload.value };
    case "SET_FORM_FIELD":
      return { ...state, [action.payload.field]: action.payload.value };
    case "SET_LOADING":
      return { ...state, isLoading: action.payload };
    case "SET_ERROR":
      return { ...state, error: action.payload };
    case "SET_VALIDATION_ERRORS":
      return { ...state, validationErrors: action.payload };
    case "SET_DATASET_VALUE":
      return { ...state, datasetValue: action.payload };
    case "RESET_STATE":
      return initialStreamingUploadState;
    default:
      return state;
  }
};

// Redux Toolkit slice
const streamingUploadSlice = createSlice({
  name: "streamingUpload",
  initialState: initialStreamingUploadState,
  reducers: {
    setTableNames: (state, action) => {
      state.tableNames = action.payload;
    },
    setCurrentTableName: (state, action) => {
      state.currentTableName = action.payload;
    },
    setTableNameError: (state, action) => {
      state.tableNameError = action.payload;
    },
    setFile: (state, action) => {
      const { field, file } = action.payload;
      (state as any)[field] = file;
    },
    setToggle: (state, action) => {
      const { field, value } = action.payload;
      (state as any)[field] = value;
    },
    setFormField: (state, action) => {
      const { field, value } = action.payload;
      (state as any)[field] = value;
    },
    setValidationErrors: (state, action) => {
      state.validationErrors = action.payload;
    },
    resetState: () => initialStreamingUploadState,
  },
  extraReducers: (builder) => {
    builder
      .addCase(submitStreamingProfiling.pending, (state) => {
        state.isLoading = true;
        state.error = "";
      })
      .addCase(submitStreamingProfiling.fulfilled, (state) => {
        state.isLoading = false;
        state.error = "";
      })
      .addCase(submitStreamingProfiling.rejected, (state, action) => {
        state.isLoading = false;
        state.error = action.payload as string;
      });
  },
});

export const {
  setTableNames,
  setCurrentTableName,
  setTableNameError,
  setFile,
  setToggle,
  setFormField,
  setValidationErrors,
  resetState,
} = streamingUploadSlice.actions;

export const streamingUploadSliceReducer = streamingUploadSlice.reducer;
