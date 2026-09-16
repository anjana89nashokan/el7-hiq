import { configureStore } from '@reduxjs/toolkit';
import { uploadIdentifierSliceReducer } from './reducers/uploadIdentifierReducers';
import dartReducer from './reducers/dartReducer';
import extractReducer from './reducers/extract/extractReducer';

export { resetAllState } from './actions';

export const store = configureStore({
  reducer: {
    file: uploadIdentifierSliceReducer,
    dart: dartReducer,
    extract: extractReducer,
  },
});

export type AppDispatch = typeof store.dispatch;
export type RootState = ReturnType<typeof store.getState>;
