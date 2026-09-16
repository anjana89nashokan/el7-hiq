import { useCallback } from "react";
import { checkSimilarity } from "../end-points/profilingResultApi";
import { validateDatasetTables, getTableSchema } from "../end-points/databaseApi";
import { updateDartTableEntry as updateEntry } from "../state/reducers/profilingResultReducers";
import type { DartTableEntry } from "../state/reducers/profilingResultReducers";

interface UseProfilingHandlersProps {
  apiInProgress: boolean;
  similarityResponse: string;
  profilingData: any;
  dartTableEntries: DartTableEntry[];
  databaseName: string;
  defaultDatabaseName: string;
  dynamicFilters: any[];
  setApiInProgress: (value: boolean) => void;
  setLoadingStates: (fn: (prev: any) => any) => void;
  setSimilarityResponse: (value: string) => void;
  setDatabaseName: (value: string) => void;
  setSteps: (fn: (prev: any) => any) => void;
  setTableSchemaError: (value: string) => void;
  setTableSchemaFields: (fn: (prev: any[]) => any[]) => void;
  setDartTableEntries: (fn: (prev: DartTableEntry[]) => DartTableEntry[]) => void;
  getStoredSession: () => any;
}

export const useProfilingHandlers = ({
  apiInProgress,
  similarityResponse,
  profilingData,
  dartTableEntries,
  databaseName,
  defaultDatabaseName,
  dynamicFilters,
  setApiInProgress,
  setLoadingStates,
  setSimilarityResponse,
  setDatabaseName,
  setSteps,
  setTableSchemaError,
  setTableSchemaFields,
  setDartTableEntries,
  getStoredSession,
}: UseProfilingHandlersProps) => {
  const handleSimilarityCheck = useCallback(async () => {
    if (apiInProgress || similarityResponse) return;
    
    setApiInProgress(true);
    setLoadingStates((prev) => ({ ...prev, 4: true }));
    
    try {
      const sourceTables = profilingData?.successful_uploads
        ?.filter((file: any) => file != null)
        ?.map((file: any) => file.table_name) || [];
      
      const response = await checkSimilarity(
        dartTableEntries,
        sourceTables,
        getStoredSession(),
        dynamicFilters,
        databaseName || defaultDatabaseName,
      );
      
      if (!databaseName) setDatabaseName(defaultDatabaseName);
      
      setSimilarityResponse(response);

      setSteps((prev) =>
        prev.map((step: any) =>
          step.id === 4 ? { ...step, completed: true } : step,
        ),
      );
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : "Unknown error occurred";
      setSimilarityResponse(`Error occurred during similarity check: ${errorMessage}`);
    } finally {
      setLoadingStates((prev) => ({ ...prev, 4: false }));
      setApiInProgress(false);
    }
  }, [apiInProgress, similarityResponse, profilingData?.successful_uploads, dartTableEntries, getStoredSession, dynamicFilters, databaseName, defaultDatabaseName, setApiInProgress, setLoadingStates, setSimilarityResponse, setDatabaseName, setSteps]);

  const getIndividualTableSchema = useCallback(async (dartTable: string, index: number) => {
    try {
      const schemaData = await getTableSchema(
        dartTable,
        databaseName || defaultDatabaseName
      );

      setTableSchemaFields((prev) => [...prev, schemaData]);
      
      setDartTableEntries((prev) =>
        updateEntry(prev, index, "isType2", schemaData.isType2 || false),
      );
      
      if (schemaData.isType2) {
        setDartTableEntries((prev) =>
          updateEntry(prev, index, "dartTable", prev[index].dartTable + "_cur"),
        );
      }
    } catch {
      // Skip individual table on error
    }
  }, [databaseName, defaultDatabaseName, setTableSchemaFields, setDartTableEntries]);

  const handleValidateDatabase = useCallback(async () => {
    if (!databaseName && !defaultDatabaseName) {
      setTableSchemaError("Database name is required");
      return;
    }

    const tablesToValidate = dartTableEntries.filter(entry => entry.dartTable.trim());
    if (tablesToValidate.length === 0) {
      setTableSchemaError("At least one table name is required");
      return;
    }

    setTableSchemaError("");
    setTableSchemaFields(() => []);

    const table_ids = tablesToValidate.map(entry => entry.dartTable.trim());

    try {
      const data = await validateDatasetTables(
        databaseName || defaultDatabaseName,
        table_ids
      );

      if (data?.phase === 'complete') {
        for (const entry of tablesToValidate) {
          const index = dartTableEntries.indexOf(entry);
          await getIndividualTableSchema(entry.dartTable, index);
        }
        setTableSchemaError("");
      } else {
        setTableSchemaError(data?.message || "Validation failed");
      }
    } catch (err: any) {
      const errorMessage = err.response?.data?.message || 
                          err.response?.data?.detail || 
                          (err.response ? `Server error: ${err.response.status}` : 
                          err.message || "Table validation failed.");
      setTableSchemaError(errorMessage);
    }
  }, [databaseName, defaultDatabaseName, dartTableEntries, setTableSchemaError, setTableSchemaFields, getIndividualTableSchema]);

  const handleRelationshipUseResponse = useCallback((
    response: any,
    isModified?: boolean,
    setRelationshipResponse?: (value: string) => void,
    setPrevRelationshipResponse?: (value: string) => void,
    setModifiedRelationshipResponse?: (value: any) => void,
  ) => {
    if (isModified && setModifiedRelationshipResponse) {
      const modifiedResponse = [response];
      setModifiedRelationshipResponse(modifiedResponse);
    } else if (setRelationshipResponse && setPrevRelationshipResponse) {
      const responseText = response.text_response || response.message || JSON.stringify(response);
      setRelationshipResponse(responseText);
      setPrevRelationshipResponse(responseText);
    }
  }, []);

  const handleDataDictionaryUseResponse = useCallback((
    response: any,
    isModified?: boolean,
    setModifiedDataDictionaryResponse?: (value: any) => void,
    setDataDictionaryJson?: (value: any) => void,
    setDataDictionaryState?: (fn: (prev: any) => any) => void,
  ) => {
    if (isModified && setModifiedDataDictionaryResponse && setDataDictionaryJson && setDataDictionaryState) {
      const modifiedResponse = [response];
      setModifiedDataDictionaryResponse(modifiedResponse);
      const dataToSet = response.tool_response || response;
      setDataDictionaryJson(dataToSet);
      setDataDictionaryState((prev) => ({ ...prev, isCompleted: true }));
    }
  }, []);

  const handleAnomalyUseResponse = useCallback((
    response: any,
    isModified?: boolean,
    setModifiedAnomalyResponse?: (value: any) => void,
    setAnomalyData?: (value: any) => void,
  ) => {
    if (isModified && setModifiedAnomalyResponse && setAnomalyData) {
      const modifiedResponse = [response];
      setModifiedAnomalyResponse(modifiedResponse);
      const dataToSet = response.tool_response || response;
      setAnomalyData(dataToSet);
    } else if (setAnomalyData) {
      setAnomalyData(response);
    }
  }, []);

  const handleMetadataUseResponse = useCallback((
    response: any,
    isModified?: boolean,
    setModifiedMetadataResponse?: (value: any) => void,
    setPrevMetadataResponse?: (value: any) => void,
  ) => {
    if (isModified && setModifiedMetadataResponse && setPrevMetadataResponse) {
      const modifiedResponse = response.tool_response;
      setModifiedMetadataResponse(modifiedResponse);
      setPrevMetadataResponse(JSON.stringify([{ tool_response: modifiedResponse }]));
    } else if (setPrevMetadataResponse) {
      setPrevMetadataResponse(response);
    }
  }, []);

  return {
    handleSimilarityCheck,
    handleValidateDatabase,
    handleRelationshipUseResponse,
    handleDataDictionaryUseResponse,
    handleAnomalyUseResponse,
    handleMetadataUseResponse,
  };
};
