import { createSlice } from "@reduxjs/toolkit";

const initialState = {
  loadingMessage: "",
  inlineLoader: false,
}

export const commonSlice = createSlice({
  name: "common",
  initialState,
  reducers: {
    updateLoadingMessage: (state, action) => {
      state.loadingMessage = action.payload.message;
    },
    setInlineLoader: (state, action) => {
      state.inlineLoader = action.payload;
    },
    apiInitiated: (state) => {
      state.inlineLoader = true; // Set inline loader to true when API is initiated
    },
    apiCompleted: (state) => {
      state.inlineLoader = false; // Set inline loader to false when API is completed
    },
  },
});

// Action creators are generated for each case reducer function
export const { updateLoadingMessage, setInlineLoader, apiInitiated, apiCompleted } = commonSlice.actions;

export default commonSlice.reducer;
