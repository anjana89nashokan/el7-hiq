import type { Step, DataDictionaryState } from "./profilingResultReducers";
import { hasProfilingToolResult } from "../../end-points/profilingResultApi";

export const updateStepCompletion = (
  step: Step,
  data: {
    profilingData: any;
    messages: any[];
    initialMessageData: any[];
    relationshipResponse: string;
    dataDictionaryJson: any;
    dataDictionaryState: DataDictionaryState;
    similarityResponse: string;
    anomalyData: any;
    prevMetadataResponse: any;
    dartSuggestionResponse: any;
  }
) => {
  switch (step.id) {
    case 1:
      return {
        ...step,
        completed:
          !!data.profilingData &&
          (data.messages.some((m) => m.isBot) ||
            data.initialMessageData.some((item) => hasProfilingToolResult(item))),
      };
    case 2:
      return { ...step, completed: !!data.relationshipResponse };
    case 3:
      return {
        ...step,
        completed: !!data.dataDictionaryJson || data.dataDictionaryState.isCompleted,
      };
    case 4:
      return { ...step, completed: !!data.similarityResponse };
    case 5:
      return { ...step, completed: !!data.dartSuggestionResponse };
    case 6:
      return { ...step, completed: !!data.anomalyData };
    case 7:
      return { ...step, completed: !!data.prevMetadataResponse };
    case 8:
      // Step 8 completes only when the user clicks Finish, not when data is present.
      return step;
    default:
      return step;
  }
};

export const resetStepData = (fromStep: number) => {
  const resetData: any = {};

  if (fromStep <= 1) {
    resetData.messages = [];
    resetData.initialMessageData = [];
    resetData.isInitialized = false;
  }
  if (fromStep <= 2) {
    resetData.relationshipResponse = "";
    resetData.prevRelationshipResponse = "";
    resetData.modifiedRelationshipResponse = null;
  }
  if (fromStep <= 3) {
    resetData.dataDictionaryJson = undefined;
    resetData.dataDictionaryResponse = "";
    resetData.modifiedDataDictionaryResponse = null;
  }
  if (fromStep <= 4) {
    resetData.similarityResponse = "";
    resetData.dartTableEntries = [{ dartTable: "", column: "", isType2: false }];
    resetData.skippedSteps = new Set();
  }
  if (fromStep <= 5) {
    resetData.anomalyData = undefined;
    resetData.modifiedAnomalyResponse = null;
  }
  if (fromStep <= 6) {
    resetData.prevMetadataResponse = undefined;
    resetData.modifiedMetadataResponse = null;
  }

  return resetData;
};
