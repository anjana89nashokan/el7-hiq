import { createSlice } from '@reduxjs/toolkit';
import type { PayloadAction } from '@reduxjs/toolkit';

export interface DownloadState {
  isDownloading: boolean;
  progress: {
    current: number;
    total: number;
    message: string;
  };
  selectedFiles: string[];
  downloadType: 'individual' | 'zip' | null;
  error: string | null;
}

const initialState: DownloadState = {
  isDownloading: false,
  progress: {
    current: 0,
    total: 0,
    message: ''
  },
  selectedFiles: [],
  downloadType: null,
  error: null
};

const downloadSlice = createSlice({
  name: 'download',
  initialState,
  reducers: {
    startDownload: (state, action: PayloadAction<{ files: string[]; type: 'individual' | 'zip' }>) => {
      state.isDownloading = true;
      state.selectedFiles = action.payload.files;
      state.downloadType = action.payload.type;
      state.error = null;
      state.progress = { current: 0, total: action.payload.files.length, message: 'Initializing...' };
    },
    
    updateProgress: (state, action: PayloadAction<{ current: number; total: number; message: string }>) => {
      state.progress = action.payload;
    },
    
    completeDownload: (state) => {
      state.isDownloading = false;
      state.downloadType = null;
      state.selectedFiles = [];
      state.progress = { current: 0, total: 0, message: '' };
    },
    
    setDownloadError: (state, action: PayloadAction<string>) => {
      state.error = action.payload;
      state.isDownloading = false;
    },
    
    clearDownloadError: (state) => {
      state.error = null;
    },
    
    setSelectedFiles: (state, action: PayloadAction<string[]>) => {
      state.selectedFiles = action.payload;
    }
  }
});

export const {
  startDownload,
  updateProgress,
  completeDownload,
  setDownloadError,
  clearDownloadError,
  setSelectedFiles
} = downloadSlice.actions;

export default downloadSlice.reducer;