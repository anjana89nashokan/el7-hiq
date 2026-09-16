import { BRANDING } from "./branding";

export const MESSAGES = {
  LOADING: {
    PROFILING_RESULTS: "Loading profiling results...",
    GENERATING_REPORT: "Generating profiling report...",
    PROCESSING_SIMILARITY: "Processing similarity check...",
    CHECKING_SIMILARITY: "Checking...",
  },

  NO_DATA: {
    TITLE: "No Data Available",
    NO_UPLOADS: "No files were successfully uploaded for profiling.",
    NO_FILES: "No Files Available",
    NO_FILES_DESC: "No successfully uploaded files found for profiling.",
    NO_PROFILING_DATA: "No Profiling Data Available",
    NO_PROFILING_DESC: "The profiling analysis did not return any data. Please try regenerating the report.",
    NO_ANALYSIS_DATA: "No Analysis Data",
    NO_ANALYSIS_DESC: "Profiling analysis has not been completed yet. Please wait or try refreshing.",
    NO_RESPONSE: "No response data available",
    NO_MESSAGE_CONTENT: "No message content",
  },

  ERRORS: {
    MISSING_PROFILING_DATA: "Missing Profiling Data",
    MISSING_PROFILING_DESC: "Cannot perform relationship analysis without initial profiling data.",
    MISSING_PROFILING_DICT_DESC: "Cannot generate data dictionary without initial profiling data.",
    MISSING_DETAILED_DESC: "Cannot display detailed profiling without initial data.",
    DATA_DICT_REQUIRED: "Data Dictionary Required",
    DATA_DICT_REQUIRED_DESC: "Please complete the Data Dictionary step first.",
    SIMILARITY_FAILED: "Similarity Check Failed",
    SIMILARITY_FAILED_DESC: "An error occurred during the similarity check. Please try again or skip this step.",
    NO_DATA_ANOMALY: "Cannot perform anomaly analysis without uploaded data.",
    NO_DATA_METADATA: "Cannot generate metadata template without uploaded data.",
    CHAT_ERROR: "Sorry, I encountered an error while processing your message",
    CONNECTION_ERROR: "I encountered an issue connecting to the profiling service, but I can still help you.",
    NO_RESPONSE_ERROR: "Error: No response received",
  },

  INFO: {
    SIMILARITY_OPTIONAL: "Similarity Check (Optional)",
    SIMILARITY_DESC: "Enter reference table references and click \"Check Similarity\" to compare with existing tables, or skip this step to continue.",
    RELATIONSHIP_WAITING: "Waiting for relationship analysis to complete...",
    RELATIONSHIP_DESC: "Please complete Step 2 (Relationship Analysis) to generate the data dictionary.",
    CHAT_START: "Start a conversation...",
    CHAT_DESC: "Ask questions about your data profiling results",
  },

  BUTTONS: {
    GO_BACK: "Go Back",
    RETRY: "Retry",
    CHECK_SIMILARITY: "Check Similarity",
    SKIP_SIMILARITY: "Skip Similarity Check",
    ADD_ROW: "Add Row",
    RETURN_OVERVIEW: "Return to Dataset Overview",
    RETURN_DICT: "Go to Data Dictionary",
  },

  NAVIGATION: {
    NEXT_RELATIONSHIP: "Next: Relationship Analysis",
    NEXT_DATA_DICT: "Next: Data Dictionary",
    NEXT_SIMILARITY: "Next: Similarity Check",
    NEXT_ANOMALY: "Next: Data Anomaly Analysis",
    NEXT_METADATA: "Next: Metadata Template",
    NEXT_DETAILED: "Next: Detailed Profiling",
    PREV_OVERVIEW: "Previous: Dataset Overview",
    PREV_RELATIONSHIP: "Previous: Relationship Analysis",
    PREV_DATA_DICT: "Previous: Data Dictionary",
    PREV_SIMILARITY: "Previous: Similarity Check",
    PREV_ANOMALY: "Previous: Data Anomaly Analysis",
    PREV_METADATA: "Previous: Metadata Template",
  },

  FORM: {
    DART_TABLE_PLACEHOLDER: "Enter reference table name",
    COLUMN_PLACEHOLDER: "Enter column name",
    CHAT_PLACEHOLDER: "Type a message...",
  },

  DEFAULTS: {
    NA: "N/A",
    UNNAMED_FILE: "Unnamed File",
    DEFAULT_BOT_RESPONSE: "I've analyzed your data profile. Here are the key insights:\n\n",
  },

  SECTIONS: {
    PROFILING_SUMMARY: "Profiling Summary",
    FAILED_UPLOADS: "Failed Uploads:",
    DART_REFERENCES: "Reference Table References",
    DETAILED_REPORT: "View Detailed Profiling Report",
    BSA_ASSISTANT: BRANDING.ASSISTANT_NAME,
  },
};