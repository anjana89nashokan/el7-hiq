export interface ChatMessage {
  id: string;
  text: string;
  isBot: boolean;
  timestamp: Date;
}

export interface ChatState {
  messages: ChatMessage[];
  isLoading: boolean;
  error: string | null;
}

export const initialChatState: ChatState = {
  messages: [],
  isLoading: false,
  error: null,
};

export const getAgentType = (stepTitle: string): string => {
  switch (stepTitle) {
    case 'Relationship Analysis':
      return 'profiling';
    case 'Data Dictionary':
      return 'data_dictionary';
    case 'Data Anomaly Analysis':
      return 'profiling';
    case 'Metadata Template':
      return 'metadata_fill';
    default:
      return 'profiling';
  }
};

export const getEndpointForStep = (stepId: number): string => {
  return stepId === 6 ? '/human_in_the_loop' : '/profiling_human_in_the_loop';
};