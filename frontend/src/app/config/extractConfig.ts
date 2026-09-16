export const ALLOWED_EXTENSIONS = [".pdf", ".docx"];
export const LAYOUT_ALLOWED_EXTENSIONS = [".pdf", ".docx", ".xlsx"];
export const MAX_FILE_SIZE_MB = 10;
export const MAX_FILE_SIZE = MAX_FILE_SIZE_MB * 1024 * 1024;

export const EXTRACT_STEPS = [
  { id: 1, title: "Gathering Requirement", description: "Upload documents, review and approve extraction" },
  { id: 2, title: "Extract Metadata", description: "Review extracted metadata" },
  { id: 3, title: "Generate Driver", description: "Generate driver from extraction" },
  { id: 4, title: "Extract Mapping", description: "View mapping rules" },
];

export const FIELD_INFO: Record<string, string> = {
  "BRD Document": "Business Requirements Document (BRD) describing the data extraction requirements. Accepted formats: PDF, DOCX.",
  "File Layout Document": "File layout specification document defining the structure and attributes of the target file. Accepted formats: PDF, DOCX, XLSX.",
  "Transcript (optional)": "Optional meeting transcript or supplementary notes to provide additional context for extraction.",
  "STTM Input (optional)": "Optional notes or instructions to guide the extraction process.",
  "Interface Code": "Unique identifier code for the interface being extracted (e.g. INTF_PROVIDER_001).",
};

export const UPLOAD_STEP_LABELS: Record<string, string> = {
  uploading: "Uploading files…",
  extracting: "Extracting BRD info…",
  validating: "Validating BRD…",
};
