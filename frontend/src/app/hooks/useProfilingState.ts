import { useState, useRef } from "react";
import type {
  ChatMessage,
  Step,
  DataDictionaryState,
  DartTableEntry,
} from "../state/reducers/profilingResultReducers";
import {
  initialDataDictionaryState,
  initialSteps,
} from "../state/reducers/profilingResultReducers";

const INITIAL_DART_TABLE_ENTRY = { dartTable: "", column: "", isType2: false };
const INITIAL_LOADING_STATES = {
  1: false,
  2: false,
  3: false,
  4: false,
  5: false,
  6: false,
  7: false,
};

export const useProfilingState = () => {
  // UI State
  const [activeTab, setActiveTab] = useState<string>("");
  const [open, setOpen] = useState(false);
  const [currentStep, setCurrentStep] = useState(1);
  const [maxVisitedStep, setMaxVisitedStep] = useState(0);
  const [profileSummaryCollapsed, setProfileSummaryCollapsed] = useState(false);
  const [accordionStates, setAccordionStates] = useState<{ [key: string]: boolean }>({});
  
  // Chat and Messaging State
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [qaMessages, setQaMessages] = useState<ChatMessage[]>([]);
  const [inputMessage, setInputMessage] = useState("");
  const [isInitialized, setIsInitialized] = useState(false);
  
  // Step Data State
  const [initialMessageData, setInitialMessageData] = useState<any[]>([]);
  const [relationshipResponse, setRelationshipResponse] = useState<string>("");
  const [prevRelationshipResponse, setPrevRelationshipResponse] = useState<string>("");
  const [dataDictionaryJson, setDataDictionaryJson] = useState<any>();
  const [dataDictionaryResponse, setDataDictionaryResponse] = useState<string>("");
  const [dataDictionaryState, setDataDictionaryState] = useState<DataDictionaryState>(initialDataDictionaryState);
  const [similarityResponse, setSimilarityResponse] = useState<string>("");
  const [anomalyData, setAnomalyData] = useState<any>();
  const [prevMetadataResponse, setPrevMetadataResponse] = useState<any>();
  
  // Modified Response State
  const [modifiedRelationshipResponse, setModifiedRelationshipResponse] = useState<any>(null);
  const [modifiedDataDictionaryResponse, setModifiedDataDictionaryResponse] = useState<any>(null);
  const [modifiedAnomalyResponse, setModifiedAnomalyResponse] = useState<any>(null);
  const [modifiedMetadataResponse, setModifiedMetadataResponse] = useState<any>(null);
  
  // Similarity Check State
  const [dartTableEntries, setDartTableEntries] = useState<DartTableEntry[]>([INITIAL_DART_TABLE_ENTRY]);
  const [defaultDatabaseName, setDefaultDatabaseName] = useState<string>("");
  const [databaseName, setDatabaseName] = useState<string>("");
  const [tableSchemaFields, setTableSchemaFields] = useState<any[]>([]);
  const [dynamicFilters, setDynamicFilters] = useState<any[]>([]);
  const [tableSchemaError, setTableSchemaError] = useState<string>("");
  
  // Control State
  const [skippedSteps, setSkippedSteps] = useState<Set<number>>(new Set());
  const [loadingStates, setLoadingStates] = useState(INITIAL_LOADING_STATES);
  const [apiInProgress, setApiInProgress] = useState(false);
  const [stepApiCallTracker, setStepApiCallTracker] = useState<Set<number>>(new Set());
  const [steps, setSteps] = useState<Step[]>(initialSteps);

  // Refs
  const hasSentRef = useRef(false);
  const relationshipRetryRef = useRef<(() => void) | null>(null);
  const dataDictionaryRetryRef = useRef<(() => void) | null>(null);

  return {
    // UI State
    activeTab, setActiveTab,
    open, setOpen,
    currentStep, setCurrentStep,
    maxVisitedStep, setMaxVisitedStep,
    profileSummaryCollapsed, setProfileSummaryCollapsed,
    accordionStates, setAccordionStates,
    
    // Chat State
    messages, setMessages,
    qaMessages, setQaMessages,
    inputMessage, setInputMessage,
    isInitialized, setIsInitialized,
    
    // Step Data State
    initialMessageData, setInitialMessageData,
    relationshipResponse, setRelationshipResponse,
    prevRelationshipResponse, setPrevRelationshipResponse,
    dataDictionaryJson, setDataDictionaryJson,
    dataDictionaryResponse, setDataDictionaryResponse,
    dataDictionaryState, setDataDictionaryState,
    similarityResponse, setSimilarityResponse,
    anomalyData, setAnomalyData,
    prevMetadataResponse, setPrevMetadataResponse,
    
    // Modified Response State
    modifiedRelationshipResponse, setModifiedRelationshipResponse,
    modifiedDataDictionaryResponse, setModifiedDataDictionaryResponse,
    modifiedAnomalyResponse, setModifiedAnomalyResponse,
    modifiedMetadataResponse, setModifiedMetadataResponse,
    
    // Similarity Check State
    dartTableEntries, setDartTableEntries,
    defaultDatabaseName, setDefaultDatabaseName,
    databaseName, setDatabaseName,
    tableSchemaFields, setTableSchemaFields,
    dynamicFilters, setDynamicFilters,
    tableSchemaError, setTableSchemaError,
    
    // Control State
    skippedSteps, setSkippedSteps,
    loadingStates, setLoadingStates,
    apiInProgress, setApiInProgress,
    stepApiCallTracker, setStepApiCallTracker,
    steps, setSteps,
    
    // Refs
    hasSentRef,
    relationshipRetryRef,
    dataDictionaryRetryRef,
  };
};
