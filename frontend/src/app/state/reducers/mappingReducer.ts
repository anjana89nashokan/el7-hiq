export interface MappingState {
  currentStep: number;
  mappingData: any;
  baselineMappingData: any;
  step4Data: any;
  step4StatePath: string | null;
  step3Questions: any[];
  selectedMappingIndex: number | null;
  answers: Record<string, string>;
  feedbacks: Record<string, string>;
  editingCell: { rowIdx: number; colName: string } | null;
  activeTab: 'mappings' | 'questions' | 'issues';
  loading: {
    isSubmitting: boolean;
    isDrafting: boolean;
    isFetchingQuestions: boolean;
    isApplyingStep4: boolean;
    isBuildingSubjectArea: boolean;
  };
  validationErrors: string[];
  subjectAreaBuilt: boolean;
}

export type MappingAction =
  | { type: 'HYDRATE_STATE'; payload: Partial<MappingState> }
  | { type: 'SET_STEP'; payload: number }
  | { type: 'SET_MAPPING_DATA'; payload: any }
  | { type: 'SET_STEP4_DATA'; payload: { step4Data: any; step4StatePath: string | null } }
  | { type: 'SET_QUESTIONS'; payload: any[] }
  | { type: 'SET_SELECTED_INDEX'; payload: number | null }
  | { type: 'UPDATE_ANSWER'; payload: { id: string; value: string } }
  | { type: 'UPDATE_FEEDBACK'; payload: { id: string; value: string } }
  | { type: 'SET_EDITING_CELL'; payload: { rowIdx: number; colName: string } | null }
  | { type: 'SET_ACTIVE_TAB'; payload: 'mappings' | 'questions' | 'issues' }
  | { type: 'SET_LOADING'; payload: Partial<MappingState['loading']> }
  | { type: 'SET_ERRORS'; payload: string[] }
  | { type: 'UPDATE_MAPPING_FIELD'; payload: { rowIdx: number; field: string; value: any } }
  | { type: 'SET_SUBJECT_AREA_BUILT'; payload: boolean };

export const initialState: MappingState = {
  currentStep: 1,
  mappingData: null,
  baselineMappingData: null,
  step4Data: null,
  step4StatePath: null,
  step3Questions: [],
  selectedMappingIndex: 0,
  answers: {},
  feedbacks: {},
  editingCell: null,
  activeTab: 'mappings',
  loading: {
    isSubmitting: false,
    isDrafting: false,
    isFetchingQuestions: false,
    isApplyingStep4: false,
    isBuildingSubjectArea: false,
  },
  validationErrors: [],
  subjectAreaBuilt: false,
};

export function mappingReducer(state: MappingState, action: MappingAction): MappingState {
  switch (action.type) {
    case 'HYDRATE_STATE':
      return {
        ...state,
        ...action.payload,
        loading: { ...state.loading, ...(action.payload.loading || {}) },
      };

    case 'SET_STEP':
      return { ...state, currentStep: action.payload };
    
    case 'SET_MAPPING_DATA':
      return {
        ...state,
        mappingData: action.payload,
        // Keep an immutable baseline snapshot for Step 3.5 diffing (JSON-safe deep clone).
        baselineMappingData: action.payload ? JSON.parse(JSON.stringify(action.payload)) : null,
      };

    case 'SET_STEP4_DATA':
      return {
        ...state,
        step4Data: action.payload.step4Data,
        step4StatePath: action.payload.step4StatePath,
      };
    
    case 'SET_QUESTIONS':
      return { ...state, step3Questions: action.payload };
    
    case 'SET_SELECTED_INDEX':
      return { ...state, selectedMappingIndex: action.payload };
    
    case 'UPDATE_ANSWER':
      return {
        ...state,
        answers: { ...state.answers, [action.payload.id]: action.payload.value }
      };
    
    case 'UPDATE_FEEDBACK':
      return {
        ...state,
        feedbacks: { ...state.feedbacks, [action.payload.id]: action.payload.value }
      };
    
    case 'SET_EDITING_CELL':
      return { ...state, editingCell: action.payload };
    
    case 'SET_ACTIVE_TAB':
      return { ...state, activeTab: action.payload };
    
    case 'SET_LOADING':
      return {
        ...state,
        loading: { ...state.loading, ...action.payload }
      };
    
    case 'SET_ERRORS':
      return { ...state, validationErrors: action.payload };
    
    case 'UPDATE_MAPPING_FIELD': {
      if (!state.mappingData) return state;
      
      const newData = { ...state.mappingData };
      const { rowIdx, field, value } = action.payload;
      
      if (field === "source_column") {
        newData.column_mappings[rowIdx].source_entity = value.source_entity;
        newData.column_mappings[rowIdx].source_field_names = [value.source_column_name];
      } else if (field === "join_condition") {
        if (newData.column_mappings[rowIdx].join_condition) {
          newData.column_mappings[rowIdx].join_condition.join_text = value;
        } else {
          newData.column_mappings[rowIdx].join_condition = { join_text: value };
        }
      } else {
        newData.column_mappings[rowIdx][field] = value;
      }
      
      return { ...state, mappingData: newData, editingCell: null };
    }
    
    case 'SET_SUBJECT_AREA_BUILT':
      return { ...state, subjectAreaBuilt: action.payload };
    
    default:
      return state;
  }
}
