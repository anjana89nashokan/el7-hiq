export interface ChatMessage {
  id: string;
  text: string;
  isBot: boolean;
  timestamp: Date;
}

export interface Step {
  id: number;
  title: string;
  description: string;
  completed: boolean;
}

export interface DataDictionaryState {
  response: string;
  json: any;
  resultData: any[];
  validationAuditLog: any[];
  isDdPresent: boolean;
  isCompleted: boolean;
  dataDictionaryTableId?: string;
}

export interface DartTableEntry {
  dartTable: string;
  column: string;
  isType2: boolean;
}

export const initialDataDictionaryState: DataDictionaryState = {
  response: "",
  json: null,
  resultData: [],
  validationAuditLog: [],
  isDdPresent: false,
  isCompleted: false,
};

export const initialSteps: Step[] = [
  {
    id: 1,
    title: "Dataset Overview",
    description: "Review dataset information and profiling report",
    completed: false,
  },
  {
    id: 2,
    title: "Relationship Analysis",
    description: "Analyze relationships between datasets",
    completed: false,
  },
  {
    id: 3,
    title: "Data Dictionary",
    description: "Generate comprehensive data dictionary",
    completed: false,
  },
  {
    id: 4,
    title: "Similarity Check",
    description: "Check similarity with reference tables",
    completed: false,
  },
  {
    id: 5,
    title: "Reference Suggestion",
    description: "Get reference table suggestions",
    completed: false,
  },
  {
    id: 6,
    title: "Data Anomaly Analysis",
    description: "Generate comprehensive data anomaly analysis",
    completed: false,
  },
  {
    id: 7,
    title: "Metadata Template",
    description: "Define and manage metadata templates",
    completed: false,
  },
  {
    id: 8,
    title: "Detailed Profiling",
    description: "View detailed table profiling information",
    completed: false,
  },
];

export const addDartTableEntry = (
  entries: DartTableEntry[],
): DartTableEntry[] => {
  return [...entries, { dartTable: "", column: "", isType2: false }];
};

export const updateDartTableEntry = (
  entries: DartTableEntry[],
  index: number,
  field: "dartTable" | "column" | "isType2",
  value: string,
): DartTableEntry[] => {
  return entries.map((entry, i) =>
    i === index ? { ...entry, [field]: value } : entry,
  );
};

export const removeDartTableEntry = (
  entries: DartTableEntry[],
  index: number,
): DartTableEntry[] => {
  if (entries.length > 1) {
    return entries.filter((_, i) => i !== index);
  }
  return entries;
};
