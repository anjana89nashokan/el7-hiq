import { createSlice, createAsyncThunk } from '@reduxjs/toolkit';
import { resetAllState } from '../actions';
import { uploadFilesApi } from '../../end-points/uploadIdentifier';

// Type definitions
interface UploadPayload {
  project_context: {
    project_name: string;
  };
  vendor_details: {
    name: string;
    contact_person: string;
    file_delivery_frequency: number;
  };
  brd_details?: {
    filename: string;
    description: string;
  };
  erwin_model?: {
    filename: string;
    description: string;
  };
}

interface UploadFilesParams {
  files: File[];
  payload: UploadPayload;
  brdFile?: File | null;
  erwinModelFile?: File | null;
}

interface UploadState {
  loading: boolean;
  error: string | null;
  data: any;
  profilingInProgress: boolean;
}

// Initial state
export const initialState: UploadState = {
  loading: false,
  error: null,
  data: null,
  profilingInProgress: false,
};

// Async thunk for Redux Toolkit
export const uploadFiles = createAsyncThunk(
  'uploadIdentifier/uploadFiles',
  async (params: UploadFilesParams, thunkAPI) => {
    try {
      const { files, payload } = params;
      // const { files, payload, brdFile, erwinModelFile } = params;
      const response = await uploadFilesApi(files, payload);
      return response;
    } catch (error: any) {
      return thunkAPI.rejectWithValue(error.message);
    }
  }
);

// Simple reducer for useReducer hook
export const uploadIdentifierReducer = (
  state: UploadState,
  action: any
): UploadState => {
  switch (action.type) {
    case 'UPLOAD_START':
      return {
        ...state,
        loading: true,
        error: null,
      };
    case 'START_PROFILING':
      return {
        ...state,
        profilingInProgress: true,
      };
    case 'STOP_PROFILING':
      return {
        ...state,
        profilingInProgress: false,
      };
    case 'UPLOAD_SUCCESS':
      return {
        ...state,
        loading: false,
        data: action.payload,
        error: null,
      };
    case 'UPLOAD_ERROR':
      return {
        ...state,
        loading: false,
        error: action.payload,
      };
    case 'RESET_STATE':
      return initialState;
    default:
      return state;
  }
};

// Redux Toolkit slice (optional - for Redux integration)
const uploadIdentifierSlice = createSlice({
  name: 'uploadIdentifier',
  initialState,
  reducers: {
    resetState: () => initialState,
  },
  extraReducers: (builder) => {
    builder
      .addCase(resetAllState, () => initialState)
      .addCase(uploadFiles.pending, (state) => {
        state.loading = true;
        state.error = null;
      })
      .addCase(uploadFiles.fulfilled, (state, action) => {
        state.loading = false;
        state.data = action.payload;
        state.error = null;
      })
      .addCase(uploadFiles.rejected, (state, action) => {
        state.loading = false;
        state.error = action.payload as string;
      });
  },
});

export const { resetState } = uploadIdentifierSlice.actions;
export const uploadIdentifierSliceReducer = uploadIdentifierSlice.reducer;